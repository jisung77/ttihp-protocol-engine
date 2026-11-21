"""Compare I²C pin captures from the real HDL clock and the independent ISS."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import cocotb
from cocotb.task import bridge

from experiments.i2c_write.run import CASES, run_case
from model.protocol_machine import ProtocolMachine
from live_pins import LivePins


def compare_pins(case, reference, actual):
    if reference == actual:
        return
    offset = next((i for i, (a, b) in enumerate(zip(reference, actual)) if a != b),
                  min(len(reference), len(actual)))
    tick = offset // 3
    start, end = 3 * max(0, tick - 20), 3 * (tick + 1)
    raise AssertionError(f"{case} differs at clock {tick}, bytes per capture "
                         f"{len(reference)} (ISS), {len(actual)} (HDL). "
                         f"Up to 20 preceding clocks and failing sample, OUT/OE/target-low: "
                         f"ISS={reference[start:end].hex(' ', -3)} HDL={actual[start:end].hex(' ', -3)}")


@cocotb.test()
async def i2c_standard_mode_write(dut):
    dut.serial_inputs.value = dut.spi_miso.value = dut.uart_rx.value = 0
    run_hdl = bridge(run_case)
    output = Path(os.environ.get('I2C_PIN_TRACE_DIR', 'output'))
    with TemporaryDirectory() as temporary:
        for case in CASES:
            reference = run_case(ProtocolMachine(), case, temporary)
            result = await run_hdl(LivePins(dut), case, output)
            name = f'i2c-{case.name}.bin'
            compare_pins(case.name, (Path(temporary) / name).read_bytes(), (output / name).read_bytes())
            assert reference['host'] == result['host'], case.name
            dut._log.info("I2C pins %s", json.dumps(result, sort_keys=True))
