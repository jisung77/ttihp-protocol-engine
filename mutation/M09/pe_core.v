/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/10ps
`default_nettype none
module pe_core (
    input wire clk, reset_n,
    input wire start_run, stop, begin_image,
    input wire [8:0] image_len,
    input wire [15:0] instruction,
    output wire fetch,
    output reg [7:0] pc,
    output reg running, fault,
    input wire [7:0] pins, prev,
    input wire history_valid,
    output reg [7:0] data_out, oe_out,
    input wire [2:0] tx_count, rx_count,
    input wire [7:0] tx_head,
    output wire [7:0] rx_data,
    output wire tx_read, rx_write,
    output reg exec_start, retire, tx_pop, rx_push
);
    localparam [4:0] HALT = 0, DATA = 1, OE = 2, COMMIT = 3, TLO = 4, THI = 5,
                     WAIT_PIN = 6, DELAY = 7, SHIN = 8, SHOUT = 9, BR = 10,
                     LSET = 11, DJNZ = 12, PUSH = 13, PULL = 14, SRIMM = 15;
    localparam [1:0] FETCH = 0, EXECUTE = 1, HOLD = 2;
    localparam [1:0] NONE = 0, MATCH = 1, TIMEOUT = 2;
    reg [1:0] phase, wait_result;
    reg [7:0] prepared_data, prepared_oe, osr, isr, lc;
    reg [15:0] timer_value, remaining;

    wire [4:0] op = instruction[15:11];
    wire [10:0] arg = instruction[10:0];
    wire [7:0] imm = arg[7:0];
    wire [2:0] pin = arg[2:0];

    // §3: Every opcode's unused bits must be zero, including HALT.
    reg encoding_valid;
    always @* begin
        case (op)
            HALT, DELAY, PUSH, PULL: encoding_valid = arg == 0;
            DATA, OE, COMMIT, TLO, THI, LSET, DJNZ, SRIMM: encoding_valid = arg[10:8] == 0;
            WAIT_PIN: encoding_valid = arg[10:6] == 0;
            SHIN, SHOUT: encoding_valid = arg[10:4] == 0;
            BR: encoding_valid = !arg[10];
            default: encoding_valid = 0;
        endcase
    end

    reg branch_condition, pin_match;
    always @* begin
        case (arg[9:8])
            0: branch_condition = 1;
            1: branch_condition = wait_result == MATCH;
            2: branch_condition = wait_result == TIMEOUT;
            3: branch_condition = lc == 0;
        endcase
        case (arg[4:3])
            0: pin_match = !pins[pin];
            1: pin_match = pins[pin];
            2: pin_match = history_valid && !prev[pin] && pins[pin];
            3: pin_match = history_valid && prev[pin] && !pins[pin];
        endcase
    end
    wire jump = (op == BR && branch_condition) || (op == DJNZ && lc > 1);
    wire [8:0] next_pc = jump ? {1'b0, imm} : {1'b0, pc} + 9'd1;
    wire active = running && phase != FETCH && !stop;
    wire invalid = !encoding_valid || (op != HALT && next_pc >= image_len);
    wire trap = active && phase == EXECUTE && invalid;
    assign fetch = reset_n && running && phase == FETCH && !stop;

    // §4: First check uses T. If S did not retire, the next edge is one
    // clock closer to expiry. Both success and expiry are evaluated on N.
    wire [15:0] ticks_left = phase == EXECUTE ? timer_value : remaining;
    reg can_finish;
    always @* begin
        case (op)
            WAIT_PIN: can_finish = pin_match || (!arg[5] && ticks_left == 0);
            DELAY: can_finish = ticks_left == 0;
            PUSH: can_finish = rx_count < 4;
            PULL: can_finish = tx_count != 0;
            default: can_finish = 1;
        endcase
    end
    wire finish = active && !trap && can_finish;
    assign tx_read = finish && op == PULL;
    assign rx_write = finish && op == PUSH;
    assign rx_data = isr;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            exec_start <= 0;
            retire <= 0;
            tx_pop <= 0;
            rx_push <= 0;
        end else begin
            exec_start <= active && phase == EXECUTE;
            retire <= finish;
            tx_pop <= tx_read;
            rx_push <= rx_write;
        end
    end

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            running <= 0;
            fault <= 0;
            phase <= FETCH;
            remaining <= 0;
        end else if (stop || start_run) begin
            running <= start_run;
            phase <= FETCH;
            remaining <= 0;
        end else if (begin_image) begin
            fault <= 0;
        end else if (fetch) begin
            phase <= EXECUTE;
        end else if (trap) begin
            running <= 0;
            fault <= 1;
        end else if (finish) begin
            phase <= FETCH;
            if (op == HALT) running <= 0;
        end else if (active) begin
            phase <= HOLD;
            if (op == DELAY || (op == WAIT_PIN && !arg[5]))
                remaining <= ticks_left - 1'b1;
        end
    end

    // §3/§6: A fault or HALT releases OE without changing output data.
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            pc <= 0;
            prepared_data <= 0;
            prepared_oe <= 0;
            data_out <= 0;
            oe_out <= 0;
            osr <= 0;
            isr <= 0;
            lc <= 0;
            timer_value <= 0;
            wait_result <= NONE;
        end else if (start_run || stop) begin
            pc <= 0;
            prepared_data <= 0;
            prepared_oe <= 0;
            data_out <= 0;
            oe_out <= 0;
            osr <= 0;
            isr <= 0;
            lc <= 0;
            timer_value <= 0;
            wait_result <= NONE;
        end else if (trap || (finish && op == HALT)) begin
            oe_out <= 0;
        end else if (finish) begin
            pc <= next_pc[7:0];
            case (op)
                DATA: prepared_data <= imm;
                OE: prepared_oe <= imm;
                COMMIT: begin
                    data_out <= (data_out & ~imm) | (prepared_data & imm);
                    oe_out <= (oe_out & ~imm) | (prepared_oe & imm);
                end
                TLO: timer_value[7:0] <= imm;
                THI: timer_value[15:8] <= imm;
                WAIT_PIN: wait_result <= pin_match ? MATCH : TIMEOUT;
                SHIN: isr <= arg[3] ? {pins[pin], isr[7:1]} : {isr[6:0], pins[pin]};
                SHOUT: begin
                    data_out[pin] <= arg[3] ? osr[0] : osr[7];
                    prepared_data[pin] <= arg[3] ? osr[0] : osr[7];
                    osr <= {osr[6:0], 1'b0};
                end
                LSET: lc <= imm;
                DJNZ: if (lc != 0) lc <= lc - 1'b1;
                PULL: osr <= tx_head;
                SRIMM: osr <= imm;
                default: ;
            endcase
        end
    end
endmodule
`default_nettype wire
