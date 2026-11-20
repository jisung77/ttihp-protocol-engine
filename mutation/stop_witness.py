"""Diagnostic outside the scored regression, based on spec §2 and §6."""
import cocotb
from host_driver import Host


@cocotb.test()
async def stop_on_branch_execute(dut):
    host = Host(dut)
    await host.reset()
    await host.load(bytes.fromhex("00 50"))  # BR 0 forever: execute on even K offsets.
    await host.run()
    assert await host.command(5) == 0      # two 2FF handshakes, acceptance on K+6.
    assert not (int(dut.uo_out.value) & 0x40)
    assert int(dut.user_project.retire.value) == 0, "STOP must suppress same-edge retirement"
