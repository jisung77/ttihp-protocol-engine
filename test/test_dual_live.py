"""Close the SPI-target feedback loop on the live Tiny Tapeout pins."""
import json
from tempfile import TemporaryDirectory

import cocotb
from cocotb.task import bridge

from experiments.two_context.verify_v02 import build_images, verify_case
from live_pins import LivePins


@cocotb.test()
async def dual_uart_spi_closed_loop(dut):
    dut.serial_inputs.value = dut.spi_miso.value = dut.uart_rx.value = 0
    run_case = bridge(verify_case)
    with TemporaryDirectory() as directory:
        uart, spi = build_images(directory)
        for mode in range(4):
            for parity in (0, 1):
                pads = LivePins(dut)
                result = await run_case(pads, mode, parity, uart, spi[mode])
                assert pads.samples == result["trace_clocks"]
                dut._log.info("Live TT pins mode=%d parity=%d %s",
                              mode, parity, json.dumps(result, sort_keys=True))
