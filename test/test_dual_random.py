"""Seeded, legal §3/§9 program pairs, replayed through the physical top-level pins."""
import random

import cocotb

from model.test_v02_pins import Pins
from test_dual import RecordingMachine, replay


SEEDS = (0x219, 0x2027, 0x5052, 0x5EED)
(HALT, DATA, OE, COMMIT, TLO, THI, WAIT, DELAY, SHIN, SHOUT, BR, LSET,
 DJNZ, PUSH, PULL, SRIMM) = range(16)


def word(op, arg=0):
    return (op << 11) | arg


def program(rng, cid):
    """Randomize operands, block order and safe forward skips around a bounded loop."""
    owned = 0x0f if cid else 0xf0
    pin = rng.randrange(4) + (0 if cid else 4)
    words = [word(DATA, rng.randrange(256)), word(OE, owned),
             word(COMMIT, owned), word(PULL), word(THI), word(TLO, rng.randrange(1, 6))]
    def shift():
        return rng.randrange(8) | (rng.randrange(2) << 3)

    def optional_data(condition):
        # Both PCs are valid, distinct and inside this image.
        target = len(words) + 2
        words.extend([word(BR, condition << 8 | target), word(DATA, rng.randrange(256))])

    # Finite HIGH and LOW on tied-high pad 7 guarantee both BR OK outcomes.
    levels = [0, 1]
    rng.shuffle(levels)
    for level in levels:
        words.append(word(WAIT, level << 3 | 7))
        for condition in rng.sample([1, 2], 2):
            optional_data(condition)

    words.append(word(LSET, rng.randrange(2, 5)))
    optional_data(3)  # LC nonzero
    loop = len(words)
    blocks = [[word(SRIMM, rng.randrange(256)), word(SHOUT, pin | (rng.randrange(2) << 3))],
              [word(SHIN, shift()), word(DELAY)],
              [word(WAIT, rng.randrange(4) << 3 | rng.randrange(7))],
              [word(OE, rng.randrange(256)), word(COMMIT, (rng.randrange(1, 16)
                    << (0 if cid else 4)))]]
    rng.shuffle(blocks)
    for block in blocks:
        words.extend(block)
    words.append(word(DJNZ, loop))
    optional_data(3)  # LC zero
    optional_data(0)  # unconditional
    for _ in range(5):  # Fifth PUSH must wait for a real host RX transaction.
        words.extend([word(SHIN, shift()), word(PUSH)])
    words.extend([word(PULL), word(SHOUT, pin), word(HALT)])
    assert len(words) <= 128
    return words


def read_rx(p, cid):
    assert p.command(cid, 7) == 0
    return (p.nibble(0, 1 - cid) << 4) | p.nibble(0, 1 - cid)


def check_coverage(p, words, observed):
    """Reconstruct ISR from external E-2 pin values and independently check RX order."""
    metrics = []
    for cid, image in enumerate(words):
        ops, branches, pushes = set(), set(), []
        isr = pop_count = stalls = 0
        for edge, row in p.rows.items():
            if edge == 1:
                continue
            previous = p.rows[edge - 1]
            pc = previous["pc"][cid]
            opcode, arg = divmod(image[pc], 1 << 11)
            if opcode == PULL and previous["tx_count"][cid] == 0 and row["pc"][cid] == pc:
                if edge % 2 != cid:  # potential own execute slot
                    stalls += 1
            if not row["retire"][cid]:
                continue
            ops.add(opcode)
            if opcode in (BR, DJNZ):
                cond = arg >> 8 if opcode == BR else None
                branches.add((opcode, cond, row["pc"][cid] != pc + 1))
            if opcode == SHIN:
                bit = (p.inputs[edge - 2] >> (arg & 7)) & 1
                isr = (isr >> 1 | bit << 7) if arg & 8 else ((isr << 1 | bit) & 255)
            if opcode == PUSH:
                assert row["rx_push"][cid]
                pushes.append(isr)
            if opcode == PULL:
                assert row["tx_pop"][cid]
                pop_count += 1
        assert ops == set(range(16)), (cid, ops)
        wanted = {(BR, 0, True), (DJNZ, None, True), (DJNZ, None, False)}
        wanted.update((BR, c, taken) for c in (1, 2, 3) for taken in (True, False))
        assert branches == wanted
        assert stalls > 1 and pop_count == 2
        assert pushes == observed[cid] and len(pushes) == 5, (cid, pushes, observed[cid])
        metrics.append(dict(opcodes=len(ops), branch_bins=len(branches),
                            rx=pushes, tx_pops=pop_count, empty_pull_slots=stalls))
    return metrics


def scenario(reference, seed):
    rng = random.Random(seed)
    p = Pins(tick=reference.tick)
    words = [program(rng, cid) for cid in (0, 1)]
    for cid in (0, 1):
        p.load(cid, words[cid])
    # Hold each independent pin pattern for >=3 clocks. Pin 7 is always HIGH.
    cursor = p.edge + 1
    while cursor < p.edge + 1500:
        value = rng.randrange(128) | 0x80
        span = rng.randint(3, 9)
        for edge in range(cursor, cursor + span):
            p.inputs[edge] = value
        cursor += span

    p.run(0, seed & 1)
    p.run(1, 1 - (seed & 1))
    p.until(p.edge + 24)
    assert p.rows[p.edge]["pc"] == (3, 3)
    assert p.rows[p.edge]["uio_oe"] == 255
    assert p.command(1, 6, [rng.randrange(256)]) == 0
    before_stop = p.edge
    assert p.command(0, 5) == 0
    assert p.rows[p.edge]["uio_oe"] & 0xf0 == 0
    assert any(p.rows[e]["retire"][1] for e in range(before_stop + 1, p.edge + 1))
    p.run(0, rng.randrange(2))
    assert p.command(0, 6, [rng.randrange(256)]) == 0
    p.until(p.edge + 280)
    for cid, image in enumerate(words):
        row = p.rows[p.edge]
        assert row["rx_count"][cid] == 4
        assert row["pc"][cid] == len(image) - 4  # waiting on fifth PUSH
    received = [[], []]
    order = rng.sample([0, 1], 2)
    for _ in range(5):
        for cid in order:
            received[cid].append(read_rx(p, cid))
    for cid in order:
        assert p.command(cid, 7) == 1
        assert p.status(cid) == 0x0d
        assert p.command(cid, 6, [rng.randrange(256)]) == 0
    p.until(p.edge + 12)
    for cid in (0, 1):
        assert p.status(cid) == 0x05
    assert p.rows[p.edge]["uio_oe"] == 0
    return dict(seed=seed, cycles=p.edge, contexts=check_coverage(p, words, received),
                words=[[f"{w:04x}" for w in image] for image in words])


@cocotb.test()
async def seeded_valid_dual_programs(dut):
    for seed in SEEDS:
        reference = RecordingMachine()
        report = scenario(reference, seed)
        dut._log.info("dual random seed=%#x cycles=%d contexts=%s",
                      seed, report["cycles"], report["contexts"])
        try:
            await replay(dut, reference.cycles)
        except AssertionError as error:
            raise AssertionError(f"seed={seed:#x} {error}") from error
