"""Close the SPI-target feedback loop on the live Tiny Tapeout pins."""
import json
from tempfile import TemporaryDirectory

import cocotb
from cocotb.task import bridge, resume
from cocotb.triggers import Timer

from experiments.two_context.verify_v02 import build_images, verify_case


class LivePins:
    """Read only the three public outputs after each actual HDL clock edge."""

    def __init__(self, dut):
        self.dut = dut
        self.half_period = Timer(10, unit="ns")
        self.samples = 0

    @resume
    async def tick(self, ui_in, uio_in, rst_n, ena):
        dut = self.dut
        dut.clk.value = 0
        dut.ui_in.value = ui_in
        dut.uio_in.value = uio_in
        dut.rst_n.value = rst_n
        dut.ena.value = ena
        await self.half_period
        dut.clk.value = 1
        await self.half_period
        self.samples += 1
        return {
            "uo_out": int(dut.uo_out.value),
            "uio_out": int(dut.uio_out.value),
            "uio_oe": int(dut.uio_oe.value),
        }


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
