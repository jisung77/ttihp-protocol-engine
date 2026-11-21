"""Generate one Standard-mode I²C write for the existing single-context ISA."""
import argparse


def build_source(address: int, data: int) -> str:
    if type(address) is not int or not 0x08 <= address <= 0x77:
        raise ValueError('address must be a non-reserved 7-bit address (0x08..0x77)')
    if type(data) is not int or not 0 <= data <= 0xff:
        raise ValueError('data must be a byte (0x00..0xff)')

    # SDA is pin 0 and SCL is pin 1. DATA stays zero, so OE alone pulls a line low.
    lines = [
        f'# I2C write to 0x{address:02x}, data 0x{data:02x}, single mode at 50 MHz',
        'DATA 0', 'TLO 255', 'THI 0',
        'OE 1', 'DELAY', 'COMMIT 1  # START after an idle interval',
        'OE 3', 'DELAY', 'COMMIT 2  # hold START before pulling SCL low',
    ]
    for label, byte in (('address', address << 1), ('data', data)):
        lines.append(f'\n# {label} byte 0x{byte:02x}, most significant bit first')
        for index in range(7, -1, -1):
            pull_sda = 0 if byte & (1 << index) else 1
            lines.extend((
                f'OE {2 | pull_sda}  # bit {index}', 'COMMIT 1', 'DELAY',
                f'OE {pull_sda}', 'COMMIT 2', 'WAIT HIGH 1 FOREVER', 'DELAY',
                f'OE {2 | pull_sda}', 'COMMIT 2',
            ))
        lines.extend((
            f'\n# Release SDA for {label} ACK and clock the target response',
            'OE 2', 'COMMIT 1', 'DELAY',
            'OE 0', 'COMMIT 2', 'WAIT HIGH 1 FOREVER', 'SHIN 0 MSB', 'PUSH',
        ))
        if label == 'address':
            # Test the same stable ACK slot once. Branch only after SCL falls.
            lines.extend(('TLO 0', 'WAIT LOW 0 FINITE', 'TLO 255'))
        lines.extend(('DELAY', 'OE 2', 'COMMIT 2'))
        if label == 'address':
            lines.append('BR TIMEOUT send_stop  # address NACK skips the data byte')

    lines.extend((
        '\nsend_stop:', 'OE 3', 'COMMIT 1', 'DELAY',
        'OE 1', 'COMMIT 2', 'WAIT HIGH 1 FOREVER',
        'OE 0', 'DELAY', 'COMMIT 1  # STOP: release SDA while SCL is high', 'HALT',
    ))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('address', type=lambda n: int(n, 0))
    parser.add_argument('data', type=lambda n: int(n, 0))
    args = parser.parse_args()
    try:
        source = build_source(args.address, args.data)
    except ValueError as err:
        parser.error(str(err))
    print(source, end='')


if __name__ == '__main__':
    main()
