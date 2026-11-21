// §§2,6,9. Public TT pins and named architectural probes only.
// Assertions sample the trace of the preceding edge, after its NBA updates.
`ifndef PE_DUAL_PIN_WITNESS
module pe_dual_formal(input wire clk, rst_n, ena,
                      input wire [7:0] ui_in, uio_in);
    wire [7:0] uo_out, uio_out, uio_oe, pc_a, pc_b, sram_addr;
    wire dual_mode, exec_start_a, exec_start_b, retire_a, retire_b;
    wire fetch_valid, fetch_ctx, sram_we, instr_valid, instr_ctx;
    wire [15:0] instruction;
    tt_um_jisung_protocol_engine dut(.*);

    wire active = rst_n && ena;
    reg history = 0;
    reg [1:0] age = 0;
    reg last_odd = 0, selected_dual = 0;
    always @(posedge clk) begin
        history <= 1;
        if (!history)
            assume(!active);
        if (!active) begin
            age <= 0;
            last_odd <= 0;
        end else begin
            if (age != 3)
                age <= age + 1'b1;
            last_odd <= !last_odd;
            if (age == 0)
                selected_dual <= ui_in[7:5] == 3'b001;
            if (age < 3) begin
                assume(!ui_in[4]);       // §9.1 REQ low through E3
                if (age != 0)
                    assume(ui_in[7:5] == $past(ui_in[7:5]));
            end
        end

`ifdef PE_DUAL_SLOTS
        if (history && (!active || !$past(active) || age < 3))
            assert(uio_oe == 0);        // §6 safe throughout reset/release
        if (history && active && age == 3) begin
            assert(dual_mode == selected_dual);
            if (dual_mode) begin
                assert(!(fetch_valid && sram_we));   // §9.1 single SRAM port
                assert(!(retire_a && retire_b));
                assert(!(exec_start_a && exec_start_b));
                if (fetch_valid)
                    assert(fetch_ctx == last_odd && sram_addr[7] == fetch_ctx);
                if (retire_a || exec_start_a)
                    assert(last_odd);
                if (retire_b || exec_start_b)
                    assert(!last_odd);

                // §9.5 only the context that consumed a tagged response starts.
                assert(exec_start_a == (instr_valid && !instr_ctx));
                assert(exec_start_b == (instr_valid && instr_ctx));
                if (instr_valid)
                    assert($past(fetch_valid) && instr_ctx == $past(fetch_ctx));
                // An ACK transition could be STOP. Without any accepted host
                // nibble or reset, an issued fetch must reach the next S.
                if ($past(dual_mode && fetch_valid) && uo_out[4] == $past(uo_out[4]))
                    assert(instr_valid);
            end
        end
`endif

`ifdef PE_DUAL_OWNER
        if (history && active && $past(active) && age == 3 && dual_mode && instr_valid) begin
            if (!instr_ctx && ((instruction[15:11] == 3 && |instruction[3:0]) ||
                               (instruction[15:11] == 9 && !instruction[2]))) begin
                // §9.2 A's forbidden COMMIT/SHOUT is atomic and releases only A.
                assert(!retire_a && pc_a == $past(pc_a) && uio_oe[7:4] == 0);
                assert(uio_out[7:4] == $past(uio_out[7:4]));
                if (uo_out[4] == $past(uo_out[4]))
                    assert({uio_out[3:0], uio_oe[3:0]} == $past({uio_out[3:0], uio_oe[3:0]}));
            end
            if (instr_ctx && ((instruction[15:11] == 3 && |instruction[7:4]) ||
                              (instruction[15:11] == 9 && instruction[2]))) begin
                assert(!retire_b && pc_b == $past(pc_b) && uio_oe[3:0] == 0);
                assert(uio_out[3:0] == $past(uio_out[3:0]));
                if (uo_out[4] == $past(uo_out[4]))
                    assert({uio_out[7:4], uio_oe[7:4]} == $past({uio_out[7:4], uio_oe[7:4]}));
            end
        end
`endif

`ifdef PE_DUAL_BOOT_COVER
        if (history && active && age == 3)
            cover(selected_dual && dual_mode);
`endif
    end
endmodule
`endif

