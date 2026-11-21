"""Cycle-exact TT host pins, reset and straps (§§2, 6, 9).

Pass the stable inputs for a rising edge to ProtocolMachine.tick(). The
result contains the pins and architectural observations just after that edge.
FIFO transfer permissions and STATUS snapshots use the pre-edge state.
"""

from model.dual_iss import Engine

A, B = 0, 1

CMD_STATUS, CMD_BEGIN, CMD_WRITE, CMD_END = 0x00, 0x01, 0x02, 0x03
CMD_RUN, CMD_STOP, CMD_TX, CMD_RX = 0x04, 0x05, 0x06, 0x07

# Payload length in nibbles for each defined command.
PAYLOAD_NIBBLES = {
    CMD_STATUS: 0, CMD_BEGIN: 4, CMD_WRITE: 4, CMD_END: 4,
    CMD_RUN: 0, CMD_STOP: 0, CMD_TX: 2, CMD_RX: 0,
}

PER_CTX_KEYS = ("exec_start", "retire", "pc", "tx_pop", "rx_push",
                "tx_count", "rx_count")
SCALAR_KEYS = ("fetch_valid", "fetch_ctx", "sram_addr", "instruction",
               "instr_ctx", "instr_valid", "uio_out", "uio_oe")

# Frame phases
PH_CMD0, PH_CMD1, PH_PAY, PH_DUM0, PH_DUM1 = range(5)


def _zero_trace():
    t = {k: (0, 0) for k in PER_CTX_KEYS}
    t.update({k: 0 for k in SCALAR_KEYS})
    return t


