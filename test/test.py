"""Independent v0.1 checks at the TT host pins and at the §2 RTL boundary."""
from collections import deque
import json
import os
from pathlib import Path
import random

import cocotb
from cocotb.triggers import Timer
from cocotb_coverage.coverage import CoverPoint, coverage_db

from host_driver import Host
from model.iss import trace_engine


def binary(words):
    return b"".join(w.to_bytes(2, "little") for w in words)


@CoverPoint("engine.opcode", xf=lambda opcode: opcode, bins=list(range(16)))
def observed_opcode(opcode):
    pass


async def compare(dut, host, image, raw, *, initial_pins=0, tx=(), steps=512, retire_limit=None, seed=None):
    """Use stable, identical raw inputs and compare just after each design clock."""
    dut.uio_in.value = initial_pins
    await host.load(image)
    for value in tx:
        await host.byte_tx(value)
    assert raw[0] == initial_pins
    await host.run()
    model = trace_engine(image, raw, pre_run=(initial_pins,) * 3, tx_initial=tx)
    past = deque(maxlen=20)
    count = 0
    last_pc = 0
    for tick in range(steps):
        if tick:
            dut.uio_in.value = raw[min(tick, len(raw)-1)]
            await host.tick()
        frame = next(model)
        edge = frame.edge
        status = int(dut.uo_out.value)
        actual = [int(dut.uio_out.value), int(dut.uio_oe.value),
                  (status >> 6) & 1, (status >> 7) & 1]
        expected = [edge.uio_out, edge.uio_oe, int(edge.running), int(frame.fault)]
        if os.getenv("GATES") != "yes":
            signals = ("pc", "exec_start", "retire", "tx_count", "rx_count", "tx_pop", "rx_push")
            actual += [int(getattr(dut.user_project, name).value) for name in signals]
            expected += [edge.pc, edge.exec_start, edge.retire, frame.tx_count, frame.rx_count,
                         frame.tx_pop, frame.rx_push]
        if actual != expected:
            raise AssertionError(json.dumps(dict(seed=seed, image=image.hex(), tick=tick,
                raw=raw[:tick+1], expected=expected, actual=actual, previous=list(past))))
        past.append((tick, raw[min(tick,len(raw)-1)], expected, actual))
        if edge.exec_start:
            observed_opcode(int.from_bytes(image[2*last_pc:2*last_pc+2], "little") >> 11)
        last_pc = edge.pc
        count += edge.retire
        if not edge.running or (retire_limit is not None and count == retire_limit):
            if retire_limit is not None:
                assert count == retire_limit
            return frame
    raise AssertionError(f"program exceeded {steps} clocks (seed={seed}, retires={count})")


@cocotb.test()
async def first_pin_trace_and_loader_errors(dut):
    h = Host(dut)
    await h.reset()
    assert await h.command(4) == 2
    assert await h.command(0x98) == 3
    assert await h.command(1, bytes((0,0))) == 3
    assert await h.command(1, bytes((1,0))) == 0
    assert await h.command(3, bytes((0,0))) == 3
    assert not (await h.status() & 0x24)
    assert await h.command(1, bytes((1,0))) == 0
    assert await h.command(2, bytes((0,0))) == 0
    assert await h.command(2, bytes((0,0))) == 3
    assert await h.command(4) == 2
    await compare(dut, h, bytes.fromhex("01 08 01 10 01 18 00 00"), [0])
    assert int(dut.uio_out.value) == 1 and int(dut.uio_oe.value) == 0
    assert await h.status() & 4


