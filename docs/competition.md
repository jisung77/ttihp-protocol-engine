# A programmable I/O engine for Tiny Tapeout

**[실행 확인]** One 256 × 16-bit SRAM holds the program. A host can reload it after fabrication to change which pins the chip drives, when it samples them, and what it does on a timeout. The host uses eight input pins and eight return pins. Eight bidirectional pins carry the emulated protocol. UART and SPI are programs for this same hardware. [Pinout](info.md) · [Architecture and ISA](../spec/spec.md) · [Host loader](../host/README.md)

## How an instruction reaches the pins

**[실행 확인]** In single mode the context has 256 words and all eight I/O pins. In dual mode two contexts alternate pipeline slots and use 128 words each. Context A owns the upper four outputs and B the lower four. Each has its own PC, shift state and FIFOs. DATA and OE prepare pin values and output enables. COMMIT applies both under one mask at retirement. WAIT accepts a level or edge from the synchronized input and can time out. DELAY, the shift instructions, branches, a loop counter and FIFO instructions complete the instruction set. [Instruction encoding and edge rules](../spec/spec.md#s3) · [Dual scheduling and ownership](../spec/spec.md#s9)

**[실행 확인]** The fixed loader accepts a length, program words and a checksum before RUN. Reset or deselection requires a reload. The host can query either context while the other continues. The PC presented to the SRAM is relative to the executing context. In dual mode a fetch at PC 127 addresses physical word 127 for A or 255 for B. Both cases were reached through real host transactions in the behavioral SRAM. [Loader protocol](../spec/spec.md#s6) · [Formal proofs and witness replay](../formal/README.md)

## Programs tested on the engine

| Program | What the independent checker observed |
|---|---|
| UART TX and RX | 8N1, LSB first. A nominal bit spans 434 project clocks. At an assumed 50 MHz, this is 50,000,000 / 434 ≈ 115,207 baud. Received frames with invalid stop bits are discarded. [Programs and timing](../examples/README.md) |
| SPI master | Modes 0–3. A target drives MISO from CS and SCK while checking MOSI. With the provided programs a period is 50 clocks, or 1 MHz at an assumed 50 MHz project clock. [Protocol tests](../test/README.md) |
| Concurrent UART + SPI | UART TX on A overlapped each SPI transfer on B. In eight mode/start-phase combinations the host read the expected SPI response from B's RX FIFO. [Measured pin traces](two_context_results.md) |
| I²C write | Single-context, open-drain, one data byte to a 7-bit address. The image is 189 words. ACK, address NACK, data NACK and clock stretching have distinct pin-level checks. External pull-ups are required. [Image generator and test scope](../experiments/i2c_write/README.md) |
| Held-out PS/2 receive | The program was added after the candidate ISA and RTL were frozen. The two historical Git revisions have identical RTL, spec, assembler and ISS for that experiment. It exercises a new wire format entirely in software. [Freeze and replay](../experiments/ps2/README.md) |

**[추론]** CRC remains a design option. These demonstrated wire formats do not require a common CRC operation. A new instruction needs a protocol that fixes its polynomial, initialization, reflection, final XOR and byte order. [Extension decisions](spec_questions.md)

## Verification and physical evidence

**[실행 확인]** An independently authored Python ISS and cocotb compare the implementation to the clocked specification. Separate wire-level receivers decode protocol behavior. Formal properties cover FIFO ordering and flags, pin ownership and both PC bounds. For the PC proofs the SRAM read data, loader word count and checksum are unconstrained. The SRAM pin replay uses the actual behavioral memory and a complete upload. In the 15-mutant dual campaign the frozen suite detected 13/15. After two directed cases were added the augmented suite detected 15/15. [Verification plan](verification_plan.md) · [Mutation-by-mutation results](../mutation/v02/results.md) · [Formal assumptions](../formal/README.md)

**[실행 확인]** The signed-off hardware revision 824c2cb passed 235 Python tests, 29 RTL tests, 26 gate-level tests and 10 Tiny Tapeout precheck items. The gate tests skipped three RTL-internal checks. The toolchain was Tiny Tapeout's ttihp27a action, LibreLane 3.1.0.dev4 and IHP CMOS5L PDK revision 89d42ee6d2fd9b50b86a1aada6fee6673c3e6008. The GDS SHA-256 for that build is a5f4c3402b6202cd5f29204479c36c912b6f62673e155d0225393390406ee12e. Each release records its own rebuilt GDS and checksum. [Official test](https://github.com/jisung77/ttihp-protocol-engine/actions/runs/44905942003) · [Official GDS, precheck and GLS](https://github.com/jisung77/ttihp-protocol-engine/actions/runs/44905941980) · [Release files](https://github.com/jisung77/ttihp-protocol-engine/releases)

**[실행 확인]** The routed design used 8.7759% of the available core. The numerator includes the engine, loader, FIFOs, clock tree and one SRAM footprint. At a 20 ns constraint the smallest setup and hold slacks were +6.6262 ns and +0.0923 ns. One clock fanout and two SRAM load warnings remain. The pre-extension power-grid check warned about disconnected shapes. After the SRAM stripe extension, post-route OpenROAD reported both supply grids connected. The streamed layout has eleven top-level supply stripes and eleven ground stripes contacting the SRAM on Metal4. Magic extraction reported 28 obstruction/metal overlap warnings. Netgen matched the top-level circuits with the SRAM abstracted, and the official KLayout DRC passed. These observations cover distinct checks. [Physical results](two_context_results.md) · [Official gate records](https://github.com/jisung77/ttihp-protocol-engine/actions/runs/44905941980)

**[실행 확인]** A digital pulse sweep drove every input, both polarities, at four phases relative to a 20 ns clock. In each of RTL and functional GLS, all 128 trials with widths of 60 or 80 ns triggered WAIT. Shorter pulses were phase dependent. The simulation models neither metastability nor board-level pulse capture. [Pulse stimulus and checks](../test/test_async_input.py)

**[미실행]** Silicon I/O timing and a transfer to the fabricated design on an RP2350 demo board remain unmeasured. The existing board-driver test loads and runs both programs against RTL through the documented board interface. [Driver and board procedure](../host/README.md)

## Reproduce a program

Run from the repository root with Python 3.11 or newer.

~~~sh
python -m model.assembler examples/uart_tx.asm uart_tx.bin
python -m pip install -r test/requirements.txt
python -m pytest -q model experiments/i2c_write
python experiments/two_context/verify_v02.py dual-demo --negative
~~~

**[실행 확인]** Jisung IC set the scope, chose the WAIT rising-edge rule and delegated the design decisions. Claude Code led planning and split implementation and critical review into separate roles. Codex checked results against the specification, reran the RTL and formal work, injected faults and compared the official build artifacts. The ISA, tests, failures and evidence are available for inspection. [AI use and source credits](../README.md#ai-활용) · [Bug log](bug_log.md)

## Human review points

1. Check an actual concurrent UART transition inside an SPI CS interval.
2. Compare the frozen and augmented mutation suites using the same 15 faults.
3. Match the precheck input archive digest to the GDS checksum in the release.
