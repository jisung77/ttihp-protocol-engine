"""Cycle trace from an accepted RUN (K) through HALT or an accepted STOP.

Input is the program payload in loader byte order, without commands or nibbles.
This slice supports HALT through DELAY (opcodes 0x00..0x07).
Malformed images, invalid supported instructions and sequential fall-through
raise ValueError when encountered. Other defined opcodes raise NotImplementedError
before their operands are inspected. This slice does not simulate fault edges.
"""

from collections.abc import Iterable, Iterator
from typing import NamedTuple


class Edge(NamedTuple):
    """Pin and PC values just after an edge, plus the events on that edge."""

    pc: int
    uio_out: int
    uio_oe: int
    running: bool
    fetch: bool
    exec_start: bool
    retire: bool


class TimedEdge(NamedTuple):
    """Original edge observation and the WAIT result just after this edge."""

    edge: Edge
    wait_result: str


def _byte(raw: int) -> int:
    if not isinstance(raw, int) or not 0 <= raw <= 0xFF:
        raise ValueError("expected uio_in sample in 0..255")
    return raw


def _synchronized_inputs(
    uio_in: Iterable[int], pre_run: Iterable[int]
) -> Iterator[tuple[int | None, int | None]]:
    """Yield the pre-edge second stage and its predecessor, starting at K."""
    q1 = q2 = previous = None
    for raw in pre_run:
        previous, q2, q1 = q2, q1, _byte(raw)

    inputs = iter(uio_in)
    while True:
        raw = next(inputs, q1 if q1 is not None else 0)
        # All three registers sample their old inputs on this same edge.
        # Execution sees q2 and previous from before that update.
        observed = q2, previous
        previous, q2, q1 = q2, q1, _byte(raw)
        yield observed


def _wait_matches(operand: int, sample: int | None, previous: int | None) -> bool:
    if sample is None:
        return False
    mask = 1 << (operand & 7)
    mode = (operand >> 3) & 3
    high = bool(sample & mask)
    if mode == 0:  # LOW
        return not high
    if mode == 1:  # HIGH
        return high
    if previous is None:  # First valid level has no preceding edge history.
        return False
    was_high = bool(previous & mask)
    if mode == 2:  # RISE
        return high and not was_high
    return was_high and not high  # FALL


def _check_instruction(opcode: int, operand: int, pc: int, words: int) -> None:
    if 0x08 <= opcode <= 0x0F:
        raise NotImplementedError(f"opcode {opcode:#04x} not yet modeled at PC {pc}")
    if opcode > 0x0F:
        raise ValueError(f"reserved opcode {opcode:#04x} at PC {pc}")
    if opcode == 0x00:
        if operand:
            raise ValueError(f"nonzero HALT operand at PC {pc}")
        return
    if opcode == 0x06:
        limit = 0x3F
    elif opcode == 0x07:
        limit = 0
    else:
        limit = 0xFF
    if operand > limit:
        raise ValueError(f"reserved operand bits at PC {pc}")
    if pc + 1 >= words:
        raise ValueError(f"next PC outside image at PC {pc}")


def trace_timed(
    image: bytes, uio_in: Iterable[int] = (), *,
    pre_run: Iterable[int] = (), stop_at: int | None = None,
) -> Iterator[TimedEdge]:
    """Yield K and each following edge, with raw input sampled once per edge.

    pre_run gives prior raw samples in chronological order. Exhausted input holds
    its last value (initially zero). stop_at is an accepted STOP at tick K+n, n>0.
    """
    if not isinstance(image, bytes):
        raise TypeError("expected program payload as bytes")
    if not 2 <= len(image) <= 512 or len(image) % 2:
        raise ValueError("expected 1 to 256 complete 16-bit words")
    if stop_at is not None and (type(stop_at) is not int or stop_at <= 0):
        raise ValueError("stop_at must be a positive integer tick after K")

    pc = data = oe = 0
    prepared_data = prepared_oe = timer = 0
    wait_result = "NONE"
    phase = "run"

    def observe(*, running=True, fetch=False, exec_start=False, retire=False):
        edge = Edge(pc, data, oe, running, fetch, exec_start, retire)
        return TimedEdge(edge, wait_result)

    for tick, (sample, previous) in enumerate(_synchronized_inputs(uio_in, pre_run)):
        if tick == stop_at:
            pc = data = oe = 0
            prepared_data = prepared_oe = timer = 0
            wait_result = "NONE"
            yield observe(running=False)
            return

        if phase == "run":  # K
            phase = "fetch"
            yield observe()
            continue
        if phase == "fetch":  # F
            offset = 2 * pc
            word = int.from_bytes(image[offset : offset + 2], "little")
            opcode, operand = word >> 11, word & 0x7FF
            phase = "start"
            yield observe(fetch=True)
            continue

        start = phase == "start"  # Pulses once at S, even when R comes later.
        if start:
            _check_instruction(opcode, operand, pc, len(image) // 2)
            remaining = timer

        retire = True
        if opcode == 0x00:  # HALT
            oe = 0
            yield observe(running=False, exec_start=True, retire=True)
            return
        if opcode == 0x01:  # DATA
            prepared_data = operand
        elif opcode == 0x02:  # OE
            prepared_oe = operand
        elif opcode == 0x03:  # COMMIT
            data = (data & ~operand) | (prepared_data & operand)
            oe = (oe & ~operand) | (prepared_oe & operand)
        elif opcode == 0x04:  # TLO
            timer = (timer & 0xFF00) | operand
        elif opcode == 0x05:  # THI
            timer = (timer & 0x00FF) | (operand << 8)
        elif opcode == 0x06:  # WAIT, including the final finite edge S+T
            if _wait_matches(operand, sample, previous):
                wait_result = "OK"
            elif remaining == 0 and not (operand & 0x20):
                wait_result = "TIMEOUT"
            else:
                retire = False
        elif opcode == 0x07:  # DELAY
            retire = remaining == 0

        if retire:
            pc += 1
            phase = "fetch"
        else:
            if remaining:
                remaining -= 1
            phase = "retry"
        yield observe(exec_start=start, retire=retire)


def trace_run(image: bytes) -> Iterator[Edge]:
    """Yield just the original seven fields of each edge from K through HALT."""
    for timed in trace_timed(image):
        yield timed.edge
