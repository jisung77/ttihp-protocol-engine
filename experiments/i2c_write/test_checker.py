"""Independent digital Standard-mode bus vectors at one sample per 20 ns."""
import pytest

from experiments.i2c_write.check_bus import check_bus


SDA, SCL = 1, 2
ADDRESS_WRITE, DATA = 0xA4, 0xA5


def assert_transfer(rows, byte_values, ack_bits):
    report = check_bus(rows, byte_values, ack_bits)
    assert report["ok"] and not report["errors"], report["errors"]
    assert report["bytes"] == byte_values and report["acks"] == ack_bits


def assert_rejected(rows):
    report = check_bus(rows, [ADDRESS_WRITE, DATA], [0, 0])
    assert not report["ok"] and report["errors"], report


def write_trace(acks=(0, 0), *, data=DATA, low=310, high=310, setup=None,
                idle=300, start_hold=300, stop_setup=300, finish=True, extra_pulse=False):
    """Return raw (output, enable, target-low mask) samples and address-ACK high span."""
    rows = []

    def hold(clocks, enable=0, target=0):
        rows.extend([(0, enable, target)] * clocks)

    hold(idle)
    hold(start_hold, SDA)
    master, target = SDA, 0
    for index, (byte, ack) in enumerate(zip((ADDRESS_WRITE, data), acks)):
        # During the first few clocks after SCL falls, retain the previous SDA driver.
        for shift in range(7, -1, -1):
            data_setup = low - 20 if setup is None else setup
            hold(low - data_setup, SCL | master, target)
            master = 0 if byte & (1 << shift) else SDA
            target = 0
            hold(data_setup, SCL | master)
            hold(high, master)
        hold(20, SCL | master)
        master, target = 0, SDA if ack == 0 else 0
        hold(low - 20, SCL, target)
        if index == 0:
            first_ack = slice(len(rows), len(rows) + high)
        hold(high, master, target)

    hold(20, SCL, target)
    hold(low - 20, SCL | SDA)  # prepare SDA low before the rise used for STOP
    if extra_pulse:
        hold(high, SDA)
        hold(low, SCL | SDA)
    hold(stop_setup, SDA)
    if finish:
        hold(300)
    return rows, first_ack


@pytest.mark.parametrize("acks", [(0, 0), (1,), (0, 1)])
def test_independent_transfers(acks):
    rows, _ = write_trace(acks)
    assert_transfer(rows, [ADDRESS_WRITE, DATA][:len(acks)], list(acks))


@pytest.mark.parametrize(("low", "high"), [(235, 265), (300, 200)])
def test_standard_mode_timing_boundaries(low, high):
    rows, _ = write_trace(low=low, high=high, idle=235,
                          start_hold=200, stop_setup=200)
    assert_transfer(rows, [ADDRESS_WRITE, DATA], [0, 0])


@pytest.mark.parametrize("changes", [
    pytest.param({"data": DATA ^ 1}, id="wrong-payload-bit"),
    pytest.param({"finish": False}, id="missing-stop"),
    pytest.param({"extra_pulse": True}, id="extra-full-pulse-before-stop"),
    pytest.param({"idle": 234}, id="short-bus-free"),
    pytest.param({"start_hold": 199}, id="short-start-hold"),
    pytest.param({"stop_setup": 199}, id="short-stop-setup"),
    pytest.param({"low": 234}, id="short-scl-low"),
    pytest.param({"high": 199}, id="short-scl-high"),
    pytest.param({"low": 260, "high": 239}, id="499-clock-period"),
    pytest.param({"setup": 12}, id="240ns-data-setup"),
])
def test_malformed_trace_is_rejected(changes):
    rows, _ = write_trace(**changes)
    assert_rejected(rows)


def test_controller_cannot_supply_its_own_ack():
    rows, ack = write_trace()
    # The resolved bus remains low, but the supposed target is now high impedance.
    rows[ack] = [(0, SDA, 0)] * (ack.stop - ack.start)
    assert_rejected(rows)


def test_active_high_drive_on_idle_bus_is_rejected():
    rows, _ = write_trace()
    rows[30] = (SDA, SDA, 0)  # digital SDA stays high while the controller sources it
    assert_rejected(rows)


def test_redundant_sda_drive_mid_ack_is_rejected():
    rows, ack = write_trace()
    # Resolved SDA stays low before, during and after this one-clock ownership error.
    rows[(ack.start + ack.stop) // 2] = (0, SDA, SDA)
    assert_rejected(rows)


def test_stretched_clock_uses_resolved_scl():
    rows, ack = write_trace()
    # First 100 release samples stay low, leaving a valid 210-clock ACK high.
    rows[ack.start:ack.start + 100] = [(0, 0, SDA | SCL)] * 100
    assert_transfer(rows, [ADDRESS_WRITE, DATA], [0, 0])
    # The same master waveform now has only 199 resolved high samples.
    rows[ack.start + 100:ack.start + 111] = [(0, 0, SDA | SCL)] * 11
    assert_rejected(rows)


def test_controller_data_valid_max_on_unstretched_clock():
    # Same 8 µs low and 6.2 µs high: both waveforms meet the 250 ns setup minimum.
    # Only the second exceeds 3.45 µs from SCL falling to a valid controller bit.
    valid, _ = write_trace(low=400, setup=400 - 172)
    late, _ = write_trace(low=400, setup=400 - 173)
    assert_transfer(valid, [ADDRESS_WRITE, DATA], [0, 0])
    assert_rejected(late)


def test_target_must_release_a_controller_driven_zero_bit():
    rows, ack = write_trace()
    low_bit_rise = next(t for t in range(1, ack.start)
                        if rows[t - 1][1] == SCL | SDA and rows[t][1] == SDA)
    tick = low_bit_rise + 1
    assert rows[tick] == (0, SDA, 0)
    rows[tick] = (0, SDA, SDA)  # Both drivers now pull low. Neither wire changes.
    report = check_bus(rows, [ADDRESS_WRITE, DATA], [0, 0])
    assert not report["ok"] and report["errors"][0].startswith(f"tick {tick} ")
