`timescale 1ns/1ps

module baud_rate_generator_tb;

    logic        clk    = 0;
    logic        resetn = 0;
    logic [15:0] limit  = 0;
    logic        tick;

    always #5 clk = ~clk; // 100 MHz

    baud_rate_generator #(.COUNTER_WIDTH(16)) uut (
        .clk(clk), .resetn(resetn),
        .limit(limit), .tick(tick)
    );

    int pass = 0;
    int fail = 0;
    int tick_count;

    // Count ticks over N cycles and check period
    task automatic check_period(logic [15:0] lim, int exp_period, string name);
        int cycles;
        limit = lim;
        cycles = 0;
        // Wait for first tick to sync
        @(posedge clk);
        while (!tick) @(posedge clk);
        // Now count cycles between ticks
        @(posedge clk);
        cycles = 0;
        while (!tick) begin
            @(posedge clk);
            cycles++;
        end
        cycles++; // include the tick cycle
        if (cycles == exp_period) begin
            $display("  PASS [%0s] period=%0d cycles", name, cycles);
            pass++;
        end else begin
            $display("  FAIL [%0s] period=%0d expected=%0d", name, cycles, exp_period);
            fail++;
        end
    endtask

    // Count total ticks in a window and check
    task automatic check_tick_count(logic [15:0] lim, int window, int exp_ticks, string name);
        int ticks;
        limit = lim;
        ticks = 0;
        for (int c = 0; c < window; c++) begin
            @(posedge clk);
            if (tick) ticks++;
        end
        if (ticks == exp_ticks) begin
            $display("  PASS [%0s] ticks=%0d in %0d cycles", name, ticks, window);
            pass++;
        end else begin
            $display("  FAIL [%0s] ticks=%0d expected=%0d in %0d cycles", name, ticks, exp_ticks, window);
            fail++;
        end
    endtask

    initial begin
        $dumpfile("baud_rate_generator_tb.vcd");
        $dumpvars(0, baud_rate_generator_tb);

        repeat(3) @(posedge clk);
        resetn <= 1;
        repeat(2) @(posedge clk);

        // ── Test 1: limit=0 → tick every cycle ─────────────────────────────
        $display("\n── Test 1: limit=0 (tick every 1 cycle) ──");
        check_period(16'd0, 1, "limit_0");

        // ── Test 2: limit=1 → tick every 2 cycles ──────────────────────────
        $display("\n── Test 2: limit=1 (tick every 2 cycles) ──");
        check_period(16'd1, 2, "limit_1");

        // ── Test 3: limit=9 → tick every 10 cycles ─────────────────────────
        $display("\n── Test 3: limit=9 (tick every 10 cycles) ──");
        check_period(16'd9, 10, "limit_9");

        // ── Test 4: 9600 baud TX @ 100MHz → limit=10415 ────────────────────
        $display("\n── Test 4: 9600 baud TX (limit=10415) ──");
        check_period(16'd10415, 10416, "9600_baud_tx");

        // ── Test 5: 115200 baud TX @ 100MHz → limit=867 ────────────────────
        $display("\n── Test 5: 115200 baud TX (limit=867) ──");
        check_period(16'd867, 868, "115200_baud_tx");

        // ── Test 6: 115200 baud RX 16x → limit=53 ──────────────────────────
        $display("\n── Test 6: 115200 baud RX 16x (limit=53) ──");
        check_period(16'd53, 54, "115200_baud_rx");

        // ── Test 7: runtime limit change ────────────────────────────────────
        $display("\n── Test 7: runtime limit change ──");
        limit <= 16'd9;
        repeat(50) @(posedge clk);
        limit <= 16'd4;  // change to 5-cycle period
        check_period(16'd4, 5, "runtime_change");

        // ── Test 8: reset stops ticks ───────────────────────────────────────
        $display("\n── Test 8: reset stops ticks ──");
        limit <= 16'd9;
        repeat(5) @(posedge clk);
        resetn <= 0;
        tick_count = 0;
        repeat(50) @(posedge clk) if (tick) tick_count++;
        if (tick_count == 0) begin
            $display("  PASS [reset_stops_ticks]");
            pass++;
        end else begin
            $display("  FAIL [reset_stops_ticks] got %0d ticks during reset", tick_count);
            fail++;
        end
        resetn <= 1;
        repeat(2) @(posedge clk);

        // ── Test 9: tick count in known window ──────────────────────────────
        $display("\n── Test 9: tick count in 100-cycle window (limit=9) ──");
        check_tick_count(16'd9, 100, 10, "tick_count_100");

        // ── Test 10: max limit value ─────────────────────────────────────────
        $display("\n── Test 10: large limit=65534 ──");
        check_period(16'd65534, 65535, "max_limit");

        repeat(5) @(posedge clk);
        $display("\n═══════════════════════════════════════");
        $display("Results: %0d passed, %0d failed", pass, fail);
        if (fail == 0) $display("ALL TESTS PASSED.");
        else           $display("SOME TESTS FAILED.");
        $finish;
    end

endmodule
