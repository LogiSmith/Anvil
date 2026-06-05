// picorv32_core_tb.sv — Test core boots, runs from RAM, exposes IO transactions
//
// Strategy: stub IO bus with a fake responder.
// Inline a tiny RAM module with a hand-coded program:
//   write 0xDEADBEEF to address 0x20000000 in a loop.
// Verify IO bus sees the transaction with correct address/data.

`timescale 1ns/1ps

// Minimal hand-coded RAM for testbench (replaces ram.v from programator)
module ram (
    input  logic        clk,
    input  logic        mem_valid,
    input  logic [10:0] mem_addr,    // RAM_ADDR_BITS=11 → 11-bit addr
    input  logic [31:0] mem_wdata,
    input  logic [ 3:0] mem_wstrb,
    output logic [31:0] mem_rdata,
    output logic        mem_ready
);
    logic [31:0] mem [0:2047];

    initial begin
        // RISC-V program: store 0xDEADBEEF to 0x20000000, loop
        //   lui  x1, 0x20000     # x1 = 0x20000000
        //   lui  x2, 0xDEADC     # x2 = 0xDEADC000
        //   addi x2, x2, -273    # x2 = 0xDEADBEEF (sign-extended)
        //   sw   x2, 0(x1)       # store
        //   jal  x0, -16         # loop back
        mem[0] = 32'h200000B7;   // lui x1, 0x20000
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

module picorv32_core_tb;

    logic clk    = 0;
    logic resetn = 0;
    always #5 clk = ~clk;

    logic        io_valid;
    logic [31:0] io_addr;
    logic [31:0] io_wdata;
    logic [ 3:0] io_wstrb;
    logic [31:0] io_rdata = 32'h0;
    logic        io_ready = 0;

    picorv32_core #(.RAM_ADDR_BITS(11)) u_core (
        .clk     (clk),
        .resetn  (resetn),
        .io_valid(io_valid),
        .io_addr (io_addr),
        .io_wdata(io_wdata),
        .io_wstrb(io_wstrb),
        .io_rdata(io_rdata),
        .io_ready(io_ready)
    );

    // Ack IO transactions in 1 cycle
    always_ff @(posedge clk) begin
        if (io_valid && !io_ready) io_ready <= 1'b1;
        else                       io_ready <= 1'b0;
    end

    int          pass = 0, fail = 0;
    int          io_writes = 0;
    logic [31:0] last_io_addr  = 0;
    logic [31:0] last_io_wdata = 0;

    always_ff @(posedge clk) begin
        if (io_valid && io_ready && |io_wstrb) begin
            io_writes     <= io_writes + 1;
            last_io_addr  <= io_addr;
            last_io_wdata <= io_wdata;
            $display("[IO] Write 0x%08x to 0x%08x", io_wdata, io_addr);
        end
    end

    initial begin
        $dumpfile("tb/picorv32_core_tb.vcd");
        $dumpvars(0, picorv32_core_tb);

        repeat(5) @(posedge clk);
        resetn <= 1;
        $display("[TB] Reset released, CPU running...");

        wait(io_writes >= 1);

        if (last_io_addr == 32'h20000000) begin
            $display("[PASS] IO addr = 0x20000000");
            pass++;
        end else begin
            $display("[FAIL] IO addr expected 0x20000000, got 0x%08x", last_io_addr);
            fail++;
        end

        if (last_io_wdata == 32'hDEADBEEF) begin
            $display("[PASS] IO data = 0xDEADBEEF");
            pass++;
        end else begin
            $display("[FAIL] IO data expected 0xDEADBEEF, got 0x%08x", last_io_wdata);
            fail++;
        end

        repeat(500) @(posedge clk);

        if (io_writes >= 2) begin
            $display("[PASS] CPU loops (%0d IO writes seen)", io_writes);
            pass++;
        end else begin
            $display("[FAIL] CPU should loop, only %0d IO writes seen", io_writes);
            fail++;
        end

        $display("\n========================================");
        $display("[CORE TB] %0d passed, %0d failed", pass, fail);
        $display("========================================");
        $finish;
    end

    initial begin
        #(50_000);
        $display("[CORE TB] TIMEOUT — got %0d IO writes", io_writes);
        $finish;
    end

endmodule
