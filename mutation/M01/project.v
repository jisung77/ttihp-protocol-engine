/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/10ps
`default_nettype none

module tt_um_jisung_protocol_engine (
    input wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input wire ena,
    input wire clk,
    input wire rst_n
);
    // §6: Asynchronous assertion on either input, synchronous two-edge release.
    wire selected_n = ena && rst_n;
    (* async_reg = "true" *) reg [1:0] release_pipe;
    always @(posedge clk or negedge selected_n) begin
        if (!selected_n) release_pipe <= 2'b00;
        else release_pipe <= {release_pipe[0], 1'b1};
    end
    wire reset_n = release_pipe[1];

    // §2: A transition requires two real, consecutive synchronized samples.
    (* async_reg = "true" *) reg [7:0] pins_meta, pins_sync;
    (* async_reg = "true" *) reg req_meta, req_sync;
    reg [7:0] pins_prev;
    reg [2:0] sample_valid;
    reg [3:0] nibble;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            pins_meta <= 0;
            pins_sync <= 0;
            pins_prev <= 0;
            sample_valid <= 0;
            req_meta <= 0;
            req_sync <= 0;
            nibble <= 0;
        end else begin
            pins_meta <= uio_in;
            pins_sync <= pins_meta;
            pins_prev <= pins_sync;
            sample_valid <= {sample_valid[1:0], 1'b1};
            req_meta <= ui_in[4];
            req_sync <= req_meta;
            // Host holds bundled data from before its toggle until ACK (§6).
            nibble <= ui_in[3:0];
        end
    end

    wire ack, ready, running, fault;
    wire [3:0] reply;
    assign uo_out = {fault, running, ready, ack, reply};

    wire start_run, stop, begin_image, write_word, fetch;
    wire [8:0] image_len;
    wire [7:0] write_addr;
    wire [15:0] write_data, instruction;
    wire tx_write, rx_read, engine_tx_read, engine_rx_write;
    wire [7:0] tx_data, tx_head, rx_data, rx_head;

    // §2: Registered pulses and architectural values are sampled after each edge.
    wire exec_start, retire, tx_pop, rx_push;
    wire [7:0] pc;
    wire [2:0] tx_count, rx_count;

    pe_host host (
        .clk(clk), .reset_n(reset_n), .req(req_sync), .nibble(nibble),
        .ack(ack), .reply(reply), .ready(ready), .running(running), .fault(fault),
        .start_run(start_run), .stop(stop), .begin_image(begin_image),
        .image_len(image_len), .write_word(write_word),
        .write_addr(write_addr), .write_data(write_data),
        .tx_count(tx_count), .rx_count(rx_count), .rx_head(rx_head),
        .tx_write(tx_write), .tx_data(tx_data), .rx_read(rx_read)
    );

    pe_sram program_memory (
        .clk(clk), .read_en(fetch), .write_en(write_word),
        .addr(write_word ? write_addr : pc), .din(write_data), .dout(instruction)
    );

    wire clear_fifo = stop || begin_image;
    pe_fifo tx_fifo (
        .clk(clk), .reset_n(reset_n), .clear(clear_fifo),
        .push(tx_write), .data_in(tx_data), .pop(engine_tx_read),
        .head(tx_head), .count(tx_count)
    );
    pe_fifo rx_fifo (
        .clk(clk), .reset_n(reset_n), .clear(clear_fifo),
        .push(engine_rx_write), .data_in(rx_data), .pop(rx_read),
        .head(rx_head), .count(rx_count)
    );
    pe_core core (
        .clk(clk), .reset_n(reset_n), .start_run(start_run), .stop(stop),
        .begin_image(begin_image), .image_len(image_len), .instruction(instruction),
        .fetch(fetch), .pc(pc), .running(running), .fault(fault),
        .pins(uio_in), .prev(pins_prev), .history_valid(sample_valid[2]),
        .data_out(uio_out), .oe_out(uio_oe), .tx_count(tx_count),
        .tx_head(tx_head), .rx_count(rx_count), .rx_data(rx_data),
        .tx_read(engine_tx_read), .rx_write(engine_rx_write),
        .exec_start(exec_start), .retire(retire), .tx_pop(tx_pop), .rx_push(rx_push)
    );

    wire _unused = &{1'b0, ui_in[7:5], exec_start, retire, tx_pop, rx_push};
endmodule
`default_nettype wire
