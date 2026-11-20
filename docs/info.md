<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This 8x4 baseline checks the IHP toolchain. The template adder drives the low eight bits of ui_in + uio_in on uo_out. All bidirectional pins are inputs. The adder is combinational and uses neither clk nor rst_n.

## How to test

From the test directory, run the template cocotb test with make. The test drives ui_in = 20 and uio_in = 30 and checks that uo_out = 50. Check results.xml for test_project and inspect tb.fst for the pin values.

For the extra baseline checks, activate the RTL virtual environment, open Bash in the test directory and run the following commands. Set `TT_EVIDENCE_DIR` to choose a different evidence directory.

```bash
set -o pipefail
evidence_dir=$(realpath -m "${TT_EVIDENCE_DIR:-../../evidence}")
mkdir -p "$evidence_dir" &&
run_dir=$(mktemp -d "$evidence_dir/rtl-XXXXXXXX") &&
COCOTB_RESULTS_FILE="$run_dir/results.xml" make -B COCOTB_TEST_MODULES=test,baseline_extra 2>&1 | tee "$run_dir/rtl.log" &&
python check_baseline_results.py "$run_dir/results.xml" &&
cp tb.fst "$run_dir/" &&
printf 'Evidence saved to %s\n' "$run_dir"
```

The checker requires both named tests to run once and pass. The extra test covers zero, eight-bit wraparound, and an asymmetric input pair. It also checks that the bidirectional pin output data (`uio_out`) and output enable (`uio_oe`) are both zero. Each run gets a fresh results.xml path. The log and waveform are saved alongside the checked results.

## External hardware

The simulation requires no external hardware.
