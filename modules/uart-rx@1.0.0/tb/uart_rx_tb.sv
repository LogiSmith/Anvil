`timescale 1ns/1ps

// ─────────────────────────────────────────────────────────────────────────────
// uart_rx testbench
// Uses 16x oversampling tick (every 10 clk cycles = 1 baud period / 16)
// Tests: basic receive, all bit patterns, back-to-back, rx_busy, framing
// ─────────────────────────────────────────────────────────────────────────────

module uart_rx_tb;

    logic        clk         = 0;
    logic        resetn      = 0;
    logic        rx          = 1; // idle high
    logic        sample_tick = 0;
    logic [7:0]  data_out;
    logic        rx_done;
    logic        rx_busy;

    always #5 clk = ~clk; // 100 MHz

    uart_rx #(.DATA_WIDTH(8)) uut (
        .clk(clk), .resetn(resetn),
        .rx(rx), .sample_tick(sample_tick),
        .data_out(data_out), .rx_done(rx_done), .rx_busy(rx_busy)
    );

    // 16x oversample tick — 1 tick every 10 clk cycles
    // So 1 bit = 16 ticks = 160 clk cycles
    logic [3:0] tick_count = 0;
    always_ff @(posedge clk) begin
        if (!resetn) begin
            tick_count  <= '0;
            sample_tick <= 1'b0;
        end else begin
            sample_tick <= 1'b0;
            if (tick_count == 9) begin
                tick_count  <= '0;
                sample_tick <= 1'b1;
            end else begin
                tick_count <= tick_count + 1'b1;
            end
        end
    end

    int pass = 0;
    int fail = 0;

    task automatic check8(logic [7:0] got, logic [7:0] exp, string name);
        if (got === exp) begin
            $display("  PASS [%0s] got=%08b (0x%02x)", name, got, got);
            pass++;
        end else begin
            $display("  FAIL [%0s] got=%08b exp=%08b", name, got, exp);
            fail++;
        end
    endtask

    task automatic check1(logic got, logic exp, string name);
        if (got === exp) begin
            $display("  PASS [%0s] got=%b", name, got);
            pass++;
        end else begin
            $display("  FAIL [%0s] got=%b exp=%b", name, got, exp);
            fail++;
        end
    endtask

    // Send one UART byte (8N1, LSB first)
    // Each bit = 160 clk cycles (16 sample ticks × 10 clk/tick)
    task automatic uart_send(logic [7:0] data);
        // Start bit
        rx = 0;
        repeat(160) @(posedge clk);
        // 8 data bits LSB first
        for (int b = 0; b < 8; b++) begin
            rx = data[b];
            repeat(160) @(posedge clk);
        end
        // Stop bit
        rx = 1;
        repeat(160) @(posedge clk);
    endtask

    // Send and check received data
    task automatic send_and_check(logic [7:0] data, string name);
        fork
            uart_send(data);
            begin
                @(posedge clk);
                while (!rx_done) @(posedge clk);
                check8(data_out, data, name);
            end
        join
    endtask

    initial begin
        $dumpfile("tb/uart_rx_tb.vcd");
        $dumpvars(0, uart_rx_tb);

        repeat(5) @(posedge clk);
        resetn <= 1;
        repeat(5) @(posedge clk);

        // ── Test 1: Idle state ───────────────────────────────────────────────
        $display("\n── Test 1: Idle state ──");
        check1(rx_busy, 1'b0, "idle_not_busy");
        check1(rx_done, 1'b0, "idle_no_done");

        // ── Test 2: Receive 0x55 ─────────────────────────────────────────────
        $display("\n── Test 2: Receive 0x55 (01010101) ──");
        send_and_check(8'h55, "rx_0x55");

        // ── Test 3: Receive 0xAA ─────────────────────────────────────────────
        $display("\n── Test 3: Receive 0xAA (10101010) ──");
        send_and_check(8'hAA, "rx_0xAA");

        // ── Test 4: Receive 0x00 ─────────────────────────────────────────────
        $display("\n── Test 4: Receive 0x00 ──");
        send_and_check(8'h00, "rx_0x00");

        // ── Test 5: Receive 0xFF ─────────────────────────────────────────────
        $display("\n── Test 5: Receive 0xFF ──");
        send_and_check(8'hFF, "rx_0xFF");

        // ── Test 6: Receive ASCII 'H' ─────────────────────────────────────────
        $display("\n── Test 6: Receive 'H' (0x48) ──");
        send_and_check(8'h48, "rx_H");

        // ── Test 7: Receive ASCII 'i' ─────────────────────────────────────────
        $display("\n── Test 7: Receive 'i' (0x69) ──");
        send_and_check(8'h69, "rx_i");

        // ── Test 8: rx_busy during reception ─────────────────────────────────
        $display("\n── Test 8: rx_busy during reception ──");
        fork
            uart_send(8'hAB);
            begin
                repeat(50) @(posedge clk);
                check1(rx_busy, 1'b1, "rx_busy_during_rx");
                while (!rx_done) @(posedge clk);
            end
        join

        // ── Test 9: Back-to-back reception ───────────────────────────────────
        $display("\n── Test 9: Back-to-back ──");
        send_and_check(8'h12, "back2back_1");
        send_and_check(8'h34, "back2back_2");
        send_and_check(8'h56, "back2back_3");

        // ── Test 10: All zeros and all ones ──────────────────────────────────
        $display("\n── Test 10: 0x01 and 0x80 (edge bits) ──");
        send_and_check(8'h01, "lsb_only");
        send_and_check(8'h80, "msb_only");

        // ── Test 11: Reset during reception ──────────────────────────────────
        $display("\n── Test 11: Reset during reception ──");
        rx <= 0; // start bit
        repeat(80) @(posedge clk); // halfway through
        resetn <= 0;
        @(posedge clk);
        check1(rx_busy, 1'b0, "busy_clear_after_reset");
        check1(rx_done, 1'b0, "done_clear_after_reset");
        resetn <= 1;
        rx     <= 1;
        repeat(10) @(posedge clk);

        repeat(5) @(posedge clk);
        $display("\n═══════════════════════════════════════");
        $display("Results: %0d passed, %0d failed", pass, fail);
        if (fail == 0) $display("ALL TESTS PASSED.");
        else           $display("SOME TESTS FAILED.");
        $finish;
    end

endmodule
