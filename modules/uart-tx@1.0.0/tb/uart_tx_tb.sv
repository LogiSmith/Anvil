`timescale 1ns/1ps

// ─────────────────────────────────────────────────────────────────────────────
// uart_tx testbench
// Uses a fast baud tick (every 10 cycles) for simulation speed
// Verifies: start bit, 8 data bits LSB first, stop bit, tx_done, tx_busy
// ─────────────────────────────────────────────────────────────────────────────

module uart_tx_tb;

    logic        clk    = 0;
    logic        resetn = 0;
    logic [7:0]  wr_data = 0;
    logic        wr_uart = 0;
    logic        baud_tick = 0;
    logic        tx;
    logic        tx_done;
    logic        tx_busy;

    always #5 clk = ~clk; // 100 MHz

    uart_tx #(.DATA_WIDTH(8)) uut (
        .clk(clk), .resetn(resetn),
        .wr_data(wr_data), .wr_uart(wr_uart),
        .baud_tick(baud_tick),
        .tx(tx), .tx_done(tx_done), .tx_busy(tx_busy)
    );

    // Generate baud tick every 10 cycles (fast for simulation)
    logic [3:0] baud_count = 0;
    always_ff @(posedge clk) begin
        if (!resetn) begin
            baud_count <= '0;
            baud_tick  <= 1'b0;
        end else begin
            baud_tick <= 1'b0;
            if (baud_count == 9) begin
                baud_count <= '0;
                baud_tick  <= 1'b1;
            end else begin
                baud_count <= baud_count + 1'b1;
            end
        end
    end

    int pass = 0;
    int fail = 0;

    task automatic check(logic got, logic exp, string name);
        if (got === exp) begin
            $display("  PASS [%0s]", name);
            pass++;
        end else begin
            $display("  FAIL [%0s] got=%b exp=%b", name, got, exp);
            fail++;
        end
    endtask

    // Capture one full UART frame
    // Returns: {stop, d7, d6, d5, d4, d3, d2, d1, d0, start} = 10 bits
    logic [9:0] captured_frame;
    task automatic capture_frame();
        // Wait for start bit (tx goes low)
        @(posedge clk);
        while (tx !== 1'b0) @(posedge clk);

        // Sample at each baud_tick
        @(posedge baud_tick);
        captured_frame[0] = tx; // start bit

        for (int b = 1; b <= 9; b++) begin
            @(posedge baud_tick);
            captured_frame[b] = tx;
        end
    endtask

    // Send one byte and verify the frame
    task automatic send_and_check(logic [7:0] data, string name);
        logic [7:0] received;
        logic       start_bit, stop_bit;

        @(posedge clk);
        wr_data <= data;
        wr_uart <= 1;
        @(posedge clk);
        wr_uart <= 0;

        capture_frame();

        start_bit = captured_frame[0];
        received  = captured_frame[8:1];
        stop_bit  = captured_frame[9];

        $display("  Sending 0x%02x — start=%b data=%08b stop=%b",
                 data, start_bit, received, stop_bit);

        check(start_bit, 1'b0, "start_bit");
        check(stop_bit,  1'b1, "stop_bit");
        if (received === data) begin
            $display("  PASS [%0s_data] got=%08b", name, received);
            pass++;
        end else begin
            $display("  FAIL [%0s_data] got=%08b exp=%08b", name, received, data);
            fail++;
        end

        // Wait for tx_done then one more cycle for tx_busy to clear
        @(posedge clk);
        while (!tx_done) @(posedge clk);
        @(posedge clk);
        check(tx_busy, 1'b0, "tx_busy_clear");
    endtask

    initial begin
        $dumpfile("tb/uart_tx_tb.vcd");
        $dumpvars(0, uart_tx_tb);

        repeat(5) @(posedge clk);
        resetn <= 1;
        repeat(5) @(posedge clk);

        // ── Test 1: idle line is high ────────────────────────────────────────
        $display("\n── Test 1: Idle line ──");
        check(tx, 1'b1, "idle_high");
        check(tx_busy, 1'b0, "idle_not_busy");

        // ── Test 2: send 0x55 (01010101) ────────────────────────────────────
        $display("\n── Test 2: Send 0x55 (01010101) ──");
        send_and_check(8'h55, "0x55");

        // ── Test 3: send 0xAA (10101010) ────────────────────────────────────
        $display("\n── Test 3: Send 0xAA (10101010) ──");
        send_and_check(8'hAA, "0xAA");

        // ── Test 4: send 0x00 ───────────────────────────────────────────────
        $display("\n── Test 4: Send 0x00 ──");
        send_and_check(8'h00, "0x00");

        // ── Test 5: send 0xFF ───────────────────────────────────────────────
        $display("\n── Test 5: Send 0xFF ──");
        send_and_check(8'hFF, "0xFF");

        // ── Test 6: send ASCII 'H' 'i' ──────────────────────────────────────
        $display("\n── Test 6: Send 'H' (0x48) ──");
        send_and_check(8'h48, "H");

        $display("\n── Test 7: Send 'i' (0x69) ──");
        send_and_check(8'h69, "i");

        // ── Test 8: tx_busy during transmission ─────────────────────────────
        $display("\n── Test 8: tx_busy during transmission ──");
        @(posedge clk);
        wr_data <= 8'hAB;
        wr_uart <= 1;
        @(posedge clk);
        wr_uart <= 0;
        @(posedge clk);
        check(tx_busy, 1'b1, "tx_busy_during_tx");
        // wait for done
        while (!tx_done) @(posedge clk);

        // ── Test 9: back-to-back transmissions ──────────────────────────────
        $display("\n── Test 9: Back-to-back ──");
        send_and_check(8'h12, "back2back_1");
        send_and_check(8'h34, "back2back_2");
        send_and_check(8'h56, "back2back_3");

        // ── Test 10: reset during transmission ──────────────────────────────
        $display("\n── Test 10: Reset during TX ──");
        @(posedge clk);
        wr_data <= 8'hFF;
        wr_uart <= 1;
        @(posedge clk);
        wr_uart <= 0;
        repeat(3) @(posedge clk);
        resetn <= 0;
        @(posedge clk);
        check(tx,      1'b1, "tx_high_after_reset");
        check(tx_busy, 1'b0, "busy_clear_after_reset");
        resetn <= 1;

        repeat(5) @(posedge clk);
        $display("\n═══════════════════════════════════════");
        $display("Results: %0d passed, %0d failed", pass, fail);
        if (fail == 0) $display("ALL TESTS PASSED.");
        else           $display("SOME TESTS FAILED.");
        $finish;
    end

endmodule
