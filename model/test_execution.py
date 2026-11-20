"""Independent binary programs and post-edge expectations from spec §§2–6."""

from itertools import islice

import pytest

from model.iss import trace_engine


def run(words, terminal, **inputs):
    image = b"".join(word.to_bytes(2, "little") for word in words)
    rows = list(islice(trace_engine(image, **inputs), terminal + 2))
    assert len(rows) == terminal + 1, "wrong terminal clock or program did not stop"
    assert not rows[-1].edge.running
    return rows


@pytest.mark.parametrize("setup, raw, condition, taken, flag", [
    (0x5801, 0, 0, True,  "NONE"),     # unconditional
    (0x5800, 0, 1, False, "NONE"),     # no WAIT has run
    (0x5800, 0, 2, False, "NONE"),
    (0x3008, 1, 1, True,  "OK"),       # WAIT HIGH 0
    (0x3008, 0, 1, False, "TIMEOUT"),
    (0x3008, 1, 2, False, "OK"),
    (0x3008, 0, 2, True,  "TIMEOUT"),
    (0x5800, 0, 3, True,  "NONE"),     # LSET 0 or 1
    (0x5801, 0, 3, False, "NONE"),
])
def test_branch_selects_retired_pc_and_pin_value(setup, raw, condition, taken, flag):
    # At PC=1 BR targets word 4. Only the fall-through arm executes BR 5.
    words = [setup, 0x5004 | (condition << 8), 0x085A, 0x5005, 0x08A5,
             0x10FF, 0x18FF, 0]
    commit = 10 if taken else 12
    rows = run(words, commit + 2, uio_in=(raw,))
    assert rows[4].edge.pc == (4 if taken else 2)
    assert rows[4].edge.exec_start and rows[4].edge.retire
    assert rows[5].edge.fetch and rows[5].edge.pc == rows[4].edge.pc
    assert all(row.wait_result == flag for row in rows[2:])
    assert all((r.edge.uio_out, r.edge.uio_oe) == (0, 0) for r in rows[:commit])
    value = 0xA5 if taken else 0x5A
    assert (rows[commit].edge.uio_out, rows[commit].edge.uio_oe) == (value, 255)
    assert (rows[-1].edge.uio_out, rows[-1].edge.uio_oe) == (value, 0)
    assert not any(r.fault for r in rows)


@pytest.mark.parametrize("counter, iterations", [(0, 1), (1, 1), (2, 2), (255, 255)])
def test_djnz_decrements_only_positive_and_takes_positive_result(counter, iterations):
    # LSET n, DJNZ 1, HALT. A zero count still executes DJNZ once.
    rows = run([0x5800 | counter, 0x6001, 0], 4 + 2 * iterations)
    assert rows[2].lc == counter
    for i in range(1, iterations + 1):
        fetch, result = rows[1 + 2*i : 3 + 2*i]
        assert fetch.edge.fetch and fetch.edge.pc == 1
        assert result.edge.exec_start and result.edge.retire
        assert (result.lc, result.edge.pc) == (max(0, counter - i), 1 if i < iterations else 2)
    assert rows[-1].edge.pc == 2 and not rows[-1].fault


@pytest.mark.parametrize("setup, op, fault, lc", [
    (0x5803, 0x50FF, True,  3),    # selected BR target 255 outside length 3
    (0x5803, 0x51FF, False, 3),    # WAIT result NONE, ignores unused target
    (0x5802, 0x60FF, True,  2),    # invalid taken DJNZ preserves old LC
    (0x5801, 0x60FF, False, 0),    # valid fall-through, decrements
    (0x5800, 0x60FF, False, 0),
])
def test_only_selected_address_is_validated_before_side_effects(setup, op, fault, lc):
    rows = run([setup, op, 0], 4 if fault else 6)
    at_s = rows[4]
    assert (at_s.fault, at_s.edge.running, at_s.edge.retire) == (fault, not fault, not fault)
    assert (at_s.edge.pc, at_s.lc) == (1 if fault else 2, lc)


