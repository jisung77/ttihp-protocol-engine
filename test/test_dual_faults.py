"""Additional pin scenarios from §6 and §9.2/4, following the frozen mutation run."""

import cocotb

from model.test_v02_pins import Pins, UART_PINS
from test_dual import RecordingMachine, replay


def forbidden_shout(machine):
    p = Pins(tick=machine.tick)
    # A waits on pin 7 after committing pin 4; it can later commit pin 5.
    p.load(0, (*UART_PINS[:3], 0x302f, 0x0820, 0x1020, 0x1830, 0))
    # B commits pin 1, then attempts SHOUT on A's pin 4.
    p.load(1, (0x0802, 0x1002, 0x1802, 0x7800, 0x4804, 0))
    ka = p.run(0, 1)
    p.until(ka + 8)
    k = p.run(1, 0)
    p.inputs.update({edge: 0x80 for edge in range(k + 10, k + 22)})
    p.until(k + 21)
    for offset in (6, 7, 8, 9):
        before = p.rows[k + offset]
        assert (before["uio_out"], before["uio_oe"]) == (0x12, 0x12)
    fault = p.rows[k + 10]
    assert fault["exec_start"][1] and not fault["retire"][1] and fault["pc"][1] == 4
    assert (fault["uo_out"] >> 5, fault["uio_out"], fault["uio_oe"]) == (5, 0x12, 0x10)
    # The sibling resumes its own WAIT, then its COMMIT reaches physical pins.
    assert (p.rows[k + 19]["uio_out"], p.rows[k + 19]["uio_oe"]) == (0x22, 0x20)
    assert (p.rows[k + 21]["uio_out"], p.rows[k + 21]["uio_oe"]) == (0x22, 0)
    assert p.status(1) == 0x15 and p.status(0) == 5


def wrong_checksum(machine):
    p = Pins(tick=machine.tick)
    p.load(0, (0,))  # ensure BEGIN revokes an already READY image
    # Collect eight HIGH samples, push 0xff, then fault on an undefined opcode.
    p.load(1, (0x0800, *([0x400a] * 8), 0x6800, 0xffff))
    assert p.command(1, 6, (0x96,)) == 0  # B TX remains populated through its fault
    k = p.run(1, 0)
    p.inputs.update({edge: 4 for edge in range(k + 1, k + 24)})
    p.until(k + 24)
    assert p.status(1) == 0x17
    assert p.command(0, 1, (2, 0)) == 0
    assert p.command(0, 2, (1, 8)) == 0 and p.command(0, 2, (0, 0)) == 0
    assert p.status(0) == 0x21
    lo = p.edge
    assert p.command(0, 3, (0, 8)) == 3  # actual 0x0801; same count, one wrong checksum bit
    for edge in range(lo + 1, p.edge + 1):
        row = p.rows[edge]
        assert not row["sram_we"]
        assert tuple(row["tx_count"]) == tuple(row["rx_count"]) == (0, 1)
    assert p.status(0) == 1 and p.command(0, 4) == 2
    assert p.command(0, 3, (1, 8)) == 2  # failed session ended, no retroactive repair
    assert p.status(1) == 0x17 and p.command(1, 7) == 0
    assert p.nibble(0, 0) << 4 | p.nibble(0, 1) == 0xff
    assert tuple(p.rows[p.edge]["tx_count"]) == (0, 1)
    p.load(0, (0,))
    p.run(0, 1)
    p.until(p.edge + 3)
    assert p.status(0) == 5 and p.status(1) == 0x15


@cocotb.test()
async def dual_fault_isolation_and_bad_end(dut):
    for oracle in (forbidden_shout, wrong_checksum):
        reference = RecordingMachine()
        oracle(reference)
        dut._log.info("Replaying %s", oracle.__name__)
        await replay(dut, reference.cycles)
