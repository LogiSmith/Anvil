`timescale 1ns/1ps

module uart_tb;

    logic        clk    = 0;
    logic        resetn = 0;
    logic [7:0]  tx_data  = 0;
    logic        tx_start = 0;
    logic        tx;
    logic        tx_done;
    logic        tx_busy;
    logic [7:0]  rx_data;
    logic        rx_done;
    logic        rx_busy;

    always #5 clk = ~clk;

    // TX=31 (32 cycles/bit), RX=1 (2 cycles/tick, 16 ticks=32 cycles/bit)
    uart #(
        .DATA_WIDTH(8),
        .TX_LIMIT  (31),
        .RX_LIMIT  (1)
    ) uut (
        .clk      (clk),
        .resetn   (resetn),
        .tx_data  (tx_data),
        .tx_start (tx_start),
        .tx       (tx),
        .tx_done  (tx_done),
        .tx_busy  (tx_busy),
        .rx       (tx),      // loopback
        .rx_data  (rx_data),
        .rx_done  (rx_done),
        .rx_busy  (rx_busy)
    );

    int pass = 0, fail = 0;

    task automatic check8(logic [7:0] got, logic [7:0] exp, string name);
        if (got === exp) begin $display("  PASS [%0s] got=0x%02x", name, got); pass++; end
        else begin $display("  FAIL [%0s] got=0x%02x exp=0x%02x", name, got, exp); fail++; end
    endtask

    task automatic check1(logic got, logic exp, string name);
        if (got === exp) begin $display("  PASS [%0s] got=%b", name, got); pass++; end
        else begin $display("  FAIL [%0s] got=%b exp=%b", name, got, exp); fail++; end
    endtask

    // Send byte and wait for RX done — waits for !tx_busy before sending
    task automatic send_recv(logic [7:0] data, string name);
        @(posedge clk);
        while (tx_busy) @(posedge clk);
        tx_data  <= data;
        tx_start <= 1;
        @(posedge clk);
        tx_start <= 0;
        @(posedge clk);
        while (!rx_done) @(posedge clk);
        check8(rx_data, data, name);
    endtask

    initial begin
        $dumpfile("tb/uart_tb.vcd");
        $dumpvars(0, uart_tb);
        repeat(5) @(posedge clk); resetn<=1; repeat(5) @(posedge clk);

        $display("\n── Test 1: Idle ──");
        check1(tx_busy, 1'b0, "tx_idle");
        check1(rx_busy, 1'b0, "rx_idle");
        check1(tx,      1'b1, "tx_line_high");

        $display("\n── Test 2: Loopback 0x55 ──");
        send_recv(8'h55, "loopback_0x55");

        $display("\n── Test 3: Loopback 0xAA ──");
        send_recv(8'hAA, "loopback_0xAA");

        $display("\n── Test 4: Loopback 0x00 ──");
        send_recv(8'h00, "loopback_0x00");

        $display("\n── Test 5: Loopback 0xFF ──");
        send_recv(8'hFF, "loopback_0xFF");

        $display("\n── Test 6: ASCII 'Hello' ──");
        send_recv(8'h48, "H");
        send_recv(8'h65, "e");
        send_recv(8'h6C, "l");
        send_recv(8'h6C, "l");
        send_recv(8'h6F, "o");

        $display("\n── Test 7: Edge bits ──");
        send_recv(8'h01, "lsb_only");
        send_recv(8'h80, "msb_only");

        $display("\n── Test 8: tx_done pulse ──");
        @(posedge clk);
        while (tx_busy) @(posedge clk);
        tx_data  <= 8'hAB;
        tx_start <= 1;
        @(posedge clk);
        tx_start <= 0;
        while (!tx_done) @(posedge clk);
        check1(tx_done, 1'b1, "tx_done_pulse");
        @(posedge clk);
        check1(tx_done, 1'b0, "tx_done_oneshot");

        $display("\n── Test 9: Back-to-back ──");
        send_recv(8'h11, "seq_1");
        send_recv(8'h22, "seq_2");
        send_recv(8'h33, "seq_3");
        send_recv(8'h44, "seq_4");

        $display("\n── Test 10: Reset ──");
        while (tx_busy) @(posedge clk);
        tx_data  <= 8'hFF;
        tx_start <= 1;
        @(posedge clk);
        tx_start <= 0;
        repeat(20) @(posedge clk);
        resetn <= 0;
        @(posedge clk);
        check1(tx_busy, 1'b0, "tx_busy_after_reset");
        check1(tx,      1'b1, "tx_high_after_reset");
        resetn <= 1;

        repeat(5) @(posedge clk);
        $display("\n═══════════════════════════════════════");
        $display("Results: %0d passed, %0d failed", pass, fail);
        if (fail == 0) $display("ALL TESTS PASSED.");
        else           $display("SOME TESTS FAILED.");
        $finish;
    end

endmodule
