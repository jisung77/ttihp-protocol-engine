"""Architectural execution state for §§2–5 and §9.

Each step consumes the pre-edge pin sample and reports post-edge state and
pulses. In dual mode a context can observe WAIT on a sibling slot, but only
its own execute slot can change PC, the visible result, pins or a FIFO.
"""

from collections import deque

A, B = 0, 1
NONE, OK, TIMEOUT = "NONE", "OK", "TIMEOUT"

(HALT, DATA, OE, COMMIT, TLO, THI, WAIT, DELAY,
 SHIN, SHOUT, BR, LSET, DJNZ, PUSH, PULL, SRIMM) = range(16)

# Largest legal 11-bit argument per opcode; any higher bit set is a fault.
ARG_LIMIT = (0x000, 0x0FF, 0x0FF, 0x0FF, 0x0FF, 0x0FF, 0x03F, 0x000,
             0x00F, 0x00F, 0x3FF, 0x0FF, 0x0FF, 0x000, 0x000, 0x0FF)

DUAL_OWNED_PINS = (0xF0, 0x0F)  # A owns uio[7:4], B owns uio[3:0]
DUAL_BASE = (0, 128)            # A owns words 0-127, B owns 128-255

# Pipeline phases of one context.
IDLE = "IDLE"    # not running
FETCH = "FETCH"  # needs an SRAM read at its next usable fetch edge
START = "START"  # word was read on the previous edge; this edge is S
BUSY = "BUSY"    # multi-cycle WAIT / DELAY / PUSH / PULL after S

DONE = "DONE"    # DELAY decision marker


def _wait_matches(arg, sample, previous):
    """WAIT condition on the executor's view: [2:0] pin, [4:3] mode."""
    mask = 1 << (arg & 7)
    mode = (arg >> 3) & 3
    high = bool(sample & mask)
    if mode == 0:
        return not high
    if mode == 1:
        return high
    if previous is None:  # history not valid yet: no events
        return False
    was_high = bool(previous & mask)
    if mode == 2:
        return high and not was_high
    return was_high and not high


class Context:
    """Architectural and pipeline state of one execution context."""

    def __init__(self):
        self.tx = deque()
        self.rx = deque()
        self.reset()

    def clear_run_state(self):
        """Registers cleared by RUN / STOP (FIFOs, flags handled by caller)."""
        self.pc = 0
        self.out = 0            # active uio data (8 bits)
        self.oe = 0             # active uio OE (8 bits)
        self.prep_data = 0
        self.prep_oe = 0
        self.osr = 0
        self.isr = 0
        self.t = 0
        self.lc = 0
        self.wait_result = NONE
        self.cancel_pipeline()

    def cancel_pipeline(self):
        self.phase = IDLE
        self.ir = 0             # fetched instruction word
        self.op = 0
        self.arg = 0
        self.next_pc = 0        # next address selected at S
        self.remaining = 0      # WAIT/DELAY countdown, loaded from T at S
        self.decision = None    # first WAIT/DELAY decision (OK/TIMEOUT/DONE)

    def reset(self):
        self.ready = False
        self.running = False
        self.fault = False
        self.loading = False
        self.length = 0
        self.tx.clear()
        self.rx.clear()
        self.clear_run_state()


