"""Run the real MicroPython Loader against the v0.2 pin-level reference (§§6, 9)."""
from types import SimpleNamespace

import pytest

from host import rp2350_loader
from model.protocol_machine import ProtocolMachine


class ClockedBoard:
    """Tick the public TT pins at 1 MHz; polling an output advances time by 1 µs."""

    def __init__(self):
        self.machine = ProtocolMachine()
        self.ui_in = SimpleNamespace(value=0)
        self.uio_oe_pico = SimpleNamespace(value=0)
        self.uo_out = self
        self.rst_n = 0
        self.cycles = 0
        self.row = {"uo_out": 0}
        self.accepted = []
        self.boot = []

    def reset_project(self, active):
        self.rst_n = int(not active)
        if not active:
            self.boot = []

    def sleep_us(self, count):
        for _ in range(count):
            self.cycles += 1
            ui = self.ui_in.value
            old_ack = self.row["uo_out"] & 16
            self.row = self.machine.tick(ui_in=ui, uio_in=0x80, rst_n=self.rst_n, ena=1)
            if self.rst_n:
                if len(self.boot) < 3:
                    self.boot.append(ui)
                if self.row["uo_out"] & 16 != old_ack:
                    self.accepted.append((ui >> 6 & 1, ui & 15))

    @property
    def value(self):
        self.sleep_us(1)
        return self.row["uo_out"]

    def ticks_ms(self):
        return self.cycles // 1000

    @staticmethod
    def ticks_diff(new, old):
        return new - old


@pytest.fixture
def board(monkeypatch):
    board = ClockedBoard()
    monkeypatch.setattr(rp2350_loader, "time", board)
    return board


def test_dual_board_concurrent_programs_and_reset(board):
    driver = rp2350_loader.Loader(board, dual=True)
    driver.reset()
    assert board.boot == [0x20] * 3 and board.row["dual_mode"]
    # A and B drive their own bit, then WAIT LOW FOREVER on pad 7 (held high).
    # B also samples pad 7 and pushes the resulting 0x01 to its RX FIFO.
    a = bytes.fromhex("10 08 10 10 10 18 27 30 00 00")
    b = bytes.fromhex("01 08 01 10 01 18 07 40 00 68 27 30 00 00")
    driver.load(a)
    count_a = len(board.accepted)
    driver.load(b, target=1)
    assert [cid for cid, _ in board.accepted[:count_a]] == [0] * (16 + 6 * 5)
    assert [cid for cid, _ in board.accepted[count_a:]] == [1] * (16 + 6 * 7)

    for value in range(4):
        assert driver.try_send(value)
    assert not driver.try_send(4)
    assert driver.try_send(0xa6, target=1)
    driver.run()
    driver.run(target=1)
    board.sleep_us(32)
    assert (board.row["uio_out"], board.row["uio_oe"]) == (0x11, 0x11)
    assert driver.status() == 0x0c and driver.status(target=1) == 0x0f
    assert driver.try_receive() is None
    assert driver.try_receive(target=1) == 1
    assert driver.try_receive(target=1) is None
    driver.stop(target=1)
    assert (board.row["uio_out"], board.row["uio_oe"]) == (0x10, 0x10)
    assert driver.status() == 0x0c and driver.status(target=1) == 0x05

    # Partial B frame: the next reset must clear the selector before E1.
    driver._nibble(0, target=1)
    driver.reset()
    assert board.boot == [0x20] * 3 and board.row["dual_mode"]
    assert board.row["uio_oe"] == 0
    assert driver.status() == driver.status(target=1) == 1


@pytest.mark.parametrize(("dual", "cid", "limit"), [(False, 0, 256), (True, 0, 128), (True, 1, 128)])
def test_board_image_size_limits_and_invalid_target(board, dual, cid, limit):
    driver = rp2350_loader.Loader(board, dual=dual)
    driver.reset()
    assert board.boot == [0x20 if dual else 0] * 3
    driver.load(bytes(2 * limit), target=cid)
    assert driver.status(target=cid) == 5
    accepted = len(board.accepted)
    with pytest.raises(ValueError):
        driver.load(bytes(2 * (limit + 1)), target=cid)
    for target in (-1, 2, "B", 1.5) if dual else (1, 2):
        with pytest.raises(ValueError):
            driver.run(target)
    assert len(board.accepted) == accepted and driver.synced
    assert driver.status(target=cid) == 5
