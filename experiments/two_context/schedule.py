"""A two-slot timing experiment for the published TX programs. Not an ISA implementation."""
from collections import deque


def program(path, mode=None, tuned=True):
    """Load the timing-relevant path of an example; labels remain instruction addresses."""
    code, labels = [], {}
    for line in path.read_text().splitlines():
        text = line.partition("#")[0].strip()
        if not text:
            continue
        if text.endswith(":"):
            labels[text[:-1]] = len(code)
            continue
        op, *args = text.split()
        if mode is None:  # UART TX moves from pin 0 to pin 4.
            if op in ("DATA", "OE", "COMMIT"):
                args[0] = str(int(args[0], 0) << 4)
            elif op == "SHOUT":
                args[0] = "4"
        elif tuned:
            if op == "TLO" and args == ["15"]:
                args = ["14"]
            if mode % 2 == 0:
                if op == "SHOUT":
                    code.append(("COMMIT", ["0"]))  # lengthen interval into leading edge
                if op == "COMMIT" and args == ["8"] and code[-1][0] == "COMMIT":
                    code.append(("COMMIT", ["0"]))  # lengthen final CS hold
            else:
                if op == "LSET":
                    code.append(("COMMIT", ["0"]))  # first CS setup
                if op == "DELAY" and code[-2][0] == "SHIN":
                    code.append(("COMMIT", ["0"]))  # lengthen interval into leading edge
        code.append((op, args))
    return code, labels


OPCODES = "HALT DATA OE COMMIT TLO THI WAIT DELAY SHIN SHOUT BR LSET DJNZ PUSH PULL SRIMM".split()


class Thread:
    def __init__(self, base, length, ownership, arrival, tx):
        self.base, self.length = base, length
        self.ownership, self.arrival, self.tx = ownership, arrival, deque(tx)
        self.rx = []
        self.pc = self.delay = self.loop = self.osr = self.isr = 0
        self.shadow_data = self.shadow_oe = self.data = self.oe = 0
        self.instruction = self.start = None
        self.fetches = self.retires = self.wait_slots = self.fifo_slots = 0

    def execute(self, tick, synchronized_miso):
        if self.instruction is None:
            return
        op = OPCODES[self.instruction >> 11]
        arg = self.instruction & 0x7ff
        if self.start is None:
            self.start = tick
        next_pc = self.pc + 1
        if op in ("DATA", "OE", "COMMIT"):
            value = arg
            if value & ~self.ownership:
                raise ValueError(f"{op} exceeds pin ownership at cycle {tick}")
            if op == "DATA":
                self.shadow_data = value
            elif op == "OE":
                self.shadow_oe = value
            else:
                self.data = (self.data & ~value) | (self.shadow_data & value)
                self.oe = (self.oe & ~value) | (self.shadow_oe & value)
        elif op == "TLO":
            self.delay = (self.delay & 0xff00) | arg
        elif op == "THI":
            self.delay = (arg << 8) | (self.delay & 0xff)
        elif op == "DELAY":
            if tick < self.start + self.delay:
                self.wait_slots += 1
                return
        elif op == "PULL":
            if tick < self.arrival or not self.tx:
                self.fifo_slots += 1
                return
            self.osr = self.tx.popleft()
        elif op == "PUSH":
            self.rx.append(self.isr)
        elif op == "SHOUT":
            pin = 1 << (arg & 7)
            assert pin & self.ownership
            if arg & 8:
                bit, self.osr = self.osr & 1, self.osr >> 1
            else:
                bit, self.osr = self.osr >> 7, (self.osr << 1) & 255
            self.data = (self.data & ~pin) | (pin if bit else 0)
            self.shadow_data = (self.shadow_data & ~pin) | (self.data & pin)
        elif op == "SHIN":
            assert arg == 2
            self.isr = ((self.isr << 1) | synchronized_miso) & 255
        elif op == "LSET":
            self.loop = arg
        elif op == "DJNZ":
            self.loop = max(0, self.loop - 1)
            if self.loop:
                next_pc = arg
        elif op == "BR":
            assert arg <= 255  # BR ALWAYS
            next_pc = arg
        else:
            raise ValueError(f"unmodelled example instruction {op!r}")
        self.pc, self.instruction, self.start = next_pc, None, None
        self.retires += 1


class Target:
    """External SPI speaker. Update MISO from CS/SCK changes observed after each edge."""
    def __init__(self, mode, messages):
        self.idle, self.phase = mode >> 1, mode & 1
        self.prev_sck, self.prev_cs, self.miso = self.idle, 1, 0
        self.messages = iter(messages)

    def observe(self, bus, oe):
        sck = (bus & 1) if oe & 1 else self.idle
        cs = ((bus >> 3) & 1) if oe & 8 else 1
        if self.prev_cs and not cs:
            byte = next(self.messages)
            self.bits = iter((byte >> shift) & 1 for shift in range(7, -1, -1))
            if not self.phase:
                self.miso = next(self.bits)
        if not cs and sck != self.prev_sck:
            leading = sck != self.idle
            if leading == bool(self.phase):
                self.miso = next(self.bits, 0)
        self.prev_cs, self.prev_sck = cs, sck
        return self.miso


def simulate(uart_code, spi_code, mode, parity, arrivals, clocks=10000,
             uart_tx=(0x55, 0xa6), spi_tx=(0x96, 0x53), spi_rx=(0x3d, 0xc6), external_miso=None):
    memory = [None] * 256
    for base, words in ((0, uart_code), (128, spi_code)):
        assert 0 < len(words) <= 128
        memory[base:base + len(words)] = words
    uart = Thread(0, len(uart_code), 0x10, arrivals[0], uart_tx)
    spi = Thread(128, len(spi_code), 0x0b, arrivals[1], spi_tx)
    threads = (uart, spi)
    assert not (uart.ownership & spi.ownership)
    target = Target(mode, spi_rx)
    rows, sync1, sync2 = [], 0, 0
    shared_reads = shared_retires = overlapping = 0
    for tick in range(clocks):
        if tick:
            owner = (tick + parity) % 2
            fetching = threads[owner]
            reading = fetching.instruction is None
            if reading:
                assert 0 <= fetching.pc < fetching.length
                fetching.instruction = memory[fetching.base + fetching.pc]
                fetching.fetches += 1
            executing = threads[1 - owner]
            before = executing.retires
            executing.execute(tick, sync2)
            retired = executing.retires - before
            shared_reads += reading
            shared_retires += retired
            overlapping += int(reading and retired)
        output, oe = uart.data | spi.data, uart.oe | spi.oe
        # A transition generated by the target after edge t enters stage 1 at t+1.
        old_pin = rows[-1][2] if rows else 0
        sync1, sync2 = old_pin, sync1
        pin = target.observe(output, oe) if external_miso is None else external_miso[tick]
        rows.append((output, oe, pin))
    assert overlapping and shared_reads == sum(t.fetches for t in threads)
    assert shared_retires == sum(t.retires for t in threads)
    return rows, {"logical_words": [len(uart_code), len(spi_code)], "spi_fifo": spi.rx, "fetches": shared_reads, "retires": shared_retires,
                  "overlap_edges": overlapping, "fifo_stall_slots": [t.fifo_slots for t in threads]}
