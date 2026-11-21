"""§§3–6 and §9 execution tests from preloaded, post-END checkpoints.

Each test drives the engine's synchronized input observation on absolute rising
edges. Loader framing, raw-pin synchronization and strap timing are exercised
separately at the pin boundary. Opcode words and expected cycles come from the
ISA and the fixed A/B slot assignment.
"""

import pytest

from model.dual_iss import Engine


class Bench:
    def __init__(self):
        self.engine = Engine()
        self.edge = 0
        self.sample = 0

    def image(self, cid, words):
        """Install an already confirmed image without modeling the nibble loader."""
        self.engine.host_begin(cid, len(words))
        base = 128 * cid
        self.engine.mem[base:base + len(words)] = words
        ctx = self.engine.ctx[cid]
        ctx.loading = False
        ctx.ready = True

    def tick(self, sample=None, **events):
        previous = self.sample
        if sample is not None:
            self.sample = sample
        self.edge += 1
        trace = self.engine.step(self.edge, True, self.sample, previous, **events)
        assert sum(trace["retire"]) <= 1, f"two retires at E{self.edge}"
        return trace

    def until(self, edge):
        assert edge > self.edge
        while self.edge < edge - 1:
            self.tick()
        return self.tick()

    def run(self, cid, edge):
        if self.edge < edge - 1:
            self.until(edge - 1)
        assert self.edge == edge - 1
        self.engine.host_run(cid)
        return self.tick(started=cid)


@pytest.mark.parametrize(("delay", "elapsed"), [(0, 0), (1, 2), (2, 2), (65535, 65536)])
def test_delay_in_wall_clocks(delay, elapsed):
    """§9.3: odd delays round up, including the carry past 16 bits."""
    b = Bench()
    b.image(0, [0x2800 | (delay >> 8), 0x2000 | (delay & 255), 0x3800, 0])
    b.run(0, 7)
    trace = b.until(13)  # THI at E9, TLO at E11, DELAY starts at E13.
    assert trace["exec_start"][0]
    assert bool(trace["retire"][0]) == (elapsed == 0)
    if elapsed:
        assert trace["pc"][0] == 2
        for _ in range(elapsed - 1):
            trace = b.tick()
            assert not trace["fetch_valid"] and not trace["retire"][0]
            assert not trace["exec_start"][0] and trace["pc"][0] == 2
        trace = b.tick()
        assert trace["retire"][0] and not trace["exec_start"][0]
    assert b.edge == 13 + elapsed and trace["pc"][0] == 3
    trace = b.tick()
    assert trace["fetch_valid"] and trace["sram_addr"] == 3


def start_two_waits():
    b = Bench()
    # Both wait for pin 6 RISE. A starts its WAIT at E11, B at E10.
    # Successful WAIT branches to HALT at 4; timeout falls through to PC 3.
    b.image(0, [0x2001, 0x3016, 0x5104, 0x0000, 0x0000])
    b.image(1, [0x2002, 0x3016, 0x5104, 0x0000, 0x0000])
    b.run(1, 6)
    b.run(0, 7)
    b.until(9)
    return b


@pytest.mark.parametrize(
    ("rise", "retire_at", "branch_to"),
    [(10, (13, 10), (3, 4)), (11, (11, 12), (4, 4)),
     (12, (13, 12), (4, 4)), (13, (13, 12), (3, 3))],
)
def test_wait_edge_windows_and_simultaneous_decisions(rise, retire_at, branch_to):
    """§§4,9.3: one clock of HIGH; S−1, S, N and N+1 differ between contexts."""
    b = start_two_waits()
    rows = {n: b.tick(0x40 if n == rise else 0) for n in range(10, 17)}
    assert rows[10]["exec_start"][1] and rows[11]["exec_start"][0]
    for cid, start in [(0, 11), (1, 10)]:
        for n in range(start, retire_at[cid]):
            assert rows[n]["pc"][cid] == 1 and not rows[n]["retire"][cid]
        r = retire_at[cid]
        assert rows[r]["retire"][cid] and rows[r]["pc"][cid] == 2
        assert rows[r + 2]["retire"][cid]
        assert rows[r + 2]["pc"][cid] == branch_to[cid]


