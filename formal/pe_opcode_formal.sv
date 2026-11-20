// Observe the macro's registered output and remember the issued opcode
// across stalls. No instruction legality or expected decode is assumed.
module pe_opcode_formal (
    input wire clk, rst_n, ena,
    input wire [7:0] ui_in, uio_in
);
    wire [7:0] uo_out, uio_out, uio_oe, pc;
    wire [15:0] instruction;
    wire exec_start, retire;
    tt_um_jisung_protocol_engine dut(
        .clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_in), .uo_out(uo_out),
        .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe),
        .pc(pc), .retire(retire), .exec_start(exec_start), .instruction(instruction)
    );

    reg valid = 0;
    wire active = rst_n && ena;
    wire running = uo_out[6];
    reg [4:0] sampled_opcode, issued_opcode;
    wire [4:0] completing_opcode = exec_start ? sampled_opcode : issued_opcode;
    reg pending;
    always @(posedge clk) begin
        valid <= 1;
        if (!valid)
            assume(!active);
        sampled_opcode <= instruction[15:11];
        if (exec_start)
            issued_opcode <= sampled_opcode;

        if (!active || !running || retire)
            pending <= 0;
        else if (exec_start)
            pending <= 1;

        if (valid && active && $past(active)) begin
            if (running && $past(running)) begin
                if (retire) begin
                    assert(exec_start || pending);
                    if (completing_opcode != 5'h0a && completing_opcode != 5'h0c)
                        assert({1'b0, pc} == {1'b0, $past(pc)} + 9'd1);
                end else begin
                    assert(pc == $past(pc));
                end
                if (uio_oe != $past(uio_oe))
                    assert(retire && completing_opcode == 5'h03);
            end
            if (running && !$past(running))
                assert(pc == 0 && uio_oe == 0);
            if (!running) begin
                assert(uio_oe == 0);
                if ($past(running))
                    assert(pc == 0 || pc == $past(pc));
            end
        end
    end
endmodule
