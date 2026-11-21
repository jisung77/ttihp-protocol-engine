"""Exercise a one-byte I²C write using the public Tiny Tapeout host and bus pins."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct

from model.assembler import assemble
from .check_bus import check_bus
from .exercise import exercise
from .write_image import build_source


@dataclass(frozen=True)
class Case:
    name: str
    address: int
    data: int
    acks: tuple
    stretch_pulse: int | None = None
    stretch_ticks: int | None = 0


CASES = (
    Case("write", 0x52, 0xA5, (0, 0)),
    Case("address_nack", 0x52, 0xA5, (1,)),
    Case("data_nack", 0x52, 0xA5, (0, 1)),
    Case("stretch_data", 0x2D, 0x5A, (0, 0), 5, 750),
    Case("stretch_ack", 0x08, 0x00, (0, 0), 9, 1000),
    Case("stretch_stop_after_nack", 0x52, 0xA5, (1,), 10, 620),
    Case("all_ones_data", 0x77, 0xFF, (0, 0)),
    Case("held_clock_host_abort", 0x52, 0xA5, (0, 0), 5, None),
)


def run_case(machine, case, trace_dir=None):
    image = assemble(build_source(case.address, case.data))
    words = [word for word, in struct.iter_unpack('<H', image)]
    rows, host = exercise(machine, words, address=case.address, data=case.data,
                          acks=case.acks, stretch_pulse=case.stretch_pulse,
                          stretch_ticks=case.stretch_ticks)
    raw = bytes(value for row in rows for value in row)
    if trace_dir is not None:
        directory = Path(trace_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'i2c-{case.name}.bin').write_bytes(raw)
    aborted = case.stretch_ticks is None
    assert host['aborted'] == aborted
    if aborted:
        assert host['host_rx'] == []
        decoded = None
    else:
        sent = [case.address << 1, case.data][:len(case.acks)]
        assert host['host_rx'] == list(case.acks)
        assert host['target_bytes'] == sent
        decoded = check_bus(rows, sent, list(case.acks))
        assert decoded['ok'] and not decoded['errors'], decoded['errors']
        assert decoded['bytes'] == sent and decoded['acks'] == list(case.acks)
    return dict(case=case.name, words=len(words), binary_sha256=sha256(image).hexdigest(),
                clocks=len(rows), pins_sha256=sha256(raw).hexdigest(), host=host, decoded=decoded)


def main():
    import argparse
    from model.protocol_machine import ProtocolMachine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='JSON file for the ISS results')
    parser.add_argument('--traces', type=Path, help='directory for raw per-clock output, OE, target-LOW triplets')
    args = parser.parse_args()
    results = [run_case(ProtocolMachine(), case, args.traces) for case in CASES]
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(results, indent=2) + '\n')
    print(f'{len(results)} I2C scenarios passed')


if __name__ == '__main__':
    main()
