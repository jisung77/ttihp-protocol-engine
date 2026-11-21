"""Drive the TT nibble loader and an ideal open-drain I2C target using public pins."""

SDA, SCL = 1, 2
LINES = SDA | SCL


def resolved(out, oe, target_low):
    """Pull-ups supply HIGH on released SDA/SCL; each participant can pull LOW."""
    return LINES & ~(target_low | (oe & ~out))


class Target:
    def __init__(self, address, acks, pulse, length):
        self.address_byte = address << 1
        self.bytes = []
        self.shift = 0
        self.acks = acks
        self.pulse = pulse
        self.length = length
        self.low = 0
        self.previous = LINES
        self.starts = []
        self.stops = []
        self.active = False
        self.rises = 0
        self.held = 0

    def observe(self, tick, out, oe):
        """Use only bus transitions to schedule the target's drive for the next tick."""
        bus = resolved(out, oe, self.low)
        last, self.previous = self.previous, bus
        if self.low & SCL:
            if self.held and oe & SCL:
                raise AssertionError(f"tick {tick}: controller reasserted SCL during stretch")
            if not oe & SCL:
                self.held += 1
                if self.held == self.length:
                    self.low &= ~SCL

        if last & bus & SCL and (last ^ bus) & SDA:
            self.active = not bool(bus & SDA)
            if self.active:
                self.starts.append(tick)
                self.rises = 0
            else:
                self.stops.append(tick)
            return
        if not self.active:
            return
        if not last & SCL and bus & SCL:
            self.rises += 1
            byte, bit = divmod(self.rises - 1, 9)
            if bit < 8 and byte < len(self.acks):
                self.shift = (self.shift << 1 | int(bool(bus & SDA))) & 255
                if bit == 7:
                    self.bytes.append(self.shift)
        elif last & SCL and not bus & SCL:
            # The ACK is prepared after the eighth falling edge. Release it
            # after the ninth, when SCL is LOW again.
            byte, phase = divmod(self.rises, 9)
            matches_address = self.bytes and self.bytes[0] == self.address_byte
            if phase == 8 and byte < len(self.acks) and matches_address and self.acks[byte] == 0:
                self.low |= SDA
            elif phase == 0:
                self.low &= ~SDA
            if self.rises + 1 == self.pulse:
                self.low |= SCL


class Host:
    def __init__(self, machine, target):
        self.machine = machine
        self.target = target
        self.rows = []
        self.ui = 0
        self.req = 0
        self.out = self.oe = self.reply = 0

    def tick(self, reset=False):
        target_low = self.target.low
        bus = resolved(self.out, self.oe, target_low)
        row = self.machine.tick(ui_in=self.ui, uio_in=bus, rst_n=int(not reset), ena=1)
        self.out, self.oe, self.reply = (int(row[k]) for k in ("uio_out", "uio_oe", "uo_out"))
        tick = len(self.rows)
        assert not self.out & self.oe, f"tick {tick}: actively driven HIGH"
        assert not self.oe & ~LINES, f"tick {tick}: unrelated pin enabled"
        assert not self.reply & 0x80, f"tick {tick}: engine fault"
        self.rows.append((self.out, self.oe, target_low))
        self.target.observe(tick, self.out, self.oe)
        return self.reply

    def nibble(self, data):
        self.ui = (self.req << 4) | data
        self.tick()
        self.req ^= 1
        self.ui ^= 0x10
        for _ in range(8):
            reply = self.tick()
            if (reply >> 4) & 1 == self.req:
                return reply & 15
        raise AssertionError(f"tick {len(self.rows)}: no host ACK for nibble {data:x}")

    def command(self, opcode, payload=b"", expected_code=0):
        nibbles = [n for b in bytes([opcode]) + payload for n in (b >> 4, b & 15)]
        for nibble in nibbles[:-1]:
            assert self.nibble(nibble) == 0
        code = self.nibble(nibbles[-1])
        assert code == expected_code, f"opcode {opcode:02x}: reply {code}, expected {expected_code}"
        if not code and opcode in (0, 7):
            return (self.nibble(0) << 4) | self.nibble(0)
        return None

    def load(self, words):
        assert 1 <= len(words) <= 256
        self.command(1, len(words).to_bytes(2, "little"))
        for word in words:
            self.command(2, word.to_bytes(2, "little"))
        self.command(3, (sum(words) & 0xffff).to_bytes(2, "little"))
        assert self.command(0) == 0x05


