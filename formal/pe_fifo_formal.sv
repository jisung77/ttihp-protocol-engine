// The DUT is opaque. A unary occupancy mask and recent accepted inputs are
// sufficient to describe a FIFO independently of any physical pointers.
module pe_fifo_formal #(parameter BIT_INDEX = 0) (
    input wire clk, reset_n, clear, push, pop,
    input wire [7:0] data_in
);
    wire [7:0] head;
    wire [2:0] count;
    pe_fifo dut(.clk(clk), .reset_n(reset_n), .clear(clear), .push(push),
                .pop(pop), .data_in(data_in), .head(head), .count(count));

    reg past_valid = 0;
    reg [3:0] used;
    reg [3:0] recent;  // bit zero is the most recently accepted input bit
    wire take_push = push && !used[3];
    wire take_pop = pop && used[0];
    wire [2:0] expected_count = used[3] ? 3'd4 : used[2] ? 3'd3 :
                                used[1] ? 3'd2 : used[0] ? 3'd1 : 3'd0;

    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid)
            assume(!reset_n);

        if (!reset_n || clear) begin
            used <= 0;
        end else begin
            if (take_push && !take_pop)
                used <= {used[2:0], 1'b1};
            if (take_pop && !take_push)
                used <= used >> 1;
            if (take_push)
                recent <= {recent[2:0], data_in[BIT_INDEX]};
        end

        if (past_valid && (reset_n || !$past(reset_n))) begin
            assert(count == expected_count || (!reset_n && count == 0));
            assert(count <= 4);
            if (reset_n && |used)
                assert(head[BIT_INDEX] == recent[expected_count - 1'b1]);
        end
        if (past_valid && (!$past(reset_n) || $past(clear)))
            assert(count == 0);
    end

`ifdef FIFO_COVER
    // Saturate at eight transfers. Returning to empty after eight pushes
    // and eight pops exercises two full turns of a four-slot circular FIFO.
    reg [3:0] writes, reads;
    always @(posedge clk) begin
        if (!reset_n || clear) begin
            writes <= 0;
            reads <= 0;
        end else begin
            if (take_push && writes != 8)
                writes <= writes + 1'b1;
            if (take_pop && reads != 8)
                reads <= reads + 1'b1;
        end
        if (past_valid && reset_n) begin
            if ($past(reset_n && !clear && push && pop)) begin
                simultaneous_empty: cover($past(expected_count) == 0 && count == 1);
                simultaneous_one:   cover($past(expected_count) == 1 && count == 1);
                simultaneous_three: cover($past(expected_count) == 3 && count == 3);
                simultaneous_full:  cover($past(expected_count) == 4 && count == 3);
            end
            clear_busy: cover($past(reset_n && clear && push && pop && used[3]) && count == 0);
            wrap_twice: cover(writes == 8 && reads == 8 && count == 0);
        end
        // Use oracle occupancy for the pre-reset figure: an asynchronous
        // implementation may already display zero when reset is asserted.
        if (past_valid && $past(past_valid))
            reset_busy: cover($past(!reset_n && push && pop && used[3]) && count == 0);
    end
`endif
endmodule