@cocotb.test()
async def selected_pc_wait_fifo_and_fault(dut):
    h = Host(dut)
    await h.reset()
    # §3 valid and ignored out-of-range branch targets, §4 timed WAIT.
    for rise, succeeds in ((1,False), (2,True), (3,True), (4,False)):
        img = binary([0x2001,0x3010,0x5106,0x5801,0x52ff,0,0])
        # WAIT TIMEOUT path at 4 intentionally faults on selected address FF.
        frame = await compare(dut,h,img,[0] * rise + [1])
        if succeeds:
            assert not frame.fault and frame.edge.pc == 6
        else:
            assert frame.fault and frame.edge.pc == 4
    image = binary([0x7000,0x1002,0x1802,0x4801,0x4002,0x6800,0x78A5,0x4809,0])
    await compare(dut,h,image,[0x04],initial_pins=4,tx=(0x80,))
    assert await h.byte_rx() == 1
    assert await h.command(7) == 1
    # Last word 255 may halt or branch backward, but sequential 256 faults before PUSH.
    for final, expected_fault in ((0,False),(0x5001,False),(0x6800,True)):
        frame = await compare(dut,h,binary([0x50ff]+[0]*254+[final]),[0],steps=10)
        assert frame.fault == expected_fault and frame.rx_count == 0
        assert frame.edge.pc == (1 if final == 0x5001 else 255)


@cocotb.test()
async def fifo_stop_reset_and_reload(dut):
    h = Host(dut)
    await h.reset()
    # PULL, SHOUT pin0 MSB, SHIN pin1 MSB, PUSH, repeat: echo independently sensed bit.
    image = binary([0x7000,0x4800,0x4001,0x6800,0x5000])
    dut.uio_in.value = 2
    await h.load(image)
    for val in (0x80,0,0xff,0x55):
        await h.byte_tx(val)
    assert await h.command(6,b"\x99") == 1
    await h.run()
    for _ in range(60):
        await h.tick()
    assert [await h.byte_rx() for _ in range(4)] == [1,3,7,15]
    assert await h.command(7) == 1
    assert await h.command(2,b"\x00\x00") == 2
    assert await h.command(5) == 0
    assert (await h.status()) & 31 == 5
    assert int(dut.uio_out.value) == 0 and int(dut.uio_oe.value) == 0
    await h.run()
    assert await h.command(5) == 0
    # A pending partial nibble is discarded by deselection. Re-select with REQ=0.
    await h.nibble(0)
    dut.ena.value = 0
    dut.ui_in.value = 0
    await Timer(2, unit="ns")
    assert int(dut.uo_out.value) == 0 and int(dut.uio_oe.value) == 0
    h.req = 0
    dut.ena.value = 1
    for _ in range(3):
        await h.tick()
    assert await h.command(4) == 2
    await h.load(bytes((0,0)))
    await h.run()
    for _ in range(2):
        await h.tick()
    assert await h.status() & 31 == 5


@cocotb.test(skip=os.getenv("GATES") == "yes")
async def seeded_cycle_differential(dut):
    h = Host(dut)
    await h.reset()
    for seed in range(32):
        rng = random.Random(seed)
        # Reachable backward branch repeats a stateful body; all generated branch targets are legal.
        choices = [0x0800,0x1000,0x1800,0x2000,0x2800,0x3000,
                   0x3800,0x4000,0x4800,0x5800,0x7800]
        words = [0x2001]
        for index in range(18):
            op = rng.choice(choices)
            arg = rng.randrange(256)
            if op in (0x2000,0x2800):
                arg = rng.randrange(3) if op == 0x2000 else 0
            elif op == 0x3000:
                arg &= 31
            elif op in (0x4000,0x4800):
                arg &= 15
            elif op == 0x3800:
                arg = 0
            words.append(op | arg)
        words.extend([0x5802,0x6014,0x5001])
        raw = [0] + [rng.randrange(256) for _ in range(512)]
        await compare(dut,h,binary(words),raw,retire_limit=100,steps=512,seed=seed)
        assert await h.command(5) == 0
    out = Path(os.getenv("COVERAGE_FILE", "output/isa-coverage.yml"))
    out.parent.mkdir(exist_ok=True, parents=True)
    coverage_db.export_to_yaml(filename=str(out))


