"""Pin-level v0.2 acceptance vectors. All numeric oracles come from spec §§2–7, 9."""
import pytest

SECTION7 = (0x0801, 0x1001, 0x1801, 0)
UART_PINS = (0x0810, 0x1010, 0x1810, 0)
WAIT_PROGRAM = (0x0801, 0x1001, 0x2001, 0x3016, 0x5206, 0x1801, 0)


def new_machine():
    # HDL scenarios import this module without loading the Python execution model.
    from model.protocol_machine import ProtocolMachine
    return ProtocolMachine()


class Pins:
    """Drive one pre-edge TT pin tuple, then keep the returned post-edge observations."""

    def __init__(self, strap=1, tick=None):
        self.tick = new_machine().tick if tick is None else tick
        self.ui = strap << 5
        self.req = 0
        self.edge = 0
        self.inputs = {}
        self.rows = {}
        self.drive(0, 1)
        for _ in range(3):
            self.step()
        assert self.rows[3]["dual_mode"] == (strap == 1)

    def drive(self, rst_n=1, ena=1):
        return self.tick(ui_in=self.ui, uio_in=self.inputs.get(self.edge, 0),
                         rst_n=rst_n, ena=ena)

    def step(self):
        self.edge += 1
        row = dict(self.drive())
        assert row["edge"] == self.edge
        assert sum(row["retire"]) <= 1, self.edge
        if row["dual_mode"]:
            assert sum(row["exec_start"]) <= 1
            if row["fetch_valid"]:
                assert row["fetch_ctx"] == self.edge % 2
            if any(row["exec_start"]) or any(row["retire"]):
                other = self.edge % 2  # the fetch owner cannot execute here
                assert not row["exec_start"][other] and not row["retire"][other]
        self.rows[self.edge] = row
        return row

    def until(self, edge):
        assert edge >= self.edge
        while self.edge < edge:
            self.step()

    def nibble(self, value, target):
        # The requested data/target are settled before the first sampled edge.
        self.req ^= 1
        self.ui = (target << 6) | (self.req << 4) | value
        for age in range(1, 4):
            row = self.step()
            ack = (row["uo_out"] >> 4) & 1
            assert ack == (self.req if age == 3 else 1 - self.req), (self.edge, age)
        return row["uo_out"] & 15

    def command(self, target, op, payload=(), flip_at=()):
        nibbles = [n for b in (op, *payload) for n in (b >> 4, b & 15)]
        replies = [self.nibble(v, target ^ (i in flip_at)) for i, v in enumerate(nibbles)]
        assert replies[:-1] == [0] * (len(replies) - 1)
        return replies[-1]

    def status(self, target):
        assert self.command(target, 0) == 0
        return self.nibble(0, 1 - target) << 4 | self.nibble(0, 1 - target)

    def load(self, target, words):
        def le16(n):
            return (n & 255, n >> 8)
        assert self.command(target, 1, le16(len(words))) == 0
        for word in words:
            assert self.command(target, 2, le16(word)) == 0
        assert self.command(target, 3, le16(sum(words) & 0xffff)) == 0

    def run(self, target, parity):
        if (self.edge + 6) % 2 != parity:
            self.step()
        assert self.command(target, 4) == 0
        return self.edge

    def pin_window(self, k, changes, last):
        expected = (0, 0)
        for offset in range(last + 1):
            expected = changes.get(offset, expected)
            row = self.rows[k + offset]
            observed = row["uio_out"], row["uio_oe"]
            assert observed == expected, (f"K+{offset}", observed, expected)


@pytest.mark.parametrize("parity", [0, 1])
def test_single_legacy_section7(parity):
    p = Pins(strap=0)
    p.load(0, SECTION7)
    k = p.run(1, parity)  # target input is ignored after the single-mode strap
    p.until(k + 8)
    p.pin_window(k, {6: (1, 1), 8: (1, 0)}, 8)
    assert [p.rows[k + n]["pc"][0] for n in range(9)] == [0, 0, 1, 1, 2, 2, 3, 3, 3]


@pytest.mark.parametrize("strap", range(8))
def test_mode_latches_through_reset_release(strap):
    p = Pins(strap)
    p.ui = (0 if strap == 1 else 1) << 5
    p.until(9)
    assert p.rows[9]["dual_mode"] == (strap == 1)
    assert p.command(0, 1, (129, 0)) == (3 if strap == 1 else 0)


def aligned_runs_and_tagged_words(machine):
    p = Pins(tick=machine.tick)
    a = (0x2006, 0x3800, *UART_PINS)
    p.load(0, a)
    p.load(1, SECTION7)
    k = p.run(0, 1)
    p.until(k + 3)
    assert p.command(1, 4) == 0 and p.edge == k + 9
    p.until(k + 20)
    p.pin_window(k, {15: (1, 1), 16: (17, 17), 17: (17, 16), 18: (17, 0)}, 20)
    for offset, owner, address, word in ((1, 0, 0, a[0]), (3, 0, 1, a[1]),
                                         (10, 1, 128, SECTION7[0]),
                                         (11, 0, 2, a[2]), (12, 1, 129, SECTION7[1])):
        f, s = p.rows[k + offset], p.rows[k + offset + 1]
        assert f["fetch_valid"] and (f["fetch_ctx"], f["sram_addr"]) == (owner, address)
        assert s["instr_valid"] and s["exec_start"][owner]
        assert (s["instr_ctx"], s["instruction"]) == (owner, word)


