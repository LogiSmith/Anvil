`timescale 1ns/1ps

module picorv32_ram_tb;

    logic         clk    = 0;
    logic         resetn = 0;
    logic         mem_valid = 0;
    logic [31:0]  mem_addr  = 0;
    logic [31:0]  mem_wdata = 0;
    logic [ 3:0]  mem_wstrb = 0;
    logic         mem_ready;
    logic [31:0]  mem_rdata;

    always #5 clk = ~clk;

    picorv32_ram #(
        .MEM_WORDS(256)
    ) uut (
        .clk(clk), .resetn(resetn),
        .mem_valid(mem_valid),
        .mem_ready(mem_ready),
        .mem_addr(mem_addr),
        .mem_wdata(mem_wdata),
        .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata)
    );

    task automatic mem_write(logic [31:0] addr, logic [31:0] data, logic [3:0] strb);
        @(posedge clk);
        mem_valid <= 1;
        mem_addr  <= addr;
        mem_wdata <= data;
        mem_wstrb <= strb;
        @(posedge clk);
        while (!mem_ready) @(posedge clk);
        mem_valid <= 0;
        mem_wstrb <= 0;
    endtask

    task automatic mem_read(logic [31:0] addr);
        @(posedge clk);
        mem_valid <= 1;
        mem_addr  <= addr;
        mem_wstrb <= 4'h0;
        @(posedge clk);
        while (!mem_ready) @(posedge clk);
        mem_valid <= 0;
    endtask

    int pass = 0;
    int fail = 0;

    task automatic check(logic [31:0] got, logic [31:0] expected, string name);
        if (got === expected) begin
            $display("PASS [%s] got=%08x", name, got);
            pass++;
        end else begin
            $display("FAIL [%s] got=%08x expected=%08x", name, got, expected);
            fail++;
        end
    endtask

    initial begin
        $dumpfile("picorv32_ram_tb.vcd");
        $dumpvars(0, picorv32_ram_tb);

        repeat(5) @(posedge clk);
        resetn <= 1;
        repeat(2) @(posedge clk);

        // ── Test 1: full word write + read ──────────────────────────────────
        mem_write(32'h00000000, 32'hDEADBEEF, 4'hF);
        mem_read (32'h00000000);
        check(mem_rdata, 32'hDEADBEEF, "full_word_write");

        // ── Test 2: different address ────────────────────────────────────────
        mem_write(32'h00000004, 32'hCAFEBABE, 4'hF);
        mem_read (32'h00000004);
        check(mem_rdata, 32'hCAFEBABE, "second_addr");

        // ── Test 3: byte write — byte 0 only ────────────────────────────────
        mem_write(32'h00000008, 32'hFFFFFFFF, 4'hF);
        mem_write(32'h00000008, 32'h000000AA, 4'h1);
        mem_read (32'h00000008);
        check(mem_rdata, 32'hFFFFFFAA, "byte0_write");

        // ── Test 4: byte write — byte 3 only ────────────────────────────────
        mem_write(32'h0000000C, 32'h00000000, 4'hF);
        mem_write(32'h0000000C, 32'hBB000000, 4'h8);
        mem_read (32'h0000000C);
        check(mem_rdata, 32'hBB000000, "byte3_write");

        // ── Test 5: uninit read = 0 ──────────────────────────────────────────
        mem_read(32'h00000100);
        check(mem_rdata, 32'h00000000, "uninit_read");

        // ── Test 6: half-word write low (strb=0x3) ───────────────────────────
        mem_write(32'h00000010, 32'hFFFFFFFF, 4'hF);
        mem_write(32'h00000010, 32'h0000AABB, 4'h3);
        mem_read (32'h00000010);
        check(mem_rdata, 32'hFFFFAABB, "halfword_low");

        // ── Test 7: half-word write high (strb=0xC) ──────────────────────────
        mem_write(32'h00000014, 32'hFFFFFFFF, 4'hF);
        mem_write(32'h00000014, 32'hCCDD0000, 4'hC);
        mem_read (32'h00000014);
        check(mem_rdata, 32'hCCDDFFFF, "halfword_high");

        // ── Test 8: sequential write + read ──────────────────────────────────
        mem_write(32'h00000020, 32'h00000001, 4'hF);
        mem_write(32'h00000024, 32'h00000002, 4'hF);
        mem_write(32'h00000028, 32'h00000003, 4'hF);
        mem_write(32'h0000002C, 32'h00000004, 4'hF);
        mem_read(32'h00000020); check(mem_rdata, 32'h00000001, "sequential_0");
        mem_read(32'h00000024); check(mem_rdata, 32'h00000002, "sequential_1");
        mem_read(32'h00000028); check(mem_rdata, 32'h00000003, "sequential_2");
        mem_read(32'h0000002C); check(mem_rdata, 32'h00000004, "sequential_3");

        // ── Test 9: overwrite same address ───────────────────────────────────
        mem_write(32'h00000030, 32'h11111111, 4'hF);
        mem_write(32'h00000030, 32'h22222222, 4'hF);
        mem_read (32'h00000030);
        check(mem_rdata, 32'h22222222, "overwrite");

        // ── Test 10: unaligned addr bits ignored (addr[1:0]) ─────────────────
        mem_write(32'h00000040, 32'hABCDABCD, 4'hF);
        mem_read(32'h00000041); // byte offset — should still read word
        check(mem_rdata, 32'hABCDABCD, "unaligned_ignored");

        repeat(5) @(posedge clk);
        $display("─────────────────────────────────");
        $display("Results: %0d passed, %0d failed", pass, fail);
        if (fail == 0)
            $display("ALL TESTS PASSED.");
        else
            $display("SOME TESTS FAILED.");
        $finish;
    end

endmodule
