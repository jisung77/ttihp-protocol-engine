/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/10ps
`default_nettype none
// Fixed nibble loader (spec §6), extended with the two-context rules of §9.4.
// Single mode (dual_mode=0): selector ignored, target stays A, v0.1 behaviour.
// Packing: index 0 = context A (low bits), index 1 = context B (high bits).
module pe_host (
    input wire clk,
    input wire reset_n,
    input wire dual_mode,
    input wire req,
    input wire [3:0] nibble,
    input wire selector,
    input wire [1:0] running,
    input wire [1:0] fault,
    input wire [5:0] tx_count,
    input wire [5:0] rx_count,
    input wire [15:0] rx_head,
    output reg ack,
    output reg [3:0] reply,
    output reg target,
    output reg [1:0] ready,
    output wire [1:0] start_run,
    output wire [1:0] stop,
    output wire [1:0] begin_image,
    output wire [17:0] image_len,
    output wire write_word,
    output wire [7:0] write_addr,
    output wire [15:0] write_data,
    output wire [1:0] tx_write,
    output wire [7:0] tx_data,
    output wire [1:0] rx_read
);
    localparam [7:0] STATUS = 0, BEGIN_IMG = 1, WRITE = 2, END_IMG = 3,
                     RUN = 4, STOP = 5, TX = 6, RX = 7;
    localparam [2:0] CMD_HI = 0, CMD_LO = 1, LOWBYTE_HI = 2, LOWBYTE_LO = 3,
                     HIGHBYTE_HI = 4, HIGHBYTE_LO = 5, REPLY_HI = 6, REPLY_LO = 7;
    localparam [3:0] OK = 0, RETRY = 1, REJECT = 2, BAD = 3;

    reg [2:0] phase;
    reg [3:0] high_nibble;
    reg [7:0] saved_cmd, low_byte, reply_byte;
    reg mismatch;                 // §9.4 sticky target-mismatch flag
    reg [1:0] loading;            // §9.4 at most one bit set
    reg [8:0] len_a, len_b;       // §9.2/§9.4 per-context image length
    reg [8:0] words;              // shared: only one target loads at a time
    reg [15:0] checksum;

    // §6: first nibble is high within a byte, first byte is low in a word.
    wire accept = reset_n && req != ack;
    wire [7:0] assembled = {high_nibble, nibble};
    wire [7:0] cmd = (phase == CMD_LO) ? assembled : saved_cmd;
    wire word_cmd = cmd == BEGIN_IMG || cmd == WRITE || cmd == END_IMG;
    wire complete = accept && ((phase == CMD_LO && !word_cmd && cmd != TX) ||
                               (phase == LOWBYTE_LO && cmd == TX) ||
                               phase == HIGHBYTE_LO);
    assign write_data = {assembled, low_byte};
    assign tx_data = assembled;
    // §9.2: A owns physical words 0-127, B owns 128-255. Single: v0.1.
    assign write_addr = dual_mode ? {target, words[6:0]} : words[7:0];
    assign image_len = {len_b, len_a};

    // §9.4: per-target views (target is always 0 in single mode).
    wire run_t   = target ? running[1] : running[0];
    wire fault_t = target ? fault[1]   : fault[0];
    wire ready_t = target ? ready[1]   : ready[0];
    wire load_t  = target ? loading[1] : loading[0];
    wire [8:0] len_t = target ? len_b : len_a;
    wire [2:0] txc_t = target ? tx_count[5:3] : tx_count[2:0];
    wire [2:0] rxc_t = target ? rx_count[5:3] : rx_count[2:0];
    wire [7:0] rxh_t = target ? rx_head[15:8] : rx_head[7:0];
    wire run_any   = running[0] | running[1];
    wire load_any  = loading[0] | loading[1];
    wire load_other = target ? loading[0] : loading[1];
    // §9.4: dual lock for BEGIN/WRITE/END (either running, or other loading).
    wire word_lock = dual_mode && (run_any || load_other);
    // §9.4: selector differing on any later command/payload nibble,
    // including the current (final) one.
    wire mismatch_now = dual_mode && (mismatch || selector != target);
    // §6 / §9.2: legal BEGIN length 1-256 single, 1-128 dual.
    wire bad_len = write_data == 0 ||
                   (dual_mode ? write_data > 16'd128 : write_data > 16'd256);

    // §6 / §9.4 decision. Priority: mismatch(3) > undefined(3) > state(2) >
    // argument(3) > FIFO retry(1) > success(0).
    reg [3:0] code;
    always @* begin
        code = OK;
        case (cmd)
            STATUS, STOP: ;
            BEGIN_IMG: begin
                if (run_t || word_lock) code = REJECT;
                else if (bad_len) code = BAD;
            end
            WRITE: begin
                if (word_lock || !load_t) code = REJECT;
                else if (words >= len_t) code = BAD;
            end
            END_IMG: begin
                if (word_lock || !load_t) code = REJECT;
                else if (words != len_t || write_data != checksum) code = BAD;
            end
            RUN: begin
                if (dual_mode && load_t) code = REJECT;
                else if (run_t || !ready_t || fault_t) code = REJECT;
            end
            TX: begin
                if (!ready_t || fault_t) code = REJECT;
                else if (txc_t == 4) code = RETRY;
            end
            RX: begin
                if (!ready_t) code = REJECT;
                else if (rxc_t == 0) code = RETRY;
            end
            default: code = BAD;
        endcase
        if (mismatch_now) code = BAD;
    end

    // §6 / §9.4: effects only at the final nibble of a legal frame,
    // one-hot on the target.
    wire success = complete && code == OK;
    wire [1:0] tsel = target ? 2'b10 : 2'b01;
    assign start_run   = (success && cmd == RUN)       ? tsel : 2'b00;
    assign stop        = (success && cmd == STOP)      ? tsel : 2'b00;
    assign begin_image = (success && cmd == BEGIN_IMG) ? tsel : 2'b00;
    assign tx_write    = (success && cmd == TX)        ? tsel : 2'b00;
    assign rx_read     = (success && cmd == RX)        ? tsel : 2'b00;
    assign write_word  = success && cmd == WRITE;
    // §6 / §9.4: load-format error (allowed state, code 3); never on mismatch.
    wire load_err = complete && word_cmd && code == BAD && !mismatch_now;
    // §6 / §9.4: STATUS of the target, pre-edge, bits[7:6]=0.
    wire [7:0] status_byte = {2'b00, load_t, fault_t, run_t, ready_t,
                              rxc_t != 0, txc_t < 4};

    // §6 nibble FSM, §9.4 target selection and mismatch tracking.
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            ack <= 0;
            reply <= 0;
            phase <= CMD_HI;
            high_nibble <= 0;
            low_byte <= 0;
            saved_cmd <= 0;
            reply_byte <= 0;
            target <= 0;
            mismatch <= 0;
        end else if (accept) begin
            ack <= req;
            reply <= 0;
            // §9.4: target chosen at the first command nibble; later
            // command/payload nibbles are compared. Dummies ignore selector.
            if (dual_mode) begin
                if (phase == CMD_HI) begin
                    target <= selector;
                    mismatch <= 0;
                end else if (phase != REPLY_HI && phase != REPLY_LO &&
                             selector != target) begin
                    mismatch <= 1;
                end
            end
            if (complete) begin
                reply <= code;
                if (code == OK && (cmd == STATUS || cmd == RX)) begin
                    reply_byte <= cmd == RX ? rxh_t : status_byte;
                    phase <= REPLY_HI;
                end else phase <= CMD_HI;
            end else begin
                case (phase)
                    CMD_HI: begin high_nibble <= nibble; phase <= CMD_LO; end
                    CMD_LO: begin saved_cmd <= assembled; phase <= LOWBYTE_HI; end
                    LOWBYTE_HI: begin high_nibble <= nibble; phase <= LOWBYTE_LO; end
                    LOWBYTE_LO: begin low_byte <= assembled; phase <= HIGHBYTE_HI; end
                    HIGHBYTE_HI: begin high_nibble <= nibble; phase <= HIGHBYTE_LO; end
                    REPLY_HI: begin reply <= reply_byte[7:4]; phase <= REPLY_LO; end
                    REPLY_LO: begin reply <= reply_byte[3:0]; phase <= CMD_HI; end
                    default: phase <= CMD_HI;
                endcase
            end
        end
    end

    // §6 / §9.4 per-target image state (ready, loading, length) and the
    // shared write counter/checksum.
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            ready <= 0;
            loading <= 0;
            len_a <= 0;
            len_b <= 0;
            words <= 0;
            checksum <= 0;
        end else if (begin_image != 2'b00) begin
            ready[target] <= 0;
            loading[target] <= 1;
            if (target) len_b <= write_data[8:0];
            else len_a <= write_data[8:0];
            words <= 0;
            checksum <= 0;
        end else if (write_word) begin
            words <= words + 1'b1;
            checksum <= checksum + write_data;
        end else if (success && cmd == END_IMG) begin
            ready[target] <= 1;
            loading[target] <= 0;
        end else if (load_err) begin
            ready[target] <= 0;
            loading[target] <= 0;
        end else if (stop != 2'b00) begin
            loading[target] <= 0;
        end
    end
endmodule
`default_nettype wire
