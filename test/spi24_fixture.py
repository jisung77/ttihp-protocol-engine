"""Test-only assembly for three bytes with uninterrupted CS and SCK."""
from model.assembler import assemble


def program(mode):
    idle, phase = mode >> 1, mode & 1
    active = idle ^ 1
    words = [f'DATA {8 | idle}', 'OE 11', 'COMMIT 11', 'TLO 15', 'PULL',
             f'DATA {idle}', 'COMMIT 8', 'COMMIT 0']
    if phase:
        words += [f'DATA {active}', 'DELAY', 'COMMIT 0']

    def bit(first_slot, last_slot):
        # Two simple instructions fit the existing examples' timing padding.
        if phase:
            return ['COMMIT 1', 'SHOUT 1 MSB', f'DATA {idle}', first_slot, 'DELAY',
                    'COMMIT 1', 'SHIN 2 MSB', f'DATA {active}', 'DELAY', last_slot]
        return ['SHOUT 1 MSB', f'DATA {active}', 'DELAY', 'COMMIT 1', 'SHIN 2 MSB',
                f'DATA {idle}', first_slot, 'DELAY', 'COMMIT 1', last_slot]

    for byte in range(3):
        # First bit primes the loop; the middle six repeat. The eighth schedules
        # FIFO service inside the existing bit time, after data is available.
        words += bit('LSET 6', 'COMMIT 0')
        words += [f'middle_{byte}:']
        words += bit('COMMIT 0', f'DJNZ middle_{byte}')
        pull = 'PULL' if byte < 2 else 'COMMIT 0'
        words += bit(pull, 'PUSH') if phase else bit('PUSH', pull)
    words += [f'DATA {8 | idle}']
    if not phase:
        words += ['DELAY', 'COMMIT 0']
    words += ['COMMIT 8', 'HALT']
    return assemble('\n'.join(words))
