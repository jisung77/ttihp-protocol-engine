"""Find legal host pin traces reaching the two single-engine PC boundaries."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def find_witness(model, destination, length):
    directory = destination / f'length-{length}'
    directory.mkdir(parents=True, exist_ok=True)
    inputs = [0] * 8  # reset for two clocks, then release with the single-mode strap
    checks = []
    req = 0

    def frame(opcode, word=None, reply=0):
        nonlocal req
        octets = [opcode] if word is None else [opcode, word & 255, word >> 8]
        nibbles = [nibble for byte in octets for nibble in (byte >> 4, byte & 15)]
        for index, nibble in enumerate(nibbles):
            inputs.append(req | nibble)  # establish bundled data before toggling REQ
            req ^= 16
            inputs.extend([req | nibble] * 3)
            ack = len(inputs) + 1
            code = reply if index + 1 == len(nibbles) else 0
            checks.extend([(ack, 'uo_out[4]', req >> 4), (ack, 'uo_out[3:0]', code)])
        return ack

    bad = None
    if length == 256:
        bad = frame(1, 0, reply=3)
        checks.append((bad + 1, 'declared_length', 0))
    begin = frame(1, length)
    checks.append((begin + 1, 'declared_length', length))
    # The solver may accept END without WRITE in this abstraction. A concrete
    # 256-word upload is a separate check. The three cutpoints remain symbolic.
    frame(3, 0)
    run = frame(4)
    inputs.extend([req] * 7)
    end = len(inputs)
    for step, pc in ((run, 0), (end, length - 1)):
        checks.extend([(step, 'uo_out[6]', 1), (step, 'pc', pc),
                       (step, 'declared_length', length)])

    args = ['-seq', str(end), '-set-assumes', '-set ena 1', '-set rst_n 1',
            '-set uio_in 0', '-set-at 1 rst_n 0', '-set-at 2 rst_n 0']
    args.extend(f'-set-at {step} ui_in {pins}' for step, pins in enumerate(inputs, 1))
    args.extend(f'-set-at {step} {signal} {value}' for step, signal, value in checks)
    args.extend(f'-show {signal}' for signal in ('rst_n', 'ui_in', 'uo_out', 'declared_length', 'pc'))
    args.append('-dump_vcd witness.vcd')
    script = f'read_rtlil {json.dumps(str(model))}\nflatten\nformalff -anyinit2ff\nmemory_map\nopt -full\nsat ' + ' '.join(args) + '\n'
    (directory / 'cover.ys').write_text(script)
    subprocess.run(['yosys', '-Q', '-q', '-l', 'yosys.log', 'cover.ys'], cwd=directory, check=True)
    log = (directory / 'yosys.log').read_text()
    if 'SAT solving finished - model found' not in log or not (directory / 'witness.vcd').stat().st_size:
        raise RuntimeError(f'No witness. See {directory / "yosys.log"}')
    receipt = dict(length=length, invalid_begin_ack=bad, begin_ack=begin, run_step=run,
                   last_step=end, last_pc=length - 1,
                   model_sha256=hashlib.sha256(model.read_bytes()).hexdigest())
    (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path, help='SBY bounds_abstract model/design_prep.il')
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    for length in (1, 256):
        find_witness(args.model.resolve(), args.destination.resolve(), length)
