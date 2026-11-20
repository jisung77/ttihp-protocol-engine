"""Pin-level acceptance checks for spec §8 at a 20 ns system clock."""

import os
from pathlib import Path

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer, with_timeout
from cocotb.utils import get_sim_time
from cocotb_coverage.coverage import CoverPoint, coverage_db
from cocotbext.spi import SpiBus, SpiConfig, SpiSlaveBase
from cocotbext.uart import UartSink, UartSource

from host_driver import Host
from model.assembler import assemble


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
BAUD = 115200
PS_PER_SEC = 10**12


async def loaded(dut, program):
    host = Host(dut)
    await host.reset()
    dut.serial_inputs.value = 1
    dut.uart_rx.value = 1
    dut.spi_miso.value = 0
    await host.load(assemble((EXAMPLES / (program + ".asm")).read_text(encoding="utf-8")))
    return host


async def record_transitions(pin, events):
    while True:
        await pin.value_change
        await ReadOnly()
        events.append((int(get_sim_time(unit="ps")), int(pin.value)))


def check_uart_intervals(events, expected, end_time):
    """Check each observable run of equal symbols to 1%, then check minimum stop width."""
    cursor = next(i for i, (_, level) in enumerate(events) if level == 0)
    for idx, byte in enumerate(expected):
        start, level = events[cursor]
        assert level == 0, ("missing start", idx, start)
        cursor += 1
        prev_time, prev_slot = start, 0
        symbols = [0] + [(byte >> bit) & 1 for bit in range(8)] + [1]
        for slot in range(1, 10):
            if symbols[slot] == symbols[slot - 1]:
                continue
            at, value = events[cursor]
            cursor += 1
            assert value == symbols[slot], ("UART transition", idx, slot)
            span = slot - prev_slot
            error = abs((at - prev_time) * BAUD - span * PS_PER_SEC)
            assert 100 * error <= span * PS_PER_SEC, ("UART interval", idx, slot, at - prev_time)
            prev_time, prev_slot = at, slot
        # Stop can be extended when the program waits for data. The tail since
        # the last observable transition must contain its remaining data + stop.
        following = events[cursor][0] if cursor < len(events) else end_time
        assert 100 * (following - prev_time) * BAUD >= 99 * (10 - prev_slot) * PS_PER_SEC, (
            "short stop", idx, following - prev_time)
    assert cursor == len(events), ("extra UART transitions", events[cursor:])


@cocotb.test(timeout_time=32, timeout_unit="ms")
async def uart_tx_8n1_all_bytes(dut):
    host = await loaded(dut, "uart_tx")
    sink = UartSink(dut.uart_tx, baud=BAUD, bits=8, stop_bits=1)
    pins, enables = [], []
    pin_task = cocotb.start_soon(record_transitions(dut.uart_tx, pins))
    oe_task = cocotb.start_soon(record_transitions(dut.uio_oe, enables))
    expected = bytes(range(256))
    actual = bytearray()
    for start in range(0, len(expected), 4):
        batch = expected[start:start + 4]
        for value in batch:
            await host.byte_tx(value)
        if start == 0:
            await host.run()
        for _ in batch:
            actual.extend(await with_timeout(sink.read(1), 130, "us"))
    await Timer(12, unit="us")
    assert actual == expected
    assert int(dut.uart_tx.value) == 1 and int(dut.uio_oe.value) & 1
    pin_task.cancel()
    oe_task.cancel()
    driven = next(i for i, (_, oe) in enumerate(enables) if oe & 1)
    assert all(oe & 1 for _, oe in enables[driven:]), "TX drive dropped in the stream"
    check_uart_intervals([(t, v) for t, v in pins if t >= enables[driven][0]], expected,
                         int(get_sim_time(unit="ps")))


