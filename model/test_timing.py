from itertools import chain, islice, repeat

import pytest

from model.iss import trace_timed


def run_to_halt_or_stop(program, last_tick, raw=(), **options):
    """Collect through the expected terminal edge, with one extra probe for overruns."""
    trace = trace_timed(bytes.fromhex(program), raw, **options)
    rows = list(islice(trace, last_tick + 2))
    assert len(rows) == last_tick + 1
    return rows


def check(row, edge, result):
    # Spec §2 post-edge order: PC, data, OE, running, fetch, exec_start, retire.
    assert tuple(row.edge) == edge
    assert row.wait_result == result


@pytest.mark.parametrize(
    "tlo, raw_rise, retire, result",
    [
        ("02 20", 1, 6, "TIMEOUT"),  # observed at S-1
        ("02 20", 2, 4, "OK"),       # observed at S
        ("02 20", 4, 6, "OK"),       # observed at N
        ("02 20", 5, 6, "TIMEOUT"),  # observed at N+1
        ("00 20", 2, 4, "OK"),       # T=0 checks S
        ("00 20", 3, 4, "TIMEOUT"),  # T=0 has no second check
        ("01 20", 3, 5, "OK"),       # T=1 still checks S+1
        ("01 20", 4, 5, "TIMEOUT"),
    ],
)
def test_wait_rise_window(tlo, raw_rise, retire, result):
    # TLO, WAIT RISE pin 0, HALT. S=4. A pin sampled at k is compared at k+2.
    program = tlo + "  10 30  00 00"
    raw = (0,) * raw_rise + (1,)
    rows = run_to_halt_or_stop(program, retire + 2, raw)
    check(rows[3], (1, 0, 0, 1, 1, 0, 0), "NONE")
    for tick in range(4, retire):
        check(rows[tick], (1, 0, 0, 1, 0, tick == 4, 0), "NONE")
    check(rows[retire], (2, 0, 0, 1, 0, retire == 4, 1), result)
    check(rows[retire + 1], (2, 0, 0, 1, 1, 0, 0), result)
    check(rows[retire + 2], (2, 0, 0, 0, 0, 1, 1), result)


@pytest.mark.parametrize("instruction, pins", [("0D 30", 0xA0), ("05 30", 0x89)])
def test_already_true_high_and_low_on_pin_five(instruction, pins):
    rows = run_to_halt_or_stop(instruction + "  00 00", 4, (pins,), pre_run=(pins,) * 3)
    check(rows[2], (1, 0, 0, 1, 0, 1, 1), "OK")


@pytest.mark.parametrize("instruction, pins", [("10 30", 1), ("18 30", 0)])
def test_first_valid_input_after_reset_creates_no_edge(instruction, pins):
    # No pre-RUN samples. K supplies the first raw value, observed at S=2.
    rows = run_to_halt_or_stop(instruction + "  00 00", 4, (pins,))
    check(rows[2], (1, 0, 0, 1, 0, 1, 1), "TIMEOUT")


def test_fall_across_run_preserves_pre_run_samples():
    # Raw pin 5 falls at K. Pins 7 and 0 remain high throughout.
    rows = run_to_halt_or_stop("1D 30  00 00", 4, (0x81,), pre_run=(0xA1,) * 3)
    check(rows[0], (0, 0, 0, 1, 0, 0, 0), "NONE")
    check(rows[2], (1, 0, 0, 1, 0, 1, 1), "OK")


@pytest.mark.parametrize("timer_bytes, delay", [
    ("00 20  00 28", 0),
    ("01 20  00 28", 1),
    ("02 20  01 28", 0x0102),
    ("01 28  02 20", 0x0102),
])
def test_delay_retires_once_at_start_plus_timer(timer_bytes, delay):
    # Two timer-byte writes, DELAY, HALT. D is sampled before S=6.
    retire = 6 + delay
    rows = run_to_halt_or_stop(timer_bytes + "  00 38  00 00", retire + 2)
    for tick in range(6, retire):
        check(rows[tick], (2, 0, 0, 1, 0, tick == 6, 0), "NONE")
    check(rows[retire], (3, 0, 0, 1, 0, delay == 0, 1), "NONE")
    check(rows[retire + 1], (3, 0, 0, 1, 1, 0, 0), "NONE")
    check(rows[retire + 2], (3, 0, 0, 0, 0, 1, 1), "NONE")


@pytest.mark.parametrize("timer_bytes, duration", [
    ("00 20  00 28", 0),
    ("FF 20  FF 28", 65535),
])
def test_two_delays_reuse_the_same_timer(timer_bytes, duration):
    # §2/§4: S1=6, R1=S1+D, F2=R1+1, S2=R1+2, R2=S2+D.
    program = bytes.fromhex(timer_bytes + "  00 38  00 38  00 00")
    first_retire = 6 + duration
    second_start = first_retire + 2
    second_retire = second_start + duration
    halt = second_retire + 2
    trace = trace_timed(program)
    starts, retires = [], []
    for tick, row in enumerate(islice(trace, halt + 2)):
        if row.edge.exec_start:
            starts.append(tick)
        if row.edge.retire:
            retires.append(tick)
        assert row.edge.running == (tick < halt)
    assert starts == [2, 4, 6, second_start, halt]
    assert retires == [2, 4, first_retire, second_retire, halt]
    assert tick == halt


