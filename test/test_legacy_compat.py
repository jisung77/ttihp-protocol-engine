"""Pin-only v0.1 compatibility vectors for the §7 program at both RUN parities."""
import json
import random

import cocotb
from cocotb.triggers import Timer

from host_driver import Host


# Each row gives the full output bus, full OE bus and FAULT/RUNNING/READY.
# §7 COMMIT becomes visible at K+6; HALT keeps DATA and releases OE at K+8.
SECTION7 = ((0, 0, 3),) * 6 + ((1, 1, 3), (1, 1, 3), (1, 0, 1))


def check_section7(rows):
    assert len(rows) == len(SECTION7)
    for offset, (actual, expected) in enumerate(zip(rows, SECTION7)):
        assert tuple(actual) == expected, f"K+{offset}: observed {tuple(actual)}, expected {expected}"


class CountedHost(Host):
    def __init__(self, dut):
        super().__init__(dut)
        self.edge = 0
        self.noise = None
        self.observed_upper = set()

    async def tick(self):
        if self.noise is not None:
            # Allow the host's newly driven REQ and nibble to settle first.
            await Timer(1, unit="ns")
            lower = int(self.dut.ui_in.value) & 0x1f
            self.dut.ui_in.value = (self.noise.randrange(8) << 5) | lower
        await super().tick()
        self.edge += 1
        self.observed_upper.add(int(self.dut.ui_in.value) >> 5)


@cocotb.test(timeout_time=100, timeout_unit="us")
@cocotb.parametrize(vary_upper=[False, True])
async def legacy_section7_run_parities(dut, vary_upper):
    h = CountedHost(dut)
    await h.reset()
    assert h.observed_upper == {0}
    if vary_upper:
        h.noise = random.Random(18455)  # §1: v0.1 ignores [7:5]; legacy strap sampled already.

    for parity in (0, 1):
        await h.load(bytes.fromhex("01 08 01 10 01 18 00 00"))
        # Use the first nibble to measure transfer latency before aligning the second.
        before = h.edge
        assert await h.nibble(0) == 0
        assert int(dut.uo_out.value) >> 5 == 1
        if (h.edge + (h.edge - before)) % 2 != parity:
            await h.tick()
        assert await h.nibble(4) == 0
        assert ((int(dut.uo_out.value) >> 4) & 1) == h.req
        accepted = h.edge
        assert accepted % 2 == parity

        rows = []
        for offset in range(len(SECTION7)):
            if offset:
                await h.tick()
            rows.append((int(dut.uio_out.value), int(dut.uio_oe.value), int(dut.uo_out.value) >> 5))
        dut._log.info("legacy_section7 %s", json.dumps(dict(upper=vary_upper, accepted=accepted, rows=rows)))
        check_section7(rows)
    assert h.observed_upper == (set(range(8)) if vary_upper else {0})
