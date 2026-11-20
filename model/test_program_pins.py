"""Wire-level checks for assembled programs executing on the ISS.

TT_FULL_WIRE_SWEEP=1 includes all byte values in each UART configuration, and
uses every byte as SPI MOSI against a different target byte. Each run is bounded.
"""

from bisect import bisect_right
from collections import deque
from fractions import Fraction
from functools import cache
from itertools import chain, islice, repeat
import os
from pathlib import Path

import pytest

from model.assembler import assemble
from model.iss import trace_engine


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FULL = os.environ.get("TT_FULL_WIRE_SWEEP") == "1"
BYTES = tuple(range(256)) if FULL else (0x00, 0xFF, 0x96, 0x69, 0x55, 0xAA)
CLOCK = 50_000_000
BAUD = 115_200
BIT_PERIOD = Fraction(CLOCK, BAUD)


@cache
def image(name):
    return assemble((EXAMPLES / f"{name}.asm").read_text(encoding="utf-8"))


def uart_bits(value, stop=1):
    return (0, *((value >> bit) & 1 for bit in range(8)), stop)


def test_uart_tx_wire():
    # Refill a byte near the beginning of the preceding frame. The host is then
    # ready before the next start. Inspect only the TX pin and output enable.
    tx = {25 + i * round(10 * BIT_PERIOD): b for i, b in enumerate(BYTES[1:])}
    trace = trace_engine(image("uart_tx"), tx_initial=BYTES[:1], host_tx=tx)
    limit = 100 + len(BYTES) * round(10 * BIT_PERIOD)
    changes = []
    last = 1
    enabled = False
    for tick, step in enumerate(islice(trace, limit)):
        out = step.edge.uio_out & 1
        if step.edge.uio_oe & 1:
            if not enabled:
                assert out == 1
            enabled = True
            if out != last:
                changes.append(tick)
                last = out
        else:
            assert not enabled
    assert tick == limit - 1 and enabled

    # Each entry is the number of nominal bit slots from the first start.
    # Comparing edge-to-edge run lengths measures widths even for repeated bits.
    wanted_edges = []
    last = 1
    for index, bit in enumerate(chain.from_iterable(map(uart_bits, BYTES))):
        if bit != last:
            wanted_edges.append(index)
            last = bit
    assert len(changes) == len(wanted_edges) and changes
    for i in range(1, len(changes)):
        integer_slots = wanted_edges[i] - wanted_edges[i - 1]
        ideal_width = integer_slots * BIT_PERIOD
        observed = changes[i] - changes[i - 1]
        assert abs(observed - ideal_width) * 100 <= ideal_width, (i, observed, ideal_width)

    # Decode by finding falling starts after the previous stop sample. Sample
    # each slot at its centre using the external receiver's own baud rate.
    def level(t):
        return 1 ^ (bisect_right(changes, t) & 1)

    cursor = 0
    decoded = []
    for _ in BYTES:
        start = changes[cursor]
        assert level(start) == 0
        bits = [level(start + (i + Fraction(3, 2)) * BIT_PERIOD) for i in range(8)]
        decoded.append(sum(bit << i for i, bit in enumerate(bits)))
        stop_sample = start + Fraction(19, 2) * BIT_PERIOD
        assert level(stop_sample) == 1
        cursor = bisect_right(changes, stop_sample)
    assert tuple(decoded) == BYTES


def drive_uart_rx(cells, period, phase, read_slots):
    # phase 0 changes immediately *after* an integer clock's sampling edge.
    # floor(t)+1 is the first edge that sees a new electrical level in all phases.
    start = Fraction(32) + Fraction(phase, 4)
    def first_observation(slot):
        return int(start + slot * period) + 1

    reads = [first_observation(slot) - 12 for slot in read_slots]
    initial = first_observation(0)
    def pins():
        yield from repeat(2, initial)
        for slot, value in enumerate(cells):
            clocks = first_observation(slot + 1) - first_observation(slot)
            yield from repeat(value << 1, clocks)
        yield from repeat(2)

    limit = first_observation(len(cells)) + 32
    trace = trace_engine(image("uart_rx"), pins(), pre_run=(2, 2, 2), host_rx=reads)
    received = []
    pushes = 0
    for tick, step in enumerate(islice(trace, limit)):
        pushes += step.rx_push
        if step.host_rx_ok:
            received.append((tick, step.host_rx_data))
        assert not step.fault
    assert tick == limit - 1
    assert pushes == len(received)
    return received, reads


