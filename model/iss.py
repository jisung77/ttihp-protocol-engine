"""Cycle-level reference for one accepted RUN of a decoded loader payload.

trace_engine exposes architectural state and FIFO transactions through HALT,
STOP, or fault. trace_timed and trace_run retain their smaller observations
and raise ValueError if the program faults.
"""

from collections import deque
from collections.abc import Iterable, Iterator, Mapping
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


class EngineStep(NamedTuple):
    """Post-edge ISA state, FIFO accept pulses and an optional terminal fault."""

    edge: Edge
    wait_result: str
    lc: int
    osr: int
    isr: int
    tx_count: int
    rx_count: int
    tx_pop: bool
    rx_push: bool
    host_tx_ok: bool
    host_rx_ok: bool
    host_rx_data: int | None
    fault_reason: str | None

    @property
    def fault(self) -> bool:
        return self.fault_reason is not None


def _byte(raw: int) -> int:
    if not isinstance(raw, int) or not 0 <= raw <= 0xFF:
        raise ValueError("expected byte in 0..255")
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


def _next_pc(opcode: int, operand: int, pc: int, words: int,
             wait_result: str, lc: int) -> int:
    """§3: validate the encoding and only the address selected at S."""
    if opcode > 0x0F:
        raise ValueError(f"reserved opcode {opcode:#04x} at PC {pc}")
    if opcode == 0x00:
        if operand:
            raise ValueError(f"nonzero HALT operand at PC {pc}")
        return pc

    # Largest legal operand for each opcode, in the §3 table order.
    limits = (0, 255, 255, 255, 255, 255, 63, 0, 15, 15, 1023, 255, 255, 0, 0, 255)
    if operand > limits[opcode]:
        raise ValueError(f"reserved operand bits at PC {pc}")
    address = pc + 1  # May reach 256. Check before narrowing to an 8-bit address.
    if opcode == 0x0A:
        conditions = (True, wait_result == "OK", wait_result == "TIMEOUT", lc == 0)
        if conditions[operand >> 8]:
            address = operand & 0xFF
    elif opcode == 0x0C and lc > 1:  # DJNZ branches on the decremented LC.
        address = operand
    if address >= words:
        raise ValueError(f"next PC outside image at PC {pc}")
    return address


def _initial_fifo(items: Iterable[int]) -> deque[int]:
    fifo = deque()
    for item in items:
        if len(fifo) == 4:
            raise ValueError("FIFO initial contents exceed depth 4")
        fifo.append(_byte(item))
    return fifo


