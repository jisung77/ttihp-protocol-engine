/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/10ps
`default_nettype none
module pe_host (
    input wire clk, reset_n, req,
    input wire [3:0] nibble,
    output reg ack,
    output reg [3:0] reply,
    output reg ready,
    input wire running, fault,
    output wire start_run, stop, begin_image,
    output reg [8:0] image_len,
    output wire write_word,
    output wire [7:0] write_addr,
    output wire [15:0] write_data,
    input wire [2:0] tx_count, rx_count,
    input wire [7:0] rx_head,
    output wire tx_write,
    output wire [7:0] tx_data,
    output wire rx_read
);
    localparam [7:0] STATUS = 0, BEGIN_IMG = 1, WRITE = 2, END_IMG = 3,
                     RUN = 4, STOP = 5, TX = 6, RX = 7;
    localparam [2:0] CMD_HI = 0, CMD_LO = 1, LOWBYTE_HI = 2, LOWBYTE_LO = 3,
                     HIGHBYTE_HI = 4, HIGHBYTE_LO = 5, REPLY_HI = 6, REPLY_LO = 7;
    localparam [3:0] OK = 0, RETRY = 1, REJECT = 2, BAD = 3;
    reg [2:0] phase;
    reg [3:0] high_nibble;
    reg [7:0] saved_cmd, low_byte, reply_byte;
    reg loading;
    reg [8:0] words;
    reg [15:0] checksum;

    // §6: First nibble is high within a byte. The first byte is low in a word.
    wire accept = reset_n && req != ack;
    wire [7:0] assembled = {high_nibble, nibble};
    wire [7:0] cmd = (phase == CMD_LO) ? assembled : saved_cmd;
    wire word_cmd = cmd == BEGIN_IMG || cmd == WRITE || cmd == END_IMG;
    wire complete = accept && ((phase == CMD_LO && !word_cmd && cmd != TX) ||
                               (phase == LOWBYTE_LO && cmd == TX) ||
                               phase == HIGHBYTE_LO);
    assign write_data = {assembled, low_byte};
    assign tx_data = assembled;
    assign write_addr = words[7:0];

    reg [3:0] code;
    always @* begin
        code = OK;
        case (cmd)
            STATUS, STOP: ;
            BEGIN_IMG: begin
                if (running) code = REJECT;
                else if (write_data == 0 || write_data > 16'd256) code = BAD;
            end
            WRITE: begin
                if (!loading) code = REJECT;
                else if (words >= image_len) code = BAD;
            end
            END_IMG: begin
                if (!loading) code = REJECT;
                else if (words != image_len || write_data != checksum) code = BAD;
            end
            RUN: if (running || !ready || fault) code = REJECT;
            TX: begin
                if (!ready || fault) code = REJECT;
                else if (tx_count == 4) code = RETRY;
            end
            RX: begin
                if (!ready) code = REJECT;
                else if (rx_count == 0) code = RETRY;
            end
            default: code = BAD;
        endcase
    end
    wire success = complete && code == OK;
    assign start_run = success && cmd == RUN;
    assign stop = success && cmd == STOP;
    assign begin_image = success && cmd == BEGIN_IMG;
    assign write_word = success && cmd == WRITE;
    assign tx_write = success && cmd == TX;
    assign rx_read = success && cmd == RX;
    wire [7:0] status_byte = {2'b00, loading, fault, running, ready,
                              rx_count != 0, tx_count < 4};

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            ack <= 0;
            reply <= 0;
            phase <= CMD_HI;
            high_nibble <= 0;
            low_byte <= 0;
            saved_cmd <= 0;
            reply_byte <= 0;
        end else if (accept) begin
            ack <= req;
            reply <= 0;
            if (complete) begin
                reply <= code;
                if (code == OK && (cmd == STATUS || cmd == RX)) begin
                    reply_byte <= cmd == RX ? rx_head : status_byte;
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

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            ready <= 0;
            loading <= 0;
            image_len <= 0;
            words <= 0;
            checksum <= 0;
        end else if (begin_image) begin
            ready <= 0;
            loading <= 1;
            image_len <= write_data[8:0];
            words <= 0;
            checksum <= 0;
        end else if (write_word) begin
            words <= words + 1'b1;
            checksum <= checksum + write_data;
        end else if (success && cmd == END_IMG) begin
            ready <= 1;
            loading <= 0;
        end else if (complete && word_cmd && code == BAD) begin
            ready <= 0;
            loading <= 0;
        end else if (stop) begin
            loading <= 0;
        end
    end
endmodule
`default_nettype wire