@CoverPoint("control.branch", xf=lambda condition, taken: (condition, taken),
            bins=[(0, True)] + [(c, t) for c in (1, 2, 3) for t in (False, True)])
def observed_branch(condition, taken):
    pass


@CoverPoint("fifo.tx_count", bins=list(range(5)))
def observed_tx_count(value):
    pass


@CoverPoint("fifo.rx_count", bins=list(range(5)))
def observed_rx_count(value):
    pass


@CoverPoint("engine.reset_state", bins=[(0, 0, 0, 0, 0)])
def observed_reset(value):
    pass


@cocotb.test(skip=os.getenv("GATES") == "yes")
async def branch_fifo_reset_coverage(dut):
    h = Host(dut)
    await h.reset()
    core = dut.user_project
    observed_reset((int(core.pc.value), int(core.tx_count.value), int(core.rx_count.value),
                    int(dut.uio_oe.value), int(dut.uo_out.value) >> 5))
    # At word 1, PC 3 is taken and PC 2 is sequential. Setup instructions
    # create NONE/OK/TIMEOUT and LC=0/nonzero without reading private registers.
    for condition, setup, taken in (
        (0,0x5801,True), (1,0x3000,True), (1,0x3008,False),
        (2,0x3008,True), (2,0x3000,False), (3,0x5800,True), (3,0x5801,False)
    ):
        await h.load(binary([setup,0x5003 | (condition << 8),0,0]))
        await h.run()
        for _ in range(4):
            await h.tick()
        pc = int(core.pc.value)
        assert int(core.retire.value) and pc == (3 if taken else 2)
        observed_branch(condition, pc == 3)
        await h.tick()
        await h.tick()
    # All five TX and RX occupancies originate from actual top-level observations.
    await h.load(binary([0x7000,0x6800,0x5000]))
    observed_tx_count(int(core.tx_count.value))
    observed_rx_count(int(core.rx_count.value))
    for val in (0x18,0x22,0xff,0xa5):
        await h.byte_tx(val)
        observed_tx_count(int(core.tx_count.value))
    await h.run()
    for _ in range(25):
        await h.tick()
        observed_rx_count(int(core.rx_count.value))
    for _ in range(4):
        assert await h.byte_rx() == 0
    assert await h.command(5) == 0
    for name in ("control.branch","fifo.tx_count","fifo.rx_count","engine.reset_state"):
        item = coverage_db[name]
        assert item.coverage == item.size, (name, item.detailed_coverage)
    report = Path(os.getenv("COVERAGE_FILE", "output/isa-coverage.yml"))
    report.parent.mkdir(exist_ok=True, parents=True)
    coverage_db.export_to_yaml(filename=str(report))


@cocotb.test(skip=os.getenv("GATES") == "yes")
async def accepted_stop_preempts_same_edge_retire(dut):
    # §6: STOP's low nibble is accepted at K+6. BR 0 would retire on that edge.
    h = Host(dut)
    await h.reset()
    for image in (binary([0x5000]), binary([0x3028, 0])):
        dut.uio_in.value = 0
        await h.load(image)
        await h.run()
        start_ns = cocotb.utils.get_sim_time(unit="ns")

        async def raise_raw_pin():
            for _ in range(3):
                await h.tick()
            # Raw pin sampled at K+4 -> WAIT HIGH sees it at K+6.
            dut.uio_in.value = 1

        pin_task = cocotb.start_soon(raise_raw_pin())
        assert await h.command(5) == 0
        await pin_task
        assert cocotb.utils.get_sim_time(unit="ns") - start_ns == 6 * 20
        assert int(dut.uo_out.value) & 0xe0 == 0x20
        p = dut.user_project
        assert (int(p.pc.value), int(p.exec_start.value), int(p.retire.value)) == (0, 0, 0)
        assert int(dut.uio_out.value) == 0 and int(dut.uio_oe.value) == 0
