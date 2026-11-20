"""Independent transcript of the board driver's serial transfers (spec §6/§7)."""
import pytest

from host import rp2350_loader


class Port:
    def __init__(self):
        self.value = 0


class ScriptedBoard:
    def __init__(self, expected, responses):
        self._expected = iter(expected)
        self._responses = iter(responses)
        self.req = 0
        self.uo_out = Port()
        self.uio_oe_pico = Port()
        self.ui_in = self
        self.staged = 0
        self.resetting = False

    def reset_project(self, active):
        self.resetting = active

    @property
    def value(self):
        return self.staged

    @value.setter
    def value(self, v):
        req, n = v >> 4, v & 15
        if req == self.req:
            self.staged = n
            return
        assert not self.resetting and self.staged == n == next(self._expected)
        self.req = req
        self.uo_out.value = (req << 4) | next(self._responses)


def nibbles(text):
    return [int(n,16) for n in text.replace(" ","")]


def test_real_board_driver_uses_reference_nibble_transcript(monkeypatch):
    # Hand-transcribed §7 bytes plus STATUS, RUN and blocked RX.
    expected = nibbles("01 04 00 02 01 08 02 01 10 02 01 18 02 00 00 03 03 30 00 00 04 07")
    reply = [0] * len(expected)
    reply[-5] = 5  # STATUS reply low nibble: READY + TX room
    reply[-1] = 1  # RX retry
    board = ScriptedBoard(expected, reply)
    monkeypatch.setattr(rp2350_loader.time, "sleep_us", lambda _: None, raising=False)
    monkeypatch.setattr(rp2350_loader.time, "ticks_ms", lambda: 0, raising=False)
    monkeypatch.setattr(rp2350_loader.time, "ticks_diff", lambda a,b:a-b, raising=False)
    host = rp2350_loader.Loader(board)
    host.reset()
    host.load(bytes.fromhex("01 08 01 10 01 18 00 00"))
    host.run()
    assert host.try_receive() is None
    assert next(board._expected, None) is None and next(board._responses, None) is None


def test_rejects_invalid_image_before_driving_any_pin():
    host = rp2350_loader.Loader(None)
    for image in (b"", b"a", bytes(513), bytes(514)):
        with pytest.raises(ValueError):
            host.load(image)
