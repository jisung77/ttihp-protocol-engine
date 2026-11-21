// §§3,6,9.2,9.4,9.5. These programs branch forever to their last legal word.
`timescale 1ns/1ps
module dual_pc_bounds_pins;
    reg clk = 0, rst_n = 0, ena = 1, req = 0;
    reg [7:0] ui_in = 8'h20;
    wire [7:0] uo_out, uio_out, uio_oe;
    tt_um_jisung_protocol_engine dut(.clk(clk), .rst_n(rst_n), .ena(ena),
        .ui_in(ui_in), .uo_out(uo_out), .uio_in(8'h00),
        .uio_out(uio_out), .uio_oe(uio_oe));
    always #10 clk = !clk;
    initial if ($test$plusargs("trace")) begin
        $dumpfile("pins.vcd");
        $dumpvars(0, clk, rst_n, ena, ui_in, uo_out, uio_out, uio_oe,
                  dut.pc_a, dut.pc_b, dut.fetch_valid, dut.fetch_ctx,
                  dut.sram_addr, dut.sram_we, dut.instr_valid, dut.instr_ctx);
    end

    integer tick = 0, length[2], fetches[2], writes[2], boundary_fetches[2];
    integer accepted_writes[2], nonselected_fetches[2];
    reg [1:0] run_expected = 0;
    reg [1:0] boundary_seen = 0;
    reg tracking = 0, writing = 0, write_target = 0, selected = 0;
    reg first_command_nibble = 0, previous_ack = 0;
    wire [7:0] logical_pc[2];
    assign logical_pc[0] = dut.pc_a;
    assign logical_pc[1] = dut.pc_b;
    reg [7:0] previous_pc[2];
    reg owner;
    reg [7:0] expected_address;
    always @(posedge clk) begin
        tick = tick + 1;
        previous_pc[0] = logical_pc[0];
        previous_pc[1] = logical_pc[1];
        #1;
        if (tracking && ena && rst_n) begin
            if (uio_out !== 8'h00 || uio_oe !== 8'h00)
                $fatal(1, "unexpected branch-program output tick=%0d data=%h oe=%h", tick, uio_out, uio_oe);
            if (first_command_nibble && uo_out[4] != previous_ack)
                selected = ui_in[6];
            previous_ack = uo_out[4];
            if (dut.sram_we) begin
                if (!writing) $fatal(1, "unexpected SRAM write at tick %0d", tick);
                expected_address = 128 * write_target + writes[write_target];
                if (dut.sram_addr !== expected_address)
                    $fatal(1, "write address tick=%0d got=%0h expected=%0h", tick, dut.sram_addr, expected_address);
                writes[write_target] = writes[write_target] + 1;
            end
            if (dut.fetch_valid) begin
                owner = dut.fetch_ctx;
                expected_address = 128 * owner + previous_pc[owner];
                if (dut.sram_we !== 0 || dut.sram_addr !== expected_address)
                    $fatal(1, "fetch address tick=%0d ctx=%0d addr=%0h expected=%0h", tick, owner, dut.sram_addr, expected_address);
                if (previous_pc[owner] >= length[owner])
                    $fatal(1, "bounds tick=%0d ctx=%0d PC=%0d limit_A=%0d limit_B=%0d", tick, owner, previous_pc[owner], length[0], length[1]);
                fetches[owner] = fetches[owner] + 1;
                if (selected != owner) nonselected_fetches[owner] = nonselected_fetches[owner] + 1;
                if (previous_pc[owner] == length[owner]-1) begin
                    boundary_fetches[owner] = boundary_fetches[owner] + 1;
                    if (!boundary_seen[owner])
                        $display("LIMIT tick=%0d bank=%0d length=%0d PC=%0d physical=%0d",
                                 tick, owner, length[owner], previous_pc[owner], dut.sram_addr);
                    boundary_seen[owner] = 1;
                end
            end
            if (dut.instr_valid && dut.instruction !== (16'h5000 | (length[dut.instr_ctx]-1)))
                $fatal(1, "wrong tagged instruction at tick=%0d", tick);
            for (integer bank=0; bank<2; bank=bank+1)
                if (run_expected[bank] && (^logical_pc[bank] === 1'bx || logical_pc[bank] >= length[bank]))
                    $fatal(1, "running PC out of range at tick %0d bank %0d", tick, bank);
        end
    end

    task automatic nibble(input bit target_id, input reg [3:0] value, output reg [3:0] reply);
        begin : wait_for_ack
            @(negedge clk);
            ui_in = {1'b0, target_id, 1'b0, req, value};
            #2; req = !req;
            ui_in[4] = req;
            repeat (8) begin
                @(posedge clk); #2;
                if (uo_out[4] === req) begin
                    reply = uo_out[3:0];
                    disable wait_for_ack;
                end
            end
            $fatal(1, "ACK timeout");
        end
    endtask

    task automatic frame(input bit target_id, input [7:0] op, input [15:0] value,
                         input [3:0] expected_code, input integer wrong_nibble);
        integer nibbles;
        reg [23:0] payload;
        reg [3:0] reply, expected;
        begin
            payload = {op, value[7:0], value[15:8]};
            nibbles = (op == 1 || op == 2 || op == 3) ? 6 : 2;
            for (integer i=0; i<nibbles; i=i+1) begin
                first_command_nibble = (i == 0);
                nibble(target_id ^ (i == wrong_nibble), payload[23 - 4*i -: 4], reply);
                first_command_nibble = 0;
                expected = (i == nibbles-1) ? expected_code : 0;
                if (reply !== expected)
                    $fatal(1, "tick=%0d bank=%0d op=%0h nibble=%0d rc=%0h expected=%0h", tick, target_id, op, i, reply, expected);
            end
            if (expected_code != 0)
                $display("REJECT tick=%0d bank=%0d op=%02h value=%04h rc=%0d live=%03b",
                         tick, target_id, op, value, reply, uo_out[7:5]);
            if (op==2 && reply==0) accepted_writes[target_id] = accepted_writes[target_id]+1;
        end
    endtask

    task automatic status(input bit target_id, input [2:0] flags);
        reg [3:0] high_nibble, low_nibble;
        reg [7:0] snapshot;
        begin
            frame(target_id, 0, 0, 0, -1);
            if (uo_out[7:5] !== flags) $fatal(1, "live status bank=%0d", target_id);
            nibble(!target_id, 4'hb, high_nibble); // §9.4 dummies ignore their selector.
            nibble(!target_id, 4'he, low_nibble);
            snapshot = {high_nibble, low_nibble};
            if (snapshot !== {3'b000, flags, 2'b01} || uo_out[7:5] !== flags)
                $fatal(1, "bad snapshot bank=%0d status=%02h live=%0h", target_id, snapshot, uo_out[7:5]);
        end
    endtask

    task automatic load_image(input bit bank, input integer words);
        reg [15:0] data_word, sum;
        begin
            frame(bank, 1, words, 0, -1);
            sum = 0;
            writing = 1;
            write_target = bank;
            for (integer i=0; i<words; i=i+1) begin
                data_word = (i == 0 || i == words-1) ? (16'h5000 | (words-1)) : 0;
                frame(bank, 2, data_word, 0, -1);
                sum = sum + data_word;
            end
            writing = 0;
            frame(bank, 3, sum, 0, -1);
            if (writes[bank] != words || accepted_writes[bank] != words)
                $fatal(1, "missing write bank=%0d requested=%0d writes=%0d accepted=%0d", bank, words, writes[bank], accepted_writes[bank]);
            length[bank] = words;
            $display("IMAGE bank=%0d length=%0d accepted_writes=%0d physical_writes=%0d sum=%04h", bank, words, accepted_writes[bank], writes[bank], sum);
        end
    endtask

    task automatic restart(input bit deselect);
        begin
            @(negedge clk);
            tracking = 0;
            req = 0; ui_in = 8'h20;
            if (deselect) ena = 0; else rst_n = 0;
            repeat (3) @(negedge clk);
            if (uo_out !== 0 || logical_pc[0] !== 0 || logical_pc[1] !== 0)
                $fatal(1, "state not cleared");
            ena = 1; rst_n = 1;
            repeat (4) @(negedge clk);
            if (dut.dual_mode !== 1) $fatal(1, "dual strap not captured");
            for (integer i=0; i<2; i=i+1) begin
                length[i]=0; fetches[i]=0; boundary_fetches[i]=0;
                writes[i]=0; accepted_writes[i]=0; nonselected_fetches[i]=0;
            end
            previous_ack = 0; first_command_nibble = 0; selected = 0; boundary_seen = 0; run_expected = 0; tracking = 1;
        end
    endtask

    bit long_bank, short_bank;
    integer before_fetches;
    initial begin
        for (integer trial=0; trial<2; trial=trial+1) begin
            restart(trial[0]);
            long_bank = trial[0]; short_bank = !long_bank;
            load_image(short_bank, 1);
            load_image(long_bank, 128);
            frame(long_bank, 4, 0, 0, -1); run_expected[long_bank]=1;
            frame(short_bank, 4, 0, 0, -1); run_expected[short_bank]=1;
            status(long_bank, 3'b011); status(short_bank, 3'b011);
            if (boundary_seen !== 3 || logical_pc[long_bank] !== 127 || logical_pc[short_bank] !== 0)
                $fatal(1, "bad simultaneous PCs");

            // Cross-target BEGIN while both run must report mismatch, then STOP only the short bank.
            frame(short_bank, 1, 7, 3, 3);
            status(short_bank, 3'b011);
            frame(short_bank, 5, 0, 0, -1); run_expected[short_bank]=0;
            before_fetches = nonselected_fetches[long_bank];
            frame(short_bank, 1, 3, 2, -1); // peer still running: global load lock
            status(short_bank, 3'b001);
            if (nonselected_fetches[long_bank] <= before_fetches || logical_pc[long_bank] !== 127)
                $fatal(1, "nonselected peer did not keep fetching");
            status(long_bank, 3'b011);
            if (!nonselected_fetches[0] || !nonselected_fetches[1]) $fatal(1, "missing nonselected activity");
            $display("PAIR long=%0d short=%0d pcA=%0d pcB=%0d boundaryA=%0d boundaryB=%0d hiddenA=%0d hiddenB=%0d",
                     long_bank, short_bank, logical_pc[0], logical_pc[1], boundary_fetches[0], boundary_fetches[1],
                     nonselected_fetches[0], nonselected_fetches[1]);
        end
        $display("PASS dual PC physical SRAM pin witnesses ticks=%0d", tick);
        $finish;
    end
endmodule
