"""Production RP2350 loader against the HDL top `tb`, external pins only (spec §1/§2/§5/§6/§7).

Gap closed: model/test_board_loader.py checks host/rp2350_loader.py only against a scripted
transcript; test.py/test_protocols.py use the async host_driver.Host. Here the UNMODIFIED
synchronous `rp2350_loader.Loader` runs in a cocotb bridge thread against a ttboard-like
shim; its module `time` is swapped for a sim-time shim (restored in `finally`). The thread
reaches the simulator only through @resume coroutines. No internal signal is read (GLS-eligible).
Anti-vacuity: (1) strict REQ/ACK pin oracle - every REQ toggle must be ACKed with the same
phase after > 1 clock before the next REQ toggle, the nibble must not change with the REQ
toggle or while pending, REQ=ACK=0 after reset, completed handshakes must equal the
hand-counted nibble totals (82 / 139), no X/Z while armed; (2) the §7 table is checked edge by
edge at K..K+8 from the on-wire RUN-accept edge K, with a COMMIT-mask-0 image as negative
control; (3) monitor crashes are re-raised via task.result(), violations are listed.
Limits: uo_out is polled every 250 ns of sim time; Pico GPIO skew and MicroPython speed are
not modelled (the thread runs 100 ns off the clock grid so pin writes never meet an edge);
the 100 ms ACK-timeout path is not exercised; §7 timing is anchored to the on-wire accept edge.
"""
from types import SimpleNamespace

import cocotb
from cocotb.clock import Clock
from cocotb.task import bridge, resume
from cocotb.triggers import First, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from host import rp2350_loader

IMAGE = bytes.fromhex("01 08 01 10 01 18 00 00")        # §7: DATA 1, OE 1, COMMIT 1, HALT
MASK0_IMAGE = bytes.fromhex("01 08 01 10 00 18 00 00")  # §3 COMMIT mask 0: pin 0 never driven
POLL_NS, PHASE_NS = 250, 100  # sim time per uo_out read; thread offset from the clock grid
TX_ROOM, RX_VALID, READY, RUNNING, FAULT, LOADING = (1 << i for i in range(6))  # §6 STATUS
# Edges K..K+8 as (uio_out[0], uio_oe[0], RUNNING=uo_out[6], FAULT=uo_out[7]). uio columns:
# §7 table. RUNNING: §6 RUN sets it at K; HALT (§3 simple, R=S=K+8) clears it at K+8, the
# same edge where §7 drops uio_oe[0] (§6 normal HALT). FAULT: no fault anywhere.
RUN_COL, ZERO = (1,) * 8 + (0,), (0,) * 9
SECTION7 = list(zip((0, 0, 0, 0, 0, 0, 1, 1, 1), (0, 0, 0, 0, 0, 0, 1, 1, 0), RUN_COL, ZERO))
MASK0 = list(zip(ZERO, ZERO, RUN_COL, ZERO))
# Hand-counted REQ toggles (= nibbles, §6) while rst_n=1.
# load()=40 (BEGIN 6, WRITE 4x6, END 6, STATUS 4); STATUS 4; TX 4; RX/RUN/STOP 2.
NIBBLES_A = 40 + 4 + 5 * 4 + 2 + 4 + 2 + 4 + 2 + 4                     # 82
NIBBLES_B = 40 + 1 + 4 + 2 + 4 + (40 + 2) + (40 + 2 + 4)                # 139


def _bits(handle):
    """Integer pin value, or None if any bit is X/Z (int() raises ValueError in cocotb 2.0)."""
    try:
        return int(handle.value)
    except ValueError:
        return None


@resume  # ---- the only paths from the Loader thread into the simulator ----
async def _drive(handle, value):
    handle.value = value


@resume
async def _sample_after(handle, delay_ns):
    await Timer(delay_ns, unit="ns")
    return int(handle.value)


@resume
async def _wait_us(us):
    await Timer(us, unit="us")


@resume
async def _now_ms():
    return int(get_sim_time(unit="ms"))


SIM_TIME = SimpleNamespace(sleep_us=_wait_us, ticks_ms=_now_ms, ticks_diff=lambda a, b: a - b)


class _Pin:
    def __init__(self, read=None, write=None):
        self._read, self._write = read, write
    value = property(lambda self: self._read(), lambda self, v: self._write(v))


