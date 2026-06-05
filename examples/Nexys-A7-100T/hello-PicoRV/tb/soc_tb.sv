// soc_tb.sv — soc-hello-v2 self-checking testbench (FPRO SoC)
// Receives "Hello from PicoRV32!\n" via simulated UART RX (top.uart_tx)
// PASS if all 21 bytes match, FAIL otherwise.

`timescale 1ns/1ps

module soc_tb;

    logic clk    = 0;
    logic resetn = 0;
    always #5 clk = ~clk;

    logic uart_tx;
    logic uart_rx;
    assign uart_rx = 1'b1;

    top #(.TX_LIMIT(31), .RX_LIMIT(1)) u_top (
        .clk        (clk),
        .cpu_resetn (resetn),
        .uart_tx    (uart_tx),
        .uart_rx    (uart_rx),
        .led0       (),
        .led1       ()
    );

    localparam int BIT_PERIOD   = 32;
    localparam int HALF_BIT     = BIT_PERIOD / 2;
    localparam int EXPECTED_LEN = 21;

    task automatic recv_byte(output logic [7:0] data);
        @(negedge uart_tx);
        repeat(HALF_BIT) @(posedge clk);
        data = '0;
        for (int i = 0; i < 8; i++) begin
            repeat(BIT_PERIOD) @(posedge clk);
            data = data | (uart_tx << i);
        end
        repeat(BIT_PERIOD) @(posedge clk);
    endtask

    logic [7:0] rx_byte;
    logic [7:0] expected [0:EXPECTED_LEN-1];
    int idx;
    int fail_count;

    initial begin
        // "Hello from PicoRV32!\n"
        expected[ 0] = "H"; expected[ 1] = "e"; expected[ 2] = "l"; expected[ 3] = "l";
        expected[ 4] = "o"; expected[ 5] = " "; expected[ 6] = "f"; expected[ 7] = "r";
        expected[ 8] = "o"; expected[ 9] = "m"; expected[10] = " "; expected[11] = "P";
        expected[12] = "i"; expected[13] = "c"; expected[14] = "o"; expected[15] = "R";
        expected[16] = "V"; expected[17] = "3"; expected[18] = "2"; expected[19] = "!";
        expected[20] = 8'h0A;
    end

    initial begin
        $dumpfile("tb/soc_tb.vcd");
        $dumpvars(0, soc_tb);

        fail_count = 0;
        repeat(10) @(posedge clk);
        resetn <= 1;
        $display("[TB] Reset released, CPU starting...");

        for (idx = 0; idx < EXPECTED_LEN; idx++) begin
            recv_byte(rx_byte);
            $write("%c", rx_byte);
            if (rx_byte !== expected[idx]) begin
                $display("\n[TB] FAIL at byte %0d: expected 0x%02x, got 0x%02x",
                    idx, expected[idx], rx_byte);
                fail_count++;
            end
        end

        $display("");
        if (fail_count == 0)
            $display("[TB] PASS — received '%s' (%0d bytes)", "Hello from PicoRV32!", EXPECTED_LEN);
        else
            $display("[TB] FAIL — %0d byte mismatches", fail_count);
        $finish;
    end

    initial begin
        #(5_000_000);
        $display("\n[TB] TIMEOUT");
        $finish;
    end

endmodule
