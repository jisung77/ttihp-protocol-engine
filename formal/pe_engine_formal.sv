module pe_engine_formal (
    input wire clk, rst_n, ena,
    input wire [7:0] ui_in, uio_in
);
    wire [7:0] uo_out, uio_out, uio_oe, pc;
    wire [2:0] tx_count, rx_count;
    wire retire;
    tt_um_jisung_protocol_engine dut(
        .clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_in), .uo_out(uo_out),
        .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe),
        .pc(pc), .retire(retire), .tx_count(tx_count), .rx_count(rx_count)
    );

    wire active = rst_n && ena;
    wire ready = uo_out[5], running = uo_out[6], fault = uo_out[7];
    reg valid = 0;
    reg [1:0] release_age = 0;

    // §§1,9.1: this checker interprets uo_out[6] as the single engine's
    // RUNNING. Select any of the seven legacy straps through E3. Upper pins
    // are unconstrained after capture and on later host transactions.
    reg [2:0] boot_edges = 0;
    always @(posedge clk) begin
        boot_edges <= active ? {boot_edges[1:0], 1'b1} : 3'b000;
        if (active && !boot_edges[2]) begin
            assume(ui_in[7:5] != 3'b001);
            if (boot_edges[0])
                assume(ui_in[7:5] == $past(ui_in[7:5]));
        end
    end

    always @(posedge clk) begin
        valid <= 1;
        if (!valid)
            assume(!active);
        if (active)
            release_age <= {release_age[0], 1'b1};
        else
            release_age <= 0;

        if (valid) begin
            assert(tx_count <= 4 && rx_count <= 4);
            if (!active || !$past(active) || $past(release_age) != 2'b11) begin
                assert({uo_out, uio_out, uio_oe, pc} == 0);
                assert({retire, tx_count, rx_count} == 0);
            end
            if (!running)
                assert(uio_oe == 0);
            if (running)
                assert(ready && !fault);
            if (fault)
                assert(!running && uio_oe == 0 && !retire);

            if (active && $past(active)) begin
                if (running && $past(running) && !retire)
                    assert(pc == $past(pc));
                if (fault && !$past(fault))
                    assert(pc == $past(pc) && !retire);
                if (running && !$past(running))
                    assert(pc == 0 && uio_oe == 0);
                if (running && $past(running) && !retire)
                    assert(uio_oe == $past(uio_oe));
            end
        end
    end
`ifdef ENGINE_LENGTH

    // The host must keep REQ low through release and hold a pending
    // request, including its payload nibble, until ACK matches (§6).
    always @(posedge clk) begin
        if (!active || release_age != 2'b11)
            assume(!ui_in[4]);
        if (valid && active && $past(active) && $past(ui_in[4]) != uo_out[4])
            assume(ui_in[4:0] == $past(ui_in[4:0]));
    end
    // Reconstruct the committed BEGIN length from pin-level ACK transitions.
    // The acknowledged nibble was present immediately before the previous
    // rising edge. Bytes are high-nibble first; length is low-byte first.
    reg high_nibble;
    reg [3:0] prefix;
    reg [7:0] opcode;
    reg [2:0] remaining;
    reg [11:0] preceding;
    reg [8:0] declared_length;

    always @(posedge clk) begin
        if (!active || !valid) begin
            high_nibble <= 1;
            remaining <= 0;
            declared_length <= 0;
        end else if ($past(active) && uo_out[4] != $past(uo_out[4])) begin
            if (remaining != 0) begin
                remaining <= remaining - 1'b1;
                preceding <= {preceding[7:0], $past(ui_in[3:0])};
                if (remaining == 1 && opcode == 8'h01 && uo_out[3:0] == 0)
                    declared_length <= {$past(ui_in[0]), preceding[11:4]};
            end else if (high_nibble) begin
                high_nibble <= 0;
                prefix <= $past(ui_in[3:0]);
            end else begin
                high_nibble <= 1;
                opcode <= {prefix, $past(ui_in[3:0])};
                case ({prefix, $past(ui_in[3:0])})
                    8'h01, 8'h02, 8'h03: remaining <= 4;
                    8'h06: remaining <= 2;
                    8'h00, 8'h07: if (uo_out[3:0] == 0) remaining <= 2;
                    default: remaining <= 0;
                endcase
            end
        end
        if (valid && running)
            assert({1'b0, pc} < declared_length && declared_length <= 256);
    end
`endif
endmodule