@pytest.mark.parametrize("stop", [12, 13])
def test_stop_cancels_wait_only_for_addressed_context(stop):
    """§9.3: STOP A on d or delayed R, while B can retire or fetch normally."""
    b = start_two_waits()
    rows = {}
    for n in range(10, 16):
        if n == stop:
            b.engine.host_stop(0)
        rows[n] = b.tick(0x40 if n == 12 else 0, stopped=0 if n == stop else None)
    assert rows[12]["retire"][1] and rows[14]["pc"][1] == 4
    assert not any(rows[n]["retire"][0] for n in range(11, 16))
    assert rows[stop]["pc"][0] == 0 and b.engine.ctx[0].ready
    assert not b.engine.ctx[0].running and not b.engine.ctx[0].fault


@pytest.mark.parametrize(("last", "halts"), [(0, True), (0x0801, False), (0x5080, False)])
def test_partition_high_address(last, halts):
    """§9.2: B's logical PC 127 uses physical word 255 without wrapping."""
    b = Bench()
    b.image(1, [0x507F] + [0] * 126 + [last])
    b.run(1, 6)
    assert b.until(8)["pc"][1] == 127
    t = b.tick()
    assert t["fetch_valid"] and t["fetch_ctx"] == 1 and t["sram_addr"] == 255
    t = b.tick()
    assert t["pc"][1] == 127 and not b.engine.ctx[1].running
    assert bool(t["retire"][1]) == halts
    assert bool(b.engine.ctx[1].fault) != halts


def test_untaken_target_outside_partition_is_ignored():
    b = Bench()
    b.image(0, [0x51FF, 0])  # BR OK 255 with WAIT result NONE.
    b.run(0, 7)
    t = b.until(9)
    assert t["retire"][0] and t["pc"][0] == 1 and not b.engine.ctx[0].fault


@pytest.mark.parametrize(("lc", "after_djnz"), [(0, 2), (1, 2), (2, 3)])
def test_djnz_uses_decremented_count(lc, after_djnz):
    """§3: BR LC=0 makes underflow observable using only the PC trace."""
    b = Bench()
    b.image(0, [0x5800 | lc, 0x6003, 0x5304, 0x5001, 0])
    b.run(0, 7)
    assert b.until(11)["pc"][0] == after_djnz
    if lc == 2:
        assert b.until(13)["pc"][0] == 1
        assert b.until(15)["pc"][0] == 2
    assert b.until(b.edge + 2)["pc"][0] == 4


@pytest.mark.parametrize("lsb", [False, True])
def test_shift_streams_and_shadow_commit(lsb):
    """§§3,9.2: A reads sibling-owned pin 1 and emits a different byte on pin 4."""
    b = Bench()
    order = 8 if lsb else 0
    mask = 0x10
    b.image(0, [0x7897, 0x1010, 0x1810] + [0x4001 | order, 0x4804 | order] * 8
            + [0x6800, 0x1810, 0])
    b.run(0, 7)
    t = b.until(13)
    assert (t["uio_out"], t["uio_oe"]) == (0, mask)
    for bit in (range(8) if lsb else range(7, -1, -1)):
        wire = (0xA6 >> bit) & 1
        b.tick((1 - wire) << 1)
        t = b.tick(wire << 1)  # SHIN examines the S observation.
        assert t["retire"][0]
        b.tick((1 - wire) << 1)
        t = b.tick()  # SHOUT uses the previous bit of the independent OSR.
        assert (t["uio_out"], t["uio_oe"]) == (mask * ((0x97 >> bit) & 1), mask)
    t = b.until(b.edge + 2)
    assert t["rx_push"][0] and list(b.engine.ctx[0].rx) == [0xA6]
    t = b.until(b.edge + 2)  # Recommit: SHOUT also updated prepared DATA.
    assert (t["uio_out"], t["uio_oe"]) == (mask, mask)
    t = b.until(b.edge + 2)
    assert (t["uio_out"], t["uio_oe"]) == (mask, 0)


def test_empty_pull_cannot_consume_same_edge_host_write():
    b = Bench()
    b.image(0, [0x7000, 0x4804, 0])
    b.run(0, 7)
    b.until(8)
    b.engine.ctx[0].tx.append(0x80)  # Accepted host TX at E9 after pre-edge sampling.
    t = b.tick(tx_sizes=(0, 0))
    assert t["pc"][0] == 0 and not t["tx_pop"][0] and t["tx_count"][0] == 1
    assert not b.tick()["tx_pop"][0]
    t = b.tick()
    assert t["tx_pop"][0] and t["retire"][0] and t["tx_count"][0] == 0
    assert b.until(13)["uio_out"] == 0x10


