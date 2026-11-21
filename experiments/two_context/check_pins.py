"""Decode two-context timing traces using only external pad observations."""


def require(ok, message, tick):
    if not ok:
        raise ValueError(f"clock {tick}: {message}")


def uart_frames(rows, expected):
    bit_ticks = 434
    line = [((out >> 4) & 1) if oe & 0x10 else None for out, oe, _ in rows]
    edges = [t for t in range(1, len(line))
             if line[t - 1] is not None and line[t] is not None and line[t - 1] != line[t]]
    pos = 0
    starts, received, lengths = [], [], []
    earliest_next = 0
    max_boundary_error = 0
    for byte in expected:
        require(pos < len(edges), "missing UART start", len(line))
        start = edges[pos]
        require(start >= earliest_next and line[start] == 0, "early or invalid UART start", start)
        starts.append(start)
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]
        centers = [start + (i * 2 + 1) * bit_ticks // 2 for i in range(10)]
        require(centers[-1] < len(line), "truncated UART frame", start)
        samples = [line[t] for t in centers]
        require(samples == bits, f"UART 8N1 samples {samples}, expected {bits}", start)
        received.append(sum(samples[i + 1] << i for i in range(8)))
        previous_tick, previous_bit = start, 0
        pos += 1
        for i in range(1, 10):
            if bits[i] == bits[i - 1]:
                continue
            require(pos < len(edges), "missing UART bit transition", previous_tick)
            t = edges[pos]
            elapsed, bit_count = t - previous_tick, i - previous_bit
            nominal = bit_count * bit_ticks
            require(line[t] == bits[i] and abs(elapsed - nominal) * 100 <= nominal,
                    f"UART transition for bit {i} took {elapsed}/{bit_count} clocks", t)
            # Absolute error from START is bounded by 1% of one ideal 115200-baud bit.
            error = abs((t - start) * 115200 - i * 50000000)
            max_boundary_error = max(max_boundary_error, error)
            require(error * 100 <= 50000000,
                    f"UART boundary {i} drift from START", t)
            lengths.append(elapsed / bit_count)
            previous_tick, previous_bit = t, i
            pos += 1
        min_high = ((10 - previous_bit) * bit_ticks * 99 + 99) // 100
        earliest_next = previous_tick + min_high
        require(earliest_next <= len(line) and all(v is not None for v in line[start:earliest_next]),
                "UART drive interrupted before the end of STOP", start)
    require(pos == len(edges), "unexpected UART transition", edges[pos] if pos < len(edges) else len(line))
    return starts, received, lengths, edges, max_boundary_error / 115200


def spi_frames(rows, mode):
    idle, phase = mode >> 1, mode & 1
    spans, edges = [], []
    start = None
    for t, (out, oe, _) in enumerate(rows):
        require((oe & ~0x1b) == 0, f"unexpected driven pins OE={oe:#x}", t)
        if not (oe & 8):
            require(start is None, "CS became undriven while selected", t)
            continue
        selected, clock = (out & 8) == 0, out & 1
        require(oe & 3 == 3, "CS driven without SCK and MOSI", t)
        if selected and start is None:
            require(t > 0 and rows[t-1][1] & 8 and rows[t-1][0] & 8 and clock == idle,
                    "CS asserted without driven idle state", t)
            start, edges = t, []
        if selected and t > start and clock != (rows[t-1][0] & 1):
            edges.append(t)
        if not selected:
            require(clock == idle, "selected polarity disagrees with idle clock", t)
            if start is not None:
                require(len(edges) == 16, f"SPI transfer has {len(edges)} edges", t)
                spans.append((start, t, edges))
                start = None
    require(start is None, "CS remained active at end of trace", len(rows))
    words, halves, setups, holds = [], [], [], []
    stability = {"MOSI": [32, 32], "MISO": [32, 32]}
    for start, end, edges in spans:
        setup, hold = edges[0] - start, end - edges[-1]
        require(min(setup, hold) >= 25, f"SPI CS setup={setup}, hold={hold}", start)
        setups.append(setup)
        holds.append(hold)
        for i, tick in enumerate(edges[1:], 1):
            interval = tick - edges[i - 1]
            require(24 <= interval <= 26, f"SPI half-period {interval}", tick)
            if i >= 2:
                require(tick - edges[i - 2] == 50, "SPI full period differs from 50", tick)
            halves.append(interval)
        tx, rx = 0, 0
        for tick in edges[phase::2]:
            def sampled(t, signal):
                return (rows[t][0] >> 1) & 1 if signal == "MOSI" else rows[t][2]
            for signal, margin in (("MOSI", 2), ("MISO", 3)):
                bit = sampled(tick, signal)
                require(bit in (0, 1) and all(sampled(t, signal) == bit
                        for t in range(tick-margin, tick+margin+1)), f"{signal} unstable near sample", tick)
                for side, step in enumerate((-1, 1)):
                    count = 0
                    while count < stability[signal][side]:
                        t = tick + (count + 1) * step
                        if not start <= t < end or sampled(t, signal) != bit:
                            break
                        count += 1
                    stability[signal][side] = min(stability[signal][side], count)
            tx = (tx << 1) | sampled(tick, "MOSI")
            rx = (rx << 1) | sampled(tick, "MISO")
        words.append((tx, rx))
    return spans, words, halves, setups, holds, stability


def check(rows, mode, uart=(0x55, 0xa6), mosi=(0x96, 0x53), miso=(0x3d, 0xc6)):
    """Check an after-edge trace with one (output bus, OE bus, MISO bit) per clock."""
    if mode not in range(4) or not uart or not mosi or len(mosi) != len(miso):
        raise ValueError("invalid mode or expected byte sequences")
    starts, received, lengths, uart_edges, boundary_error = uart_frames(rows, uart)
    spans, words, halves, setups, holds, stability = spi_frames(rows, mode)
    require(words == list(zip(mosi, miso)), f"incorrect SPI transfers {words}", len(rows))
    overlap = [t for t in uart_edges if any(start <= t < end for start, end, _ in spans)]
    require(overlap, "no UART transition during selected SPI", len(rows))
    data_overlap = sum(max(0, min(end, frame + 9*434) - max(start, frame + 434))
                       for start, end, _ in spans for frame in starts)
    require(data_overlap > 0, "SPI never selected during UART data bits", len(rows))
    return {"max_uart_boundary_error_clocks": boundary_error, "stable_before_after_clocks_capped32": stability,
            "uart_bytes": received, "uart_starts": starts,
            "uart_bit_ticks_min_max": [min(lengths), max(lengths)] if lengths else [],
            "spi_mosi": [tx for tx, _ in words], "spi_miso": [rx for _, rx in words],
            "spi_half_ticks": sorted(set(halves)), "spi_setup_ticks": setups, "spi_hold_ticks": holds,
            "spi_select_windows": [[a, b] for a, b, _ in spans], "uart_edges_under_cs": overlap,
            "uart_data_clocks_under_cs": data_overlap}
