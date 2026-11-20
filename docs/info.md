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

## External hardware

The simulation requires no external hardware.
