"""Independent clock-sampled pin oracle for one generic 24-bit SPI transaction."""

TX = bytes.fromhex("96 2d b1")
RX = bytes.fromhex("53 c6 8b")
SCK, MOSI, CS = 1, 2, 8
DRIVE_MASK = SCK | MOSI | CS


class ProtocolError(AssertionError):
    def __init__(self, rule, detail):
        self.rule = rule
        super().__init__(f"{rule}: {detail}")


def require(ok, rule, detail):
    if not ok:
        raise ProtocolError(rule, detail)


def selected_interval(samples):
    """Count physical CS assertions even when SCK or MOSI is undriven."""
    asserted = [bool(oe & CS) and not (pins & CS) for pins, oe, _ in samples]
    starts = [tick for tick, low in enumerate(asserted) if low and (tick == 0 or not asserted[tick - 1])]
    require(len(starts) == 1, "CS_COUNT", starts)
    start = starts[0]
    end = next((tick for tick in range(start + 1, len(samples)) if not asserted[tick]), None)
    require(end is not None, "CS_FRAME", "capture ended during CS")
    # CS is actively driven high at release. HALT can subsequently make the bus high-Z.
    release_pins, release_oe, _ = samples[end]
    require(release_oe & CS and release_pins & CS, "CS_FRAME", "no driven CS release")
    return start, end


def payload(bits):
    number = 0
    for bit in bits:
        number = (number << 1) | bit
    return number.to_bytes(3, "big")


def decode(samples, mode, tx=TX, rx=RX):
    """Decode settled external pins without inspecting program state or instructions."""
    idle, phase = mode >> 1, mode & 1
    start, end = selected_interval(samples)
    before = samples[:start]
    idle_samples = [pins for pins, oe, _ in before if (oe & 15) == DRIVE_MASK and (pins & CS)]
    require(idle_samples, "CS_FRAME", "no driven idle sample before CS")
    require(all((pins & SCK) == idle for pins in idle_samples), "CPOL", "preframe idle clock")
    require(all((oe & 15) == DRIVE_MASK for _, oe, _ in samples[start:end + 1]), "OE", "drive mask")
    require(all(not (oe & SCK) or (pins & SCK) == idle for pins, oe, _ in samples[end:]),
            "CS_EARLY", "clock active after release")

    clocks = [i for i in range(start, end + 1) if (samples[i-1][0] ^ samples[i][0]) & SCK]
    require(len(clocks) >= 48, "MISSING_CLOCK", len(clocks))
    require(len(clocks) <= 48, "EXTRA_CLOCK", len(clocks))
    setup, hold = clocks[0] - start, end - clocks[-1]
    require(setup >= 25, "CS_SETUP", setup)
    require(hold >= 25, "CS_HOLD", hold)
    intervals = [b - a for a, b in zip(clocks, clocks[1:])]
    require(all(24 <= n <= 26 for n in intervals), "HALF_PERIOD", intervals)
    for i, tick in enumerate(clocks):
        expected = idle ^ int(i % 2 == 0)
        require((samples[tick][0] & SCK) == expected, "CPOL", tick)

    captures = clocks[phase::2]
    launches = clocks[::2] if phase else [start] + clocks[1:-1:2]
    mosi = [i for i in range(start, end + 1) if (samples[i-1][0] ^ samples[i][0]) & MOSI]
    miso = [i for i in range(start, end + 1) if samples[i][2] != samples[i-1][2]]
    for moves, window, name in [(mosi, 2, "MOSI"), (miso, 3, "MISO")]:
        for tick in captures:
            require(not any(tick - window < m <= tick for m in moves), name + "_SETUP", tick)
            require(not any(tick < m < tick + window for m in moves), name + "_HOLD", tick)
    windows = list(zip(launches, captures))
    for move in mosi:
        if move <= captures[-1]:
            require(any(lo <= move <= hi - 2 for lo, hi in windows), "MOSI_LAUNCH", move)

    sent = payload((samples[i][0] >> 1) & 1 for i in captures)
    received = payload(samples[i][2] for i in captures)
    require(sent == tx, "MOSI_PAYLOAD", sent.hex())
    require(received == rx, "MISO_PAYLOAD", received.hex())
    return {
        "start": start, "end": end, "clock_edges": clocks, "capture_edges": captures,
        "mosi_hex": sent.hex(), "miso_hex": received.hex(), "setup": setup, "hold": hold,
        "min_half": min(intervals), "max_half": max(intervals),
    }


def negative_controls(samples, mode, clean):
    clocks = clean["clock_edges"]
    selected, released = clean["start"], clean["end"]
    capture = clean["capture_edges"][7]

    def changed(lo, hi, field, bit):
        result = list(samples)
        for tick in range(lo, hi):
            values = list(result[tick])
            values[field] ^= bit
            result[tick] = tuple(values)
        return result

    def reversed_bits(word):
        return bytes(int(f"{byte:08b}"[::-1], 2) for byte in word)

    def with_extra_cs(where, oe):
        idle = mode >> 1
        low = (idle, oe, 1)
        high_z = (idle, 0, 1)
        return [low, high_z] + list(samples) if where == "pre" else list(samples) + [high_z, low]

    # Nine waveform perturbations and two changed expected bit streams.
    middle = clocks[3] + 4
    end_early = clocks[9] + 4
    trials = [
        ("EXTRA_CLOCK", changed(middle, middle + 2, 0, SCK), TX, RX),
        ("MISSING_CLOCK", changed(clocks[4], clocks[5], 0, SCK), TX, RX),
        ("CS_EARLY", changed(end_early, released, 0, CS), TX, RX),
        ("CS_SETUP", changed(selected, clocks[0] - 24, 0, CS), TX, RX),
        ("CS_HOLD", changed(clocks[-1] + 24, released, 0, CS), TX, RX),
        ("MOSI_SETUP", changed(capture - 1, capture, 0, MOSI), TX, RX),
        ("MOSI_HOLD", changed(capture + 1, capture + 2, 0, MOSI), TX, RX),
        ("MISO_SETUP", changed(capture - 2, capture - 1, 2, 1), TX, RX),
        ("MISO_HOLD", changed(capture + 1, capture + 2, 2, 1), TX, RX),
        ("MOSI_PAYLOAD", samples, reversed_bits(TX), RX),
        ("MISO_PAYLOAD", samples, TX, reversed_bits(RX)),
    ]
    observed = {}

    def reject(label, rule, trace, tx, rx):
        try:
            decode(trace, mode, tx, rx)
        except ProtocolError as exc:
            require(exc.rule == rule, "WRONG_NEGATIVE_REASON", (label, rule, exc.rule))
            observed[label] = str(exc)
        else:
            raise AssertionError("Checker accepted " + label)

    for rule, trace, tx, rx in trials:
        reject(rule, rule, trace, tx, rx)
    for where in ("pre", "post"):
        for name, oe in (("all_driven", DRIVE_MASK), ("cs_only", CS)):
            reject(f"{where}_{name}", "CS_COUNT", with_extra_cs(where, oe), TX, RX)
    drop_mosi = changed(capture, capture + 1, 1, MOSI)
    reject("partial_oe_inside", "OE", drop_mosi, TX, RX)
    # High-Z after HALT and before initial idle can read back either pin level.
    high_z = [(0, 0, 0), (15, 0, 1)]
    padded = decode(high_z + list(samples) + high_z, mode)
    require(padded["mosi_hex"] == clean["mosi_hex"] and padded["miso_hex"] == clean["miso_hex"],
            "HIGH_Z_PADDING", "high-Z samples changed the payload")
    return observed
