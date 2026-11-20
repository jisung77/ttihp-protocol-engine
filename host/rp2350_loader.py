"""MicroPython driver for a selected and clocked TT demo-board design (spec §6)."""
import time


class Loader:
    def __init__(self, board):
        self.board = board
        self.req = 0
        self.synced = False

    def reset(self):
        """Start a new session. Board must have a running project clock >= 100 kHz."""
        b = self.board
        b.uio_oe_pico.value = 0
        b.reset_project(True)
        b.ui_in.value = 0
        self.req = 0
        time.sleep_us(50)
        b.reset_project(False)
        time.sleep_us(50)
        self.synced = (int(b.uo_out.value) == 0)
        if not self.synced:
            raise RuntimeError("reset did not clear ACK/status")

    def _nibble(self, data):
        if not self.synced:
            raise RuntimeError("reset the loader session first")
        b = self.board
        b.ui_in.value = (self.req << 4) | data
        time.sleep_us(1)
        self.req ^= 1
        b.ui_in.value = (self.req << 4) | data
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < 100:
            sample = int(b.uo_out.value)
            if ((sample >> 4) & 1) == self.req:
                return sample & 15
        self.synced = False
        raise RuntimeError("ACK timed out; reset and reload")

    def _command(self, cmd, payload=b""):
        status = 0
        for b in bytes([cmd]) + payload:
            if status or self._nibble(b >> 4):
                self.synced = False
                raise RuntimeError("unexpected response on intermediate nibble")
            status = self._nibble(b & 15)
        return status

    def _ok(self, cmd, payload=b""):
        status = self._command(cmd, payload)
        if status:
            raise RuntimeError("command %02x returned %d" % (cmd, status))

    def _reply(self):
        return (self._nibble(0) << 4) | self._nibble(0)

    def status(self):
        self._ok(0)
        return self._reply()

    def stop(self):
        self._ok(5)

    def load(self, image):
        if not isinstance(image, (bytes, bytearray)) or not 2 <= len(image) <= 512 or len(image) % 2:
            raise ValueError("image must contain 1..256 little-endian 16-bit words")
        words = len(image) // 2
        self._ok(1, bytes((words & 255, words >> 8)))
        total = 0
        for i in range(0, len(image), 2):
            lo, hi = image[i], image[i+1]
            self._ok(2, bytes((lo, hi)))
            total += lo | (hi << 8)
        self._ok(3, bytes((total & 255, (total >> 8) & 255)))
        if self.status() & 0x3c != 4:
            raise RuntimeError("END did not leave the image ready and stopped")

    def run(self):
        self._ok(4)

    def try_send(self, value):
        status = self._command(6, bytes([value]))
        if status > 1:
            raise RuntimeError("TX returned %d" % status)
        return status == 0

    def try_receive(self):
        status = self._command(7)
        if status == 1:
            return None
        if status:
            raise RuntimeError("RX returned %d" % status)
        return self._reply()
