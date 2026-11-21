"""Witness each bank at its declared final PC on the original optimized proof model."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def validate_vcd(path, checks):
    """Check actual model values and the full physical port width from the waveform."""
    import re
    widths, names, state, samples = {}, {}, {}, {}
    step = None
    for line in path.read_text().splitlines():
        if line.startswith('$var '):
            _, _, size, ident, name, *_ = line.split()
            name = name.lstrip(chr(92))
            names[ident] = name
            widths[name] = int(size)
        elif line.startswith('#'):
            if step is not None:
                samples[step] = state.copy()
            step = int(line[1:])
        elif line and line[0] in '01xz':
            if line[1:] in names:
                state[names[line[1:]]] = line[0]
        elif line.startswith('b'):
            value, ident = line[1:].split()
            state[names[ident]] = value
    if step is not None:
        samples[step] = state.copy()
    if widths.get('dut.sram_addr') != 8:
        raise ValueError('Physical SRAM address port must have all eight bits')
    for cycle,signal,expected in checks:
        match = re.fullmatch(r'([.a-z_]+)(?:\[(\d+)(?::(\d+))?\])?',signal)
        net, msb, lsb = match.groups()
        actual = int(samples[cycle][net], 2)
        if msb is not None:
            low = int(lsb or msb)
            actual = (actual >> low) & ((1 << (int(msb) - low + 1)) - 1)
        if actual != expected:
            raise ValueError((cycle, signal, actual, expected))


def reach(model, destination, watched_bank, lengths):
    destination.mkdir(parents=True, exist_ok=True)
    # Two reset edges, then sample the 001 mode strap across E1-E3.
    inputs = [32] * 8
    checks = []
    req = 0

    def frame(bank, opcode, word=None):
        nonlocal req
        octets = [opcode] if word is None else [opcode, word & 255, word >> 8]
        for byte in octets:
            for nibble in (byte >> 4, byte & 15):
                data = bank * 64 + nibble
                inputs.append(req | data)
                req ^= 16
                inputs.extend([req | data] * 3)
                ack = len(inputs) + 1
                checks.extend([(ack, 'uo_out[4]', req >> 4), (ack, 'uo_out[3:0]', 0)])
        return ack

    for bank, length in enumerate(lengths):
        accepted = frame(bank, 1, length)
        if bank == watched_bank:
            checks.append((accepted, 'observed_length', length))
        # Symbolic word count/checksum permit this shortened upload to END.
        frame(bank, 3, 0)
    runs = [frame(i, 4) for i in range(2)]
    for edge in runs:
        checks.append((edge, 'uo_out[6]', 1))
    inputs.extend([req | 64] * 12)
    last = len(inputs)
    # Require two consecutive accepted fetches. The alternating slots are fixed
    # by §9.2, so both contexts must fetch while both PCs are simultaneously at limit.
    for step in (last-1, last):
        for bank, signal in enumerate(('pc_a', 'pc_b')):
            checks.append((step, signal, lengths[bank] - 1))
        checks.extend([(step, 'fetch_valid', 1), (step, 'observed_epoch', 1),
                       (step, 'observed_length', lengths[watched_bank])])
        bank = (step - 3) % 2
        checks.extend([(step, 'fetch_ctx', bank),
                       (step, 'dut.sram_addr', bank*128 + lengths[bank] - 1)])
    sat = ['-seq',str(last),'-set-assumes','-set ena 1','-set rst_n 1','-set uio_in 0',
           '-set-at 1 rst_n 0','-set-at 2 rst_n 0']
    sat.extend(f'-set-at {i} ui_in {ui}' for i,ui in enumerate(inputs,1))
    sat.extend(f'-set-at {i} {net} {v}' for i,net,v in checks)
    sat.extend(f'-show {net}' for net in ('rst_n','ui_in','uo_out','pc_a','pc_b',
               'observed_epoch','observed_length','fetch_valid','fetch_ctx','dut.sram_addr'))
    sat.append('-dump_vcd witness.vcd')
    script = (f'read_rtlil {json.dumps(str(model))}\nflatten\nformalff -anyinit2ff\n'
              'memory_map\nopt -full\nsat ' + ' '.join(sat) + '\n')
    (destination/'cover.ys').write_text(script)
    subprocess.run(['yosys','-Q','-q','-l','yosys.log','cover.ys'],cwd=destination,
                   check=True, timeout=300)
    log = (destination/'yosys.log').read_text()
    if 'SAT solving finished - model found' not in log or not (destination/'witness.vcd').is_file():
        raise RuntimeError(f'Unreachable setup; inspect {destination}/yosys.log')
    validate_vcd(destination/'witness.vcd', checks)
    receipt = dict(vcd_checked_values=len(checks),physical_address_width=8,watched_bank=watched_bank, lengths=lengths, run_samples=runs,
                   fetches=[dict(sample=s, bank=(s-3)%2,
                            address=((s-3)%2)*128+lengths[(s-3)%2]-1) for s in (last-1,last)],
                   pc_at_both_fetches=[length-1 for length in lengths],
                   model_sha256=hashlib.sha256(model.read_bytes()).hexdigest())
    (destination/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('model', type=Path)
    p.add_argument('destination', type=Path)
    p.add_argument('bank', type=int, choices=(0,1))
    p.add_argument('length_a', type=int, choices=range(1,129), metavar='LENGTH_A')
    p.add_argument('length_b', type=int, choices=range(1,129), metavar='LENGTH_B')
    args = p.parse_args()
    reach(args.model.resolve(), args.destination.resolve(),args.bank,[args.length_a,args.length_b])
