// picorv32_soc_tb.sv — Tests full SoC: CPU + RAM + APB bridge + 64-slot interconnect
// Inline RAM with hand-coded program: write 0xDEADBEEF to 0xC0000000 (slot 0).
// Verify slot 0 sees the transaction.

`timescale 1ns/1ps

module ram (
    input  logic        clk,
    input  logic        mem_valid,
    input  logic [10:0] mem_addr,
    input  logic [31:0] mem_wdata,
    input  logic [ 3:0] mem_wstrb,
    output logic [31:0] mem_rdata,
    output logic        mem_ready
);
    logic [31:0] mem [0:2047];

    initial begin
        // Program: store 0xDEADBEEF to 0xC0000000 (slot 0, reg 0), loop forever
        //   lui  x1, 0xC0000     # x1 = 0xC0000000
        //   lui  x2, 0xDEADC     # x2 = 0xDEADC000
        //   addi x2, x2, -273    # x2 = 0xDEADBEEF
        //   sw   x2, 0(x1)       # store to 0xC0000000
        //   jal  x0, -16         # loop back
        mem[0] = 32'hC00000B7;   // lui x1, 0xC0000
        mem[1] = 32'hDEADC137;   // lui x2, 0xDEADC
        mem[2] = 32'hEEF10113;   // addi x2, x2, -273
        mem[3] = 32'h0020A023;   // sw x2, 0(x1)
        mem[4] = 32'hFF1FF06F;   // jal x0, -16
    end

    always_ff @(posedge clk) begin
        mem_ready <= 1'b0;
        if (mem_valid) begin
            if (mem_wstrb[0]) mem[mem_addr][ 7: 0] <= mem_wdata[ 7: 0];
            if (mem_wstrb[1]) mem[mem_addr][15: 8] <= mem_wdata[15: 8];
            if (mem_wstrb[2]) mem[mem_addr][23:16] <= mem_wdata[23:16];
            if (mem_wstrb[3]) mem[mem_addr][31:24] <= mem_wdata[31:24];
            mem_rdata <= mem[mem_addr];
            mem_ready <= 1'b1;
        end
    end
endmodule

module picorv32_soc_tb;

    parameter int NUM_SLOTS = 64;

    logic clk    = 0;
    logic resetn = 0;
    always #5 clk = ~clk;

    logic [31:0]              PADDR;
    logic                     PENABLE, PWRITE;
    logic [31:0]              PWDATA;
    logic [NUM_SLOTS-1:0]     SpSEL;
    logic [NUM_SLOTS*32-1:0]  SpRDATA  = 0;
    logic [NUM_SLOTS-1:0]     SpREADY  = 0;
    logic [NUM_SLOTS-1:0]     SpSLVERR = 0;

    picorv32_soc #(
        .NUM_SLOTS    (NUM_SLOTS),
        .RAM_ADDR_BITS(11)
    ) u_soc (
        .clk(clk), .resetn(resetn),
        .PADDR(PADDR), .PENABLE(PENABLE),
        .PWRITE(PWRITE), .PWDATA(PWDATA),
        .SpSEL(SpSEL), .SpRDATA(SpRDATA),
        .SpREADY(SpREADY), .SpSLVERR(SpSLVERR)
    );

    // Slot 0: simple APB responder — ack writes immediately
    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) SpREADY[0] <= 1'b0;
        else begin
            if (SpSEL[0] && PENABLE && !SpREADY[0])
                SpREADY[0] <= 1'b1;
            else
                SpREADY[0] <= 1'b0;
        end
    end

    int          pass = 0, fail = 0;
    int          slot0_writes = 0;
    logic [31:0] last_addr  = 0;
    logic [31:0] last_wdata = 0;

    always_ff @(posedge clk) begin
        if (SpSEL[0] && PENABLE && SpREADY[0] && PWRITE) begin
            slot0_writes <= slot0_writes + 1;
            last_addr    <= PADDR;
            last_wdata   <= PWDATA;
            $display("[SLOT 0] Write 0x%08x to 0x%08x", PWDATA, PADDR);
        end
    end

    initial begin
        $dumpfile("tb/picorv32_soc_tb.vcd");
        $dumpvars(0, picorv32_soc_tb);

        repeat(5) @(posedge clk);
        resetn <= 1;
        $display("[TB] Reset released");

        wait(slot0_writes >= 1);

        if (last_addr == 32'hC0000000) begin
            $display("[PASS] Slot 0 addr = 0xC0000000");
            pass++;
        end else begin
            $display("[FAIL] Slot 0 addr: 0x%08x", last_addr);
            fail++;
        end

        if (last_wdata == 32'hDEADBEEF) begin
            $display("[PASS] Slot 0 data = 0xDEADBEEF");
            pass++;
        end else begin
            $display("[FAIL] Slot 0 data: 0x%08x", last_wdata);
            fail++;
        end

        repeat(500) @(posedge clk);

        if (slot0_writes >= 2) begin
            $display("[PASS] CPU loops (%0d writes)", slot0_writes);
            pass++;
        end else begin
            $display("[FAIL] Only %0d writes", slot0_writes);
            fail++;
        end

        $display("\n========================================");
        $display("[SOC TB] %0d passed, %0d failed", pass, fail);
        $display("========================================");
        $finish;
    end

    initial begin
        #(50_000);
        $display("[SOC TB] TIMEOUT (%0d writes)", slot0_writes);
        $finish;
    end

endmodule
