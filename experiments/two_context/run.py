"""Build programs with the project assembler, then run the shared-slot experiment."""
import argparse
import gzip
import hashlib
import itertools
import json
import struct
import subprocess
import sys
from pathlib import Path

from byte_sweep import run_sweep
from check_pins import check, spi_frames
from schedule import program, simulate
from wait_slots import sweep


def build(repo, output, name, mode=None, tuned=True):
    source = repo / "examples" / (("uart_tx" if mode is None else f"spi_mode{mode}") + ".asm")
    ops, labels = program(source, mode, tuned)
    lines = []
    for i, (op, args) in enumerate(ops):
        lines.extend(label + ":" for label, pc in labels.items() if pc == i)
        lines.append(" ".join([op, *args]))
    assembly = output / (name + ".asm")
    binary = output / (name + ".bin")
    assembly.write_text("\n".join(lines) + "\n")
    subprocess.run([sys.executable, "-m", "model.assembler", assembly, binary],
                   cwd=repo, check=True, capture_output=True)
    raw = binary.read_bytes()
    assert len(raw) == 2 * len(ops)
    return [word for word, in struct.iter_unpack("<H", raw)]


def wait_boundary():
    # Reference uses an already synchronized rising-edge indication at S+1.
    # Both rules are expressed on the SAME global clock; no analog pulse assumption.
    witnesses = []
    for start in (8, 9):
        for duration in (0, 1, 2, 3):
            end = start + duration
            for event in range(start - 1, end + 2):
                expected = "OK" if start <= event <= end else "TIMEOUT"
                only_slots = range(start, end + 1, 2)
                slot_only = "OK" if event in only_slots else "TIMEOUT"
                if expected != slot_only:
                    witnesses.append(dict(start=start, deadline=end, event=event,
                                          reference=expected, slot_only=slot_only))
    assert any(w["deadline"] == w["event"] == w["start"] + 1 for w in witnesses)
    return witnesses


def stress_miso(uart, spi, mode, parity, arrivals, rows):
    spans = spi_frames(rows, mode)[0]
    stimulus = [0] * len(rows)
    for (_, _, edges), byte in zip(spans, (0x3d, 0xc6)):
        for i, tick in enumerate(edges[mode % 2::2]):
            bit = (byte >> (7 - i)) & 1
            stimulus[tick-4:tick+5] = [1 - bit] + [bit] * 7 + [1 - bit]
    stressed, engine = simulate(uart, spi, mode, parity, arrivals, external_miso=stimulus)
    checked = check(stressed, mode)
    margin = checked["stable_before_after_clocks_capped32"]["MISO"]
    assert margin == [3, 3] and engine["spi_fifo"] == [0x3d, 0xc6]
    return margin


def main(repo, out):
    out.mkdir(parents=True, exist_ok=True)
    images, traces = out / "images", out / "traces"
    images.mkdir(exist_ok=True)
    traces.mkdir(exist_ok=True)
    uart = build(repo, images, "uart_pin4")
    passed, controls, spi_programs = [], [], []
    for mode in range(4):
        tuned = build(repo, images, f"spi{mode}_tuned", mode)
        spi_programs.append(tuned)
        original = build(repo, images, f"spi{mode}_original", mode, tuned=False)
        for parity, arrivals in itertools.product(range(2), ((0, 307), (180, 0))):
            for name, spi in (("original", original), ("tuned", tuned)):
                rows, engine = simulate(uart, spi, mode, parity, arrivals)
                assert engine["spi_fifo"] == [0x3d, 0xc6]
                case = dict(mode=mode, uart_fetch_parity=parity, queue_ready=list(arrivals))
                if name == "original":
                    try:
                        check(rows, mode)
                    except ValueError as err:
                        if "full period differs from 50" not in str(err):
                            raise
                        first = str(err)
                    else:
                        raise AssertionError("original waveform unexpectedly met 1 MHz")
                    # Record an observed same-polarity edge interval for the counterexample.
                    active_edges = [i for i in range(1, len(rows)) if rows[i][1] & 8
                                    and not rows[i][0] & 8 and (rows[i-1][0] ^ rows[i][0]) & 1]
                    controls.append(dict(**case, first_failure=first, full_ticks=active_edges[2]-active_edges[0]))
                    continue
                decoded = check(rows, mode)
                case["stressed_miso_before_after_clocks"] = stress_miso(uart, spi, mode, parity, arrivals, rows)
                assert all(any(a < t < b for t in decoded["uart_edges_under_cs"])
                           for a, b in decoded["spi_select_windows"])
                payload = bytes(value for row in rows for value in row)
                trace = f"m{mode}-p{parity}-u{arrivals[0]}-s{arrivals[1]}.bin.gz"
                (traces / trace).write_bytes(gzip.compress(payload, mtime=0))
                passed.append(dict(**case, **engine, **decoded, trace=trace,
                                   trace_sha256=hashlib.sha256(payload).hexdigest()))
    assert len(passed) == len(controls) == 16 and all(c["full_ticks"] == 52 for c in controls)
    report = dict(pass_cases=passed, original_spi_controls=controls, wait_counterexamples=wait_boundary(),
                  wait_proposal=sweep(), byte_sweep=run_sweep(uart, spi_programs),
                  model_clock_hz=50000000, rows_per_trace=10000,
                  trace_format="three uint8 per after-edge wall clock: uio_out, uio_oe, external_miso")
    (out / "timing.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(passed)} simultaneous cases passed; {len(controls)} original SPI schedules rejected; "
          f'{len(report["wait_counterexamples"])} missed-event examples with slot-only WAIT; '
          f'{sum(row["trials"] for row in report["byte_sweep"])} byte-sweep cases passed.')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.repo.resolve(), args.output.resolve())
