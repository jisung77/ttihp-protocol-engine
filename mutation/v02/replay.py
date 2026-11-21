"""Rebuild a v0.2 RTL mutant and replay one frozen or augmented cocotb suite."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = (HERE / 'source-revision.txt').read_text().strip()
SUITES = {
    'directed': ('test_dual', r'dual_run_and_wait_edges$'),
    'random-legacy': ('test_dual_random,test', ''),
    'protocol': ('test_dual', r'dual_uart_spi_modes$'),
    'faults': ('test_dual_faults', ''),
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('variant', help='clean or M01 through M15')
    parser.add_argument('directory', type=Path, help='new output directory for this run')
    parser.add_argument('--suite', choices=SUITES, default='directed')
    args = parser.parse_args()
    catalog = {row['id']: row for row in json.loads((HERE / 'catalog.json').read_text())}
    if args.variant != 'clean' and args.variant not in catalog:
        parser.error('unknown variant')
    target = args.directory.resolve()
    target.mkdir(parents=True, exist_ok=False)
    src = target / 'src'
    src.mkdir()
    hashes = {}
    for path in sorted((ROOT / 'src').glob('*.v')):
        raw = subprocess.check_output(['git', 'show', f'{BASE}:src/{path.name}'], cwd=ROOT)
        change = catalog.get(args.variant)
        if change and path.name == change['file']:
            old, new = (change[k].encode() for k in ('old', 'new'))
            if raw.count(old) != 1:
                raise ValueError('mutation site must be unique')
            raw = raw.replace(old, new)
            if raw != (HERE / args.variant / path.name).read_bytes():
                raise ValueError('published mutant differs from the catalog and source revision')
        (src / path.name).write_bytes(raw)
        hashes[path.name] = hashlib.sha256(raw).hexdigest()
    for name in ('test', 'model', 'examples', 'experiments', 'host'):
        shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns(
            '__pycache__', 'sim_build', '*.xml', '*.fst', 'output', '.pytest_cache'))
    modules, pattern = SUITES[args.suite]
    test = target / 'test'
    env = dict(os.environ, PWD=str(test), PLUSARGS='+NO_DUMP', COCOTB_RANDOM_SEED='13977')
    env.pop('GATES', None)
    cmd = ['make', f'COCOTB_TEST_MODULES={modules}', f'COCOTB_TEST_FILTER={pattern}']
    with (target / 'simulation.log').open('w') as log:
        run = subprocess.run(cmd, cwd=test, env=env, stdout=log, stderr=subprocess.STDOUT)
    cases = list(ET.parse(test / 'results.xml').iter('testcase'))
    expected = {
        'directed': {'dual_run_and_wait_edges'},
        'protocol': {'dual_uart_spi_modes'},
        'random-legacy': {'first_pin_trace_and_loader_errors', 'selected_pc_wait_fifo_and_fault',
                          'fifo_stop_reset_and_reload', 'seeded_cycle_differential',
                          'branch_fifo_reset_coverage', 'accepted_stop_preempts_same_edge_retire',
                          'seeded_valid_dual_programs'},
        'faults': {'dual_fault_isolation_and_bad_end'},
    }[args.suite]
    cases = [c for c in cases if c.find('skipped') is None]
    if {c.get('name') for c in cases} != expected or len(cases) != len(expected) or any(c.find('error') is not None for c in cases):
        raise RuntimeError('suite incomplete, inspect simulation.log')
    failed = [c for c in cases if c.find('failure') is not None]
    if bool(failed) != bool(run.returncode) or any(
            c.find('failure').get('error_type','') != 'AssertionError' for c in failed):
        raise RuntimeError('infrastructure or non-assertion failure, inspect simulation.log')
    verdict = dict(variant=args.variant, suite=args.suite, rtl=BASE, sources=hashes,
                   tests=[c.get('name') for c in cases], failures=[c.get('name') for c in failed],
                   make_exit=run.returncode, command=cmd)
    (target / 'verdict.json').write_text(json.dumps(verdict,indent=2)+'\n')
    print(f"{args.variant}: {len(failed)} assertion failure(s) in {len(cases)} test(s)")
    if args.variant == 'clean' and failed:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