class Engine:
    def __init__(self):
        self.mem = [None] * 256
        self.ctx = [Context(), Context()]
        self.reset()

    # ------------------------------------------------------------ host hooks
    def reset(self):
        for c in self.ctx:
            c.reset()
        self.inflight = None    # (cid, word) read on the previous edge
        self.instruction = 0
        self.instr_ctx = 0

    def _cancel_inflight(self, cid):
        if self.inflight is not None and self.inflight[0] == cid:
            self.inflight = None

    def host_run(self, cid):
        c = self.ctx[cid]
        c.clear_run_state()
        c.running = True
        c.phase = FETCH         # first fetch strictly after K
        self._cancel_inflight(cid)

    def host_stop(self, cid):
        c = self.ctx[cid]
        c.clear_run_state()
        c.running = False
        c.loading = False
        c.tx.clear()
        c.rx.clear()
        self._cancel_inflight(cid)

    def host_begin(self, cid, length):
        c = self.ctx[cid]
        c.ready = False
        c.fault = False
        c.tx.clear()
        c.rx.clear()
        c.loading = True
        c.length = length

    # ------------------------------------------------------------ one edge
    def step(self, edge, dual, pins_sample, pins_previous, *,
             started=None, stopped=None, tx_sizes=None, rx_sizes=None):
        sample = (pins_sample or 0) & 0xFF
        previous = None if pins_previous is None else pins_previous & 0xFF
        n_ctx = 2 if dual else 1
        rep = {k: [0, 0] for k in ("exec_start", "retire", "tx_pop", "rx_push")}
        fetch_valid = fetch_ctx = sram_addr = 0
        instr_valid = 0

        # SRAM response to the read issued on the previous edge.
        if self.inflight is not None:
            cid, word = self.inflight
            self.inflight = None
            self.instruction = word
            self.instr_ctx = cid
            instr_valid = 0 if cid == stopped else 1

        for cid in range(n_ctx):
            c = self.ctx[cid]
            # STOP wins over everything; a started ctx was idle before K.
            if cid == stopped or cid == started or not c.running:
                continue
            fetch_slot = (not dual) or edge % 2 == cid
            exec_slot = (not dual) or edge % 2 != cid

            if c.phase == FETCH:
                if fetch_slot:
                    addr = (DUAL_BASE[cid] if dual else 0) + c.pc
                    word = self.mem[addr]
                    assert word is not None, "fetch outside a completely loaded image"
                    c.ir = word
                    c.phase = START
                    self.inflight = (cid, word)
                    fetch_valid, fetch_ctx, sram_addr = 1, cid, addr
                continue

            tx_size = len(c.tx) if tx_sizes is None else tx_sizes[cid]
            rx_size = len(c.rx) if rx_sizes is None else rx_sizes[cid]
            self._execute(c, cid, dual, exec_slot, sample, previous,
                          tx_size, rx_size, rep)

        def pair(get):
            return tuple(get(self.ctx[i]) if i < n_ctx else 0 for i in range(2))

        a, b = self.ctx
        if dual:
            uio_out = (a.out & 0xF0) | (b.out & 0x0F)
            uio_oe = (a.oe & 0xF0) | (b.oe & 0x0F)
        else:
            uio_out, uio_oe = a.out & 0xFF, a.oe & 0xFF

        return {
            "exec_start": tuple(rep["exec_start"]),
            "retire": tuple(rep["retire"]),
            "pc": pair(lambda c: c.pc),
            "tx_pop": tuple(rep["tx_pop"]),
            "rx_push": tuple(rep["rx_push"]),
            "tx_count": pair(lambda c: len(c.tx)),
            "rx_count": pair(lambda c: len(c.rx)),
            "fetch_valid": fetch_valid,
            "fetch_ctx": fetch_ctx,
            "sram_addr": sram_addr,
            "instruction": self.instruction,
            "instr_ctx": self.instr_ctx,
            "instr_valid": instr_valid,
            "uio_out": uio_out,
            "uio_oe": uio_oe,
        }

    # ------------------------------------------------------------ execution
    def _execute(self, c, cid, dual, exec_slot, sample, previous,
                 tx_size, rx_size, rep):
        if c.phase == START:          # S: always an own exec slot
            rep["exec_start"][cid] = 1
            if not self._decode_at_s(c, cid, dual):
                self._fault(c)
                return
            if c.op == HALT:          # retires, PC / data / FIFOs kept
                c.running = False
                c.oe = 0
                c.cancel_pipeline()
                rep["retire"][cid] = 1
                return
            c.phase = BUSY

        op, arg = c.op, c.arg
        if op in (WAIT, DELAY):
            if c.decision is None:    # observe every edge until decided
                c.decision = self._decide(c, sample, previous)
            if c.decision is None or not exec_slot:
                return
            if op == WAIT:
                c.wait_result = c.decision
        elif op == PUSH:
            if not (exec_slot and rx_size < 4):
                return
            c.rx.append(c.isr)
            rep["rx_push"][cid] = 1
        elif op == PULL:
            if not (exec_slot and tx_size > 0):
                return
            c.osr = c.tx.popleft()
            rep["tx_pop"][cid] = 1
        else:
            self._simple(c, op, arg, sample)

        c.pc = c.next_pc
        c.cancel_pipeline()
        c.phase = FETCH               # next F strictly after R
        rep["retire"][cid] = 1

    @staticmethod
    def _fault(c):
        """Spec section 6: keep PC and registers, FAULT=1, RUNNING=0,
        active OE=0 (data kept); the instruction does not retire."""
        c.fault = True
        c.running = False
        c.oe = 0
        c.cancel_pipeline()

    def _decode_at_s(self, c, cid, dual):
        """Validate encoding, pin ownership and the selected next address."""
        op, arg = c.ir >> 11, c.ir & 0x7FF
        c.op, c.arg = op, arg
        if op > 0x0F or arg > ARG_LIMIT[op]:
            return False
        if dual:
            owned = DUAL_OWNED_PINS[cid]
            if op == COMMIT and arg & ~owned & 0xFF:
                return False
            if op == SHOUT and (1 << (arg & 7)) & ~owned:
                return False
        if op == HALT:                # exempt from the next-address check
            c.next_pc = c.pc
            return True
        nxt = c.pc + 1                # may reach length; never truncated
        if op == BR:
            taken = (True, c.wait_result == OK,
                     c.wait_result == TIMEOUT, c.lc == 0)[arg >> 8]
            if taken:
                nxt = arg & 0xFF
        elif op == DJNZ and c.lc > 1:  # new LC = LC - 1 > 0
            nxt = arg & 0xFF
        if nxt >= c.length:
            return False
        c.next_pc = nxt
        c.remaining = c.t             # T just before S
        c.decision = None
        return True

    @staticmethod
    def _decide(c, sample, previous):
        """One edge of WAIT/DELAY observation; returns a decision or None."""
        if c.op == WAIT:
            if _wait_matches(c.arg, sample, previous):
                return OK             # success beats timeout at N
            if c.arg & 0x20:          # infinite: only STOP/reset leave
                return None
        if c.remaining == 0:          # this edge is N = S + T (or S + D)
            return TIMEOUT if c.op == WAIT else DONE
        c.remaining -= 1
        return None

    @staticmethod
    def _simple(c, op, arg, sample):
        """Single-edge instructions (R = S)."""
        if op == DATA:
            c.prep_data = arg
        elif op == OE:
            c.prep_oe = arg
        elif op == COMMIT:
            c.out = (c.out & ~arg | c.prep_data & arg) & 0xFF
            c.oe = (c.oe & ~arg | c.prep_oe & arg) & 0xFF
        elif op == TLO:
            c.t = (c.t & 0xFF00) | arg
        elif op == THI:
            c.t = (c.t & 0x00FF) | (arg << 8)
        elif op == SHIN:
            bit = (sample >> (arg & 7)) & 1
            if arg & 8:               # LSB first: shift right, insert bit7
                c.isr = (c.isr >> 1) | (bit << 7)
            else:                     # MSB first: shift left, insert bit0
                c.isr = ((c.isr << 1) | bit) & 0xFF
        elif op == SHOUT:
            mask = 1 << (arg & 7)
            if arg & 8:
                bit, c.osr = c.osr & 1, c.osr >> 1
            else:
                bit, c.osr = c.osr >> 7, (c.osr << 1) & 0xFF
            value = mask if bit else 0
            c.out = (c.out & ~mask & 0xFF) | value
            c.prep_data = (c.prep_data & ~mask & 0xFF) | value
        elif op == LSET:
            c.lc = arg
        elif op == DJNZ:
            if c.lc > 0:
                c.lc -= 1
        elif op == SRIMM:
            c.osr = arg
        # BR: only the next address, already selected at S.
