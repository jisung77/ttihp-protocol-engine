"""Exercise simultaneous UART TX and full-duplex SPI through the v0.2 TT pins.

Any cycle-level adapter exposing tick(ui_in=..., uio_in=..., rst_n=..., ena=...)
and returning uo_out, uio_out and uio_oe can replace the Python ISS.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))  # Existing timing-study program build imports.
from check_pins import check
from run import build

UART = (0x55, 0xA6)
MOSI = (0x96, 0x53)
MISO = (0x3D, 0xC6)


def build_images(directory, tuned=True):
    """Assemble the same pin-4 UART and lower-nibble SPI images as the prior study."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    uart = build(REPO, directory, "uart_pin4")
    variant = "tuned" if tuned else "original"
    spi = [build(REPO, directory, f"spi{mode}_{variant}", mode, tuned) for mode in range(4)]
    return uart, spi


class SpiTarget:
    """Update MISO for the next engine edge using only the already observed pads."""

    def __init__(self, mode):
        self.idle = mode >> 1
        self.phase = mode & 1
        self.selected = False
        self.last_clock = self.idle
        self.frame = 0
        self.samples = 0
        self.miso = 0

    def observe(self, out, oe):
        selected = bool(oe & 8) and not (out & 8)
        clock = out & 1
        if selected and not self.selected:
            assert self.frame < len(MISO), "unexpected third SPI chip select"
            self.samples = 0
            self.miso = MISO[self.frame] >> 7
        elif selected and clock != self.last_clock:
            leading = clock != self.idle
            if leading != self.phase:
                self.samples += 1
                assert self.samples <= 8, "excess SPI sample edges"
            elif self.samples < 8:
                self.miso = (MISO[self.frame] >> (7 - self.samples)) & 1
        elif self.selected and not selected:
            assert self.samples == 8, f"CS rose with {self.samples} samples"
            self.frame += 1
            self.miso = 0
        self.last_clock = clock
        self.selected = selected


class Pins:
    def __init__(self, machine, mode):
        self.machine = machine
        self.target = SpiTarget(mode)
        self.rows = []
        self.edge = 0
        self.ui = 0x20  # 001 mode strap; host REQ starts low.
        self.req = 0

    def tick(self, reset=False):
        bit = self.target.miso
        pins = self.machine.tick(ui_in=self.ui, uio_in=bit << 2, rst_n=int(not reset), ena=1)
        self.edge = 0 if reset else self.edge + 1
        out, oe, reply = (int(pins[key]) for key in ("uio_out", "uio_oe", "uo_out"))
        assert not (reply & 0x80), f"E{self.edge}: selected context faulted"
        self.rows.append((out, oe, bit))
        self.target.observe(out, oe)
        return reply

    def nibble(self, cid, value):
        self.ui = (cid << 6) | (self.req << 4) | value
        self.tick()  # Bundle held before the request toggle.
        self.req ^= 1
        self.ui ^= 1 << 4
        for _ in range(8):
            reply = self.tick()
            if (reply >> 4) & 1 == self.req:
                return reply & 15
        raise AssertionError(f"E{self.edge}: missing ACK for context {cid}, nibble {value:x}")

    def command(self, cid, opcode, payload=b"", expected_code=0):
        nibbles = [n for b in bytes([opcode]) + payload for n in (b >> 4, b & 15)]
        for n in nibbles[:-1]:
            assert self.nibble(cid, n) == 0
        code = self.nibble(cid, nibbles[-1])
        assert code == expected_code, f"E{self.edge}: context {cid} cmd {opcode:02x} returned {code}"
        if code == 0 and opcode in (0, 7):
            return (self.nibble(cid, 0) << 4) | self.nibble(cid, 0)
        return None

    def load(self, cid, words):
        self.command(cid, 1, len(words).to_bytes(2, "little"))
        for word in words:
            self.command(cid, 2, word.to_bytes(2, "little"))
        self.command(cid, 3, (sum(words) & 0xffff).to_bytes(2, "little"))
        assert self.command(cid, 0) == 0x05


def verify_case(machine, mode, launch_parity, uart_words, spi_words):
    """Load both images through pins, run concurrently and return independent measurements."""
    pins = Pins(machine, mode)
    for _ in range(3):
        pins.tick(reset=True)
    for _ in range(4):
        assert pins.tick() == 0
    for cid, words, data in ((0, uart_words, UART), (1, spi_words, MOSI)):
        pins.load(cid, words)
        for value in data:
            pins.command(cid, 6, bytes([value]))

    if pins.edge % 2 != launch_parity:
        pins.tick()
    pins.command(0, 4)
    k = pins.edge
    assert k % 2 == launch_parity
    # Bring both CS windows inside the alternating first UART byte.
    for _ in range(193):
        pins.tick()
    pins.command(1, 4)
    k_b = pins.edge
    assert k_b == k + 201
    while pins.edge < k + 9000:
        pins.tick()

    received = [pins.command(1, 7) for _ in MISO]
    assert received == list(MISO), f"SPI bytes popped through the host: {received}"
    for cid in (0, 1):
        pins.command(cid, 7, expected_code=1)
        assert pins.command(cid, 0) == 0x0d, f"context {cid} should be running with empty RX"

    decoded = check(pins.rows, mode, UART, MOSI, MISO)
    assert decoded["spi_half_ticks"] == [24, 26]
    assert all(any(a < t < b for t in decoded["uart_edges_under_cs"])
               for a, b in decoded["spi_select_windows"]), "each SPI frame must overlap a UART transition"
    assert pins.target.frame == len(MISO)
    raw = bytes(v for row in pins.rows for v in row)
    return dict(mode=mode, run_edges=[k, k_b], trace_clocks=len(pins.rows),
                pin_trace_sha256=hashlib.sha256(raw).hexdigest(), host_rx=received, **decoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--negative", action="store_true",
                        help="also run the original SPI delay, which violates the 50-clock period")
    args = parser.parse_args()
    from model.protocol_machine import ProtocolMachine
    image_dir = args.output / "images"
    uart, spi = build_images(image_dir)
    cases = [verify_case(ProtocolMachine(), mode, parity, uart, spi[mode])
             for mode in range(4) for parity in range(2)]
    controls = []
    if args.negative:
        _, original = build_images(image_dir, tuned=False)
        for mode in range(4):
            try:
                verify_case(ProtocolMachine(), mode, 0, uart, original[mode])
            except ValueError as error:
                if "SPI full period differs from 50" not in str(error):
                    raise
                controls.append(dict(mode=mode, rejected=str(error)))
            else:
                raise AssertionError(f"mode {mode}: untuned image unexpectedly passed")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(image_dir.glob("*.bin"))}
    report = dict(scope="v0.2 ISS through external TT host and protocol pins", cases=cases,
                  untuned_controls=controls, images_sha256=hashes)
    (args.output / "concurrent-pins.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(cases)} concurrent cases passed; {len(controls)} untuned controls were rejected")


if __name__ == "__main__":
    main()