class ProtocolMachine:
    def __init__(self):
        self.engine = Engine()
        self._reset_state()

    # ------------------------------------------------------------------ reset
    def _reset_state(self):
        self.engine.reset()
        self.edge = 0
        self.dual = 0
        # synchronizers: [stage1, stage2]
        self.req_s1 = self.req_s2 = 0
        self.uio_s1 = self.uio_s2 = 0
        self.mode_s1 = self.mode_s2 = 0
        self.history = 0
        self.history_valid = False
        # loader frame / host response
        self.ack = 0
        self.resp = 0
        self.L = A
        self.phase = PH_CMD0
        self.cmd_hi = 0
        self.cmd = 0
        self.payload = []
        self.mismatch = False
        self.held = 0
        self.session = None  # dict(owner, length, count, sum)

    # ------------------------------------------------------------ utilities
    def _uo_out(self):
        c = self.engine.ctx[self.L]
        return ((self.resp & 0xF) | ((self.ack & 1) << 4) | (int(bool(c.ready)) << 5)
                | (int(bool(c.running)) << 6) | (int(bool(c.fault)) << 7))

    def _clock_syncs(self, ui_in, uio_in):
        self.req_s2, self.req_s1 = self.req_s1, (ui_in >> 4) & 1
        self.uio_s2, self.uio_s1 = self.uio_s1, uio_in & 0xFF
        self.mode_s2, self.mode_s1 = self.mode_s1, (ui_in >> 5) & 0x7

    # ---------------------------------------------------------------- tick
    def tick(self, ui_in=0, uio_in=0, rst_n=1, ena=1):
        ui_in &= 0xFF
        uio_in &= 0xFF
        trace = _zero_trace()

        # Reset / deselect: top priority.
        if not (rst_n and ena):
            self._reset_state()
            trace.update(edge=0, dual_mode=0, uo_out=0, sram_we=0)
            return trace

        self.edge += 1
        E = self.edge

        # Pre-edge synchronized values (stage 2) and history.
        req_sync = self.req_s2
        sample = self.uio_s2
        previous = self.history if self.history_valid else None
        mode_sync = self.mode_s2

        if E < 3:
            # Internal reset release: synchronizers advance, nothing else.
            self.history = self.uio_s2
            self._clock_syncs(ui_in, uio_in)
            trace.update(edge=E, dual_mode=0, uo_out=self._uo_out(), sram_we=0)
            return trace

        if E == 3:
            self.dual = 1 if mode_sync == 0b001 else 0

        eng = self.engine
        ctx = eng.ctx
        tx_sizes = (len(ctx[A].tx), len(ctx[B].tx))
        rx_sizes = (len(ctx[A].rx), len(ctx[B].rx))

        # ---- loader decision (pre-edge state only) ----
        plan = None
        if req_sync != self.ack:
            plan = self._accept_nibble(ui_in, tx_sizes, rx_sizes)
            self.ack = req_sync

        # Determine both commands from pre-edge state. Their local effects must
        # be visible in the very same post-edge sample as the ACK (§§6, 9.2).
        started = plan["cid"] if plan and plan["act"] == "run" else None
        stopped = plan["cid"] if plan and plan["act"] == "stop" else None
        if started is not None:
            eng.host_run(started)
        if stopped is not None:
            eng.host_stop(stopped)
            if self.session is not None and self.session["owner"] == stopped:
                self.session = None

        # ---- engine edge ----
        et = eng.step(E, bool(self.dual), sample, previous,
                      started=started, stopped=stopped,
                      tx_sizes=tx_sizes, rx_sizes=rx_sizes)
        trace.update(et)

        # ---- same-edge host effects ----
        sram_we = 0
        if plan and started is None and stopped is None:
            sram_we = self._apply(plan, trace, tx_sizes, rx_sizes)

        trace["tx_count"] = (len(ctx[A].tx), len(ctx[B].tx))
        trace["rx_count"] = (len(ctx[A].rx), len(ctx[B].rx))

        # ---- synchronizer / history update ----
        self.history = sample
        self.history_valid = True
        self._clock_syncs(ui_in, uio_in)

        trace.update(edge=E, dual_mode=self.dual, uo_out=self._uo_out(),
                     sram_we=sram_we)
        return trace

    # ------------------------------------------------------- frame machine
    def _accept_nibble(self, ui_in, tx_sizes, rx_sizes):
        """Advance the frame by one accepted nibble; return an effect plan
        (applied after engine.step) or None.  Sets self.resp."""
        nib = ui_in & 0xF
        sel = (ui_in >> 6) & 1 if self.dual else A

        if self.phase == PH_CMD0:
            self.L = sel
            self.mismatch = False
            self.cmd_hi = nib
            self.payload = []
            self.resp = 0
            self.phase = PH_CMD1
            return None

        if self.phase in (PH_DUM0, PH_DUM1):
            if self.phase == PH_DUM0:
                self.resp = (self.held >> 4) & 0xF
                self.phase = PH_DUM1
            else:
                self.resp = self.held & 0xF
                self.phase = PH_CMD0
            return None

        if sel != self.L:
            self.mismatch = True

        if self.phase == PH_CMD1:
            self.cmd = (self.cmd_hi << 4) | nib
            if PAYLOAD_NIBBLES.get(self.cmd, 0) == 0:
                return self._finalize(tx_sizes, rx_sizes)
            self.phase = PH_PAY
            self.resp = 0
            return None

        # PH_PAY
        self.payload.append(nib)
        if len(self.payload) < PAYLOAD_NIBBLES[self.cmd]:
            self.resp = 0
            return None
        return self._finalize(tx_sizes, rx_sizes)

    def _payload_value(self):
        p = self.payload
        if len(p) == 2:
            return (p[0] << 4) | p[1]
        lo = (p[0] << 4) | p[1]
        hi = (p[2] << 4) | p[3]
        return lo | (hi << 8)

    def _finalize(self, tx_sizes, rx_sizes):
        """Final command/payload nibble: decide code (priority mismatch 3 >
        undefined 3 > state 2 > format 3 > FIFO 1 > success 0) and plan."""
        self.phase = PH_CMD0
        code, plan = self._decide(tx_sizes, rx_sizes)
        self.resp = code
        if code == 0 and self.cmd in (CMD_STATUS, CMD_RX):
            self.phase = PH_DUM0
        return plan

    def _decide(self, tx_sizes, rx_sizes):
        X = self.L
        cmd = self.cmd
        if self.mismatch:
            return 3, None
        if cmd not in PAYLOAD_NIBBLES:
            return 3, None

        ctx = self.engine.ctx
        cx = ctx[X]
        if self.dual:
            cy = ctx[1 - X]
            other_running, other_loading = bool(cy.running), bool(cy.loading)
        else:
            other_running = other_loading = False
        any_running = bool(cx.running) or other_running
        any_loading = bool(cx.loading) or other_loading

        if cmd == CMD_STATUS:
            self.held = ((int(tx_sizes[X] < 4) << 0) | (int(rx_sizes[X] > 0) << 1)
                         | (int(bool(cx.ready)) << 2) | (int(bool(cx.running)) << 3)
                         | (int(bool(cx.fault)) << 4) | (int(bool(cx.loading)) << 5))
            return 0, None

        if cmd == CMD_STOP:
            return 0, {"act": "stop", "cid": X}

        if cmd == CMD_RUN:
            if cx.running or not cx.ready or cx.fault or any_loading:
                return 2, None
            return 0, {"act": "run", "cid": X}

        if cmd == CMD_TX:
            if not cx.ready or cx.fault:
                return 2, None
            if tx_sizes[X] >= 4:
                return 1, None
            return 0, {"act": "tx", "cid": X, "byte": self._payload_value()}

        if cmd == CMD_RX:
            if not cx.ready:
                return 2, None
            if rx_sizes[X] == 0:
                return 1, None
            self.held = cx.rx[0] & 0xFF   # pre-edge oldest byte
            return 0, {"act": "rx", "cid": X}

        value = self._payload_value()
        max_len = 128 if self.dual else 256

        if cmd == CMD_BEGIN:
            if any_running or other_loading:
                return 2, None
            if not 1 <= value <= max_len:
                return 3, {"act": "load_error", "cid": X}
            return 0, {"act": "begin", "cid": X, "length": value}

        # WRITE / END
        if not cx.loading or any_running or other_loading:
            return 2, None
        s = self.session
        assert s is not None and s["owner"] == X, "LOADING without loader session"
        if cmd == CMD_WRITE:
            if s["count"] >= s["length"]:
                return 3, {"act": "load_error", "cid": X}
            return 0, {"act": "write", "cid": X, "word": value}
        # CMD_END
        if s["count"] != s["length"] or s["sum"] != value:
            return 3, {"act": "load_error", "cid": X}
        return 0, {"act": "end", "cid": X}

    # ------------------------------------------------------- apply effects
    def _apply(self, plan, trace, tx_sizes, rx_sizes):
        """Apply host effects after engine.step.  Returns sram_we."""
        eng = self.engine
        X = plan["cid"]
        cx = eng.ctx[X]
        act = plan["act"]

        if act == "begin":
            eng.host_begin(X, plan["length"])
            self.session = {"owner": X, "length": plan["length"],
                            "count": 0, "sum": 0}
        elif act == "load_error":
            cx.loading = False
            cx.ready = False
            if self.session is not None and self.session["owner"] == X:
                self.session = None
        elif act == "end":
            cx.loading = False
            cx.ready = True
            self.session = None
        elif act == "write":
            s = self.session
            base = 128 if (self.dual and X == B) else 0
            addr = (base + s["count"]) & 0xFF
            assert not trace.get("fetch_valid"), \
                "SRAM port conflict: loader write and engine fetch on same edge"
            eng.mem[addr] = plan["word"] & 0xFFFF
            s["count"] += 1
            s["sum"] = (s["sum"] + plan["word"]) & 0xFFFF
            trace["sram_addr"] = addr
            return 1
        elif act == "tx":
            if tx_sizes[X] < 4:      # permission from pre-edge occupancy
                cx.tx.append(plan["byte"] & 0xFF)
        elif act == "rx":
            if rx_sizes[X] > 0:      # permission from pre-edge occupancy
                popped = cx.rx.popleft()
                assert popped == self.held, "RX head changed within edge"
        return 0