def exercise(machine, words, *, address, data, acks=(0, 0), stretch_pulse=None, stretch_ticks=0):
    """Return per-clock pin rows and raw host results for one loaded program.

    acks contains wire levels, e.g. (1,) NACKs the address. Pulse 1 is the
    first address bit, 9 is its ACK, and 9*N+1 is the rise preparing STOP.
    A finite stretch lasts exactly stretch_ticks clocks after master release.
    None holds SCL until after the test, which terminates the engine via host STOP.
    """
    acks = tuple(acks)
    if acks not in ((1,), (0, 0), (0, 1)):
        raise ValueError("expected address NACK or address ACK with one data response")
    if not 0x08 <= address <= 0x77 or not 0 <= data <= 255:
        raise ValueError("expected an ordinary 7-bit target and a data byte")
    if stretch_pulse is None:
        if stretch_ticks != 0:
            raise ValueError("specify which SCL pulse to stretch")
    elif not 1 <= stretch_pulse <= 9 * len(acks) + 1 or (stretch_ticks is not None and stretch_ticks < 1):
        raise ValueError("invalid stretch pulse or duration")

    target = Target(address, acks, stretch_pulse, stretch_ticks)
    host = Host(machine, target)
    for _ in range(3):
        assert host.tick(reset=True) == 0
    for _ in range(4):
        assert host.tick() == 0
    host.load(words)
    host.command(4)
    run_tick = len(host.rows) - 1
    assert host.reply & 0x40
    aborted = stretch_ticks is None
    for _ in range(30000 + (stretch_ticks or 0)):
        host.tick()
        if not host.reply & 0x40:
            assert not aborted, "engine stopped before host STOP despite held SCL"
            break
        if aborted and target.held >= 1024:
            break
    else:
        raise AssertionError("transfer did not reach HALT or the requested held-low condition")

    assert len(target.starts) == 1 and target.starts[0] - run_tick >= 235, target.starts
    abort_info = {}
    if aborted:
        assert target.held >= 1024 and target.rises == stretch_pulse - 1 and not target.stops
        assert host.command(0) & 0x0c == 0x0c  # READY and still RUNNING
        host.command(5)
        abort_tick = len(host.rows) - 1
        abort_info = dict(host_stop_tick=abort_tick, oe_at_host_stop=host.oe,
                          resolved_at_host_stop=resolved(host.out, host.oe, target.low) & LINES)
        assert host.oe == 0 and not host.reply & 0x40
        assert not resolved(host.out, host.oe, target.low) & SCL and not target.stops
        received = []
    else:
        assert target.bytes == [address << 1, data][:len(acks)], target.bytes
        assert len(target.stops) == 1 and target.held == stretch_ticks
        assert host.oe == 0 and resolved(host.out, host.oe, target.low) == LINES
        assert host.command(0) == 0x07  # READY, RX present, space in TX
        received = [host.command(7) for _ in acks]
        assert received == list(acks), f"host RX {received}, expected {list(acks)}"

    host.command(7, expected_code=1)
    final_status = host.command(0)
    assert final_status == 0x05
    if aborted:
        assert host.rows[abort_tick + 1][1] == 0
    return host.rows, dict(address=address, data=data, expected_bytes=[address << 1, data][:len(acks)],
                           expected_acks=list(acks), target_bytes=target.bytes, host_rx=received, run_tick=run_tick,
                           start_tick=target.starts[0], stop_ticks=target.stops,
                           stretch_pulse=stretch_pulse, stretch_observed_ticks=target.held,
                           aborted=aborted, final_status=final_status, clocks=len(host.rows), **abort_info)
