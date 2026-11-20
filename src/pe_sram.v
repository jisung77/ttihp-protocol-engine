/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/10ps
`default_nettype none

`ifdef PE_SIM_SRAM
`ifndef SYNTHESIS
`define PE_LOCAL_SRAM_ARRAY
`endif
`endif

module pe_sram (
    input wire clk, read_en, write_en,
    input wire [7:0] addr,
    input wire [15:0] din,
    output wire [15:0] dout
);
`ifdef PE_LOCAL_SRAM_ARRAY
    // §1/§2: Single port. Read data holds between fetches and during writes.
    reg [15:0] words [0:255];
    reg [15:0] read_word;
    always @(posedge clk) begin
        if (write_en) words[addr] <= din;
        else if (read_en) read_word <= words[addr];
    end
    assign dout = read_word;
`else
    RM_IHPSG13_1P_256x16_c2_bm_bist sram (
        .A_CLK(clk), .A_MEN(read_en || write_en), .A_REN(read_en),
        .A_WEN(write_en), .A_ADDR(addr), .A_DIN(din), .A_DOUT(dout),
        .A_DLY(1'b1), .A_BM(16'hffff),
        .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0),
        .A_BIST_REN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_ADDR(8'd0),
        .A_BIST_DIN(16'd0), .A_BIST_BM(16'd0)
    );
`endif
endmodule

`ifdef PE_LOCAL_SRAM_ARRAY
`undef PE_LOCAL_SRAM_ARRAY
`elsif PE_PDK_SRAM_MODEL
// The caller supplies IHP's wrapper and behavioral core for simulation.
`else
/* verilator lint_off DECLFILENAME */
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNDRIVEN */
(* blackbox *) module RM_IHPSG13_1P_256x16_c2_bm_bist (
    input wire A_CLK, A_MEN, A_REN, A_WEN,
    input wire [7:0] A_ADDR,
    input wire [15:0] A_DIN,
    output wire [15:0] A_DOUT,
    input wire A_DLY,
    input wire [15:0] A_BM,
    input wire A_BIST_CLK, A_BIST_EN, A_BIST_MEN, A_BIST_REN, A_BIST_WEN,
    input wire [7:0] A_BIST_ADDR,
    input wire [15:0] A_BIST_DIN, A_BIST_BM
);
endmodule
/* verilator lint_on UNDRIVEN */
/* verilator lint_on UNUSEDSIGNAL */
/* verilator lint_on DECLFILENAME */
`endif
`default_nettype wire
