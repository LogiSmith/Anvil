// picorv32_apb_bridge
// Bridges PicoRV32 native memory interface to APB master
//
// PicoRV32 native interface:
//   mem_valid: CPU asserts to start transfer, holds until mem_ready
//   mem_ready: asserted for ONE cycle to complete transfer
//   mem_rdata: must be valid in the cycle mem_valid && mem_ready
//   mem_wstrb: 0000=read, non-zero=write (only valid combos per spec)
//
// APB protocol:
//   IDLE   → SETUP  (PSEL=1, PENABLE=0)
//   SETUP  → ACCESS (PSEL=1, PENABLE=1)
//   ACCESS → IDLE   (when PREADY=1, assert mem_ready)
//
// Key design decisions (based on APB_master.sv reference + picorv32 README):
//   - PADDR/PWDATA/PWRITE are COMBINATORIAL from mem inputs (not registered)
//   - PRDATA is forwarded COMBINATORIALLY to mem_rdata when PREADY — no delay
//   - mem_ready is asserted combinatorially (pENABLE && pREADY)

module picorv32_apb_bridge #(
    parameter DW = 32,
    parameter AW = 32
) (
    input  logic           clk,
    input  logic           resetn,

    // PicoRV32 native memory interface
    input  logic           mem_valid,
    input  logic [AW-1:0]  mem_addr,
    input  logic [DW-1:0]  mem_wdata,
    input  logic [3:0]     mem_wstrb,
    output logic           mem_ready,
    output logic [DW-1:0]  mem_rdata,

    // APB master port
    output logic [AW-1:0]  PADDR,
    output logic           PSEL,
    output logic           PENABLE,
    output logic           PWRITE,
    output logic [DW-1:0]  PWDATA,
    output logic [3:0]     PSTRB,
    input  logic [DW-1:0]  PRDATA,
    input  logic           PREADY,
    input  logic           PSLVERR
);
    // Pack picorv32 signals into apb_master command format
    // i_cmd = {pWRITE, pWDATA, pADDR}
    logic [AW+DW:0] cmd;
    assign cmd = {|mem_wstrb, mem_wdata, mem_addr};

    // Response from apb_master
    logic [DW:0] resp;  // {PSLVERR, PRDATA}
    logic        resp_ready;

    // mem_ready comes from apb_master o_ready (combinatorial: pENABLE && pREADY)
    assign mem_ready = resp_ready;
    assign mem_rdata = resp[DW-1:0];

    apb_master #(
        .DW(DW),
        .AW(AW)
    ) u_apb_master (
        .pCLK    (clk),
        .pRESETn (resetn),
        .i_cmd   (cmd),
        .i_valid (mem_valid),
        .o_resp  (resp),
        .o_ready (resp_ready),
        .pADDR   (PADDR),
        .pSELx   (PSEL),
        .pENABLE (PENABLE),
        .pWRITE  (PWRITE),
        .pWDATA  (PWDATA),
        .pRDATA  (PRDATA),
        .pREADY  (PREADY),
        .pSLVERR (PSLVERR)
    );

    // PSTRB — pass mem_wstrb directly
    assign PSTRB = mem_wstrb;

endmodule