class SimBoard:
    """Subset of the ttboard DemoBoard API used by Loader; handles resolved in cocotb context."""
    def __init__(self, dut):
        self.polls = 0
        ui, uo, reset = dut.ui_in, dut.uo_out, dut.rst_n
        self.ui_in = _Pin(write=lambda v: _drive(ui, int(v) & 0xFF))
        self.uo_out = _Pin(read=lambda: self._poll(uo))
        self.uio_oe_pico = _Pin(write=self._pico_oe)
        self.reset_project = lambda active: _drive(reset, 0 if active else 1)

    def _poll(self, handle):
        self.polls += 1
        return _sample_after(handle, POLL_NS)

    @staticmethod
    def _pico_oe(v):
        assert int(v) == 0, "Pico must never drive uio (design owns uio_oe)"


def _stop(task):
    """Re-raise a crashed monitor instead of silently cancelling it."""
    if task.done():
        task.result()
    else:
        task.cancel()


async def _handshake(dut, period_ns, st):
    """Strict REQ(ui_in[4])/ACK(uo_out[4]) oracle. Disarmed until a rst_n 0->1 edge and whenever
    rst_n != 1 (pending REQ discarded); baseline taken at the first resolvable event after."""
    rst_prev, armed, want = _bits(dut.rst_n), False, False
    while True:
        await First(dut.ui_in.value_change, dut.uo_out.value_change, dut.rst_n.value_change)
        await ReadOnly()
        rst, ui, uo = _bits(dut.rst_n), _bits(dut.ui_in), _bits(dut.uo_out)
        want, rst_prev = want or (rst == 1 and rst_prev == 0), rst
        now = get_sim_time(unit="ns")
        if rst != 1:
            if armed and st.pend is not None:        # Loader must not abandon an un-ACKed REQ
                st.violations.append(f"{now} ns: reset with REQ {st.pend} still un-ACKed")
            armed, want, st.pend = False, False, None
            continue
        if not armed:
            if want and ui is not None and uo is not None:
                req, ack, data, armed, want = (ui >> 4) & 1, (uo >> 4) & 1, ui & 15, True, False
                if req or ack:                       # §6: REQ=0 held through release, ACK cleared
                    st.violations.append(f"{now} ns: armed with REQ={req} ACK={ack}")
            continue
        if ui is None or uo is None:
            st.violations.append(f"{now} ns: X/Z ui_in={dut.ui_in.value} uo_out={dut.uo_out.value}")
            continue
        r, a = (ui >> 4) & 1, (uo >> 4) & 1
        if ui >> 5:                                  # §1: host drives ui_in[7:5]=0
            st.violations.append(f"{now} ns: ui_in[7:5]={ui >> 5}")
        if (ui & 15) != data and (r != req or st.pend is not None):  # §6 nibble stable
            st.violations.append(f"{now} ns: nibble {data:x}->{ui & 15:x} with REQ toggle/pending")
        data = ui & 15
        if r != req:                                 # REQ handled before ACK in one event
            if st.pend is not None:
                st.violations.append(f"{now} ns: REQ->{r} while {st.pend} still un-ACKed")
            req, st.pend = r, (r, now)
        if a != ack:
            ack, pend, st.pend = a, st.pend, None
            if pend is None:
                st.violations.append(f"{now} ns: ACK->{a} with no pending REQ")
            elif a != pend[0]:
                st.violations.append(f"{now} ns: ACK->{a} != pending REQ phase {pend[0]}")
            elif now - pend[1] <= period_ns:        # §2/§6: REQ crosses a 2FF synchronizer
                st.violations.append(f"{now} ns: ACK latency {now - pend[1]} ns <= 1 clock")
            else:
                st.done += 1


async def _sample_edges(dut, out):
    """§2: record the settled state after every rising edge."""
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        uo, uio, oe = _bits(dut.uo_out), _bits(dut.uio_out), _bits(dut.uio_oe)
        out.append(None if None in (uo, uio, oe) else
                   ((uo >> 4) & 1, uio & 1, oe & 1, (uo >> 6) & 1, (uo >> 7) & 1))


@bridge  # ---- blocking scenarios executed in the bridge thread ----
def call(fn, *args):
    return fn(*args)


@bridge
def load_fill_and_stop(host):
    host.reset()                                     # raises unless uo_out == 0 after reset
    host.load(IMAGE)
    assert host.status() == TX_ROOM | READY          # §6 END -> READY; §5 empty TX FIFO
    sent = []
    while len(sent) < 6 and (not sent or sent[-1]):
        sent.append(host.try_send(0xA0 + len(sent)))
    assert sent == [True] * 4 + [False], sent        # §5 depth 4; §6 code 1 = FIFO retry
    assert host.try_receive() is None                # §6 RX on empty RX FIFO -> code 1
    assert host.status() == READY                    # TX full -> no TX room, RX still empty
    host.stop()                                      # §6 STOP clears both FIFOs, keeps READY
    assert host.status() == TX_ROOM | READY


