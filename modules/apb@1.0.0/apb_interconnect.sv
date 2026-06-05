// APB Interconnect — FPRO-style address layout
// Verilog-2001 compatible — flattened ports (no unpacked arrays)
//
// Address layout (32-bit, FPRO/Pong Chu style):
//   [31:13]  IO base prefix (must match IO_BASE_PREFIX param)
//   [12: 7]  Slot ID (6 bits, 64 slots)
//   [ 6: 2]  Register ID (5 bits, 32 regs/slot)
//   [ 1: 0]  Byte offset (= 00 for 32-bit access)
//
// Default IO_BASE_PREFIX=19'h60000 maps to 0xC0000000-0xC0001FFF (8KB total IO space).
// Each slot gets 128 bytes (32 × 32-bit registers).
//
// Slot peripheral interface (per slot N):
//   Receives common: PADDR, PENABLE, PWRITE, PWDATA
//   Receives unique: PSEL[N]   ← one-hot, asserted when bus targets slot N
//   Drives:          PRDATA[N], PREADY[N]
//
// Defaults: when no slot matches, PREADY=1 and PRDATA=0 (no CPU stall).

module apb_interconnect #(
    parameter DW = 32,
    parameter AW = 32,
    parameter NUM_SLOTS = 64,
    parameter [18:0] IO_BASE_PREFIX = 19'h60000   // 0xC0000000 >> 13
) (
    // From APB master
    input  logic [AW-1:0]            MpADDR,
    input  logic                     MpSELx,
    input  logic                     MpENABLE,
    input  logic                     MpWRITE,
    input  logic [DW-1:0]            MpWDATA,
    output logic [DW-1:0]            MpRDATA,
    output logic                     MpREADY,
    output logic                     MpSLVERR,

    // To slots — flattened arrays
    output logic [NUM_SLOTS-1:0]     SpSEL,        // one-hot, per slot
    input  logic [NUM_SLOTS*DW-1:0]  SpRDATA,      // packed: [32*N+:32] is slot N
    input  logic [NUM_SLOTS-1:0]     SpREADY,
    input  logic [NUM_SLOTS-1:0]     SpSLVERR
);
    // ── Address decode 
    logic [18:0] addr_prefix;
    logic [ 5:0] slot_id;
    logic        io_match;

    assign addr_prefix = MpADDR[31:13];
    assign slot_id     = MpADDR[12:7];
    assign io_match    = (addr_prefix == IO_BASE_PREFIX);

    // ── Per-slot select (one-hot) 
    genvar i;
    generate
        for (i = 0; i < NUM_SLOTS; i = i + 1) begin : slot_sel
            assign SpSEL[i] = MpSELx && io_match && (slot_id == i[5:0]);
        end
    endgenerate

    // ── MUX: select PRDATA/PREADY/PSLVERR from active slot ───────────────────
    // Default: PREADY=1, PRDATA=0 (no stall when address doesn't match any slot)
    always_comb begin
        MpRDATA  = {DW{1'b0}};
        MpSLVERR = 1'b0;
        MpREADY  = 1'b1;     // default: no stall

        if (MpSELx && io_match) begin
            // Check each slot — slot_id can be at most NUM_SLOTS-1
            for (int s = 0; s < NUM_SLOTS; s++) begin
                if (slot_id == s[5:0]) begin
                    MpRDATA  = SpRDATA[s*DW +: DW];
                    MpREADY  = SpREADY[s];
                    MpSLVERR = SpSLVERR[s];
                end
            end
        end
    end

endmodule