@pytest.mark.parametrize("last, stop, pc, fault", [
    (0x0000, 4, 255, False),  # final HALT, no next address required
    (0x5001, 6,   1, False),  # final BR selects address 1 even at PC 255
    (0x5100, 4, 255, True),   # false condition, PC+1=256 is outside image
    (0x5809, 4, 255, True),   # final LSET must not execute or wrap PC to zero
])
def test_last_word_of_256_word_image(last, stop, pc, fault):
    rows = run([0x50FF] + [0] * 254 + [last], stop)
    assert rows[2].edge.pc == 255 and rows[3].edge.fetch
    assert (rows[-1].edge.pc, rows[-1].fault, rows[-1].lc) == (pc, fault, 0)
    assert rows[4].edge.retire != fault


@pytest.mark.parametrize("lsb", [False, True], ids=["msb-first", "lsb-first"])
def test_alternating_shifts_exchange_different_bytes(lsb):
    # CLK ticks 8,12,... sample SHIN pin 6. Interleave SHOUT pin 2.
    tx, rx = 0x96, 0xD3
    order = 8 if lsb else 0
    words = [0x7800 | tx, 0x1004, 0x18FF] + [0x4006 | order, 0x4802 | order] * 8
    raw = [0xBF] * 43  # unrelated pins HIGH, selected input normally LOW
    for i in range(8):
        pos = i if lsb else 7 - i
        raw[6 + 4*i] |= ((rx >> pos) & 1) << 6
    rows = run(words + [0x6800, 0], 42, uio_in=raw, host_rx=(41,))
    for i in range(8):
        sample, send = rows[8 + 4*i], rows[10 + 4*i]
        n = i + 1
        partial = ((rx % (1 << n)) << (8 - n)) if lsb else (rx >> (8 - n))
        unsent = (tx >> n) if lsb else ((tx << n) & 255)
        pos = i if lsb else 7 - i
        assert sample.isr == send.isr == partial
        assert sample.osr == (tx if i == 0 else rows[6 + 4*i].osr)
        assert send.osr == unsent
        assert send.edge.uio_out == (((tx >> pos) & 1) << 2)
        assert send.edge.uio_oe == 4 and send.edge.retire
    assert rows[40].rx_push and rows[41].host_rx_data == rx


def test_shout_updates_one_bit_in_both_active_and_prepared_data():
    # §3: active A0 and prepared 40 differ just before first SHOUT.
    rows = run([0x08A0, 0x1009, 0x18FF, 0x0840, 0x7880,
                0x4800, 0x18FF, 0x4800, 0x18FF, 0], 20)
    assert [rows[t].edge.uio_out for t in (8, 12, 14, 16, 18)] == [0xA0, 0xA1, 0x41, 0x40, 0x40]
    assert all(r.edge.uio_oe == 9 for r in rows[6:20])


@pytest.mark.parametrize("count", range(5))
def test_pull_and_host_tx_at_each_occupancy(count):
    old = [0x21, 0x43, 0x65, 0x87][:count]
    # §5: host requests a write on the very clock of the first PULL.
    expected = old + ([0xAB] if count < 4 else [])
    first = 2 if count else 3
    rows = run([0x7000] * len(expected) + [0], first + 2*len(expected),
               tx_initial=old, host_tx={2: 0xAB})
    assert rows[0].tx_count == count
    same = rows[2]
    assert (same.tx_pop, same.host_tx_ok, same.edge.retire) == (count > 0, count < 4, count > 0)
    assert same.tx_count == count - int(count > 0) + int(count < 4)
    assert [r.osr for r in rows if r.tx_pop] == expected
    assert [t for t, r in enumerate(rows) if r.tx_pop] == list(range(first, first + 2*len(expected), 2))
    assert rows[-1].tx_count == 0


@pytest.mark.parametrize("count", range(5))
def test_push_and_host_rx_at_each_occupancy(count):
    old = [0x12, 0x34, 0x56, 0x78][:count]
    expected = old[1:] + [0]  # PUSH copies initial ISR=0, including full-queue retry.
    first = 3 if count == 4 else 2
    reads = (2,) + tuple(range(4, 4 + len(expected)))
    rows = run([0x6800, 0x2006, 0x3800, 0], first + 12, rx_initial=old, host_rx=reads)
    same = rows[2]
    assert (same.rx_push, same.host_rx_ok, same.edge.retire) == (count < 4, count > 0, count < 4)
    assert same.host_rx_data == (old[0] if old else None)
    assert same.rx_count == count + int(count < 4) - int(count > 0)
    assert [t for t, r in enumerate(rows) if r.rx_push] == [first]
    assert [rows[t].host_rx_data for t in reads[1:]] == expected
    assert rows[-1].rx_count == 0


