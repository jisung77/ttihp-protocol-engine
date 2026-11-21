// §§6,9.1,9.2,9.4,9.5: independent ACK-based frame decoder. No loader internals.
module pe_dual_bounds(input wire clk, rst_n, ena,
                      input wire [7:0] ui_in, uio_in);
`ifdef WATCH_B
    localparam BANK = 1'b1;
`else
    localparam BANK = 1'b0;
`endif
    wire [7:0] uo_out, uio_out, uio_oe, pc_a, pc_b, sram_addr;
    wire dual_mode, fetch_valid, fetch_ctx, exec_start_a, exec_start_b, retire_a, retire_b;
    tt_um_jisung_protocol_engine dut(.*);

    wire active = ena && rst_n;
    reg valid = 0, previous_active, previous_ack;
    reg [7:0] previous_ui;
    reg [1:0] age = 0;
    // At this sample the ACK, PC and side effects already reflect the last edge.
    wire accepted = valid && active && previous_active && uo_out[4] != previous_ack;
    reg second = 0, target = 0, mismatch = 0;
    reg [3:0] prefix;
    reg [7:0] opcode;
    reg [2:0] remaining = 0;
    reg [11:0] payload;
    reg [7:0] length = 0;
    reg epoch = 0;
    wire [3:0] nibble = previous_ui[3:0];
    wire [7:0] command = {prefix,nibble};
    wire first = remaining == 0 && !second;
    wire cmd_end = accepted && remaining == 0 && second;
    wire dummy = remaining != 0 && (opcode == 0 || opcode == 7);
    wire payload_end = accepted && remaining == 1 && !dummy;
    wire bad_selector = mismatch || previous_ui[6] != target;
    wire ok = uo_out[3:0] == 0;
    wire my_frame = target == BANK;
    wire begin_ok = payload_end && my_frame && opcode == 1 && ok;
    wire run_ok = cmd_end && my_frame && command == 4 && ok;
    wire stop_ok = cmd_end && my_frame && command == 5 && ok;
    wire [15:0] begin_length = {payload[3:0],nibble,payload[11:4]};
    wire [7:0] observed_length = begin_ok ? begin_length[7:0] : length;
    wire observed_epoch = run_ok || (epoch && !begin_ok && !stop_ok);
    wire display_target = accepted && first ? previous_ui[6] : target;
    wire [7:0] pc = BANK ? pc_b : pc_a;
    wire my_exec = BANK ? exec_start_b || retire_b : exec_start_a || retire_a;

    always @(posedge clk) begin
        valid <= 1;
        previous_active <= active;
        previous_ack <= uo_out[4];
        previous_ui <= ui_in;
        if (!valid) assume(!active);
        if (!active) age <= 0;
        else if (age != 3) age <= age + 1'b1;
        if (!active || age < 3) assume(!ui_in[4]);
        if (active && age < 3) assume(ui_in[7:5] == 3'b001);
        if (valid && active && previous_active && previous_ui[4] != uo_out[4]) begin
            assume(ui_in[4] == previous_ui[4]);
            if (!dummy) assume({ui_in[6],ui_in[3:0]} == {previous_ui[6],previous_ui[3:0]});
        end

        if (!valid || !active) begin
            second <= 0;
            remaining <= 0;
            target <= 0;
            mismatch <= 0;
            length <= 0;
            epoch <= 0;
        end else if (accepted) begin
            if (first) begin
                target <= previous_ui[6];
                prefix <= nibble;
                second <= 1;
                mismatch <= 0;
            end else if (remaining == 0) begin
                opcode <= command;
                second <= 0;
                mismatch <= bad_selector;
                case (command)
                    1,2,3: remaining <= 4;
                    6: remaining <= 2;
                    0,7: if (ok) remaining <= 2;
                    default: remaining <= 0;
                endcase
            end else begin
                remaining <= remaining - 1'b1;
                payload <= {payload[7:0],nibble};
                if (!dummy) mismatch <= bad_selector;
            end
            if (begin_ok) length <= begin_length[7:0];
            epoch <= observed_epoch;
        end

        if (valid && active && age == 3) begin
            assert(dual_mode);
            if ((payload_end || (cmd_end && command != 1 && command != 2 &&
                    command != 3 && command != 6)) && bad_selector)
                assert(uo_out[3:0] == 3);
            if (begin_ok)
                assert(begin_length >= 1 && begin_length <= 128);
            if (observed_epoch)
                assert(pc < observed_length && observed_length <= 128);
            if ((display_target == BANK && uo_out[6]) || my_exec || (fetch_valid && fetch_ctx == BANK))
                assert(observed_epoch);
            if (fetch_valid && fetch_ctx == BANK) begin
                assert(sram_addr[7] == BANK && {1'b0,sram_addr[6:0]} < observed_length);
                assert({1'b0,sram_addr[6:0]} == $past(pc));
            end
        end
    end
endmodule