@pytest.mark.parametrize("result", ["OK", "TIMEOUT"])
def test_full_16_bit_wait_checks_last_edge(result):
    # TLO FF, THI FF, WAIT RISE pin 0, HALT. N = S + 65535.
    start = 6
    last = start + 65535
    raw_tick = last - 2 if result == "OK" else last - 1
    raw = chain(repeat(0, raw_tick), (1,))
    trace = iter(trace_timed(bytes.fromhex("FF 20  FF 28  10 30  00 00"), raw))
    first_wait = next(islice(trace, start, start + 1))
    check(first_wait, (2, 0, 0, 1, 0, 1, 0), "NONE")
    for _ in range(start + 1, last):
        check(next(trace), (2, 0, 0, 1, 0, 0, 0), "NONE")
    check(next(trace), (3, 0, 0, 1, 0, 0, 1), result)
    check(next(trace), (3, 0, 0, 1, 1, 0, 0), result)
    check(next(trace), (3, 0, 0, 0, 0, 1, 1), result)
    assert next(trace, None) is None


def test_results_and_active_outputs_through_stalls_delay_and_halt():
    # DATA 5A, OE C3, COMMIT FF, HIGH pin 2, TLO 2, LOW pin 2,
    # DELAY, HIGH pin 2, HALT. Pin 2 stays high for the entire program.
    program = "5A 08  C3 10  FF 18  0A 30  02 20  02 30  00 38  0A 30  00 00"
    rows = run_to_halt_or_stop(program, 22, (4,), pre_run=(4,) * 3)
    assert [row.wait_result for row in rows] == (
        ["NONE"] * 8 + ["OK"] * 6 + ["TIMEOUT"] * 6 + ["OK"] * 3
    )
    assert [row.edge[1:3] for row in rows[6:22]] == [(0x5A, 0xC3)] * 16
    check(rows[12], (5, 0x5A, 0xC3, 1, 0, 1, 0), "OK")
    check(rows[13], (5, 0x5A, 0xC3, 1, 0, 0, 0), "OK")
    check(rows[14], (6, 0x5A, 0xC3, 1, 0, 0, 1), "TIMEOUT")
    check(rows[22], (8, 0x5A, 0, 0, 0, 1, 1), "OK")


def test_infinite_wait_succeeds_after_zero_timer_has_elapsed():
    # WAIT infinite RISE pin 3, HALT. T=0 from RUN. Raw rises at 8.
    rows = run_to_halt_or_stop("33 30  00 00", 12, (0,) * 8 + (8,))
    for tick in range(2, 10):
        check(rows[tick], (0, 0, 0, 1, 0, tick == 2, 0), "NONE")
    check(rows[10], (1, 0, 0, 1, 0, 0, 1), "OK")


@pytest.mark.parametrize("second_wait, final_pins", [
    ("09 30", 2),  # finite HIGH pin 1, would succeed at N
    ("09 30", 0),  # finite HIGH pin 1, would time out at N
    ("29 30", 2),  # infinite HIGH pin 1, would succeed at STOP
    ("29 30", 0),  # infinite HIGH pin 1, still stalled at STOP
])
def test_stop_preempts_wait_and_clears_outputs_and_result(second_wait, final_pins):
    # Commit active data and OE, record OK with LOW pin 0, set T=2.
    program = "5A 08  C3 10  FF 18  00 30  02 20  " + second_wait + "  00 00"
    rows = run_to_halt_or_stop(program, 14, (0,) * 12 + (final_pins,), stop_at=14)
    check(rows[12], (5, 0x5A, 0xC3, 1, 0, 1, 0), "OK")
    check(rows[13], (5, 0x5A, 0xC3, 1, 0, 0, 0), "OK")
    check(rows[14], (0, 0, 0, 0, 0, 0, 0), "NONE")


@pytest.mark.parametrize("program, start", [
    ("00 21  00 00", 2),         # TLO reserved bit 8
    ("00 29  00 00", 2),         # THI reserved bit 8
    ("40 30  00 00", 2),         # WAIT reserved bit 6
    ("01 38  00 00", 2),         # DELAY requires operand zero
    ("30 30", 2),                # final infinite WAIT: check fall-through at S
    ("FF 20  FF 28  00 38", 6),  # final DELAY: check before waiting 65535 clocks
])
def test_invalid_instruction_or_fall_through_is_rejected_at_first_execution(program, start):
    trace = iter(trace_timed(bytes.fromhex(program)))
    assert len(list(islice(trace, start))) == start
    with pytest.raises(ValueError):
        next(trace)