@pytest.mark.parametrize("stop", [False, True])
def test_pull_stall_preserves_state_then_stop_or_retry(stop):
    # Drive A5, stage OSR, LC, OK and ISR=1 before PULL stalls at clock 16.
    words = [0x08A5, 0x10FF, 0x18FF, 0x783C, 0x5803, 0x3000, 0x4001, 0x7000, 0]
    rows = run(words, 19 if stop else 21, uio_in=(2,), host_tx={18: 0x69},
               rx_initial=(0x8B,), stop_at=19 if stop else None)
    initial = rows[0]
    assert (initial.edge.pc, initial.osr, initial.isr, initial.lc, initial.wait_result,
            initial.rx_count, initial.tx_count) == (0, 0, 0, 0, "NONE", 1, 0)
    for t in (16, 17, 18):
        r = rows[t]
        assert tuple(r.edge) == (7, 0xA5, 255, True, False, t == 16, False)
        assert (r.wait_result, r.osr, r.isr, r.lc) == ("OK", 0x3C, 1, 3)
        assert (r.tx_pop, r.tx_count, r.host_tx_ok) == (False, int(t == 18), t == 18)
    end = rows[19]
    if stop:
        assert tuple(end.edge) == (0, 0, 0, False, False, False, False)
        assert (end.osr, end.isr, end.lc, end.tx_count, end.rx_count) == (0, 0, 0, 0, 0)
        assert end.wait_result == "NONE" and not end.tx_pop
    else:
        assert tuple(end.edge) == (8, 0xA5, 255, True, False, False, True)
        assert (end.osr, end.isr, end.lc, end.tx_count, end.rx_count) == (0x69, 1, 3, 0, 1)
        assert end.wait_result == "OK" and end.tx_pop


@pytest.mark.parametrize("bad", [0x4010, 0x4810, 0x5400, 0x5900, 0x6100,
                                 0x6801, 0x7001, 0x7900, 0x8000])
def test_fault_releases_oe_and_suppresses_all_instruction_effects(bad):
    # All candidate instructions have a legal sequential address at S.
    words = [0x08C3, 0x10F0, 0x18FF, 0x780D, 0x5802, 0x4003, bad, 0]
    rows = run(words, 14, uio_in=(8,), tx_initial=(0x45,), rx_initial=(0x67,))
    before, fault = rows[13:15]
    assert before.edge.uio_oe == 0xF0 and not before.fault
    assert fault.fault and not fault.edge.retire and not fault.edge.running
    assert (fault.edge.pc, fault.edge.uio_out, fault.edge.uio_oe) == (6, 0xC3, 0)
    assert (fault.lc, fault.osr, fault.isr, fault.wait_result) == (2, 0x0D, 1, "NONE")
    assert (fault.tx_count, fault.rx_count, fault.tx_pop, fault.rx_push) == (1, 1, False, False)


@pytest.mark.parametrize("op, tx, rx, expected_tx, expected_rx, returned", [
    (0x7000, (), (), 1, 0, None),               # empty TX, host delivers one byte
    (0x6800, (), (1, 2, 3, 4), 0, 3, 1),      # full RX, host drains one byte
    (0x7000, (0x89,), (), 2, 0, None),        # ready PULL must not pop
    (0x6800, (), (0x26,), 0, 0, 0x26),       # ready PUSH must not enqueue
])
def test_invalid_next_pc_precedes_fifo_retry_or_transfer(op, tx, rx, expected_tx, expected_rx, returned):
    # §§3/5/6: a one-word non-HALT program faults at S=2. The host's
    # one transaction on that edge still uses pre-edge permissions.
    pull = op == 0x7000
    rows = run([op], 2, tx_initial=tx, rx_initial=rx,
               host_tx={2: 0xCD} if pull else {}, host_rx=() if pull else (2,))
    end = rows[2]
    assert (end.edge.pc, end.fault, end.edge.retire, end.tx_pop, end.rx_push) == (0, True, False, False, False)
    assert (end.host_tx_ok, end.host_rx_ok, end.host_rx_data) == (pull, not pull, returned)
    assert (end.tx_count, end.rx_count, end.osr) == (expected_tx, expected_rx, 0)
