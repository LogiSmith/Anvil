// picorv32_ram — Synchronous block RAM for PicoRV32 native memory interface
// Supports byte-enable writes, parameterizable size, hex file init

module picorv32_ram #(
    parameter MEM_WORDS = 16384,        // 64KB default (words of 32bit)
    parameter MEM_FILE  = ""            // optional hex init file
) (
    input  logic        clk,
    input  logic        resetn,

    // PicoRV32 native memory interface
    input  logic        mem_valid,
    output logic        mem_ready,
    input  logic [31:0] mem_addr,
    input  logic [31:0] mem_wdata,
    input  logic [ 3:0] mem_wstrb,
    output logic [31:0] mem_rdata
);
    logic [31:0] mem [0:MEM_WORDS-1];

    // Zero-initialize, then optionally load hex file
    initial begin
        for (int i = 0; i < MEM_WORDS; i++)
            mem[i] = 32'h0;
        if (MEM_FILE != "")
            $readmemh(MEM_FILE, mem);
    end

    // Word address
    logic [$clog2(MEM_WORDS)-1:0] waddr;
    assign waddr = mem_addr[31:2];

    always_ff @(posedge clk) begin
        mem_ready <= 1'b0;

        if (mem_valid && !mem_ready) begin
            mem_ready <= 1'b1;
            mem_rdata <= mem[waddr];

            // Byte-enable writes
            if (mem_wstrb[0]) mem[waddr][ 7: 0] <= mem_wdata[ 7: 0];
            if (mem_wstrb[1]) mem[waddr][15: 8] <= mem_wdata[15: 8];
            if (mem_wstrb[2]) mem[waddr][23:16] <= mem_wdata[23:16];
            if (mem_wstrb[3]) mem[waddr][31:24] <= mem_wdata[31:24];
        end
    end

endmodule
