/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/10ps
`default_nettype none
module pe_fifo (
    input wire clk, reset_n, clear,
    input wire push, pop,
    input wire [7:0] data_in,
    output wire [7:0] head,
    output reg [2:0] count
);
    // §5: Both decisions use the old occupancy, including at 0 and 4.
    reg [7:0] bytes [0:3];
    reg [1:0] rd_ptr, wr_ptr;
    wire take = pop && count != 0;
    wire put = push && count < 4;
    assign head = bytes[rd_ptr];
    always @(posedge clk) begin
        if (reset_n && !clear && put) bytes[wr_ptr] <= data_in;
    end
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            rd_ptr <= 0;
            wr_ptr <= 0;
            count <= 0;
        end else if (clear) begin
            rd_ptr <= 0;
            wr_ptr <= 0;
            count <= 0;
        end else begin
            if (put) wr_ptr <= wr_ptr + 1'b1;
            if (take) rd_ptr <= rd_ptr + 2'd2;
            case ({put, take})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: ;
            endcase
        end
    end
endmodule
`default_nettype wire
