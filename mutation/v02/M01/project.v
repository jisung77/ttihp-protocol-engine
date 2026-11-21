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
    // Packing convention: context A = index 0 = low bits, B = index 1 = high bits.

    // §6: Asynchronous assertion on either input, synchronous two-edge release.
    // E1 and E2 release the pipe; E3 is the first edge with internal reset_n high.
    wire selected_n = ena && rst_n;
    (* async_reg = "true" *) reg [1:0] release_pipe;
    always @(posedge clk or negedge selected_n) begin
        if (!selected_n) release_pipe <= 2'b00;
        else release_pipe <= {release_pipe[0], 1'b1};
    end
    wire reset_n = release_pipe[1];

    // §9.1: Edge parity. 1 means the upcoming rising edge is odd
    // (E1 sees 1, E2 sees 0, E3 sees 1). Independent of internal reset_n.
    reg odd_edge;
    always @(posedge clk or negedge selected_n) begin
        if (!selected_n) odd_edge <= 1'b1;
        else odd_edge <= !odd_edge;
    end

    // §9.1: Mode strap ui[7:5] passes a 2FF during E1 and E2 (selected_n domain).
    (* async_reg = "true" *) reg [2:0] mode_meta, mode_sync;
    always @(posedge clk or negedge selected_n) begin
        if (!selected_n) begin
            mode_meta <= 3'b000;
            mode_sync <= 3'b000;
        end else begin
            mode_meta <= ui_in[7:5];
            mode_sync <= mode_meta;
        end
    end

    // §9.1: At E3 (first edge with reset_n high) latch dual iff pre-edge sync == 001
    // (ui[7]=0, ui[6]=0, ui[5]=1). Held until reset or deselect.
    reg dual_mode, mode_locked;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            dual_mode <= 1'b0;
            mode_locked <= 1'b0;
        end else if (!mode_locked) begin
            dual_mode <= (mode_sync == 3'b001);
            mode_locked <= 1'b1;
        end
    end

    // §6: REQ passes a 2FF in the selected_n domain (independent of E1/E2 release).
    (* async_reg = "true" *) reg req_meta, req_sync;
    always @(posedge clk or negedge selected_n) begin
        if (!selected_n) begin
            req_meta <= 1'b0;
            req_sync <= 1'b0;
        end else begin
            req_meta <= ui_in[4];
            req_sync <= req_meta;
        end
    end

    // §6/§9.4: Bundled data (target select and nibble), one register stage.
    // Host holds it from before its toggle until ACK.
    reg selector;
    reg [3:0] nibble;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            selector <= 1'b0;
            nibble <= 4'd0;
        end else begin
            selector <= ui_in[6];
            nibble <= ui_in[3:0];
        end
    end

    // §2: A transition requires two real, consecutive synchronized samples.
    (* async_reg = "true" *) reg [7:0] pins_meta, pins_sync;
    reg [7:0] pins_prev;
    reg [2:0] sample_valid;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            pins_meta <= 0;
            pins_sync <= 0;
            pins_prev <= 0;
            sample_valid <= 0;
        end else begin
            pins_meta <= uio_in;
            pins_sync <= pins_meta;
            pins_prev <= pins_sync;
            sample_valid <= {sample_valid[1:0], 1'b1};
        end
    end
    wire history_valid = sample_valid[2];

    // Host <-> core <-> FIFO interconnect (shared contract).
    wire ack, target;
    wire [3:0] reply;
    wire [1:0] ready, running, fault;
    wire [1:0] start_run, stop, begin_image;
    wire [17:0] image_len;
    wire write_word;
    wire [7:0] write_addr;
    wire [15:0] write_data;
    wire [1:0] tx_write, rx_read;
    wire [7:0] tx_data;

    wire fetch, fetch_id;
    wire [7:0] fetch_addr;
    wire [15:0] pc_bus;
    wire [1:0] core_tx_read, core_rx_write;
    wire [15:0] rx_data;
    wire [1:0] exec_start_bus, retire_bus, tx_pop_bus, rx_push_bus;

    wire [15:0] tx_head, rx_head;
    wire [5:0] tx_count_bus, rx_count_bus;
    wire [15:0] sram_dout;

    // §6/§9.4: Fixed loader (one instance, both contexts).
    pe_host host (
        .clk(clk), .reset_n(reset_n), .dual_mode(dual_mode),
        .req(req_sync), .nibble(nibble), .selector(selector),
        .running(running), .fault(fault),
        .tx_count(tx_count_bus), .rx_count(rx_count_bus), .rx_head(rx_head),
        .ack(ack), .reply(reply), .target(target), .ready(ready),
        .start_run(start_run), .stop(stop), .begin_image(begin_image),
        .image_len(image_len), .write_word(write_word),
        .write_addr(write_addr), .write_data(write_data),
        .tx_write(tx_write), .tx_data(tx_data), .rx_read(rx_read)
    );

    // §1/§2/§9.2: Single-port program SRAM; host writes and core fetches never coincide.
    wire [7:0] mem_addr = write_word ? write_addr : fetch_addr;
    pe_sram program_memory (
        .clk(clk), .read_en(fetch), .write_en(write_word),
        .addr(mem_addr), .din(write_data), .dout(sram_dout)
    );

    // §5/§9.2: Independent TX/RX FIFO pair per context; STOP or BEGIN of X clears X.
    wire [1:0] clear_fifo = stop | begin_image;
    wire [2:0] tx_count_a, tx_count_b, rx_count_a, rx_count_b;
    pe_fifo tx_fifo (
        .clk(clk), .reset_n(reset_n), .clear(clear_fifo[0]),
        .push(tx_write[0]), .data_in(tx_data), .pop(core_tx_read[0]),
        .head(tx_head[7:0]), .count(tx_count_a)
    );
    pe_fifo rx_fifo (
        .clk(clk), .reset_n(reset_n), .clear(clear_fifo[0]),
        .push(core_rx_write[0]), .data_in(rx_data[7:0]), .pop(rx_read[0]),
        .head(rx_head[7:0]), .count(rx_count_a)
    );
    pe_fifo tx_fifo_b (
        .clk(clk), .reset_n(reset_n), .clear(clear_fifo[1]),
        .push(tx_write[1]), .data_in(tx_data), .pop(core_tx_read[1]),
        .head(tx_head[15:8]), .count(tx_count_b)
    );
    pe_fifo rx_fifo_b (
        .clk(clk), .reset_n(reset_n), .clear(clear_fifo[1]),
        .push(core_rx_write[1]), .data_in(rx_data[15:8]), .pop(rx_read[1]),
        .head(rx_head[15:8]), .count(rx_count_b)
    );
    assign tx_count_bus = {tx_count_b, tx_count_a};
    assign rx_count_bus = {rx_count_b, rx_count_a};

    // §2/§3/§9: Single time-multiplexed core (never two cores).
    pe_core core (
        .clk(clk), .reset_n(reset_n), .dual_mode(dual_mode), .odd_edge(odd_edge),
        .start_run(start_run), .stop(stop), .begin_image(begin_image),
        .image_len(image_len), .instruction(sram_dout),
        .fetch(fetch), .fetch_id(fetch_id), .fetch_addr(fetch_addr),
        .pc_bus(pc_bus), .running(running), .fault(fault),
        .pins(pins_sync), .prev(pins_prev), .history_valid(history_valid),
        .data_out(uio_out), .oe_out(uio_oe),
        .tx_count(tx_count_bus), .rx_count(rx_count_bus), .tx_head(tx_head),
        .tx_read(core_tx_read), .rx_write(core_rx_write), .rx_data(rx_data),
        .exec_start(exec_start_bus), .retire(retire_bus),
        .tx_pop(tx_pop_bus), .rx_push(rx_push_bus)
    );

    // §1/§9.4: Status pins show the displayed target (host keeps target=0 in single mode).
    assign uo_out = {fault[target], running[target], ready[target], ack, reply};

    // §9.5: Suffixed per-context observation wires (post-edge values).
    wire exec_start_a = exec_start_bus[0];
    wire exec_start_b = exec_start_bus[1];
    wire retire_a = retire_bus[0];
    wire retire_b = retire_bus[1];
    wire tx_pop_a = tx_pop_bus[0];
    wire tx_pop_b = tx_pop_bus[1];
    wire rx_push_a = rx_push_bus[0];
    wire rx_push_b = rx_push_bus[1];
    wire [7:0] pc_a = pc_bus[7:0];
    wire [7:0] pc_b = pc_bus[15:8];

    // §2: Legacy scalar observation names (context A).
    wire exec_start = exec_start_a;
    wire retire = retire_a;
    wire tx_pop = tx_pop_a;
    wire rx_push = rx_push_a;
    wire [7:0] pc = pc_a;
    wire [2:0] tx_count = tx_count_a;
    wire [2:0] rx_count = rx_count_a;

    // §9.5: Registered SRAM-access and tagged-response observation.
    // fetch_valid/fetch_ctx/sram_addr after F describe the read accepted at F;
    // sram_we/sram_addr after a write edge describe that write.
    // instr_valid/instr_ctx/instr_word after F+1 describe the command first executed
    // at F+1 (STOP-cancelled responses excluded).
    reg fetch_valid, fetch_ctx, sram_we, instr_valid, instr_ctx;
    reg [7:0] sram_addr;
    reg [15:0] instr_word;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            fetch_valid <= 1'b0;
            fetch_ctx <= 1'b0;
            sram_we <= 1'b0;
            sram_addr <= 8'd0;
            instr_valid <= 1'b0;
            instr_ctx <= 1'b0;
            instr_word <= 16'd0;
        end else begin
            fetch_valid <= fetch;
            fetch_ctx <= fetch_id;
            sram_we <= write_word;
            if (fetch || write_word) sram_addr <= mem_addr;
            instr_valid <= fetch_valid && !stop[fetch_ctx];
            instr_ctx <= fetch_id;
            if (fetch_valid) instr_word <= sram_dout;
        end
    end

    // §2/§9.5: Single mode keeps the raw macro word; dual mode shows the tagged response.
    wire [15:0] instruction = dual_mode ? instr_word : sram_dout;

    wire _unused = &{1'b0, mode_locked,
        exec_start, retire, tx_pop, rx_push, pc, tx_count, rx_count, instruction,
        exec_start_b, retire_b, tx_pop_b, rx_push_b, pc_b, tx_count_b, rx_count_b,
        fetch_valid, fetch_ctx, sram_we, sram_addr, instr_valid, instr_ctx};
endmodule
`default_nettype wire
