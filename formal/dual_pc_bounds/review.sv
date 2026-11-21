// §§3,6,9.1,9.2,9.4. Independent pin decoder and REVIEW-only netlist assertions.
// ACK, data and control outputs in this sample already include the previous edge.
// Past host pins form an oracle independent of the internal decoder and PC.
module pe_dual_bounds_review(input wire clk, rst_n, ena,
                             input wire [7:0] ui_in, uio_in);
    wire [7:0] uo_out, uio_out, uio_oe, low_byte, saved_cmd;
    wire [3:0] high_nibble;
    wire [2:0] phase;
    wire [15:0] pc_bus;
    wire [17:0] image_len;
    wire [31:0] held;
    wire [1:0] ready, running, loading, hold;
    wire dual_mode, target;
    tt_um_jisung_protocol_engine dut(.*);

    wire active = rst_n && ena;
    reg valid = 0, old_active = 0, old_ack;
    reg [1:0] boot = 0;
    reg [7:0] accepted_ui, prior_ui;
    always @(posedge clk) begin
        valid <= 1;
        old_active <= active;
        old_ack <= uo_out[4];
        prior_ui <= accepted_ui;
        accepted_ui <= ui_in;
        if (!valid) assume(!active);
        if (!active) boot <= 0;
        else if (boot < 3) boot <= boot + 1'b1;
        if (active && boot < 3) begin
            assume(ui_in[7:5] == 3'b001);
            assume(!ui_in[4]);
        end
    end

    // §§6,9.4: first command nibble selects the bank through the whole frame.
    reg second, frame_target;
    reg [2:0] remaining;
    reg [3:0] prefix;
    reg [7:0] opcode;
    reg [11:0] payload;
    reg [1:0] epochs;
    reg [15:0] length_a, length_b;
    wire accepted = active && old_active && uo_out[4] != old_ack;
    wire [3:0] nibble = accepted_ui[3:0];
    wire dummy = remaining != 0 && (opcode == 0 || opcode == 7);
    wire [7:0] command = {prefix, nibble};
    wire word_command = command == 1 || command == 2 || command == 3;
    wire ok = uo_out[3:0] == 0;
    wire begin_ok = accepted && remaining == 1 && opcode == 1 && ok;
    wire run_ok = accepted && remaining == 0 && second && command == 4 && ok;
    wire stop_ok = accepted && remaining == 0 && second && command == 5 && ok;
    wire [15:0] accepted_length = {payload[3:0], nibble, payload[11:4]};

    // Bypass the registers on the ACK sample, including the RUN which starts a bank.
    reg next_second, next_target;
    reg [2:0] next_remaining;
    reg [3:0] next_prefix;
    reg [7:0] next_opcode;
    reg [11:0] next_payload;
    reg [15:0] next_length_a, next_length_b;
    reg [1:0] next_epochs;
    always @* begin
        next_second = second;
        next_target = frame_target;
        next_remaining = remaining;
        next_prefix = prefix;
        next_opcode = opcode;
        next_payload = payload;
        next_length_a = length_a;
        next_length_b = length_b;
        next_epochs = epochs;
        if (!active || !old_active || !valid) begin
            next_second = 0;
            next_target = 0;
            next_remaining = 0;
            next_epochs = 0;
            next_length_a = 0;
            next_length_b = 0;
        end else if (accepted) begin
            if (remaining != 0) begin
                next_remaining = remaining - 1'b1;
                next_payload = {payload[7:0], nibble};
            end else if (!second) begin
                next_second = 1;
                next_prefix = nibble;
                next_target = accepted_ui[6];
            end else begin
                next_second = 0;
                next_opcode = command;
                if (word_command) next_remaining = 4;
                else if (command == 6 || ((command == 0 || command == 7) && ok))
                    next_remaining = 2;
            end
            if (begin_ok) begin
                if (frame_target) next_length_b = accepted_length;
                else next_length_a = accepted_length;
            end
            if (run_ok || stop_ok || begin_ok) next_epochs[frame_target] = run_ok;
        end
    end

    always @(posedge clk) begin
        second <= next_second;
        frame_target <= next_target;
        remaining <= next_remaining;
        prefix <= next_prefix;
        opcode <= next_opcode;
        payload <= next_payload;
        length_a <= next_length_a;
        length_b <= next_length_b;
        epochs <= next_epochs;
        // Bundled nibble and selector are held through ACK on payload transfers.
        // Dummy reply transfers impose no stability requirement on those pins.
        if (valid && accepted && !dummy) begin
            assume(accepted_ui[3:0] == prior_ui[3:0]);
            assume(accepted_ui[6] == prior_ui[6]);
        end
    end

    // Induction lemmas. Implementation signals occur only in assertions.
    always @(posedge clk) if (valid) begin
        if (active && boot == 3) assert(dual_mode);
        assert(next_target == target);
        assert(!next_second || !next_remaining);
        case (phase)
            0: assert(!next_second && !next_remaining);
            1: assert(next_second && !next_remaining && next_prefix == high_nibble);
            2,3,4,5: begin
                assert(!next_second && next_opcode == saved_cmd);
                assert(next_opcode == 1 || next_opcode == 2 ||
                       next_opcode == 3 || next_opcode == 6);
                if (phase >= 4) assert(next_opcode != 6);
                case (phase)
                    2: assert(next_remaining == (next_opcode == 6 ? 2 : 4));
                    3: assert(next_remaining == (next_opcode == 6 ? 1 : 3) &&
                              next_payload[3:0] == high_nibble);
                    4: assert(next_remaining == 2 && next_payload[7:0] == low_byte);
                    5: assert(next_remaining == 1 && next_payload[11:4] == low_byte &&
                              next_payload[3:0] == high_nibble);
                endcase
            end
            6,7: begin
                assert(!next_second && (next_opcode == 0 || next_opcode == 7));
                assert(next_remaining == (phase == 6 ? 2 : 1));
            end
        endcase
        assert(next_length_a == image_len[8:0] && next_length_b == image_len[17:9]);
    end

    for (genvar k = 0; k < 2; k = k + 1) begin : bank
        wire [7:0] pc = pc_bus[8*k+:8];
        wire [8:0] actual_length = image_len[9*k+:9];
        wire [15:0] pin_length = k ? next_length_b : next_length_a;
        wire [4:0] stalled_opcode = held[16*k+11+:5];

        // This history expires only on reset/deselection or replacement by another
        // successful RUN of this bank. STOP, HALT, fault and a shorter BEGIN retain it.
        reg seen_run;
        reg [15:0] run_length;
        wire my_run = run_ok && frame_target == k;
        wire run_history_valid = valid && active && old_active && (my_run || seen_run);
        wire [15:0] length_at_last_run = my_run ? pin_length : run_length;
        always @(posedge clk) begin
            seen_run <= run_history_valid;
            run_length <= length_at_last_run;
            assert(!pc[7]);  // All samples, before RUN and while stopped as well.
            if (run_history_valid) begin
                assert(length_at_last_run >= 1 && length_at_last_run <= 128);
                assert(pc < length_at_last_run);
            end
            if (valid && my_run) assert(pc == 0);
            if (valid) begin
                assert(actual_length <= 128);
                if (loading[k] || ready[k]) assert(actual_length != 0);
                if (running[k]) begin
                    assert(next_epochs[k]);
                    assert(run_history_valid && length_at_last_run == pin_length);
                    assert({1'b0, pc} < actual_length);
                    assert(pc < pin_length && pin_length <= 128);
                    if (hold[k]) begin
                        assert({1'b0, pc} + 9'd1 < actual_length);
                        assert(stalled_opcode == 6 || stalled_opcode == 7 ||
                               stalled_opcode == 13 || stalled_opcode == 14);
                    end
                end
            end
        end
    end
endmodule
