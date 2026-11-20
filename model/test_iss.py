import pytest

from model.iss import trace_run


def test_section_7_every_edge():
    # Four hand-encoded words in WRITE payload order, starting at accepted RUN.
    image = bytes.fromhex("01 08  01 10  01 18  00 00")
    # PC, uio_out, uio_oe, running, fetch, exec_start, retire (post-edge)
    assert list(trace_run(image)) == [
        (0, 0, 0, 1, 0, 0, 0),  # K
        (0, 0, 0, 1, 1, 0, 0),  # K+1: fetch DATA
        (1, 0, 0, 1, 0, 1, 1),  # K+2: stage data
        (1, 0, 0, 1, 1, 0, 0),
        (2, 0, 0, 1, 0, 1, 1),  # K+4: stage OE
        (2, 0, 0, 1, 1, 0, 0),
        (3, 1, 1, 1, 0, 1, 1),  # K+6: drive pin 0
        (3, 1, 1, 1, 1, 0, 0),
        (3, 1, 0, 0, 0, 1, 1),  # K+8: release pin 0
    ]


def test_partial_commit_preserves_unmasked_data_and_oe():
    # Stage opposite bytes and commit all pins. Swap them and commit only pins 0..3.
    image = bytes.fromhex("5A 08  A5 10  FF 18  A5 08  5A 10  0F 18  00 00")
    retired = [(e.uio_out, e.uio_oe) for e in trace_run(image) if e.retire]
    assert retired == [
        (0, 0), (0, 0), (0x5A, 0xA5),
        (0x5A, 0xA5), (0x5A, 0xA5), (0x55, 0xAA), (0x55, 0),
    ]


def test_tlo_is_legal_but_outside_this_model():
    with pytest.raises(NotImplementedError, match="opcode 0x04"):
        list(trace_run(bytes.fromhex("01 20  00 00")))


@pytest.mark.parametrize(
    "image, reason",
    [
        (b"", "complete 16-bit words"),
        (bytes(3), "complete 16-bit words"),
        (bytes(514), "complete 16-bit words"),
        (bytes.fromhex("00 80"), "reserved opcode"),
        (bytes.fromhex("01 00"), "HALT operand"),
        (bytes.fromhex("00 09  00 00"), "reserved operand"),
        (bytes.fromhex("01 08"), "next PC outside image"),
    ],
    ids=['empty', 'odd', 'oversize', 'reserved-opcode', 'halt-operand', 'reserved-bits', 'fall-through'],
)
def test_rejects_malformed_or_unrunnable_input(image, reason):
    with pytest.raises(ValueError, match=reason):
        list(trace_run(image))
