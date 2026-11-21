"""Exercise all byte values in the two-context timing model, using two-byte FIFO bursts."""
import hashlib

from check_pins import check
from schedule import simulate


def run_sweep(uart_program, spi_programs):
    results, expected_uart = [], {}
    for mode, spi_program in enumerate(spi_programs):
        for parity in range(2):
            seen_uart, seen_tx, seen_rx = set(), set(), set()
            max_uart_error = 0
            min_mosi = [32, 32]
            for first in range(1, 256, 2):
                uart = (first, first - 1)
                mosi = tuple((b * 29 + 17) & 0xff for b in uart)
                miso = tuple(b ^ 0xa5 for b in mosi)
                rows, engine = simulate(uart_program, spi_program, mode, parity, (180, 0),
                                        clocks=9000, uart_tx=uart, spi_tx=mosi, spi_rx=miso)
                measured = check(rows, mode, uart, mosi, miso)
                max_uart_error = max(max_uart_error, measured["max_uart_boundary_error_clocks"])
                min_mosi = [min(a, b) for a, b in zip(min_mosi, measured["stable_before_after_clocks_capped32"]["MOSI"])]
                assert engine["spi_fifo"] == list(miso)
                signature = hashlib.sha256(bytes(out & 16 | (oe & 16) >> 4
                                                 for out, oe, _ in rows)).hexdigest()
                key = (first, parity)
                if mode == 0:
                    expected_uart[key] = signature
                else:
                    assert signature == expected_uart[key], (first, mode, parity)
                seen_uart.update(uart)
                seen_tx.update(mosi)
                seen_rx.update(miso)
            assert seen_uart == seen_tx == seen_rx == set(range(256))
            results.append(dict(mode=mode, uart_fetch_parity=parity, trials=128,
                                unique_uart=256, unique_mosi=256, unique_miso=256,
                                max_uart_boundary_error_clocks=max_uart_error,
                                min_mosi_before_after_clocks=min_mosi))
    return results
