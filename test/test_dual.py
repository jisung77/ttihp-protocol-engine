"""Drive the exact pin history adjudicated by the §9 ISS and independent oracles."""
from collections import deque
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cocotb
from cocotb.triggers import Timer

from model.protocol_machine import ProtocolMachine
from model import test_v02_pins as pin_oracles


class RecordingMachine:
    def __init__(self):
        self.machine = ProtocolMachine()
        self.cycles = []

    def tick(self, ui_in=0, uio_in=0, rst_n=1, ena=1):
        pins = dict(ui_in=ui_in, uio_in=uio_in, rst_n=rst_n, ena=ena)
        row = self.machine.tick(**pins)
        self.cycles.append((pins, row))
        return row


def wanted(row, core):
    values = {key: row[key] for key in ("uo_out", "uio_out", "uio_oe")}
    if not core:
        return values
    for key in ("dual_mode", "fetch_valid", "instr_valid", "sram_we"):
        values[key] = row[key]
    if row["fetch_valid"] or row["sram_we"]:
        values["sram_addr"] = row["sram_addr"]
    if row["fetch_valid"]:
        values["fetch_ctx"] = row["fetch_ctx"]
    if row["instr_valid"]:
        values["instr_ctx"] = row["instr_ctx"]
        values["instruction"] = row["instruction"]
    for key in ("pc", "exec_start", "retire", "tx_pop", "rx_push", "tx_count", "rx_count"):
        for index, suffix in enumerate(("_a", "_b")):
            values[key + suffix] = row[key][index]
    return values


async def replay(dut, cycles):
    core = os.environ.get("GATES") != "yes"
    dut.serial_inputs.value = dut.spi_miso.value = dut.uart_rx.value = 0
    dut.clk.value = 0
    history = deque(maxlen=20)
    signals = {}
    for pins, row in cycles:
        dut.clk.value = 0
        for name, value in pins.items():
            getattr(dut, name).value = value
        await Timer(10, unit="ns")
        dut.clk.value = 1
        await Timer(9, unit="ns")
        for name, expected in wanted(row, core).items():
            if name not in signals:
                owner = dut if name in ("uo_out", "uio_out", "uio_oe") else dut.user_project
                signals[name] = getattr(owner, name)
            sample = signals[name].value
            if not sample.is_resolvable or int(sample) != expected:
                raise AssertionError(f"E{row['edge']} {name}: expected {expected}, got {sample}; "
                                     f"pins={pins}, previous 20 (edge, ui, pins, out, oe)={list(history)}")
        history.append((row["edge"], pins["ui_in"], pins["uio_in"], row["uio_out"], row["uio_oe"]))
        await Timer(1, unit="ns")


def directed_cases():
    """Expand the independent test module's own parameter tables in definition order."""
    for name, fn in vars(pin_oracles).items():
        if not name.startswith("test_") or not callable(fn):
            continue
        marks = getattr(fn, "pytestmark", ())
        if not marks:
            yield name, fn, {}
            continue
        # These tests use one pytest.mark.parametrize table per function.
        mark, = marks
        if mark.name != "parametrize":
            raise ValueError(f"Unsupported mark for pin replay: {name} {mark.name}")
        keys, cases = mark.args
        if isinstance(keys, str):
            keys = [key.strip() for key in keys.split(",")]
        for params in cases:
            values = (params,) if len(keys) == 1 else params
            arguments = dict(zip(keys, values, strict=True))
            yield f"{name}{tuple(values)}", fn, arguments


@cocotb.test()
async def dual_run_and_wait_edges(dut):
    for label, oracle, arguments in directed_cases():
        dut._log.info("Replaying %s", label)
        reference = RecordingMachine()
        with patch.object(pin_oracles, "new_machine", return_value=reference):
            oracle(**arguments)
        assert reference.cycles and not reference.cycles[0][0]["rst_n"], label
        await replay(dut, reference.cycles)


@cocotb.test()
async def dual_uart_spi_modes(dut):
    from experiments.two_context.verify_v02 import build_images, verify_case

    with TemporaryDirectory() as directory:
        uart, spi = build_images(Path(directory))
        for mode in range(4):
            for parity in (0, 1):
                reference = RecordingMachine()
                verify_case(reference, mode, parity, uart, spi[mode])
                await replay(dut, reference.cycles)