def test_full_push_cannot_reuse_same_edge_host_read():
    b = Bench()
    b.image(0, [0x6800, 0])
    b.engine.ctx[0].rx.extend([0x12, 0x34, 0x56, 0x78])
    b.run(0, 7)
    b.until(8)
    q = b.engine.ctx[0].rx
    assert q[0] == 0x12
    del q[0]  # A successful simultaneous host RX, from a full pre-edge queue.
    t = b.tick(rx_sizes=(4, 0))
    assert t["pc"][0] == 0 and not t["rx_push"][0] and t["rx_count"][0] == 3
    assert not b.tick()["rx_push"][0]
    t = b.tick()
    assert t["rx_push"][0] and t["retire"][0] and t["rx_count"][0] == 4
    assert list(b.engine.ctx[0].rx) == [0x34, 0x56, 0x78, 0]


@pytest.mark.parametrize("bad", [0x1811, 0x4800, 0x8000, 0x7800 | 0x0100])
def test_fault_preserves_data_and_sibling_can_fetch(bad):
    """§§3,6,9.2: mixed mask, forbidden SHOUT, invalid opcode, reserved bit."""
    b = Bench()
    b.image(0, [0x0810, 0x1010, 0x1810, 0x0800, 0x1000, bad, 0])
    b.image(1, [0x0803, 0x1003, 0x1803, 0x5003])
    b.run(1, 6)
    b.run(0, 7)
    t = b.until(18)
    assert (t["uio_out"], t["uio_oe"]) == (0x13, 0x13)
    t = b.tick()
    assert b.engine.ctx[0].fault and not b.engine.ctx[0].running
    assert not t["retire"][0] and t["pc"][0] == 5
    assert (t["uio_out"], t["uio_oe"]) == (0x13, 3)
    assert t["fetch_valid"] and t["fetch_ctx"] == 1 and t["sram_addr"] == 131
    assert not b.engine.ctx[1].fault and b.engine.ctx[1].running


@pytest.mark.parametrize(
    ("word", "before", "at_start", "target"),
    [(0x3001, 0, 0, 3), (0x3009, 2, 2, 3),
     (0x3019, 2, 0, 3), (0x3019, 0, 0, 2)],
)
def test_zero_timeout_wait_one_observation(word, before, at_start, target):
    """§§4,9.3: a true level or a new FALL can succeed on the only allowed edge."""
    b = Bench()
    b.image(0, [word, 0x5103, 0, 0])
    b.run(0, 7)
    b.tick(before)
    t = b.tick(at_start)
    assert t["exec_start"][0] and t["retire"][0] and t["pc"][0] == 1
    assert b.until(11)["pc"][0] == target


def test_wait_forever_ignores_zero_timeout():
    b = Bench()
    b.image(1, [0x302D, 0])  # WAIT HIGH 5 FOREVER with initial T=0.
    b.run(1, 6)
    b.until(7)
    for edge in range(8, 19):
        t = b.tick()
        assert bool(t["exec_start"][1]) == (edge == 8)
        assert not t["retire"][1] and t["pc"][1] == 0
    assert not b.tick(0x20)["retire"][1]  # E19 remembers the level.
    t = b.tick(0)  # B retires at E20 even though the input already fell.
    assert t["retire"][1] and t["pc"][1] == 1


def test_simultaneous_fifo_transfer_from_middle_occupancy():
    """§§5,9.3: an authorized host write does not bypass the preexisting TX head."""
    b = Bench()
    b.image(1, [0x7000, 0x4800, 0])
    b.engine.ctx[1].tx.extend([0x80, 0x35])
    b.run(1, 6)
    b.until(7)
    b.engine.ctx[1].tx.append(0x40)
    t = b.tick(tx_sizes=(0, 2))
    assert t["tx_pop"][1] and t["tx_count"][1] == 2
    assert list(b.engine.ctx[1].tx) == [0x35, 0x40]
    assert b.until(10)["uio_out"] == 1


@pytest.mark.parametrize(("cid", "own_fetch_edge"), [(0, 8), (1, 7)])
def test_run_cannot_fetch_on_its_own_acceptance_edge(cid, own_fetch_edge):
    """§9.1: if K is already the target's fetch slot, F is K+2."""
    b = Bench()
    b.image(cid, [0])
    assert not b.run(cid, own_fetch_edge)["fetch_valid"]
    assert not b.tick()["fetch_valid"]
    trace = b.tick()
    assert trace["fetch_valid"] and trace["fetch_ctx"] == cid
    assert trace["sram_addr"] == 128 * cid
    assert b.tick()["retire"][cid]