def test_aligned_runs_and_tagged_words():
    aligned_runs_and_tagged_words(new_machine())


def wait_deadline(machine, rise, commit):
    p = Pins(tick=machine.tick)
    p.load(0, (0x2005, 0x3800, *UART_PINS))
    p.load(1, WAIT_PROGRAM)
    k = p.run(0, 0)
    p.step()
    assert p.command(1, 4) == 0 and p.edge == k + 7
    # A pin captured two rising edges earlier reaches the pre-edge 2FF sample.
    p.inputs.update({k + rise - 2 + n: 0x40 for n in range(3)})
    p.until(k + 25)
    changes = {17: (16, 16), 19: (16, 0)}
    if commit is not None:
        changes.update({commit: (17, 1), commit + 2: (17, 0)})
    p.pin_window(k, changes, 25)
    assert p.rows[k + 16]["exec_start"][1]
    assert p.rows[k + 16]["retire"][1] == (rise == 16)
    if rise != 16:
        assert not p.rows[k + 17]["retire"][1]
        assert p.rows[k + 18]["retire"][1]


@pytest.mark.parametrize(("rise", "commit"), [(15, None), (16, 20), (17, 22), (18, None)])
def test_wait_onset_deadline_and_late_edge(rise, commit):
    wait_deadline(new_machine(), rise, commit)


@pytest.mark.parametrize("stop_offset", [17, 18])
def test_stop_cancels_pending_wait_only_for_target(stop_offset):
    p = Pins()
    p.load(0, (0x2005, 0x3800, *UART_PINS))
    p.load(1, WAIT_PROGRAM)
    k = p.run(0, 0)
    p.step()
    assert p.command(1, 4) == 0
    p.inputs[k + 15] = 0x40  # digitized rise on N; STOP either at d or planned R
    p.until(k + stop_offset - 6)
    assert p.command(1, 5) == 0 and p.edge == k + stop_offset
    p.until(k + 24)
    p.pin_window(k, {17: (16, 16), 19: (16, 0)}, 24)
    assert not p.rows[k + stop_offset]["retire"][1]
    assert p.status(1) == 5  # target READY, stopped, TX has room


def test_loader_lock_mismatch_discards_frame_and_keeps_write_index():
    p = Pins()
    p.load(1, (0,))
    assert p.command(0, 1, (1, 0)) == 0
    assert p.command(1, 1, (0, 0)) == 2  # state before bad length
    assert p.command(1, 1, (0, 0), flip_at=(2,)) == 3  # mismatch before state
    assert p.command(1, 4) == 2
    assert p.status(0) == 0x21
    lo = p.edge
    assert p.command(0, 2, (0xaa, 0xbb), flip_at=(4,)) == 3
    assert not any(p.rows[e]["sram_we"] for e in range(lo + 1, p.edge + 1))
    assert p.command(0, 2, (0, 0)) == 0
    assert p.rows[p.edge]["sram_we"] and p.rows[p.edge]["sram_addr"] == 0
    assert p.command(0, 3, (0, 0)) == 0
    assert p.status(0) == p.status(1) == 5


def test_own_data_and_oe_survive_sibling_fault():
    p = Pins()
    # Wait for HIGH on pin 7 forever to maintain A's active drive.
    p.load(0, (*UART_PINS[:3], 0x302f, 0))
    p.load(1, (0x0801, 0x1001, 0x1811, 0))
    k = p.run(0, 1)
    p.until(k + 8)
    kb = p.run(1, 0)
    p.until(kb + 10)
    assert (p.rows[p.edge]["uio_out"], p.rows[p.edge]["uio_oe"]) == (16, 16)
    assert p.status(1) == 0x15  # FAULT+READY+TX space, partial mask never committed
    assert p.status(0) == 0x0d  # still RUNNING+READY
    assert p.command(1, 5) == 0
    assert p.rows[p.edge]["uio_oe"] == 16


