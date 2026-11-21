// Test-only monitor of alternating fetches from the IHP functional SRAM model.
`timescale 1ns/1ps
module sram_slots_tb;
    reg clk = 0;
    always #10 clk = ~clk;

    reg rd = 0, wr = 0;
    reg [7:0] addr = 0;
    wire [15:0] data;
    RM_IHPSG13_1P_256x16_c2_bm_bist ram (
        .A_CLK(clk), .A_MEN(rd | wr), .A_WEN(wr), .A_REN(rd), .A_ADDR(addr),
        .A_DIN({~addr, addr}), .A_DLY(1'b1), .A_DOUT(data), .A_BM(16'hffff),
        .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0),
        .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0), .A_BIST_ADDR(8'd0),
        .A_BIST_DIN(16'd0), .A_BIST_BM(16'd0)
    );

    // Sample before this clock's nonblocking updates, as an execution stage would.
    reg tag = 0, valid = 0;
    reg use_live_tag;
    wire response_owner = use_live_tag ? addr[7] : tag;
    always @(posedge clk) begin
        valid <= rd;
        if (rd)
            tag <= addr[7];
    end

    reg expect_valid = 0;
    reg [7:0] expect_addr;
    integer checked = 0;
    task drive(input read_en, input write_en, input [7:0] next_addr);
        begin
            @(negedge clk);
            rd = read_en;
            wr = write_en;
            addr = next_addr;
            @(posedge clk);
            if (valid !== expect_valid)
                $fatal(1, "invalid response-valid at %t", $time);
            if (expect_valid) begin
                if (data !== {~expect_addr, expect_addr} || response_owner !== expect_addr[7])
                    $fatal(1, "prior address=%h data=%h owner=%b", expect_addr, data, response_owner);
                checked = checked + 1;
            end
            expect_valid = read_en;
            expect_addr = next_addr;
        end
    endtask

    integer i, phase;
    reg [7:0] request;
    initial begin
        use_live_tag = $test$plusargs("live_tag");
        for (i = 0; i < 256; i = i + 1)
            drive(0, 1, i[7:0]);
        for (phase = 0; phase < 2; phase = phase + 1) begin
            for (i = 0; i < 256; i = i + 1) begin
                request = {i[0] ^ phase[0], i[7:1]};
                drive(1, 0, request);
                if (i % 17 == 16) begin
                    drive(0, 0, ~request);
                    drive(0, 0, request);
                end
            end
        end
        drive(0, 0, 8'd0);
        if (checked != 512)
            $fatal(1, "expected 512 reads; got %0d", checked);
        $display("PASS: 512 tagged reads from the IHP functional SRAM");
        $finish;
    end
endmodule
