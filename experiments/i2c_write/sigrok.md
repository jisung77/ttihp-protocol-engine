# Replay the I²C pin captures with sigrok

The RTL test artifact `test-results.zip` contains `i2c-*.bin` pin captures. Each 50 MHz sample contains three bytes in order: controller output, controller output enable, and the target's low-drive mask. Bit 0 is SDA and bit 1 is SCL. The adapter resolves the pull-ups, rejects active-high drives, and passes the samples to libsigrokdecode's stock I²C decoder. It prints the decoder's annotations using 7-bit addresses and counts SCL rising edges directly from the samples.

From the repository root on Ubuntu 24.04:

```sh
sudo apt-get install build-essential pkg-config python3-dev libsigrokdecode-dev unzip
cc -std=c99 -O2 -Wall experiments/i2c_write/sigrok_decode.c -o /tmp/i2c-replay \
    $(pkg-config --cflags --libs libsigrokdecode)
unzip test-results.zip -d i2c-ci
pins=$(dirname "$(find i2c-ci -type f -name i2c-write.bin -print -quit)")
for name in write address_nack held_clock_host_abort; do
    /tmp/i2c-replay "$pins/i2c-$name.bin" || exit 1
done
```

[실행 확인] The replay source was built with TinyCC on Ubuntu 24.04 WSL1 using the extracted Ubuntu package `libsigrokdecode4 0.5.3-4build3`. The standard system-package build command above uses `cc` and `pkg-config`. With an extracted package, the replay program also accepts the decoder directory as its second argument. The tested `i2c/pd.py` SHA-256 was `bd28a545d42d61b2c82688d8a3c056d09af3c968890312159fbc01dc807ea6ba`.

## 사람이 직접 확인할 지점

- `write` decodes START, write address `52`, ACK, data `A5`, ACK and STOP. The SCL count is 19, including the rise before STOP.
- `address_nack` decodes START, write address `52`, NACK and STOP with 10 SCL rises.
- `held_clock_host_abort` produces only the START annotation. The trace ends with SCL low after four SCL rises.
