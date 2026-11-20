import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def adder_vectors_and_pin_controls(dut):
    dut.ena.value = 1
    dut.rst_n.value = 1
    dut.clk.value = 0
    for a, b, expected in [(0, 0, 0), (255, 1, 0), (0x92, 0x27, 0xB9)]:
        dut.ui_in.value = a
        dut.uio_in.value = b
        await Timer(1, unit="ns")
        actual = int(dut.uo_out.value)
        assert actual == expected, f"{a} + {b}: expected {expected}, got {actual}"
        assert int(dut.uio_oe.value) == 0
        assert int(dut.uio_out.value) == 0
