"""Assemble protocol-engine source into the loader's little-endian word payload."""

import argparse
import re
from pathlib import Path


OPCODES = {
    "HALT": 0x00, "DATA": 0x01, "OE": 0x02, "COMMIT": 0x03,
    "TLO": 0x04, "THI": 0x05, "WAIT": 0x06, "DELAY": 0x07,
    "SHIN": 0x08, "SHOUT": 0x09, "BR": 0x0A, "LSET": 0x0B,
    "DJNZ": 0x0C, "PUSH": 0x0D, "PULL": 0x0E, "SRIMM": 0x0F,
}
LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
INTEGER = re.compile(r"(?:[0-9]+|0[xX][0-9a-fA-F]+|0[bB][01]+)")


class AssemblyError(ValueError):
    """Invalid source, with the source line number where available."""


def number(text, maximum):
    if not INTEGER.fullmatch(text):
        raise AssemblyError(f"expected an unsigned integer, got {text!r}")
    base = 16 if text.lower().startswith("0x") else 2 if text.lower().startswith("0b") else 10
    value = int(text, base)
    if value > maximum:
        raise AssemblyError(f"{text!r} exceeds {maximum}")
    return value


def choice(value, names):
    try:
        return names.index(value.upper())
    except ValueError:
        raise AssemblyError(f"expected {'/'.join(names)}, got {value!r}") from None


def encode(parts, labels):
    op, *args = parts
    op = op.upper()
    if op not in OPCODES:
        raise AssemblyError(f"unknown instruction {op!r}")
    if op in ("HALT", "DELAY", "PUSH", "PULL"):
        count = 0
    elif op == "WAIT":
        count = 3
    elif op in ("SHIN", "SHOUT", "BR"):
        count = 2
    else:
        count = 1
    if len(args) != count:
        raise AssemblyError(f"{op} requires {count} operand(s), got {len(args)}")

    operand = 0
    if op == "WAIT":
        mode = choice(args[0], ("LOW", "HIGH", "RISE", "FALL"))
        infinite = choice(args[2], ("FINITE", "FOREVER"))
        operand = number(args[1], 7) | (mode << 3) | (infinite << 5)
    elif op in ("SHIN", "SHOUT"):
        operand = number(args[0], 7) | (choice(args[1], ("MSB", "LSB")) << 3)
    elif op in ("BR", "DJNZ"):
        target = args[-1]
        if LABEL.fullmatch(target):
            if target not in labels:
                raise AssemblyError(f"undefined label {target!r}")
            operand = labels[target]
        else:
            operand = number(target, 255)
        if op == "BR":
            operand |= choice(args[0], ("ALWAYS", "OK", "TIMEOUT", "LC_ZERO")) << 8
    elif args:
        operand = number(args[0], 255)
    return (OPCODES[op] << 11) | operand


def assemble(source: str) -> bytes:
    """Resolve absolute word labels, then return one to 256 encoded words."""
    labels = {}
    statements = []
    for lineno, line in enumerate(source.splitlines(), 1):
        text = line.partition("#")[0].strip()
        if ":" in text:
            label, _, text = text.partition(":")
            label = label.strip()
            if not LABEL.fullmatch(label) or label in labels or len(statements) == 256:
                raise AssemblyError(f"line {lineno}: invalid or duplicate label {label!r}")
            labels[label] = len(statements)
        parts = text.split()
        if parts:
            if len(statements) == 256:
                raise AssemblyError(f"line {lineno}: maximum image length is 256 words")
            statements.append((lineno, parts))

    if not statements:
        raise AssemblyError("empty program")
    if len(statements) in labels.values():
        raise AssemblyError("label after the final instruction")
    output = bytearray()
    for lineno, parts in statements:
        try:
            word = encode(parts, labels)
        except AssemblyError as exc:
            raise AssemblyError(f"line {lineno}: {exc}") from exc
        output.extend(word.to_bytes(2, "little"))
    return bytes(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        payload = assemble(args.source.read_text(encoding="utf-8"))
        args.output.write_bytes(payload)
    except (OSError, UnicodeError, AssemblyError) as exc:
        parser.exit(1, f"{args.source}: {exc}\n")


if __name__ == "__main__":
    main()