// A reached pin-level witness for both PDR tasks. Uses a real behavioral SRAM,
// ordinary host ACK handshakes, and only the same named §9.5 observations.
// From formal/: iverilog -g2012 -DPE_SIM_SRAM -DPE_DUAL_PIN_WITNESS
//   -s pe_dual_pin_witness -o /tmp/dual.vvp pe_dual_formal.sv ../src/*.v
// Then run vvp /tmp/dual.vvp.
`ifdef PE_DUAL_PIN_WITNESS
`timescale 1ns/1ps
module pe_dual_pin_witness;
    reg clk = 0, rst_n = 0, ena = 1, req = 0;
    reg [7:0] ui_in = 8'h20, uio_in = 0;
    wire [7:0] uo_out, uio_out, uio_oe;
    tt_um_jisung_protocol_engine dut(.clk(clk), .rst_n(rst_n), .ena(ena),
        .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe));
    always #10 clk = !clk;

    task automatic nibble(input bit cid, input reg [3:0] data, output reg [3:0] response);
        integer i;
        begin : wait_ack
            @(negedge clk);
            ui_in = {1'b0, cid, 1'b0, req, data};
            #1 req = !req;
            ui_in[4] = req;
            for (i = 0; i < 8; i = i + 1) begin
                @(posedge clk);
                #2;
                if (uo_out[4] === req) begin
                    response = uo_out[3:0];
                    disable wait_ack;
                end
            end
            $fatal(1, "missing ACK");
        end
    endtask

    task automatic send_byte(input bit cid, input reg [7:0] data);
        reg [3:0] rc;
        begin
            nibble(cid, data[7:4], rc);
            if (rc !== 0) $fatal(1, "intermediate rc=%x", rc);
            nibble(cid, data[3:0], rc);
            if (rc !== 0) $fatal(1, "byte=%x rc=%x", data, rc);
        end
    endtask

    task automatic frame16(input bit cid, input reg [7:0] op, input reg [15:0] data);
        begin
            send_byte(cid, op);
            send_byte(cid, data[7:0]);
            send_byte(cid, data[15:8]);
        end
    endtask

    function automatic [15:0] word(input bit cid, input integer address,
                                    input bit faulty, input bit shift_bad);
        reg [7:0] mask;
        begin
            mask = cid ? 8'h01 : 8'h10;
            case (address)
            0: word = 16'h0800 | mask;  // DATA
            1: word = 16'h1000 | mask;  // OE
            2: word = 16'h1800 | mask;  // COMMIT
            3: word = faulty ? 16'h2010 : 16'h5003; // wait for peer, or loop
            4: word = 16'h3800;         // DELAY 16 wall clocks
            5: word = shift_bad ? (cid ? 16'h4804 : 16'h4800) : 16'h1811;
            default: word = 0;
            endcase
        end
    endfunction

    task automatic load(input bit cid, input bit faulty, input bit shift_bad);
        integer i, length;
        reg [15:0] code, sum;
        begin
            length = faulty ? 7 : 4;
            sum = 0;
            frame16(cid, 1, length);
            for (i = 0; i < length; i = i + 1) begin
                code = word(cid, i, faulty, shift_bad);
                frame16(cid, 2, code);
                sum = sum + code;
            end
            frame16(cid, 3, sum);
        end
    endtask

    task automatic check_status(input bit cid, input reg [2:0] fault_run_ready);
        reg [3:0] high_n, low_n;
        reg [7:0] status;
        begin
            send_byte(cid, 0);
            nibble(cid, 0, high_n);
            nibble(cid, 0, low_n);
            status = {high_n, low_n};
            if (status[4:2] !== fault_run_ready) $fatal(1, "cid=%d status=%x", cid, status);
        end
    endtask

    bit tracking, bad, shift_bad;
    reg [1:0] fetched, retired;
    bit checked_fault, overlapped, prior_valid, prior_id;
    reg [7:0] prior_out, prior_oe;
    always @(posedge clk) begin
        prior_out = uio_out;
        prior_oe = uio_oe;
        #1;
        if (tracking) begin
            if (dut.instr_valid && (!prior_valid || dut.instr_ctx !== prior_id))
                $fatal(1, "response without matching previous fetch");
            if (dut.fetch_valid) fetched[dut.fetch_ctx] = 1;
            if (dut.retire_a) retired[0] = 1;
            if (dut.retire_b) retired[1] = 1;
            if (dut.fetch_valid && dut.instr_valid && dut.fetch_ctx != dut.instr_ctx)
                overlapped = 1;
            if (dut.instr_valid && dut.instr_ctx == bad &&
                dut.instruction == word(bad, 5, 1, shift_bad)) begin
                if (prior_out !== 8'h11 || prior_oe !== 8'h11 || uio_out !== prior_out ||
                    uio_oe !== (bad ? 8'h10 : 8'h01) ||
                    dut.retire_a || dut.retire_b)
                    $fatal(1, "invalid operation changed pins or retired");
                checked_fault = 1;
            end
            prior_valid = dut.fetch_valid;
            prior_id = dut.fetch_ctx;
        end
    end

    integer scenario;
    initial begin
        for (scenario = 0; scenario < 4; scenario = scenario + 1) begin
            tracking = 0;
            @(negedge clk);
            rst_n = 0;
            req = 0;
            ui_in = 8'h20;
            repeat (3) @(negedge clk);
            if (uio_oe !== 0) $fatal(1, "OE active during reset");
            rst_n = 1;
            repeat (4) @(negedge clk);
            if (dut.dual_mode !== 1) $fatal(1, "dual strap did not latch");
            bad = scenario[0];
            shift_bad = scenario[1];
            load(bad, 1, shift_bad);
            load(!bad, 0, 0);
            fetched = 0;
            retired = 0;
            checked_fault = 0;
            overlapped = 0;
            prior_valid = 0;
            prior_id = 0;
            tracking = 1;
            send_byte(bad, 4);
            send_byte(!bad, 4);
            repeat (48) @(negedge clk);
            check_status(bad, 3'b101);
            check_status(!bad, 3'b011);
            if (fetched !== 3 || retired !== 3 || !overlapped || !checked_fault)
                $fatal(1, "vacuous witness: fetch=%x retire=%x overlap=%d fault=%d",
                       fetched, retired, overlapped, checked_fault);
            $display("PASS cid=%d illegal_shift=%d fetch=%b retire=%b overlap=%d oe=%h",
                     bad, shift_bad, fetched, retired, overlapped, uio_oe);
        end
        $finish;
    end
endmodule
`endif