@bridge
def abandon_frame_and_recover(host):
    host.reset()
    host.load(IMAGE)
    assert host._nibble(0) == 0                      # §6 intermediate nibble reply is 0
    host.reset()                                     # §6 abandoned frame recovered by reset
    assert host.status() == TX_ROOM                  # §6 reset clears READY and FIFOs
    try:
        host.run()
    except RuntimeError as exc:
        assert "returned 2" in str(exc), exc         # §6 RUN needs READY -> code 2
    else:
        raise AssertionError("RUN accepted without an image")
    assert host.synced and host.status() == TX_ROOM  # code 2 keeps the frame in sync


async def run_and_check(dut, host, period_ns, expected, pin0, final_status=True):
    ack0, samples = (int(dut.uo_out.value) >> 4) & 1, []
    sampler = cocotb.start_soon(_sample_edges(dut, samples))
    await call(host.run)
    await Timer(16 * period_ns, unit="ns")           # run() returns < 1 clock after K
    _stop(sampler)
    assert None not in samples, f"X/Z after an edge: {samples}"
    prev = [ack0] + [s[0] for s in samples]
    flips = [i for i, s in enumerate(samples) if s[0] != prev[i]]
    # RUN = 2 nibbles -> exactly 2 ACK toggles; K is the edge completing the 2nd (§6 accept).
    assert len(flips) == 2 and len(samples) >= flips[1] + 9, f"flips={flips} samples={samples}"
    got = [s[1:] for s in samples[flips[1]:flips[1] + 9]]
    assert got == expected, f"edges K..K+8 got {got} expected {expected}"   # §7 / §6
    assert int(dut.uio_out.value) & 1 == pin0 and int(dut.uio_oe.value) & 1 == 0
    assert int(dut.uo_out.value) >> 5 == 0b001       # §1 FAULT=0, RUNNING=0, READY=1
    if final_status:
        assert await call(host.status) == TX_ROOM | READY  # §6 HALT keeps image, empty FIFOs


async def _harness(dut, period_ns, nibbles, body):
    """Same pin defaults as host_driver.Host.reset; the clock runs before Loader.reset()."""
    for name, v in (("clk", 0), ("ena", 1), ("rst_n", 1), ("ui_in", 0), ("uio_in", 0),
                    ("uart_rx", 1), ("spi_miso", 0), ("serial_inputs", 0)):
        getattr(dut, name).value = v
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start(start_high=False))
    await Timer(PHASE_NS, unit="ns")
    st = SimpleNamespace(done=0, pend=None, violations=[])
    board, saved = SimBoard(dut), rp2350_loader.time
    rp2350_loader.time = SIM_TIME
    try:
        hs = cocotb.start_soon(_handshake(dut, period_ns, st))
        await body(rp2350_loader.Loader(board))
        _stop(hs)
    finally:
        rp2350_loader.time = saved
        dut._log.info("uo_out polls %d, handshakes %d, pending %s, violations %s",
                      board.polls, st.done, st.pend, st.violations)
    assert st.violations == [], "handshake violations:\n" + "\n".join(st.violations)
    assert st.pend is None and st.done == nibbles, (st.done, nibbles, st.pend)


@cocotb.test(timeout_time=50, timeout_unit="ms")
@cocotb.parametrize(period_ns=[1000, 10000])  # README: 1 MHz first load, 100 kHz minimum
async def production_loader_section7_on_hdl(dut, period_ns):
    async def body(host):
        await load_fill_and_stop(host)
        await run_and_check(dut, host, period_ns, SECTION7, 1)
    await _harness(dut, period_ns, NIBBLES_A, body)


@cocotb.test(timeout_time=50, timeout_unit="ms")
@cocotb.parametrize(period_ns=[1000, 10000])
async def production_loader_recovers_interrupted_frame(dut, period_ns):
    async def body(host):
        await abandon_frame_and_recover(host)
        await call(host.load, MASK0_IMAGE)           # negative control: pin 0 never driven
        await run_and_check(dut, host, period_ns, MASK0, 0, final_status=False)
        await call(host.load, IMAGE)
        await run_and_check(dut, host, period_ns, SECTION7, 1)
    await _harness(dut, period_ns, NIBBLES_B, body)
