#!/bin/sh
set -eu
# Run from the project root. Optional second argument selects the pinned model directory.
out=$1
models=${2:-test/sram_model}
mkdir -p "$out"
iverilog -g2012 -DFUNCTIONAL -s sram_slots_tb -o "$out/sram.vvp" \
    "$models/RM_IHPSG13_1P_core_behavioral_bm_bist.v" \
    "$models/RM_IHPSG13_1P_256x16_c2_bm_bist.v" \
    experiments/two_context/sram_slots_tb.v
vvp -N "$out/sram.vvp" < /dev/null > "$out/tagged.log"
grep -F 'PASS: 512 tagged reads' "$out/tagged.log"
if vvp -N "$out/sram.vvp" +live_tag < /dev/null > "$out/live-tag.log" 2>&1; then
    echo 'Live-tag negative control unexpectedly passed' >&2
    exit 1
fi
grep -F 'prior address=00 data=ff00 owner=1' "$out/live-tag.log"
