"""Decode one controller write from 20 ns samples of (DUT output, DUT OE, target low)."""

from collections import defaultdict
from itertools import pairwise

SDA, SCL, TICK_NS = 1, 2, 20
THRESHOLDS_TICKS = {
    "bus_free_before_start_min": 235, "start_hold_min": 200, "stop_setup_min": 200,
    "scl_low_min": 235, "scl_high_min": 200, "scl_period_min": 500,
    "sda_setup_min": 13, "master_data_valid_max": 172,
}
INTERVALS = ("scl_low", "scl_high", "scl_period", "sda_setup", "data_sda_setup",
             "ack_sda_setup", "master_data_valid", "target_ack_valid")
SINGLE = ("bus_free_before_start", "start_hold", "stop_setup", "post_stop_idle",
          "trace_length", "target_scl_low")


def _last_change(levels, fall, rise):
    return next((t for t in range(rise, fall, -1) if levels[t] != levels[t - 1]), fall)


def check_bus(rows, expected_bytes, expected_acks):
    """Return wire bytes, ACK bits, timing extrema, and errors ordered by capture tick."""
    errors, warnings, samples = [], [], defaultdict(list)
    metrics = dict(master_drives_sda_high_ticks=0, master_drives_scl_high_ticks=0,
                   dut_oe_upper_bits_ticks=0, target_scl_stretch_events_count=0)
    result = dict(bytes=[], acks=[], start_tick=None, stop_tick=None, pre_stop_rise_tick=None,
                  n_scl_pulses=0, leftover_bits=0, n_ticks=0, thresholds_ticks=dict(THRESHOLDS_TICKS))

    def record(key, value, tick):
        samples[key].append(value)
        if value < THRESHOLDS_TICKS.get(key + "_min", 0) or value > THRESHOLDS_TICKS.get(key + "_max", value):
            label = key.replace("_", " ").replace("scl", "SCL").replace("sda", "SDA")
            errors.append((tick, f"{label} {value} ticks outside timing limit"))

    def finish():
        def put(key, ticks):
            metrics[key + "_ticks"] = ticks
            metrics[key + "_ns"] = None if ticks is None else ticks * TICK_NS

        for key in SINGLE:
            put(key, samples[key][0] if samples[key] else None)
        for key in INTERVALS:
            put("min_" + key, min(samples[key], default=None))
            put("max_" + key, max(samples[key], default=None))
        issues = [("expectation: " if t < 0 else f"tick {t} ({t * TICK_NS} ns): ") + msg
                  for t, msg in sorted(errors, key=lambda item: item[0])]
        return dict(result, ok=not issues, errors=issues, warnings=warnings, metrics=metrics)

    try:
        wire_bytes, acks = list(map(int, expected_bytes)), list(map(int, expected_acks))
        if (len(wire_bytes) not in (1, 2) or len(wire_bytes) != len(acks) or wire_bytes[0] & 1
                or any(b < 0 or b > 255 for b in wire_bytes) or any(a not in (0, 1) for a in acks)
                or len(acks) == 2 and acks[0] == 1):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        errors.append((-1, "expected a write address, no data after address NACK, and one ACK bit per byte"))
        return finish()

    try:
        raw = list(rows)
    except TypeError:
        errors.append((0, "trace is not iterable"))
        return finish()
    n = result["n_ticks"] = len(raw)
    record("trace_length", n, 0)
    bus, master_sda, target_sda, enable = [], [], [], []
    was_stretched = False
    target_scl_ticks = 0
    for i, row in enumerate(raw):
        try:
            out, oe, target = map(int, row)
            if min(out, oe, target) < 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            errors.append((i, "expected nonnegative output, OE and target-low masks"))
            return finish()
        master = oe & ~out
        bus.append(~(master | target) & (SDA | SCL))
        master_sda.append(bool(master & SDA))
        target_sda.append(bool(target & SDA))
        enable.append(oe)
        for key, active, label in (
                ("master_drives_sda_high_ticks", oe & out & SDA, "master drives SDA high"),
                ("master_drives_scl_high_ticks", oe & out & SCL, "master drives SCL high"),
                ("dut_oe_upper_bits_ticks", oe >> 2, "dut_oe[7:2] nonzero")):
            if active:
                if metrics[key] == 0:
                    errors.append((i, label))
                metrics[key] += 1
        if target >> 2 and not warnings:
            warnings.append(f"tick {i}: unused target mask bits set")
        stretched = bool(target & SCL and not master & SCL)
        metrics["target_scl_stretch_events_count"] += stretched and not was_stretched
        target_scl_ticks += bool(target & SCL)
        was_stretched = stretched
    record("target_scl_low", target_scl_ticks, 0)
    if n < 2:
        errors.append((0, "trace too short"))
        return finish()

    if bus[0] != 3:
        errors.append((0, "bus not idle at trace start"))
    sda = [pins & SDA for pins in bus]
    events = []
    for i, (prev, now) in enumerate(pairwise(bus), 1):
        if not (prev ^ now) & SDA:
            continue
        if prev & now & SCL:
            events.append(i)
        elif (prev ^ now) & SCL and master_sda[i] != master_sda[i - 1]:
            errors.append((i, "controller changed SDA and SCL together"))
        elif (prev ^ now) & SCL:
            warnings.append(f"tick {i}: target changed SDA at a clock edge")
    start = result["start_tick"] = next((t for t in events if not sda[t]), None)
    if start is None:
        errors.append((next((t for t, pins in enumerate(bus) if pins != 3), n), "missing START"))
        return finish()
    stop = result["stop_tick"] = next((t for t in reversed(events) if t > start and sda[t]), None)
    errors.extend((t, "unexpected SDA transition while SCL high") for t in events if t not in (start, stop))
    busy = [t for t in range(start) if bus[t] != 3]
    if busy:
        errors.append((busy[0], "bus activity before START"))
    record("bus_free_before_start", start - (busy[-1] + 1 if busy else 0), start)
    if not master_sda[start] or target_sda[start]:
        errors.append((start, "START must be driven solely by controller"))
    end = n if stop is None else stop
    if stop is None:
        errors.append((n, "missing STOP"))
    else:
        if not master_sda[stop - 1] or target_sda[stop - 1]:
            errors.append((stop, "STOP must be released solely by controller"))
        busy_after = next((t for t in range(stop, n) if bus[t] != 3 or enable[t] & 3), None)
        if busy_after is not None:
            errors.append((busy_after, "bus not idle and released after STOP"))
        record("post_stop_idle", n - stop, stop)

    edges = [t for t in range(start + 1, end) if (bus[t] ^ bus[t - 1]) & SCL]
    falls, rises = edges[::2], edges[1::2]  # START always occurs with SCL high.
    if not falls:
        errors.append((end, "no SCL clock after START"))
        return finish()
    record("start_hold", falls[0] - start, falls[0])
    complete = min(len(rises), len(falls) - 1)
    result.update(n_scl_pulses=complete, leftover_bits=complete % 9)
    expected = 9 * len(wire_bytes)
    if complete > expected:
        errors.append((rises[expected], f"extra SCL clock: {complete} pulses, expected {expected}"))
    elif complete < expected:
        errors.append((rises[complete] if complete < len(rises) else end, f"{complete} pulses, expected {expected}"))

    for i, (fall, rise) in enumerate(zip(falls, rises)):
        record("scl_low", rise - fall, rise)
        if i:
            record("scl_period", rise - rises[i - 1], rise)
        if i == complete:  # The last rise before STOP has no falling edge and carries no bit.
            result["pre_stop_rise_tick"] = rise
            if stop is not None:
                record("stop_setup", stop - rise, stop)
            break
        next_fall = falls[i + 1]
        record("scl_high", next_fall - rise, next_fall)
        change = _last_change(sda, fall, rise)
        setup = rise - change
        ack_slot = i % 9 == 8
        record("sda_setup", setup, rise)
        record("ack_sda_setup" if ack_slot else "data_sda_setup", setup, rise)
        if ack_slot:
            record("target_ack_valid", change - fall, rise)
        else:
            record("master_data_valid", _last_change(master_sda, fall, rise) - fall, rise)
        for t in range(max(0, rise - THRESHOLDS_TICKS["sda_setup_min"]), next_fall):
            if (ack_slot and enable[t] & SDA) or (not ack_slot and target_sda[t]):
                errors.append((t, "controller did not release SDA at ACK" if ack_slot else "target contention on data"))
                break

    for group in range(complete // 9):
        offset = group * 9
        value = sum(sda[t] << (7 - j) for j, t in enumerate(rises[offset:offset + 8]))
        ack = sda[rises[offset + 8]]
        result["bytes"].append(value)
        result["acks"].append(ack)
        if group < len(wire_bytes) and value != wire_bytes[group]:
            errors.append((rises[offset], f"byte {group + 1} mismatch: got {value:#04x}, expected {wire_bytes[group]:#04x}"))
        if group < len(acks) and ack != acks[group]:
            errors.append((rises[offset + 8], f"ACK of byte {group + 1} mismatch: got {ack}, expected {acks[group]}"))
    return finish()
