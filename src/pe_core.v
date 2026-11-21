/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/10ps
`default_nettype none

// pe_core (v0.2): one shared decoder / branch / next-PC / ALU / write-back
// datapath serving two banked register sets (§9.2).
//   Packing convention: context A = index 0 = low bits, B = index 1 = high bits.
//   Single mode (dual_mode=0): only A exists and owns every edge (§§1-8, v0.1).
//   Dual mode: odd edge = A exec + B fetch, even edge = A fetch + B exec (§9.1).
module pe_core (
    input  wire        clk,
    input  wire        reset_n,
    input  wire        dual_mode,
    input  wire        odd_edge,
    input  wire [1:0]  start_run,
    input  wire [1:0]  stop,
    input  wire [1:0]  begin_image,
    input  wire [17:0] image_len,
    input  wire [15:0] instruction,
    output wire        fetch,
    output wire        fetch_id,
    output wire [7:0]  fetch_addr,
    output wire [15:0] pc_bus,
    output wire [1:0]  running,
    output wire [1:0]  fault,
    input  wire [7:0]  pins,
    input  wire [7:0]  prev,
    input  wire        history_valid,
    output wire [7:0]  data_out,
    output wire [7:0]  oe_out,
    input  wire [5:0]  tx_count,
    input  wire [5:0]  rx_count,
    input  wire [15:0] tx_head,
    output wire [1:0]  tx_read,
    output wire [1:0]  rx_write,
    output wire [15:0] rx_data,
    output reg  [1:0]  exec_start,
    output reg  [1:0]  retire,
    output reg  [1:0]  tx_pop,
    output reg  [1:0]  rx_push
);
    // ------------------------------------------------------------------
    // §3: opcodes; §2/§9.1: per-context phase; §4: WAIT result encoding.
    // ------------------------------------------------------------------
    localparam [4:0] HALT = 5'd0, DATA = 5'd1, OE = 5'd2, COMMIT = 5'd3,
                     TLO = 5'd4, THI = 5'd5, WAIT_PIN = 5'd6, DELAY = 5'd7,
                     SHIN = 5'd8, SHOUT = 5'd9, BR = 5'd10, LSET = 5'd11,
                     DJNZ = 5'd12, PUSH = 5'd13, PULL = 5'd14, SRIMM = 5'd15;
    // IDLE: not running. FETCH: wants an SRAM read at its next fetch slot.
    // EXEC: the fetched word arrives at this edge (first execution, S).
    // HOLD: instruction stalled (WAIT/DELAY/PUSH/PULL), word kept in ireg.
    localparam [1:0] IDLE = 2'd0, FETCH = 2'd1, EXEC = 2'd2, HOLD = 2'd3;
    localparam [1:0] NONE = 2'd0, MATCH = 2'd1, TIMEOUT = 2'd2;

    // ------------------------------------------------------------------
    // §9.2: banked per-context state, packed {B, A}.
    // ------------------------------------------------------------------
    reg [15:0] pc_r;        // program counter (relative to image start)
    reg [15:0] pdata_r;     // prepared data
    reg [15:0] poe_r;       // prepared OE
    reg [15:0] adata_r;     // active data
    reg [15:0] aoe_r;       // active OE
    reg [15:0] osr_r, isr_r, lc_r;
    reg [31:0] t_r;         // T
    reg [31:0] ireg_r;      // latched instruction word (§9.1 tagged reply)
    reg [31:0] rem_r;       // WAIT/DELAY remaining ticks (§4, §9.3)
    reg [3:0]  phase_r;
    reg [3:0]  wres_r;      // WAIT result NONE/MATCH/TIMEOUT
    reg [1:0]  run_r, fault_r;
    reg [1:0]  dec_r;       // §9.3: decision remembered at d
    reg [1:0]  dec_ok_r;    // §9.3: remembered decision is success

    integer m;              // loop index, combinational monitor
    integer k;              // loop index, bank write-back

    // Single mode has no context B (§9.1); ignore a stray B RUN.
    wire [1:0] run_req = start_run & {dual_mode, 1'b1};

    // ------------------------------------------------------------------
    // §9.1: slot assignment.
    // ------------------------------------------------------------------
    wire [1:0] exec_slot  = dual_mode ? {~odd_edge,  odd_edge} : 2'b01;
    wire [1:0] fetch_slot = dual_mode ? { odd_edge, ~odd_edge} : 2'b01;
    wire       exec_b     = dual_mode & ~odd_edge;   // executing context e
    wire [1:0] exec_ctx   = {exec_b, ~exec_b};       // one-hot e

    wire [1:0] ph_fetch = {phase_r[3:2] == FETCH, phase_r[1:0] == FETCH};
    wire [1:0] ph_exec  = {phase_r[3:2] == EXEC,  phase_r[1:0] == EXEC};
    wire [1:0] ph_hold  = {phase_r[3:2] == HOLD,  phase_r[1:0] == HOLD};

    // §2/§9.1: fetch request F, suppressed by the target's STOP at that edge.
    wire [1:0] fetch_v = {2{reset_n}} & run_r & ph_fetch & fetch_slot & ~stop;
    assign fetch    = |fetch_v;
    assign fetch_id = fetch_v[1];
    wire [7:0] fetch_pc = fetch_id ? pc_r[15:8] : pc_r[7:0];
    // §9.2: A owns words 0-127, B owns 128-255.
    assign fetch_addr = dual_mode ? {fetch_id, fetch_pc[6:0]} : pc_r[7:0];

    // §9.1/§6: per-context "executing at this edge" (STOP has priority).
    wire [1:0] act = run_r & (ph_exec | ph_hold) & exec_slot & ~stop;

    // ------------------------------------------------------------------
    // Operand mux: select the executing context's bank (§9.2).
    // ------------------------------------------------------------------
    wire [7:0]  pc_e     = exec_b ? pc_r[15:8]     : pc_r[7:0];
    wire [7:0]  pdata_e  = exec_b ? pdata_r[15:8]  : pdata_r[7:0];
    wire [7:0]  poe_e    = exec_b ? poe_r[15:8]    : poe_r[7:0];
    wire [7:0]  adata_e  = exec_b ? adata_r[15:8]  : adata_r[7:0];
    wire [7:0]  aoe_e    = exec_b ? aoe_r[15:8]    : aoe_r[7:0];
    wire [7:0]  osr_e    = exec_b ? osr_r[15:8]    : osr_r[7:0];
    wire [7:0]  isr_e    = exec_b ? isr_r[15:8]    : isr_r[7:0];
    wire [7:0]  lc_e     = exec_b ? lc_r[15:8]     : lc_r[7:0];
    wire [15:0] t_e      = exec_b ? t_r[31:16]     : t_r[15:0];
    wire [15:0] ireg_e   = exec_b ? ireg_r[31:16]  : ireg_r[15:0];
    wire [1:0]  wres_e   = exec_b ? wres_r[3:2]    : wres_r[1:0];
    wire [8:0]  len_e    = exec_b ? image_len[17:9] : image_len[8:0];
    wire [2:0]  txc_e    = exec_b ? tx_count[5:3]  : tx_count[2:0];
    wire [2:0]  rxc_e    = exec_b ? rx_count[5:3]  : rx_count[2:0];
    wire [7:0]  txh_e    = exec_b ? tx_head[15:8]  : tx_head[7:0];
    wire        dec_e    = exec_b ? dec_r[1]       : dec_r[0];
    wire        dec_ok_e = exec_b ? dec_ok_r[1]    : dec_ok_r[0];
    wire        ph_exec_e = exec_b ? ph_exec[1]    : ph_exec[0];
    // §9.2: A owns uio[7:4], B owns uio[3:0].
    wire [7:0]  own_e    = exec_b ? 8'h0F : 8'hF0;

    wire active = exec_b ? act[1] : act[0];
    wire first  = active & ph_exec_e;   // S: first execution of the word

    // §9.1: S uses the word arriving now; later edges use the latched copy,
    // so a sibling fetch cannot overwrite a stalled context's instruction.
    wire [15:0] word = ph_exec_e ? instruction : ireg_e;
    wire [4:0]  op   = word[15:11];
    wire [10:0] arg  = word[10:0];
    wire [7:0]  imm  = arg[7:0];
    wire [2:0]  pin  = arg[2:0];

    // ------------------------------------------------------------------
    // §3: encoding check (unused argument bits must be zero).
    // ------------------------------------------------------------------
    reg encoding_valid;
    always @* begin
        case (op)
            HALT, DELAY, PUSH, PULL: encoding_valid = (arg == 11'd0);
            DATA, OE, COMMIT, TLO, THI, LSET, DJNZ, SRIMM:
                encoding_valid = (arg[10:8] == 3'd0);
            WAIT_PIN: encoding_valid = (arg[10:6] == 5'd0);
            SHIN, SHOUT: encoding_valid = (arg[10:4] == 7'd0);
            BR: encoding_valid = ~arg[10];
            default: encoding_valid = 1'b0;
        endcase
    end

    // §3: branch conditions and the selected next PC (9 bits, untruncated).
    reg branch_condition;
    always @* begin
        case (arg[9:8])
            2'd0: branch_condition = 1'b1;
            2'd1: branch_condition = (wres_e == MATCH);
            2'd2: branch_condition = (wres_e == TIMEOUT);
            default: branch_condition = (lc_e == 8'd0);
        endcase
    end
    wire       jump    = (op == BR && branch_condition) || (op == DJNZ && lc_e > 8'd1);
    wire [8:0] next_pc = jump ? {1'b0, imm} : {1'b0, pc_e} + 9'd1;

    // §9.2: ownership violations (dual mode only).
    wire not_owned = dual_mode &&
                     ((op == COMMIT && (imm & ~own_e) != 8'd0) ||
                      (op == SHOUT && !own_e[pin]));

    // §3/§9.2: trap only at S.
    wire invalid = !encoding_valid || (op != HALT && next_pc >= len_e) || not_owned;
    wire trap    = first & invalid;

    // ------------------------------------------------------------------
    // §4/§9.3: WAIT/DELAY monitor, one per context, evaluated on EVERY
    // edge (not only own slots). Small pin evaluator + 16-bit down-counter.
    // ------------------------------------------------------------------
    function pin_cond;
        input [7:0] cur, old;
        input       hv;
        input [2:0] sel;
        input [1:0] kind;
        begin
            case (kind)
                2'd0: pin_cond = !cur[sel];                    // LOW
                2'd1: pin_cond = cur[sel];                     // HIGH
                2'd2: pin_cond = hv && !old[sel] && cur[sel];  // RISE
                default: pin_cond = hv && old[sel] && !cur[sel]; // FALL
            endcase
        end
    endfunction

    reg [1:0]  mon_match;    // WAIT condition true at this edge
    reg [1:0]  mon_decide;   // first decision taken at this edge
    reg [1:0]  mon_count;    // finite and undecided: count down
    reg [31:0] mon_ticks;    // ticks left before this edge, {B, A}
    reg [15:0] mw;
    reg        mon_wait, mon_delay, mon_finite, mon_en, mon_expire;
    always @* begin
        mon_match  = 2'b00;
        mon_decide = 2'b00;
        mon_count  = 2'b00;
        mon_ticks  = 32'd0;
        for (m = 0; m < 2; m = m + 1) begin
            mw         = ph_exec[m] ? instruction : ireg_r[16*m +: 16];
            mon_wait   = (mw[15:11] == WAIT_PIN);
            mon_delay  = (mw[15:11] == DELAY);
            mon_finite = mon_delay || (mon_wait && !mw[5]);
            // From S (when not trapping) onward, until the first decision.
            mon_en = run_r[m] && !stop[m] && !dec_r[m] && (mon_wait || mon_delay) &&
                     ((ph_exec[m] && act[m] && !(trap && exec_ctx[m])) || ph_hold[m]);
            mon_ticks[16*m +: 16] = ph_exec[m] ? t_r[16*m +: 16] : rem_r[16*m +: 16];
            mon_match[m] = mon_wait &&
                           pin_cond(pins, prev, history_valid, mw[2:0], mw[4:3]);
            mon_expire   = mon_finite && (mon_ticks[16*m +: 16] == 16'd0);
            // §4: success wins a tie with expiry at N.
            mon_decide[m] = mon_en && (mon_match[m] || mon_expire);
            mon_count[m]  = mon_en && !mon_decide[m] && mon_finite;
        end
    end

    // ------------------------------------------------------------------
    // §3/§5/§9.3: completion of the executing context's instruction.
    // ------------------------------------------------------------------
    wire decide_e  = exec_b ? mon_decide[1] : mon_decide[0];
    wire match_e   = exec_b ? mon_match[1]  : mon_match[0];
    reg can_finish;
    always @* begin
        case (op)
            // Retire at the first own exec slot >= the decision edge d.
            WAIT_PIN, DELAY: can_finish = dec_e || decide_e;
            PUSH: can_finish = (rxc_e < 3'd4);   // §5: pre-edge occupancy
            PULL: can_finish = (txc_e != 3'd0);
            default: can_finish = 1'b1;
        endcase
    end
    wire finish  = active & ~trap & can_finish;
    wire wait_ok = dec_e ? dec_ok_e : match_e;   // first decision is sticky
    wire is_halt = (op == HALT);

    assign tx_read  = {2{finish && op == PULL}} & exec_ctx;
    assign rx_write = {2{finish && op == PUSH}} & exec_ctx;
    assign rx_data  = isr_r;                     // {isr_B, isr_A}

    // ------------------------------------------------------------------
    // §3: shared ALU. Next values of the executing context's registers,
    // applied only when it finishes (R).
    // ------------------------------------------------------------------
    reg [7:0]  alu_pc, alu_pdata, alu_poe, alu_adata, alu_aoe;
    reg [7:0]  alu_osr, alu_isr, alu_lc;
    reg [15:0] alu_t;
    reg [1:0]  alu_wres;
    always @* begin
        alu_pc    = is_halt ? pc_e : next_pc[7:0];  // §6: HALT keeps PC
        alu_pdata = pdata_e;
        alu_poe   = poe_e;
        alu_adata = adata_e;
        alu_aoe   = is_halt ? 8'd0 : aoe_e;        // §6/§9.2: HALT releases OE
        alu_osr   = osr_e;
        alu_isr   = isr_e;
        alu_lc    = lc_e;
        alu_t     = t_e;
        alu_wres  = wres_e;
        case (op)
            DATA:  alu_pdata = imm;
            OE:    alu_poe   = imm;
            COMMIT: begin
                alu_adata = (adata_e & ~imm) | (pdata_e & imm);
                alu_aoe   = (aoe_e   & ~imm) | (poe_e   & imm);
            end
            TLO:   alu_t[7:0]  = imm;
            THI:   alu_t[15:8] = imm;
            WAIT_PIN: alu_wres = wait_ok ? MATCH : TIMEOUT;
            SHIN:  alu_isr = arg[3] ? {pins[pin], isr_e[7:1]} : {isr_e[6:0], pins[pin]};
            SHOUT: begin
                alu_adata[pin] = arg[3] ? osr_e[0] : osr_e[7];
                alu_pdata[pin] = arg[3] ? osr_e[0] : osr_e[7];
                alu_osr        = arg[3] ? {1'b0, osr_e[7:1]} : {osr_e[6:0], 1'b0};
            end
            LSET:  alu_lc = imm;
            DJNZ:  if (lc_e != 8'd0) alu_lc = lc_e - 8'd1;
            PULL:  alu_osr = txh_e;
            SRIMM: alu_osr = imm;
            default: ;
        endcase
    end

    // ------------------------------------------------------------------
    // §6/§9.1-§9.3: banked state update. Each bank has its own host
    // controls; only the executing bank receives the shared ALU result.
    // ------------------------------------------------------------------
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            pc_r     <= 16'd0;
            pdata_r  <= 16'd0;
            poe_r    <= 16'd0;
            adata_r  <= 16'd0;
            aoe_r    <= 16'd0;
            osr_r    <= 16'd0;
            isr_r    <= 16'd0;
            lc_r     <= 16'd0;
            t_r      <= 32'd0;
            ireg_r   <= 32'd0;
            rem_r    <= 32'd0;
            phase_r  <= {IDLE, IDLE};
            wres_r   <= {NONE, NONE};
            run_r    <= 2'b00;
            fault_r  <= 2'b00;
            dec_r    <= 2'b00;
            dec_ok_r <= 2'b00;
        end else begin
            for (k = 0; k < 2; k = k + 1) begin
                if (stop[k] || run_req[k]) begin
                    // §6 RUN/STOP: register reset; STOP also cancels the
                    // in-flight word and any remembered decision (§9.1).
                    pc_r[8*k +: 8]     <= 8'd0;
                    pdata_r[8*k +: 8]  <= 8'd0;
                    poe_r[8*k +: 8]    <= 8'd0;
                    adata_r[8*k +: 8]  <= 8'd0;
                    aoe_r[8*k +: 8]    <= 8'd0;
                    osr_r[8*k +: 8]    <= 8'd0;
                    isr_r[8*k +: 8]    <= 8'd0;
                    lc_r[8*k +: 8]     <= 8'd0;
                    t_r[16*k +: 16]    <= 16'd0;
                    ireg_r[16*k +: 16] <= 16'd0;
                    rem_r[16*k +: 16]  <= 16'd0;
                    wres_r[2*k +: 2]   <= NONE;
                    dec_r[k]           <= 1'b0;
                    dec_ok_r[k]        <= 1'b0;
                    run_r[k]           <= run_req[k];
                    phase_r[2*k +: 2]  <= run_req[k] ? FETCH : IDLE;
                end else if (begin_image[k]) begin
                    // §6 BEGIN: clears FAULT (context is not running).
                    fault_r[k] <= 1'b0;
                end else begin
                    // §2/§9.1: F -> the word arrives at the next edge.
                    if (fetch_v[k])
                        phase_r[2*k +: 2] <= EXEC;

                    // §9.1: latch the word at its first execution.
                    if (first && exec_ctx[k])
                        ireg_r[16*k +: 16] <= instruction;

                    if (active && exec_ctx[k]) begin
                        if (trap) begin
                            // §6/§9.2: fault keeps PC/data/regs, releases OE.
                            run_r[k]          <= 1'b0;
                            fault_r[k]        <= 1'b1;
                            aoe_r[8*k +: 8]   <= 8'd0;
                            phase_r[2*k +: 2] <= IDLE;
                        end else if (finish) begin
                            // §3: write-back of the shared ALU result (R).
                            pc_r[8*k +: 8]    <= alu_pc;
                            pdata_r[8*k +: 8] <= alu_pdata;
                            poe_r[8*k +: 8]   <= alu_poe;
                            adata_r[8*k +: 8] <= alu_adata;
                            aoe_r[8*k +: 8]   <= alu_aoe;
                            osr_r[8*k +: 8]   <= alu_osr;
                            isr_r[8*k +: 8]   <= alu_isr;
                            lc_r[8*k +: 8]    <= alu_lc;
                            t_r[16*k +: 16]   <= alu_t;
                            wres_r[2*k +: 2]  <= alu_wres;
                            if (is_halt) begin
                                run_r[k]          <= 1'b0;
                                phase_r[2*k +: 2] <= IDLE;
                            end else begin
                                phase_r[2*k +: 2] <= FETCH;   // next F = R+1 or later
                            end
                        end else begin
                            // §4/§5: stall; PC, word and registers held.
                            phase_r[2*k +: 2] <= HOLD;
                        end
                    end

                    // §4/§9.3: monitor bookkeeping (runs on every edge).
                    if (finish && exec_ctx[k]) begin
                        dec_r[k]    <= 1'b0;
                        dec_ok_r[k] <= 1'b0;
                    end else if (mon_decide[k]) begin
                        dec_r[k]    <= 1'b1;
                        dec_ok_r[k] <= mon_match[k];
                    end else if (mon_count[k]) begin
                        rem_r[16*k +: 16] <= mon_ticks[16*k +: 16] - 16'd1;
                    end
                end
            end
        end
    end

    // ------------------------------------------------------------------
    // §2/§9.5: registered post-edge observation pulses.
    // ------------------------------------------------------------------
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            exec_start <= 2'b00;
            retire     <= 2'b00;
            tx_pop     <= 2'b00;
            rx_push    <= 2'b00;
        end else begin
            exec_start <= {2{first}} & exec_ctx;   // includes trapping S
            retire     <= {2{finish}} & exec_ctx;  // HALT retires, trap/STOP do not
            tx_pop     <= tx_read;
            rx_push    <= rx_write;
        end
    end

    // ------------------------------------------------------------------
    // §1/§9.2: outputs.
    // ------------------------------------------------------------------
    assign pc_bus   = pc_r;
    assign running  = run_r;
    assign fault    = fault_r;
    assign data_out = dual_mode ? {adata_r[7:4], adata_r[11:8]} : adata_r[7:0];
    assign oe_out   = dual_mode ? {aoe_r[7:4],   aoe_r[11:8]}   : aoe_r[7:0];
endmodule
`default_nettype wire