def trace_engine(
    image: bytes, uio_in: Iterable[int] = (), *,
    pre_run: Iterable[int] = (), stop_at: int | None = None,
    tx_initial: Iterable[int] = (), rx_initial: Iterable[int] = (),
    host_tx: Mapping[int, int] | None = None, host_rx: Iterable[int] = (),
) -> Iterator[EngineStep]:
    """Run a loaded image until HALT, an engine fault, or an accepted STOP.

    K is tick zero. Supply one raw pin byte per rising edge. pre_run holds older
    samples in time order. Exhausted input holds its last value (initially zero).
    Initial FIFO bytes are oldest first. host_tx maps ticks to host write bytes;
    host_rx lists read request ticks. Both are finite transaction-level stimuli.
    They bypass the nibble loader. stop_at is an accepted STOP at K+n, n>0.
    """
    if not isinstance(image, bytes):
        raise TypeError("expected program payload as bytes")
    if not 2 <= len(image) <= 512 or len(image) % 2:
        raise ValueError("expected 1 to 256 complete 16-bit words")
    if stop_at is not None and (type(stop_at) is not int or stop_at <= 0):
        raise ValueError("stop_at must be a positive integer tick after K")
    tx = _initial_fifo(tx_initial)
    rx = _initial_fifo(rx_initial)
    writes = {tick: _byte(value) for tick, value in (host_tx or {}).items()}
    reads = set(host_rx)
    if any(type(tick) is not int or tick < 0 for tick in writes.keys() | reads):
        raise ValueError("host transaction ticks must be nonnegative integers")

    pc = data = oe = 0
    prepared_data = prepared_oe = timer = lc = osr = isr = 0
    wait_result = "NONE"
    phase = "run"

    def observe(*, running=True, fetch=False, exec_start=False, retire=False, fault_reason=None):
        edge = Edge(pc, data, oe, running, fetch, exec_start, retire)
        return EngineStep(edge, wait_result, lc, osr, isr, len(tx), len(rx),
                          tx_pop, rx_push, host_tx_ok, host_rx_ok, host_rx_data, fault_reason)

    for tick, (sample, previous) in enumerate(_synchronized_inputs(uio_in, pre_run)):
        tx_pop = rx_push = host_tx_ok = host_rx_ok = False
        host_rx_data = None
        if tick == stop_at:  # §6: STOP suppresses same-edge engine effects.
            pc = data = oe = 0
            prepared_data = prepared_oe = timer = lc = osr = isr = 0
            wait_result = "NONE"
            tx.clear()
            rx.clear()
            yield observe(running=False)
            return

        # §5: both sides decide from the old counts, including full and empty.
        tx_count_before, rx_count_before = len(tx), len(rx)
        host_tx_ok = tick in writes and tx_count_before < 4
        host_rx_ok = tick in reads and rx_count_before > 0
        if host_tx_ok:
            tx.append(writes[tick])
        if host_rx_ok:
            host_rx_data = rx.popleft()

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

        start = phase == "start"  # One S pulse even if R comes later.
        if start:
            try:
                next_pc = _next_pc(opcode, operand, pc, len(image) // 2, wait_result, lc)
            except ValueError as error:
                oe = 0
                yield observe(running=False, exec_start=True, fault_reason=str(error))
                return
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
        elif opcode == 0x08:  # SHIN
            # Earliest S=K+2 already sees the raw input from K.
            assert sample is not None
            bit = (sample >> (operand & 7)) & 1
            if operand & 8:
                isr = (isr >> 1) | (bit << 7)
            else:
                isr = ((isr << 1) | bit) & 0xFF
        elif opcode == 0x09:  # SHOUT updates the active and staged data bit together.
            mask = 1 << (operand & 7)
            if operand & 8:
                bit, osr = osr & 1, osr >> 1
            else:
                bit, osr = osr >> 7, (osr << 1) & 0xFF
            data = (data & ~mask) | (mask if bit else 0)
            prepared_data = (prepared_data & ~mask) | (mask if bit else 0)
        # BR only changes next_pc, already selected at S.
        elif opcode == 0x0B:  # LSET
            lc = operand
        elif opcode == 0x0C:  # DJNZ
            lc = max(0, lc - 1)
        elif opcode == 0x0D:  # PUSH
            rx_push = retire = rx_count_before < 4
            if rx_push:
                rx.append(isr)
        elif opcode == 0x0E:  # PULL
            tx_pop = retire = tx_count_before > 0
            if tx_pop:
                osr = tx.popleft()
        elif opcode == 0x0F:  # SRIMM
            osr = operand

        if retire:
            pc = next_pc
            phase = "fetch"
        else:
            if remaining and opcode in (0x06, 0x07):
                remaining -= 1
            phase = "retry"
        yield observe(exec_start=start, retire=retire)


def trace_timed(
    image: bytes, uio_in: Iterable[int] = (), *,
    pre_run: Iterable[int] = (), stop_at: int | None = None,
) -> Iterator[TimedEdge]:
    """Project the original timed view, raising ValueError on an engine fault."""
    for step in trace_engine(image, uio_in, pre_run=pre_run, stop_at=stop_at):
        if step.fault:
            raise ValueError(step.fault_reason)
        yield TimedEdge(step.edge, step.wait_result)


def trace_run(image: bytes) -> Iterator[Edge]:
    """Yield the original seven edge fields from K through HALT."""
    for timed in trace_timed(image):
        yield timed.edge