async def send_bad_stop(pin, rate):
    bit_time = Timer(10**9 // rate, unit="ns")
    for value in [0] + [(0x53 >> bit) & 1 for bit in range(8)] + [0]:
        pin.value = value
        await bit_time
    pin.value = 1
    await bit_time


@cocotb.test(timeout_time=120, timeout_unit="ms")
@cocotb.parametrize(rate=[112896, 115200, 117504])
async def uart_rx_phase_and_rate(dut, rate):
    host = await loaded(dut, "uart_rx")
    source = UartSource(dut.uart_rx, baud=rate, bits=8, stop_bits=1)
    await host.run()
    await Timer(2, unit="us")  # allow the receiver to observe idle high
    data = range(256) if os.environ.get("TT_EXTENDED") == "1" else (0, 0x55, 0xA6, 0xFF)
    for phase_ps in (1, 5000, 10000, 15000):
        for byte in data:
            await RisingEdge(dut.clk)
            await Timer(phase_ps, unit="ps")  # phase 0 is just after the rising sample
            source.write_nowait([byte])
            await with_timeout(source.wait(), 100, "us")
            assert await host.byte_rx() == byte, ("UART RX", rate, phase_ps, byte)
    # The second START immediately follows the first frame's STOP.
    source.write_nowait([0x55, 0xCA])
    await with_timeout(source.wait(), 200, "us")
    assert [await host.byte_rx(), await host.byte_rx()] == [0x55, 0xCA]
    assert not ((await host.status()) & 2)
    await send_bad_stop(dut.uart_rx, rate)
    assert not ((await host.status()) & 2), "RX FIFO accepted a frame with LOW STOP"
    source.write_nowait([0x96])
    await with_timeout(source.wait(), 100, "us")
    assert await host.byte_rx() == 0x96
    dut._log.info("RX %d baud: %d data values x 4 start phases", rate, len(data))


class ByteTarget(SpiSlaveBase):
    def __init__(self, dut, mode, replies):
        self._config = SpiConfig(cpol=bool(mode & 2), cpha=bool(mode & 1), msb_first=True)
        self.replies = replies
        self.received = []
        bus = SpiBus.from_entity(dut, sclk_name="spi_sclk", mosi_name="spi_mosi",
                                 miso_name="spi_miso", cs_name="spi_cs_n")
        super().__init__(bus)

    async def _transaction(self, frame_start, frame_end):
        await frame_start
        number = len(self.received)
        assert number < len(self.replies), "extra chip-select assertion"
        word = self.replies[number]
        if not self._config.cpha:
            # SlaveBase changes MISO after the second edge in CPHA=0.
            # Preload the first bit and give _shift the following seven bits.
            self._miso.value = (word >> 7) & 1
            word = (word << 1) & 255
        self.received.append(await self._shift(8, word))
        await frame_end


async def record_spi(dut, samples):
    # Observe settled pin values half a system clock after the DUT updates.
    while True:
        await FallingEdge(dut.clk)
        samples.append((int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.spi_miso.value)))


def check_spi_frames(samples, mode, tx, rx):
    cpol, cpha = mode >> 1, mode & 1
    idle = False
    selected = None
    frames = []
    for tick, (pins, oe, _) in enumerate(samples):
        if oe & 0x0F != 0x0B:
            assert selected is None, ("drive enables changed during CS", mode, tick)
            continue
        if pins & 8:
            assert pins & 1 == cpol, ("idle polarity", mode, tick)
            if selected is not None:
                frames.append((selected, tick))
                selected = None
            idle = True
        elif idle and selected is None:
            selected = tick
    assert selected is None and len(frames) == len(tx)
    for n, (begin, end) in enumerate(frames):
        sck = [t for t in range(begin, end + 1) if (samples[t][0] ^ samples[t-1][0]) & 1]
        assert len(sck) == 16, ("clock edges", mode, n, sck)
        assert sck[0] - begin >= 25 and end - sck[-1] >= 25, ("CS setup/hold", mode, n)
        assert all(24 <= b-a <= 26 for a, b in zip(sck, sck[1:])), ("half-period", mode, n)
        mosi_moves = [t for t in range(begin, end + 1) if (samples[t][0] ^ samples[t-1][0]) & 2]
        miso_moves = [t for t in range(begin, end + 1) if samples[t][2] != samples[t-1][2]]
        captures = sck[cpha::2]
        launches = sck[::2] if cpha else [begin] + sck[1:-1:2]
        for move in mosi_moves:
            if move <= captures[-1]:
                assert any(a <= move <= b-2 for a, b in zip(launches, captures)), (
                    "MOSI outside launch window", mode, n, move)
        for idx, t in enumerate(captures):
            assert all(abs(t-move) >= 2 for move in mosi_moves), ("MOSI setup/hold", mode, n, idx)
            assert all(abs(t-move) >= 3 for move in miso_moves), ("MISO stimulus", mode, n, idx)
            assert (samples[t][0] >> 1) & 1 == (tx[n] >> (7-idx)) & 1
            assert samples[t][2] == (rx[n] >> (7-idx)) & 1


@CoverPoint("protocol.spi_mode", bins=[0, 1, 2, 3])
def observed_spi_mode(mode):
    pass


@cocotb.test(timeout_time=1, timeout_unit="ms")
@cocotb.parametrize(mode=range(4))
async def spi_master_full_duplex(dut, mode):
    host = await loaded(dut, f"spi_mode{mode}")
    tx, rx = (0x96, 0x4B), (0x3C, 0xA5)
    target = ByteTarget(dut, mode, rx)
    samples = []
    recorder = cocotb.start_soon(record_spi(dut, samples))
    for value in tx:
        await host.byte_tx(value)
    await host.run()
    await Timer(30, unit="us")
    received = [await host.byte_rx(), await host.byte_rx()]
    recorder.cancel()
    assert target.received == list(tx)
    assert received == list(rx)
    assert not ((await host.status()) & 2)
    check_spi_frames(samples, mode, tx, rx)
    observed_spi_mode(mode)
    report = Path(os.getenv("SPI_COVERAGE_FILE", "output/spi-coverage.yml"))
    report.parent.mkdir(exist_ok=True, parents=True)
    coverage_db.export_to_yaml(filename=str(report))
