## How it works

The engine fetches 16-bit instructions from one IHP single-port SRAM. In single mode, one program can occupy all 256 words and drive all eight pins. In dual mode, A and B each have 128 words, a TX/RX FIFO pair and four output pins. A drives pins 7–4 and B drives pins 3–0. Both can read all eight pins. Each instruction can prepare or commit pin data and output enables, wait on a pin or timer, shift one bit, branch, or transfer one byte through a four-entry FIFO. The host can refill TX and drain RX while the engine runs.

In single mode, a RUN starts at address zero. The first clock after RUN fetches the instruction and the following clock executes it. Program inputs pass through two synchronization stages. HALT and instruction faults release the affected context's output enables. The pin data remains available on the digital output path.

## How to test

Select the design and assert reset with REQ low. For the single-mode example below, drive ui[7:5] to 000. For dual mode, drive 001. Keep the chosen strap stable and REQ low through the third rising edge after both select and reset release are active. To send a nibble, drive it on `ui[3:0]`, toggle REQ on `ui[4]`, and hold the nibble until ACK on `uo[4]` matches REQ. Read the response on `uo[3:0]`. Send the high nibble first in every byte and the low byte first in every 16-bit value.

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

## Running two programs

After the third rising edge, drive ui[6] low for A or high for B before sending the first nibble. Hold it through the whole command and payload. Load and finish both images before starting either context. Each RUN starts at the beginning of its image. A fetches on even edges and B on odd edges, counting the first active edge as one. A busy or idle context keeps its assigned slots. Observe READY, RUNNING and FAULT for the target selected by the last first command nibble. The concurrent example drives UART TX on A while B clocks SPI. [Dual-mode timing and pin assignments](../spec/spec.md#s9)

## External hardware

Use a clock source and a host with four data outputs, one request output and five return inputs for the nibble and ACK. READY, RUNNING and FAULT are available on the three remaining dedicated outputs. A scope or logic analyzer can observe the program pins. The design clock target is 50 MHz.
