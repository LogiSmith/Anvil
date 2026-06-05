// picorv32_soc.sv
// PicoRV32 SoC — picorv32-core + APB bridge + APB interconnect (FPRO-style)
//
// Layered structure:
//   picorv32_soc
//   ├── picorv32_core (CPU + RAM, exposes IO bus)
//   ├── picorv32_apb_bridge (native IO bus → APB master)
//   └── apb_interconnect (1 master, 64 slots, FPRO address layout)
//
// Memory map:
//   0x00000000 - 0x000xFFFF  RAM (inside core)
//   0xC0000000 - 0xC0001FFF  IO space (64 slots × 128 bytes each)
//     0xC0000000 = slot 0
//     0xC0000080 = slot 1
//     0xC0000100 = slot 2
//     ...
//     0xC0001F80 = slot 63
//
// Each slot has 32 registers (5-bit reg index). Slot N's reg R is at:
//   0xC0000000 + (N << 7) + (R << 2)
//
// Top-level connects peripherals to slots via SpSEL[N], SpRDATA[N*32+:32], SpREADY[N], SpSLVERR[N].
// Common signals (PADDR, PENABLE, PWRITE, PWDATA) are exposed once for all peripherals.

`timescale 1ns/1ps

module picorv32_soc #(
    parameter BARREL_SHIFTER  = 0,
    parameter ENABLE_MUL      = 0,
    parameter ENABLE_DIV      = 0,
    parameter ENABLE_IRQ      = 0,
    parameter RAM_ADDR_BITS   = 11,
    parameter NUM_SLOTS       = 64,
    parameter [18:0] IO_BASE_PREFIX = 19'h60000  // 0xC0000000 >> 13
) (
    input  logic        clk,
    input  logic        resetn,

    // Synchronized reset for top-level peripherals (ARM CMSDK-style).
    // Exposes the internal 2-stage-synchronized reset so user peripherals share
    // the SoC reset domain instead of taking the async input directly.
    output logic        sync_resetn,

    // Common APB signals (broadcast to all slots)
    output logic [31:0] PADDR,
    output logic        PENABLE,
    output logic        PWRITE,
    output logic [31:0] PWDATA,

    // Per-slot select (one-hot) and per-slot responses (packed)
    output logic [NUM_SLOTS-1:0]    SpSEL,
    input  logic [NUM_SLOTS*32-1:0] SpRDATA,
    input  logic [NUM_SLOTS-1:0]    SpREADY,
    input  logic [NUM_SLOTS-1:0]    SpSLVERR
);
    // ── Reset synchronizer ───────────────────────────────────────────────────
    // External `resetn` is async (e.g. push-button on Nexys A7).
    // 2-stage synchronizer protects internal flops from metastability.
    // MTBF grows with each stage; 2 stages is industry standard for ~years MTBF.
    logic [1:0] reset_sync;
    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) reset_sync <= 2'b00;
        else         reset_sync <= {reset_sync[0], 1'b1};
    end
    assign sync_resetn = reset_sync[1];

    // Core: CPU + RAM
    logic        io_valid;
    logic [31:0] io_addr;
    logic [31:0] io_wdata;
    logic [ 3:0] io_wstrb;
    logic [31:0] io_rdata;
    logic        io_ready;

    picorv32_core #(
        .BARREL_SHIFTER(BARREL_SHIFTER),
        .ENABLE_MUL    (ENABLE_MUL),
        .ENABLE_DIV    (ENABLE_DIV),
        .ENABLE_IRQ    (ENABLE_IRQ),
        .RAM_ADDR_BITS (RAM_ADDR_BITS)
    ) u_core (
        .clk     (clk),
        .resetn  (sync_resetn),
        .io_valid(io_valid),
        .io_addr (io_addr),
        .io_wdata(io_wdata),
        .io_wstrb(io_wstrb),
        .io_rdata(io_rdata),
        .io_ready(io_ready)
    );

    //  APB bridge: native IO bus -> APB master
    logic        PSEL;
    logic [ 3:0] PSTRB;
    logic [31:0] ic_prdata;
    logic        ic_pready;
    logic        ic_pslverr;

    picorv32_apb_bridge u_bridge (
        .clk      (clk),
        .resetn   (sync_resetn),

        // Native IO bus from core
        .mem_valid(io_valid),
        .mem_addr (io_addr),
        .mem_wdata(io_wdata),
        .mem_wstrb(io_wstrb),
        .mem_ready(io_ready),
        .mem_rdata(io_rdata),

        // APB master
        .PADDR  (PADDR),   .PSEL   (PSEL),
        .PENABLE(PENABLE), .PWRITE (PWRITE),
        .PWDATA (PWDATA),  .PSTRB  (PSTRB),
        .PRDATA (ic_prdata),
        .PREADY (ic_pready),
        .PSLVERR(ic_pslverr)
    );

    // ── APB interconnect (FPRO-style, 64 slots) 
    apb_interconnect #(
        .NUM_SLOTS     (NUM_SLOTS),
        .IO_BASE_PREFIX(IO_BASE_PREFIX)
    ) u_interconnect (
        .MpADDR  (PADDR),    .MpSELx  (PSEL),
        .MpENABLE(PENABLE),  .MpWRITE (PWRITE),
        .MpWDATA (PWDATA),   .MpRDATA (ic_prdata),
        .MpREADY (ic_pready),.MpSLVERR(ic_pslverr),

        .SpSEL   (SpSEL),
        .SpRDATA (SpRDATA),
        .SpREADY (SpREADY),
        .SpSLVERR(SpSLVERR)
    );

endmodule