@pytest.mark.parametrize("baud_offset", [-2, 0, 2])
@pytest.mark.parametrize("phase", [0, 1, 2, 3])
def test_uart_rx_wire(baud_offset, phase):
    period = Fraction(100 * CLOCK, BAUD * (100 + baud_offset))
    cells = list(chain.from_iterable(map(uart_bits, BYTES)))
    received, reads = drive_uart_rx(cells, period, phase, range(10, len(cells) + 1, 10))
    assert received == list(zip(reads, BYTES))


def test_uart_rx_rejects_false_start_and_bad_stop_then_recovers():
    # A short LOW glitch returns to idle before the start midpoint.
    # A complete bad frame is separated from the next start by two idle bits.
    frames = (*uart_bits(0xD3, stop=0), 1, 1, *uart_bits(0x69))
    # Make the first LOW pulse one tenth of a bit, otherwise it is a valid start.
    # Ten subslots compose a normal bit cell in this test.
    cells = [0] + [1] * 39 + list(chain.from_iterable(repeat(b, 10) for b in frames))
    received, reads = drive_uart_rx(cells, BIT_PERIOD / 10, 0, [140, 260])
    assert received == [(reads[-1], 0x69)]


def spi_exchange(mode, value, target_byte, windows=None):
    cpol, cpha = divmod(mode, 2)
    # The target knows its response byte, SCK and CS only. Change the first
    # CPHA=0 bit on selection and later bits after the clock's changing edge.
    response = deque((target_byte >> i) & 1 for i in reversed(range(8)))
    miso = 0
    drive_schedule = {}
    if windows is not None:
        # Independently recorded clock edges from a conventional target pass.
        # An adversarial target exposes just the inclusive seven-sample window.
        for i, t in enumerate(windows):
            bit = (target_byte >> (7 - i)) & 1
            drive_schedule.update({t - 4: bit ^ 1, t - 3: bit, t + 4: bit ^ 1})

    def pins():
        while True:
            yield miso << 2

    trace = trace_engine(image(f"spi_mode{mode}"), pins(), tx_initial=(value,),
                         host_rx=range(0, 640, 7))
    pin_history = []
    samples = []
    toggles = []
    data = []
    cs_start = cs_end = None
    previous_clock, previous_cs = cpol, 1
    outputs_enabled = False
    for t in range(640):
        if t in drive_schedule:
            miso = drive_schedule[t]
        step = next(trace)
        assert not step.fault
        out, oe = step.edge.uio_out, step.edge.uio_oe
        clock, mosi, cs = out & 1, (out >> 1) & 1, (out >> 3) & 1
        pin_history.append((mosi, miso))
        if not outputs_enabled and (oe & 0xB) != 0xB:
            continue
        assert oe & 0xF == 0xB
        if not outputs_enabled:
            outputs_enabled = True
            assert cs and clock == cpol
        if previous_cs and not cs:
            assert cs_start is None and clock == cpol
            cs_start = t
            if not cpha and windows is None:
                miso = response.popleft()
        if not cs and clock != previous_clock:
            toggles.append(t)
            leading = clock != cpol
            if leading != bool(cpha):
                samples.append(t)
            elif response and windows is None:
                miso = response.popleft()
        if not previous_cs and cs:
            assert cs_end is None and clock == cpol
            cs_end = t
        if cs:
            assert clock == cpol
        previous_clock, previous_cs = clock, cs
        if step.host_rx_ok:
            data.append(step.host_rx_data)
            assert cs_end is not None
            break

    assert len(toggles) == 16 and len(samples) == 8 and data == [target_byte]
    assert cs_start is not None and cs_end is not None
    assert toggles[0] - cs_start >= 25 and cs_end - toggles[-1] >= 25
    assert all(24 <= b - a <= 26 for a, b in zip(toggles, toggles[1:]))
    wire = 0
    for i, sample in enumerate(samples):
        mosi, expected_miso = pin_history[sample][0], (target_byte >> (7 - i)) & 1
        assert all(pin_history[t][0] == mosi for t in range(sample - 2, sample + 3))
        assert all(pin_history[t][1] == expected_miso for t in range(sample - 3, sample + 4))
        if cpha or i:
            # Before the designated changing edge, the previous bit must hold.
            launch = toggles[2 * i] if cpha else toggles[2 * i - 1]
            before = samples[i - 1] if i else cs_start
            assert all(pin_history[t][0] == pin_history[before][0]
                       for t in range(before, launch))
        wire = (wire << 1) | mosi
    assert wire == value
    if windows is not None:
        assert samples == windows
    return samples


@pytest.mark.parametrize("mode", range(4))
def test_spi_full_duplex_wire(mode):
    for value in BYTES:
        reply = value ^ 0xA5
        samples = spi_exchange(mode, value, reply)
        spi_exchange(mode, value, reply, windows=samples)