def test_rx_dummy_selector_is_ignored_and_pop_is_once():
    p = Pins()
    shin_lsb_pin2 = 0x400a
    push = 0x6800
    p.load(0, (0,))
    p.load(1, (0x0800, *([shin_lsb_pin2] * 8), push, *([shin_lsb_pin2] * 8), push, 0))
    k = p.run(1, 0)  # B's first F=K+1, S=K+2
    for first_pc, byte in ((1, 0x96), (10, 0x3d)):
        for bit in range(8):
            raw_edge = k + 2 * (first_pc + bit)
            for edge in (raw_edge, raw_edge + 1):
                p.inputs[edge] = ((byte >> bit) & 1) << 2
    p.until(k + 41)
    assert tuple(p.rows[p.edge]["rx_count"]) == (0, 2)
    assert p.command(1, 7, flip_at=(1,)) == 3
    assert tuple(p.rows[p.edge]["rx_count"]) == (0, 2)
    for wanted, remaining in ((0x96, 1), (0x3d, 0)):
        assert p.command(1, 7) == 0
        assert tuple(p.rows[p.edge]["rx_count"]) == (0, remaining)
        assert p.nibble(0, 0) << 4 | p.nibble(0, 1) == wanted
        assert tuple(p.rows[p.edge]["rx_count"]) == (0, remaining)
    assert p.command(1, 7) == 1
    assert p.command(0, 7) == 1


@pytest.mark.parametrize(("final_word", "status"), [(0, 5), (0x0801, 0x15), (0x5080, 0x15)])
def test_bank_b_last_word_checks_full_width_address(final_word, status):
    p = Pins()
    p.load(1, (0x507f, *([0] * 126), final_word))
    k = p.run(1, 0)
    p.until(k + 5)
    assert (p.rows[k + 1]["fetch_ctx"], p.rows[k + 1]["sram_addr"]) == (1, 128)
    assert (p.rows[k + 3]["fetch_ctx"], p.rows[k + 3]["sram_addr"]) == (1, 255)
    end = p.rows[k + 4]
    assert end["instr_valid"] and end["exec_start"][1]
    assert end["pc"][1] == 127 and bool(end["retire"][1]) == (final_word == 0)
    assert p.status(1) == status and p.status(0) == 1


@pytest.mark.parametrize("offset", (6, 7))
def test_stop_kills_return_or_request_without_ghost_exec(offset):
    p = Pins()
    p.load(0, UART_PINS)
    k = p.run(0, 1)
    p.until(k + offset - 6)
    assert p.command(0, 5) == 0 and p.edge == k + offset
    stop = p.rows[p.edge]
    assert not stop["instr_valid"] and not stop["fetch_valid"]
    assert not any(stop["exec_start"]) and not any(stop["retire"])
    assert (stop["uio_out"], stop["uio_oe"]) == (0, 0)
    p.until(k + 12)
    assert not any(any(p.rows[e]["retire"]) for e in range(k + offset, p.edge + 1))
    assert p.status(0) == 5
    restart = p.run(0, 0)  # new RUN cannot consume the old fetch response
    p.until(restart + 9)
    p.pin_window(restart, {7: (16, 16), 9: (16, 0)}, 9)



@pytest.mark.parametrize("d_parity", [0, 1])
def test_two_waits_decide_on_one_wall_clock(d_parity):
    p = Pins()
    for target in (0, 1):
        p.load(target, (0x3036, 0))  # WAIT RISE pin 6 FOREVER; HALT
    p.run(0, 1)
    k_b = p.run(1, 0)
    d = k_b + 8 + d_parity
    for edge in range(d - 2, d + 1):
        p.inputs[edge] = 0x40
    p.until(d + 1)
    first = 1 if d_parity == 0 else 0
    for e, owner in ((d, first), (d + 1, 1 - first)):
        row = p.rows[e]
        assert row["retire"][owner] and not row["retire"][1 - owner]
        assert not row["instr_valid"]  # the waiting word was consumed earlier
    assert tuple(p.rows[d + 1]["pc"]) == (1, 1)


def test_empty_pull_uses_preedge_count_on_host_tx():
    p = Pins()
    p.load(0, (0x7000, 0))  # PULL, HALT
    k = p.run(0, 1)
    assert p.command(0, 6, (0x96,)) == 0
    assert p.edge == k + 12
    accept = p.rows[p.edge]
    assert tuple(accept["tx_count"]) == (1, 0) and not any(accept["tx_pop"])
    assert not p.step()["tx_pop"][0]
    resumed = p.step()
    assert resumed["tx_pop"][0] and resumed["retire"][0]
    assert tuple(resumed["tx_count"]) == (0, 0) and resumed["pc"][0] == 1


def test_full_push_uses_preedge_count_on_host_rx():
    p = Pins()
    p.load(0, (0x6800,) * 5 + (0,))
    k = p.run(0, 1)
    p.until(k + 10)
    assert p.rows[p.edge]["rx_count"][0] == 4 and not p.rows[p.edge]["retire"][0]
    assert p.command(0, 7) == 0
    assert p.edge == k + 16
    accept = p.rows[p.edge]
    assert not accept["rx_push"][0] and accept["rx_count"][0] == 3
    assert not p.step()["rx_push"][0]
    resumed = p.step()
    assert resumed["rx_push"][0] and resumed["retire"][0] and resumed["rx_count"][0] == 4
    assert p.nibble(0, 1) == p.nibble(0, 1) == 0
