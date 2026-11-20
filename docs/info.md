## How it works

The engine runs one program of up to 256 16-bit words from an IHP single-port SRAM. Each instruction can prepare or commit pin data and output enables, wait on a pin or timer, shift one bit, branch, or transfer one byte through a four-entry FIFO. The host can refill TX and drain RX while the engine runs.

A RUN starts at address zero. The first clock after RUN fetches the instruction and the following clock executes it. Program inputs pass through two synchronization stages. HALT and instruction faults release all eight output enables. The pin data remains available on the digital output path.

## How to test

Select the design and assert reset with REQ low. After releasing reset, keep REQ low through two rising clock edges. To send a nibble, drive it on `ui[3:0]`, toggle REQ on `ui[4]`, and hold the nibble until ACK on `uo[4]` matches REQ. Read the response on `uo[3:0]`. Send the high nibble first in every byte and the low byte first in every 16-bit value.

Send the following hexadecimal bytes to drive pin 0 high and then release it.

| Host command | Bytes |
|---|---|
| Begin a four-word image | `01 04 00` |
| Prepare pin data | `02 01 08` |
| Prepare pin direction | `02 01 10` |
| Commit pin 0 | `02 01 18` |
| Halt | `02 00 00` |
| Confirm checksum | `03 03 30` |
| Run | `04` |

Each command should finish with response zero. READY rises after END. Count rising edges from the one acknowledging RUN. Pin 0 begins driving high on edge +6 and returns to high impedance on edge +8. A pulldown makes the release visible on a scope.

## External hardware

Use a clock source and a host with four data outputs, one request output and five return inputs for the nibble and ACK. READY, RUNNING and FAULT are available on the three remaining dedicated outputs. A scope or logic analyzer can observe the program pins. The design clock target is 50 MHz.
