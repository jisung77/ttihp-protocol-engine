"""Measure whether a simulated input transition releases a waiting single-mode program."""
import json
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, Timer
from host_driver import Host
from model.assembler import assemble


@cocotb.test()
async def phase_width_sweep(dut):
    host = Host(dut)
    await host.reset()
    result = []
    for pin in range(8):
        for edge in ("RISE", "FALL"):
            polarity = 1 if edge == "RISE" else 0
            initial = (1 - polarity) << pin
            dut.uio_in.value = initial
            await host.load(assemble(f"WAIT {edge} {pin} FOREVER\nHALT\n"))
            for width in (100, 5000, 15000, 20000, 60000, 80000):
                for phase in (500, 5500, 10500, 15500):
                    await host.run()
                    for _ in range(10):
                        await host.tick()
                    assert int(dut.uo_out.value) & 0x40, ("unprovoked wake", pin, edge)
                    await RisingEdge(dut.clk)
                    await Timer(phase, unit="ps")
                    dut.uio_in.value = polarity << pin
                    await Timer(width, unit="ps")
                    dut.uio_in.value = initial
                    for _ in range(12):
                        await host.tick()
                    state = int(dut.uo_out.value)
                    assert not (state & 0x80) and not int(dut.uio_oe.value)
                    assert state & 0x20
                    woke = not bool(state & 0x40)
                    result.append(dict(pin=pin, edge=edge, width_ps=width, phase_ps=phase,
                                       halted=woke))
                    if width >= 60000:
                        assert woke, result[-1]
                    assert await host.command(5) == 0
    output = Path("output/async-input.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
