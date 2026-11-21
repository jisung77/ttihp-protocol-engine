"""Public pin tests for the host ACK deadline and interrupted frames (spec §6)."""
import pytest

from host import rp2350_loader


TICKS_PERIOD = 512  # Synthetic period; 100 ms fits within a half-period.
TRANSCRIPT = "01 04 00 02 01 08 02 01 10 02 01 18 02 00 00 03 03 30 00 00 04"
IMAGE = bytes.fromhex("01 08 01 10 01 18 00 00")


class Clock:
    """MicroPython time subset. Only Board uo_out reads advance `steps` (1 ms each)."""
    def __init__(self):
        self.base = self.steps = 0

    def ticks_ms(self):
        return (self.base + self.steps) % TICKS_PERIOD

    def ticks_diff(self, a, b):
        return ((a - b + TICKS_PERIOD // 2) % TICKS_PERIOD) - TICKS_PERIOD // 2

    def sleep_us(self, _):
        pass


class Pin:
    def __init__(self, get, put=None):
        self.get, self.put = get, put

    @property
    def value(self):
        return self.get()

    @value.setter
    def value(self, v):
        self.put(v)


class TimedBoard:
    """A REQ toggle pops (nibble, response, delay|None). The ACK shows on the uo_out
    read whose unwrapped index since that toggle is >= delay (None = never); tick
    values are never consulted. `log` records ui_in/uio_oe_pico/reset_project activity."""
    def __init__(self, clock, budget=300):
        self.clock, self.budget, self.script = clock, budget, []
        self.log, self.toggles, self.armed = [], [], {}
        self.req = self.staged = self.out = self.oe = self.waited = self.stuck = 0
        self.pending, self.resetting = None, False
        self.ui_in = Pin(lambda: self.req << 4 | self.staged, self._write)
        self.uio_oe_pico = Pin(lambda: self.oe, self._oe)
        self.uo_out = Pin(self._read)

    def arm(self, kind, nth, exc):  # raise exc on the nth future "ui"/"uo"/"oe"/"rst" op
        self.armed[kind] = [nth, exc]

    def _hit(self, kind, *args):
        fault = self.armed.get(kind)
        if fault:
            fault[0] -= 1
            if fault[0] == 0:
                del self.armed[kind]
                raise fault[1]
        if kind != "uo":
            self.log.append((kind, *args))

    def _oe(self, v):
        self._hit("oe", v)
        self.oe = v

    def reset_project(self, active):
        self._hit("rst", active)
        self.resetting = active
        if active:  # spec §6: reset clears ACK, host response and loader frame
            self.req = self.staged = self.out = self.waited = 0
            self.pending = None
        else:
            self.out, self.stuck = self.stuck, 0

    def _write(self, v):
        self._hit("ui", v)
        assert 0 <= v < 32, v
        req, n = v >> 4, v & 15
        if self.resetting or req == self.req:
            assert not (self.resetting and req), "REQ must stay 0 during reset"
            self.staged = n
            return
        assert self.pending is None, "REQ toggled before ACK"
        assert self.script, "unscripted transfer"
        exp, resp, delay = self.script.pop(0)
        assert n == self.staged == exp, (n, self.staged, exp)
        self.req, self.pending, self.waited = req, (req << 4 | resp, delay), 0
        self.toggles.append(self.clock.steps)

    def _read(self):
        self._hit("uo")
        elapsed = self.waited
        self.waited += 1
        assert self.waited <= self.budget, "poll budget exceeded"
        self.clock.steps += 1
        if self.pending:
            response, delay = self.pending
            if delay is not None and elapsed >= delay:
                self.out, self.pending = response, None
        return self.out

    def late_ack(self):
        self.out, self.pending = self.pending[0], None


def script(text, tail=(), delay=lambda i: 0):
    """Transfers of `text`; the last len(tail) responses are `tail`, others 0."""
    nibbles = [int(digit, 16) for digit in text.replace(" ", "")]
    responses = [0] * (len(nibbles) - len(tail)) + list(tail)
    return [(nibble, response, delay(i))
            for i, (nibble, response) in enumerate(zip(nibbles, responses))]


STATUS_05 = ("00 00", (0, 5))  # code 0, dummy replies 0x0 / 0x5


@pytest.fixture
def rig(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(rp2350_loader, "time", clock)
    board = TimedBoard(clock)
    host = rp2350_loader.Loader(board)
    host.reset()
    return host, board, clock


def assert_inert(host, board):
    n = len(board.log)
    for call in (host.stop, host.status, host.run, host.try_receive,
                 lambda: host.try_send(0x55), lambda: host.load(IMAGE)):
        with pytest.raises(RuntimeError):
            call()
        assert board.log[n:] == []


def reset_and_reload(host, board):
    host.reset()
    board.script = script(TRANSCRIPT, (0, 5, 0, 0), lambda i: i * 37 % 100)
    host.load(IMAGE)
    host.run()
    assert board.script == [] and board.pending is None


@pytest.mark.parametrize("start", [100, TICKS_PERIOD - 50], ids=["no-wrap", "wrap-in-wait"])
def test_ack_99ms_accepted_100ms_times_out(rig, start):
    host, board, clock = rig
    for delay in (99, 100):
        board.script = script(*STATUS_05, lambda i: delay if i == 3 else 0)
        clock.base = (start - 3 - clock.steps) % TICKS_PERIOD  # 3 one-read nibbles precede target
        if delay == 99:
            assert host.status() == 0x05 and board.pending is None
        else:
            with pytest.raises(RuntimeError, match="ACK timed out"):
                host.status()
            assert board.waited == 100
        assert (clock.base + board.toggles[-1]) % TICKS_PERIOD == start and board.script == []


def test_stale_or_late_ack_needs_reset_and_reload(rig):
    host, board, _ = rig
    board.script = script(*STATUS_05, lambda i: None if i == 3 else 0)
    with pytest.raises(RuntimeError, match="ACK timed out"):
        host.status()
    assert board.out == 0x10 and board.waited == 100  # previous ACK and reply nibble
    board.late_ack()
    assert board.out == 0x05
    assert_inert(host, board)
    reset_and_reload(host, board)


@pytest.mark.parametrize("nth", [1, 4], ids=["first-status-read", "dummy-reply-read"])
@pytest.mark.parametrize("exc", [OSError("bus"), KeyboardInterrupt()],
                         ids=["OSError", "KeyboardInterrupt"])
def test_pin_exception_invalidates_session(rig, exc, nth):
    host, board, _ = rig
    board.script = script(*STATUS_05)
    board.arm("uo", nth, exc)
    with pytest.raises(type(exc)) as err:
        host.status()
    assert err.value is exc
    assert_inert(host, board)  # regression: stop() used to write [16, 0]
    reset_and_reload(host, board)


@pytest.mark.parametrize("fault", ["reset_project-OSError", "uo_out-nonzero"])
def test_aborted_reset_leaves_session_invalid(rig, fault):
    host, board, _ = rig
    if fault == "uo_out-nonzero":
        board.stuck = 0x05
        ctx = pytest.raises(RuntimeError, match="reset did not clear")
    else:
        board.arm("rst", 1, OSError("reset"))
        ctx = pytest.raises(OSError)
    with ctx:
        host.reset()
    assert_inert(host, board)
    host.reset()
    board.script = script(*STATUS_05)
    assert host.status() == 0x05 and board.script == []


def test_completed_backpressure_keeps_session(rig):
    host, board, _ = rig
    board.script = script("06 55", (1,)) + script("07", (1,)) + script(*STATUS_05)
    assert host.try_send(0x55) is False
    assert host.try_receive() is None
    assert host.status() == 0x05 and board.script == []
