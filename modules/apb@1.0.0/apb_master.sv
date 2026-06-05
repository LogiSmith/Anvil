// APB Master
// Implements AMBA APB protocol (IDLE → SETUP → ACCESS)
// PADDR/PWDATA/PWRITE: combinatorial from i_cmd (not registered)
// PRDATA: read combinatorially when o_ready — no 1-cycle delay
// i_cmd = {pWRITE[1], pWDATA[32], pADDR[32]} = 65 bits

module apb_master #(
    parameter DW = 32,
    parameter AW = 32
) (
    input  logic           pCLK,
    input  logic           pRESETn,

    // Command & Response interface
    input  logic [AW+DW:0] i_cmd,    // {pWRITE, pWDATA, pADDR}
    input  logic           i_valid,
    output logic [DW:0]    o_resp,   // {pSLVERR, pRDATA}
    output logic           o_ready,

    // APB signals
    output logic [AW-1:0]  pADDR,
    output logic           pSELx,
    output logic           pENABLE,
    output logic           pWRITE,
    output logic [DW-1:0]  pWDATA,
    input  logic [DW-1:0]  pRDATA,
    input  logic           pREADY,
    input  logic           pSLVERR
);
    typedef enum logic [1:0] {
        IDLE   = 2'b00,
        SETUP  = 2'b01,
        ACCESS = 2'b10
    } state_t;

    state_t state, next_state;

    // State register
    always_ff @(posedge pCLK or negedge pRESETn) begin
        if (!pRESETn)
            state <= IDLE;
        else
            state <= next_state;
    end

    // Next state logic
    always_comb begin
        next_state = state;
        unique case (state)
            IDLE:   if (i_valid)              next_state = SETUP;
            SETUP:                            next_state = ACCESS;
            ACCESS: if (pREADY && i_valid)        next_state = SETUP;
                    else if (pREADY && !i_valid)  next_state = IDLE;
            default:                          next_state = IDLE;
        endcase
    end

    // APB outputs — combinatorial from i_cmd (stable while i_valid)
    assign pADDR   = i_cmd[AW-1:0];
    assign pWDATA  = i_cmd[AW+DW-1:AW];
    assign pWRITE  = i_cmd[AW+DW];

    // Phase signals — combinatorial from state
    assign pSELx   = (state == SETUP || state == ACCESS);
    assign pENABLE = (state == ACCESS);

    // Response — combinatorial, PRDATA valid when pENABLE && pREADY
    assign o_ready = pENABLE && pREADY;
    assign o_resp  = {pSLVERR, pRDATA};

endmodule
