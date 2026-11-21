"""Compare the previously accepted v0.1 reference with the new pin-level model."""
from itertools import islice
from random import Random

import pytest

from model.iss import trace_engine
from model.test_v02_pins import Pins


@pytest.mark.parametrize("run_parity", (0, 1))
def test_original_execution_trace(run_parity):
    # All sixteen opcodes, including a conditional branch, FIFO and finite WAIT.
    words = [0x7000, 0x080b, 0x100f, 0x180f, 0x2003, 0x2800, 0x3800,
             0x3010, 0x510a, 0x7842, 0x5804, 0x4008, 0x4809,
             0x600b, 0x6800, 0]
    image = b"".join(x.to_bytes(2, "little") for x in words)
    host = Pins(strap=0)
    host.load(0, words)
    assert host.command(0, 6, (0xa3,)) == 0
    k = host.run(0, run_parity)
    rng = Random(0x2d71)
    pins = [0] + [rng.randrange(256) for _ in range(255)]
    legacy = trace_engine(image, pins, tx_initial=(0xa3,), pre_run=(0, 0, 0))
    steps = list(islice(legacy, 256))
    assert steps and not steps[-1].edge.running and not steps[-1].fault
    for offset, step in enumerate(steps):
        if offset:
            host.inputs[k + offset] = pins[offset]
            host.step()
        new = host.rows[k + offset]
        old = step.edge
        for name in ("pc", "retire", "exec_start", "tx_pop", "rx_push", "tx_count", "rx_count"):
            expect = getattr(old, name) if hasattr(old, name) else getattr(step, name)
            assert new[name] == (expect, 0), (offset, name, new[name], expect)
        assert (new["uio_out"], new["uio_oe"], bool(new["uo_out"] & 64),
                new["fetch_valid"]) == (old.uio_out, old.uio_oe, old.running, old.fetch)
