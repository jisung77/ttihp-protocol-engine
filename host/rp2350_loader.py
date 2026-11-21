"""MicroPython driver for a selected and clocked TT demo-board design (spec §§6, 9)."""
import time


class Loader:
    """Use Loader(board, dual=True) before reset to address A (target=0) or B (target=1).

    In dual mode, load both programs before either run. reset() restores the
    chosen strap and invalidates both images, including after a failed frame.
    """

    def __init__(self, board, dual=False):
        self.board = board
        self._dual = bool(dual)
        self.req = 0
        self.synced = False

    def reset(self):
        """Start a new session. Board must have a running project clock >= 100 kHz."""
        self.synced = False
        b = self.board
        b.uio_oe_pico.value = 0
        b.reset_project(True)
        b.ui_in.value = 0x20 if self._dual else 0  # REQ low; strap fixed through E3.
        self.req = 0
        time.sleep_us(50)
        b.reset_project(False)
        time.sleep_us(50)
        if int(b.uo_out.value) != 0:
            raise RuntimeError("reset did not clear ACK/status")
        self.synced = True

    def _nibble(self, data, target=0):
        if not self.synced:
            raise RuntimeError("reset the loader session first")
        b = self.board
        bundle = (target << 6) | data
        b.ui_in.value = (self.req << 4) | bundle
        time.sleep_us(1)
        self.req ^= 1
        b.ui_in.value = (self.req << 4) | bundle
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < 100:
            sample = int(b.uo_out.value)
            if ((sample >> 4) & 1) == self.req:
                return sample & 15
        self.synced = False
        raise RuntimeError("ACK timed out; reset and reload")

    def _command(self, cmd, payload, target):
        status = 0
        for b in bytes([cmd]) + payload:
            if status or self._nibble(b >> 4, target):
                self.synced = False
                raise RuntimeError("unexpected response on intermediate nibble")
            status = self._nibble(b & 15, target)
        return status

    def _frame(self, cmd, payload=b"", reply=False, target=0):
        # An interrupted command or reply leaves the receiver at an unknown nibble.
        if not self.synced:
            raise RuntimeError("reset the loader session first")
        if not isinstance(target, int) or not 0 <= target <= int(self._dual):
            raise ValueError("target must be 0 in single mode, or 0 or 1 in dual mode")
        try:
            status = self._command(cmd, payload, target)
            if reply and status == 0:
                return status, self._reply(target)
            return status, None
        except BaseException:
            self.synced = False
            raise

    def _ok(self, cmd, payload=b"", reply=False, target=0):
        status, data = self._frame(cmd, payload, reply, target)
        if status:
            raise RuntimeError("command %02x returned %d" % (cmd, status))
        return data

    def _reply(self, target):
        return (self._nibble(0, target) << 4) | self._nibble(0, target)

    def status(self, target=0):
        return self._ok(0, reply=True, target=target)

    def stop(self, target=0):
        self._ok(5, target=target)

    def load(self, image, target=0):
        max_words = 128 if self._dual else 256
        if not isinstance(image, (bytes, bytearray)) or not 2 <= len(image) <= 2 * max_words or len(image) % 2:
            raise ValueError("image must contain 1..%d little-endian 16-bit words" % max_words)
        words = len(image) // 2
        self._ok(1, bytes((words & 255, words >> 8)), target=target)
        total = 0
        for i in range(0, len(image), 2):
            lo, hi = image[i], image[i+1]
            self._ok(2, bytes((lo, hi)), target=target)
            total += lo | (hi << 8)
        self._ok(3, bytes((total & 255, (total >> 8) & 255)), target=target)
        if self.status(target) & 0x3c != 4:
            raise RuntimeError("END did not leave the image ready and stopped")

    def run(self, target=0):
        self._ok(4, target=target)

    def try_send(self, value, target=0):
        status = self._frame(6, bytes([value]), target=target)[0]
        if status > 1:
            raise RuntimeError("TX returned %d" % status)
        return status == 0

    def try_receive(self, target=0):
        status, data = self._frame(7, reply=True, target=target)
        if status == 1:
            return None
        if status:
            raise RuntimeError("RX returned %d" % status)
        return data
