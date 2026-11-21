"""Synchronous bridge from protocol exercises to the three public TT output buses."""
from cocotb.task import resume
from cocotb.triggers import Timer


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
