"""Reproduce the 15 single-edit mutants against the project's unchanged cocotb suite."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = Path(os.environ.get("PE_MUTATION_SOURCE", REPO)).resolve()
TEST = ROOT / "test"
SEED = "18455"
DESIGN = ["project.v", "pe_host.v", "pe_core.v", "pe_fifo.v", "pe_sram.v"]
MACRO = sorted((TEST / "sram_model").glob("*.v"))
CASES = {
    "core": [
        "first_pin_trace_and_loader_errors", "selected_pc_wait_fifo_and_fault",
        "fifo_stop_reset_and_reload", "seeded_cycle_differential", "branch_fifo_reset_coverage",
    ],
    "protocol": ["uart_tx_8n1_all_bytes"]
        + [f"uart_rx_phase_and_rate/rate={rate}" for rate in (112896, 115200, 117504)]
        + [f"spi_master_full_duplex/mode={mode}" for mode in range(4)],
    "witness": ["stop_on_branch_execute"],
}
MODULE = {"core": "test", "protocol": "test_protocols", "witness": "stop_witness"}


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def materialize(generate):
    entries = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    if len({entry["id"] for entry in entries}) != 15 or len(entries) != 15:
        raise ValueError("expected 15 unique single mutants")
    variants = {"baseline": {}}
    for entry in entries:
        file = entry["file"]
        original = (ROOT / "src" / file).read_text(encoding="utf-8")
        if original.count(entry["original"]) != 1:
            raise ValueError(f"{entry['id']} edit no longer matches one location")
        edited = original.replace(entry["original"], entry["replacement"])
        dest = HERE / entry["id"] / file
        if generate:
            dest.parent.mkdir(exist_ok=True)
            dest.write_text(edited, encoding="utf-8", newline="\n")
        if dest.read_text(encoding="utf-8") != edited or edited == original:
            raise ValueError(f"{entry['id']} copy differs from specified edit")
        variants[entry["id"]] = {file: dest}
    return variants


def regression(variant, override, suite, evidence):
    dest = evidence / variant / suite
    dest.mkdir(parents=True, exist_ok=False)
    verilog = MACRO + [override.get(f, ROOT / "src" / f) for f in DESIGN] + [TEST / "tb.v"]
    inputs = verilog + [TEST / "Makefile", TEST / "host_driver.py", ROOT / "model/iss.py",
                        ROOT / "model/assembler.py", TEST / "test.py", TEST / "test_protocols.py",
                        HERE / "manifest.json", HERE / "run.py"]
    inputs += sorted((ROOT / "examples").glob("*.asm"))
    if suite == "witness":
        inputs.append(HERE / "stop_witness.py")
    source_files = {str(p): digest(p) for p in inputs}
    input_hashes = {("source/" + str(p.relative_to(ROOT)) if p.is_relative_to(ROOT)
                     else "review/" + str(p.relative_to(REPO))): source_files[str(p)] for p in inputs}
    xml = dest / "results.xml"
    cmd = ["make", "-f", "Makefile", str(xml), f"SIM_BUILD={dest}/build",
           f"COCOTB_RESULTS_FILE={xml}", f"VERILOG_SOURCES={' '.join(map(str, verilog))}",
           f"COCOTB_TEST_MODULES={MODULE[suite]}", "COCOTB_PLUSARGS=+NO_DUMP", "WAVES=0"]
    env = os.environ.copy()
    for key in ("GATES", "COCOTB_TESTCASE", "TESTCASE", "MODULE", "COCOTB_TEST_FILTER"):
        env.pop(key, None)
    env.update(PWD=str(TEST), PYTHONPATH=os.pathsep.join([str(HERE), str(TEST), str(ROOT)]),
               PYTHONDONTWRITEBYTECODE="1", COCOTB_RANDOM_SEED=SEED,
               COVERAGE_FILE=str(dest / "coverage.yml"),
               SPI_COVERAGE_FILE=str(dest / "spi-coverage.yml"), TT_EXTENDED="1")
    def output(*args):
        res = subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")
        return (res.stdout + res.stderr).strip().splitlines()[0]
    revision_file = ROOT / "source-revision.txt"
    revision = revision_file.read_text().strip() if revision_file.is_file() else output("git", "rev-parse", "HEAD")
    header = dict(variant=variant, suite=suite, seed=SEED, extended=env["TT_EXTENDED"],
                  commit=revision,
                  iverilog=output("iverilog", "-V"),
                  cocotb=output("cocotb-config", "--version"), python=sys.version.split()[0],
                  cwd=str(TEST), command=shlex.join(cmd), inputs=input_hashes)
    write_json(dest / "run.json", header)
    with (dest / "run.log").open("w") as log:
        p = subprocess.run(cmd, cwd=TEST, env=env, stdout=log, stderr=subprocess.STDOUT)
    result = dict(variant=variant, suite=suite, make_exit=p.returncode, status="infrastructure",
                  passed=[], failed=[], messages={})
    if xml.exists() and (dest / "build/sim.vvp").is_file():
        tree = ET.parse(xml)
        cases = tree.findall(".//testcase")
        names = [c.attrib["name"] for c in cases]
        expected = set(CASES[suite])
        stop_test = "accepted_stop_preempts_same_edge_retire"
        if suite == "core" and f"async def {stop_test}(" in (TEST / "test.py").read_text(encoding="utf-8"):
            expected.add(stop_test)
        if (sorted(names) == sorted(expected) and not tree.findall(".//skipped")
                and not tree.findall(".//error")
                and tree.find(f".//property[@name='random_seed'][@value='{SEED}']") is not None):
            for case in cases:
                name = case.attrib["name"]
                failure = case.find("failure")
                result["failed" if failure is not None else "passed"].append(name)
                if failure is not None:
                    result["messages"][name] = failure.get("error_msg", "")[:250]
            if p.returncode == 0 and not result["failed"]:
                result["status"] = "pass"
            elif (p.returncode != 0 and result["failed"]
                  and all(f.get("error_type") == "AssertionError" for f in tree.findall(".//failure"))):
                result["status"] = "fail"
    if any(digest(Path(name)) != value for name, value in source_files.items()):
        result["status"] = "inputs_changed"
    write_json(dest / "verdict.json", result)
    print(variant, suite, result["status"], ", ".join(result["failed"]), flush=True)
    return result["status"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true", help="regenerate the changed RTL modules")
    parser.add_argument("--suite", choices=CASES, default="core")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("variants", nargs="*", help="baseline and/or M01 through M15")
    args = parser.parse_args()
    variants = materialize(args.generate)
    if args.evidence is None:
        if args.variants or not args.generate:
            parser.error("supply --evidence for a regression")
        return
    for name in args.variants or variants:
        if name not in variants:
            parser.error(f"unknown variant {name}")
        status = regression(name, variants[name], args.suite, args.evidence.resolve())
        if status not in ("pass", "fail") or (name == "baseline" and status != "pass"):
            raise SystemExit(f"{name}: inspect infrastructure/baseline before scoring")


if __name__ == "__main__":
    main()
