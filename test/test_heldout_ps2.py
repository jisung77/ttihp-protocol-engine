"""Black-box device-to-host PS/2 receiver acceptance through TT pins (§2, §6)."""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer, ValueChange

from host_driver import Host


def firmware():
    supplied = os.environ.get("PS2_IMAGE")
    if supplied:
        return Path(supplied).read_bytes()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "ps2.bin"
        subprocess.run([sys.executable, "-m", "model.assembler",
                        "examples/ps2_rx.asm", str(binary)], cwd=root, check=True)
        return binary.read_bytes()


async def prepare(dut, sys_period_ns):
    host = Host(dut)
    dut.clk.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.serial_inputs.value = 0
    dut.uart_rx.value = 1
    dut.spi_miso.value = 0
    dut.uio_in.value = 3  # both external lines released high
    cocotb.start_soon(Clock(dut.clk, sys_period_ns, unit="ns").start(start_high=False))
    for _ in range(4):
        await host.tick()
    assert int(dut.uio_oe.value) == 0 and int(dut.uo_out.value) == 0
    dut.rst_n.value = 1
    for _ in range(4):
        await host.tick()

    async def monitor_oe():
        while True:
            await ValueChange(dut.uio_oe)
            assert int(dut.uio_oe.value) == 0, f"PS/2 receiver drove OE={dut.uio_oe.value}"

    cocotb.start_soon(monitor_oe())
    image = firmware()
    dut._log.info("PS2_IMAGE bytes=%d sha256=%s SYS_PERIOD_NS=%d", len(image),
                  hashlib.sha256(image).hexdigest(), sys_period_ns)
    await host.load(image)
    await host.run()
    await Timer(24137, unit="ns")  # idle before the first falling edge, off the system grid
    return host


def frame_bits(byte, defect):
    data = [(byte >> bit) & 1 for bit in range(8)]
    parity = 1 ^ (sum(data) % 2)
    return [int(defect == "start"), *data, parity ^ int(defect == "parity"),
            int(defect != "stop")]


async def send_frame(dut, byte, defect, period_ns, stress_rising):
    half = period_ns // 2
    quarter = half // 2
    for bit in frame_bits(byte, defect):
        await Timer(quarter, unit="ns")  # high, retain data that was present at rising
        dut.uio_in.value = 1 | (bit << 1)
        await Timer(half - quarter, unit="ns")
        dut.uio_in.value = bit << 1  # falling clock samples this bit
        await Timer(quarter, unit="ns")
        rising_data = bit ^ int(stress_rising)
        dut.uio_in.value = rising_data << 1
        await Timer(half - quarter, unit="ns")
        dut.uio_in.value = 1 | (rising_data << 1)
    # The next frame starts in the same high phase without an extra idle bit.


async def check_group(host, label, frames, expected, period_ns, stress_rising=False):
    dut = host.dut
    for byte, defect in frames:
        await send_frame(dut, byte, defect, period_ns, stress_rising)
    # Set idle after the final rising edge; previous low phase allowed RX commit.
    await Timer(period_ns // 4, unit="ns")
    dut.uio_in.value = 3
    await Timer(12000, unit="ns")
    assert (int(dut.uo_out.value) & 0xe0) == 0x60, f"{label}: READY/RUNNING/FAULT pins"
    assert int(dut.uio_oe.value) == 0, f"{label}: active output enables"
    observed = bytearray()
    for index, want in enumerate(expected):
        code = await host.command(7)
        assert code == 0, f"{label}: RX[{index}] wanted {want:02x}, response {code}"
        got = await host.reply_byte()
        observed.append(got)
        assert got == want, f"{label}: RX[{index}] expected {want:02x}, got {got:02x}"
    code = await host.command(7)
    if code == 0:
        extra = await host.reply_byte()
        raise AssertionError(f"{label}: unexpected extra RX {extra:02x}")
    assert code == 1, f"{label}: empty RX retry returned {code}"
    assert await host.status() == 0x0d, f"{label}: expected TX space, READY, RUNNING only"
    dut._log.info("PS2_GROUP %s period_ns=%d rising_stress=%d expected=%s observed=%s empty=1",
                  label, period_ns, stress_rising, bytes(expected).hex(), observed.hex())


@cocotb.test()
async def ps2_50mhz_contiguous(dut):
    host = await prepare(dut, 20)
    assert frame_bits(0x96, None) == list(map(int, "00110100111"))
    await check_group(host, "50MHz-12.5kHz", [(0x96, None), (0x69, None)],
                      [0x96, 0x69], 80000)


@cocotb.test()
async def ps2_frame_errors_and_recovery(dut):
    host = await prepare(dut, 500)
    for bad, period, byte, recovery in (
        ("parity", 100000, 0x01, 0x81),
        ("parity", 62500, 0x03, 0x7e),
        ("stop", 80000, 0x5a, 0xa7),
        ("start", 100000, 0x36, 0xc9),
    ):
        await check_group(host, f"bad-{bad}-{byte:02x}", [(0x96, None), (byte, bad),
                         (recovery, None)], [0x96, recovery], period)
    await check_group(host, "exclusively-invalid", [(0x55, "parity"), (0xe7, "stop"),
                     (0x01, "start")], [], 62500)
    await check_group(host, "falling-sample", [(0xaa, None), (0x55, None)], [0xaa, 0x55],
                      62500, stress_rising=True)

    if os.environ.get("PS2_FULL") == "1":
        # Odd stride permutes all 256 payloads, with no adjacent source increments.
        corpus = [(73 * i + 19) % 256 for i in range(256)]
        for i in range(0, len(corpus), 4):
            group = corpus[i:i + 4]
            period = (100000, 80000, 62500)[(i // 4) % 3]
            await check_group(host, f"corpus-{i:03}", [(byte, None) for byte in group],
                              group, period, stress_rising=bool((i // 4) % 2))
