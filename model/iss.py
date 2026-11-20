"""Cycle trace from an accepted RUN (K) through HALT, for the section 7 subset.

Input is the program payload in loader byte order, without commands or nibbles.
Malformed images, invalid subset instructions and sequential fall-through raise
ValueError when encountered. Other defined opcodes raise NotImplementedError
before their operands are inspected. This slice does not simulate fault edges.
"""

from collections.abc import Iterator
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


def trace_run(image: bytes) -> Iterator[Edge]:
    """Yield K, then one fetch and one execution edge per supported instruction."""
    if not isinstance(image, bytes):
        raise TypeError("expected program payload as bytes")
    if not 2 <= len(image) <= 512 or len(image) % 2:
        raise ValueError("expected 1 to 256 complete 16-bit words")

    pc = 0
    data = oe = 0
    prepared_data = prepared_oe = 0
    yield Edge(pc, data, oe, True, False, False, False)  # K

    while True:
        offset = 2 * pc
        word = int.from_bytes(image[offset : offset + 2], "little")
        yield Edge(pc, data, oe, True, True, False, False)  # F

        opcode = word >> 11
        operand = word & 0x7FF
        if 0x04 <= opcode <= 0x0F:
            raise NotImplementedError(f"opcode {opcode:#04x} outside section 7 at PC {pc}")
        if opcode > 0x0F:
            raise ValueError(f"reserved opcode {opcode:#04x} at PC {pc}")

        if opcode == 0x00:  # HALT
            if operand:
                raise ValueError(f"nonzero HALT operand at PC {pc}")
            yield Edge(pc, data, 0, False, False, True, True)
            return

        if operand > 0xFF:
            raise ValueError(f"reserved operand bits at PC {pc}")
        if pc + 1 >= len(image) // 2:
            raise ValueError(f"next PC outside image at PC {pc}")

        if opcode == 0x01:  # DATA
            prepared_data = operand
        elif opcode == 0x02:  # OE
            prepared_oe = operand
        else:  # COMMIT
            data = (data & ~operand) | (prepared_data & operand)
            oe = (oe & ~operand) | (prepared_oe & operand)
        pc += 1
        yield Edge(pc, data, oe, True, False, True, True)  # S = R
