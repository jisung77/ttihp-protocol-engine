"""Check a PS/2 receiver binary on the checkout's v0.1 software ISS.

Run python experiments/ps2/check_iss.py path/to/ps2_rx.bin from the repo root.
The optional binary argument defaults to ps2_rx.bin beside this script.
"""

import argparse
import json
import sys
from bisect import bisect_right
from hashlib import sha256
from itertools import chain, islice, repeat
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
from model.iss import trace_engine


BASE_HZ = 2_000_000
# Each complete malformed frame precedes at least two good frames without a gap.
ERRORS = {
    0: ("start", 0xA6), 24: ("parity", 0x00), 54: ("stop", 0xFE),
    87: ("start", 0x00), 116: ("parity", 0xFF), 147: ("stop", 0x55),
    174: ("start", 0xFF), 202: ("parity", 0x80), 236: ("stop", 0x00),
}


def frame_plan():
    for index in range(256):
        if index in ERRORS:
            yield ERRORS[index]
        yield "valid", (73 * index + 29) % 256  # A permutation of all 256 bytes.


def pin_samples(frames, period):
    """Generate one uio byte per 500 ns, clock on bit 0 and data on bit 1."""
    high, low = (period + 1) // 2, period // 2
    pins = bytearray([3] * 400)
    data = 1
    for frame_index, (kind, byte) in enumerate(frames):
        payload = [(byte >> bit) & 1 for bit in range(8)]
        bits = [0, *payload, int(sum(payload) % 2 == 0), 1]
        if kind != "valid":
            bits[{"start": 0, "parity": 9, "stop": 10}[kind]] ^= 1
        for bit_index, bit in enumerate(bits):
            setup = (12, 20, 30)[(bit_index + frame_index) % 3]  # 6, 10, 15 us
            pins.extend([1 | data << 1] * (high - setup))
            data = bit
            pins.extend([1 | data << 1] * setup)
            pins.extend([data << 1] * low)
    pins.extend([1 | data << 1] * 16)
    pins.extend([3] * 400)
    return pins


def check_wire(pins, frames):
    """Decode the generated waveform independently at each falling clock edge."""
    falls = [i for i in range(1, len(pins)) if pins[i - 1] & 1 and not pins[i] & 1]
    if len(falls) != 11 * len(frames):
        raise ValueError("unexpected number of falling clock edges")
    for n, (kind, expected_byte) in enumerate(frames):
        bits = [(pins[t] >> 1) & 1 for t in falls[n * 11 : (n + 1) * 11]]
        byte = sum(bits[i + 1] << i for i in range(8))
        framing = (bits[0] == 0, sum(bits[1:10]) % 2 == 1, bits[10] == 1)
        if byte != expected_byte or framing != (kind != "start", kind != "parity", kind != "stop"):
            raise ValueError(f"stimulus mismatch in frame {n}")
    return falls[10::11]


def check_rate(image, frames, engine_hz, wire_hz):
    period = BASE_HZ // wire_hz
    base_pins = pin_samples(frames, period)
    scale = engine_hz // BASE_HZ
    stop_edges = [t * scale for t in check_wire(base_pins, frames)]
    ticks = len(base_pins) * scale
    # At 50 MHz, 25 consecutive 20 ns edges reproduce one 500 ns pin sample.
    pins = chain.from_iterable(repeat(level, scale) for level in base_pins)
    host_reads = range(17 * scale, ticks, 31 * scale)
    steps = trace_engine(image, pins, pre_run=(3, 3, 3), host_rx=host_reads)

    expected_frames = [n for n, (kind, _) in enumerate(frames) if kind == "valid"]
    expected_bytes = bytes(frames[n][1] for n in expected_frames)
    deliveries = bytearray()
    pushes = 0
    tag = f"{engine_hz} Hz engine / {wire_hz} Hz wire"
    for tick, step in enumerate(islice(steps, ticks)):
        if step.fault or not step.edge.running or step.edge.uio_oe:
            raise AssertionError(f"{tag} at K+{tick}: {step}")
        if step.rx_push:
            # Attribute each push to the latest frame whose stop bit was sampled.
            frame = bisect_right(stop_edges, tick) - 1
            if pushes >= len(expected_frames) or frame != expected_frames[pushes]:
                raise AssertionError(f"{tag}: unexpected RX push at K+{tick}, frame {frame}")
            pushes += 1
        if step.host_rx_ok:
            n = len(deliveries)
            if n >= len(expected_bytes) or step.host_rx_data != expected_bytes[n]:
                raise AssertionError(f"{tag}: read {n} returned {step.host_rx_data} at K+{tick}")
            deliveries.append(step.host_rx_data)
    if tick + 1 != ticks or pushes != 256 or bytes(deliveries) != expected_bytes or step.rx_count:
        raise AssertionError(f"{tag}: {tick + 1}/{ticks} edges, {pushes} pushes, "
                             f"{len(deliveries)} reads, {step.rx_count} pending")
    return {
        "engine_hz": engine_hz, "wire_hz": wire_hz, "engine_edges": ticks,
        "valid_bytes_ordered": len(deliveries), "malformed_rejected": len(frames) - pushes,
        "bad_start": 3, "bad_parity": 3, "bad_stop": 3, "pushes": pushes,
        "fault_edges": 0, "oe_nonzero_edges": 0, "pending_rx": step.rx_count,
        "base_pins_sha256": sha256(base_pins).hexdigest(), "ticks_per_sample": scale,
        "received_bytes_sha256": sha256(deliveries).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, nargs="?", default=HERE / "ps2_rx.bin",
                        help="little-endian program image from model.assembler")
    image = parser.parse_args().binary.read_bytes()
    frames = list(frame_plan())
    clocks = ((BASE_HZ, 10_000), (BASE_HZ, 12_500), (BASE_HZ, 16_000), (50_000_000, 16_000))
    runs = [check_rate(image, frames, engine, wire) for engine, wire in clocks]
    print(json.dumps({
        "status": "PASS", "scope": "software ISS from accepted RUN, transaction-level host RX",
        "binary_bytes": len(image), "binary_sha256": sha256(image).hexdigest(),
        "iss_sha256": sha256((REPO / "model/iss.py").read_bytes()).hexdigest(),
        "spec_sha256": sha256((REPO / "spec/spec.md").read_bytes()).hexdigest(),
        "valid_bytes_ordered": sum(run["valid_bytes_ordered"] for run in runs),
        "malformed_rejected": sum(run["malformed_rejected"] for run in runs),
        "engine_edges": sum(run["engine_edges"] for run in runs), "runs": runs,
    }, indent=2))


if __name__ == "__main__":
    main()
