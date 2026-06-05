`timescale 1ns/1ps

module apb_uart_tb;

    logic        clk    = 0;
    logic        resetn = 0;
    logic [31:0] PADDR  = 0;
    logic        PSEL   = 0;
    logic        PENABLE= 0;
    logic        PWRITE = 0;
    logic [31:0] PWDATA = 0;
    logic [31:0] PRDATA;
    logic        PREADY;
    logic        PSLVERR;
    logic        uart_tx;

    always #5 clk = ~clk;

    apb_uart #(
        .TX_LIMIT(16'd31),
        .RX_LIMIT(16'd1)
    ) uut (
        .clk     (clk),   .resetn  (resetn),
        .PADDR   (PADDR), .PSEL    (PSEL),
        .PENABLE (PENABLE),.PWRITE (PWRITE),
        .PWDATA  (PWDATA), .PRDATA (PRDATA),
        .PREADY  (PREADY), .PSLVERR(PSLVERR),
        .uart_tx (uart_tx),.uart_rx (uart_tx)  // loopback
    );

    int pass = 0, fail = 0;

    task automatic check(logic [31:0] got, logic [31:0] exp, string name);
        if (got === exp) begin $display("  PASS [%0s] got=0x%02x", name, got); pass++; end
        else begin $display("  FAIL [%0s] got=0x%02x exp=0x%02x", name, got, exp); fail++; end
    endtask

    task automatic apb_write(logic [31:0] addr, logic [31:0] data);
        @(posedge clk);
        PADDR<=addr; PWDATA<=data; PSEL<=1; PWRITE<=1;
        @(posedge clk); PENABLE<=1;
        @(posedge clk); while(!PREADY) @(posedge clk);
        PSEL<=0; PENABLE<=0; PWRITE<=0;
        @(posedge clk);
    endtask

    logic [31:0] apb_rdata;
    task automatic apb_read(logic [31:0] addr);
        int wait_count;
        @(posedge clk);
        PADDR<=addr; PWRITE<=0; PSEL<=1;
        @(posedge clk); PENABLE<=1;
        // Wait for PREADY — may take many cycles if APB slave is stalling
        wait_count = 0;
        @(posedge clk);
        while(!PREADY && wait_count < 100000) begin
            @(posedge clk);
            wait_count++;
        end
        apb_rdata<=PRDATA;
        PSEL<=0; PENABLE<=0;
        @(posedge clk);
    endtask

    task automatic send_recv(logic [7:0] data, string name);
        // TX: APB stalls automatically until UART not busy
        apb_write(32'h00, {24'b0, data});
        // RX: APB stalls automatically until rx_ready=1
        // apb_read task handles the stall — PREADY stays 0 until data ready
        apb_read(32'h04);
        check(apb_rdata[7:0], data, name);
    endtask

    initial begin
        $dumpfile("tb/apb_uart_tb.vcd");
        $dumpvars(0, apb_uart_tb);
        repeat(5) @(posedge clk); resetn<=1; repeat(5) @(posedge clk);

        $display("\n── Test 1: Status idle ──");
        apb_read(32'h08);
        check(apb_rdata[0], 1'b0, "tx_idle");
        check(apb_rdata[1], 1'b0, "rx_idle");

        $display("\n── Test 2: Loopback 0x55 ──");
        send_recv(8'h55, "loopback_0x55");

        $display("\n── Test 3: Loopback 0xAA ──");
        send_recv(8'hAA, "loopback_0xAA");

        $display("\n── Test 4: Loopback 0x00 ──");
        send_recv(8'h00, "loopback_0x00");

        $display("\n── Test 5: Loopback 0xFF ──");
        send_recv(8'hFF, "loopback_0xFF");

        $display("\n── Test 6: ASCII 'H' 'i' ──");
        send_recv(8'h48, "H");
        send_recv(8'h69, "i");

        $display("\n── Test 7: APB stalls on TX_DATA write when busy ──");
        // Send first byte — returns immediately (not busy)
        apb_write(32'h00, 32'hCD);
        // Immediately try to send second byte — APB should stall until first is done
        apb_write(32'h00, 32'hEF);  // this stalls until CD is sent
        // Wait for RX to receive both bytes
        apb_read(32'h08);
        while(!apb_rdata[1]) apb_read(32'h08);
        apb_read(32'h04);
        check(apb_rdata[7:0], 8'hCD, "stall_first_byte");
        apb_read(32'h08);
        while(!apb_rdata[1]) apb_read(32'h08);
        apb_read(32'h04);
        check(apb_rdata[7:0], 8'hEF, "stall_second_byte");

        $display("\n── Test 8: rx_ready clears on read ──");
        send_recv(8'h42, "rx_clear_send");
        apb_read(32'h08);
        check(apb_rdata[1], 1'b0, "rx_ready_cleared");

        $display("\n── Test 9: Back-to-back ──");
        send_recv(8'h11, "seq_1");
        send_recv(8'h22, "seq_2");
        send_recv(8'h33, "seq_3");

        repeat(5) @(posedge clk);
        $display("\n═══════════════════════════════════════");
        $display("Results: %0d passed, %0d failed", pass, fail);
        if(fail==0) $display("ALL TESTS PASSED.");
        else        $display("SOME TESTS FAILED.");
        $finish;
    end

endmodule
