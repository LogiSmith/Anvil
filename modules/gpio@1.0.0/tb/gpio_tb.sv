`timescale 1ns/1ps

module gpio_tb;

    parameter int WIDTH = 8;

    logic              clk = 0;
    logic              resetn = 0;
    logic [WIDTH-1:0]  dir = 0;
    logic [WIDTH-1:0]  out_data = 0;
    logic [WIDTH-1:0]  in_data;
    logic [WIDTH-1:0]  pin_out;
    logic [WIDTH-1:0]  pin_oe;
    logic [WIDTH-1:0]  pin_in_drive = 0;

    always #5 clk = ~clk;

    gpio #(.WIDTH(WIDTH)) u_gpio (
        .clk      (clk),
        .resetn   (resetn),
        .dir      (dir),
        .out_data (out_data),
        .in_data  (in_data),
        .pin_out  (pin_out),
        .pin_oe   (pin_oe),
        .pin_in   (pin_in_drive)
    );

    int pass = 0, fail = 0;

    task automatic check_byte(string name, logic [WIDTH-1:0] actual, logic [WIDTH-1:0] expected);
        if (actual === expected) begin
            $display("[PASS] %0s = 0x%02x", name, actual);
            pass++;
        end else begin
            $display("[FAIL] %0s: expected 0x%02x, got 0x%02x", name, expected, actual);
            fail++;
        end
    endtask

    initial begin
        $dumpfile("tb/gpio_tb.vcd");
        $dumpvars(0, gpio_tb);

        repeat(3) @(posedge clk);
        resetn <= 1;
        repeat(3) @(posedge clk);

        $display("\n=== Test 1: All output ===");
        dir      = 8'hFF;
        out_data = 8'hA5;
        @(posedge clk);
        check_byte("pin_out drives", pin_out, 8'hA5);
        check_byte("pin_oe = dir",   pin_oe,  8'hFF);

        $display("\n=== Test 2: Change output ===");
        out_data = 8'h5A;
        @(posedge clk);
        check_byte("pin_out updated", pin_out, 8'h5A);

        $display("\n=== Test 3: Input mode ===");
        dir = 8'h00;
        pin_in_drive = 8'h33;
        repeat(5) @(posedge clk);
        check_byte("pin_oe = 0 (no drive)", pin_oe, 8'h00);
        check_byte("in_data = pin_in",      in_data, 8'h33);

        $display("\n=== Test 4: Mixed direction ===");
        dir          = 8'h0F;       // bits 3:0 output
        out_data     = 8'hFF;
        pin_in_drive = 8'hAA;
        repeat(5) @(posedge clk);
        check_byte("pin_oe shows mask",   pin_oe, 8'h0F);
        check_byte("pin_out has output",  pin_out, 8'hFF);
        check_byte("in_data reads pin_in", in_data, 8'hAA);

        $display("\n=== Test 5: Reset ===");
        dir          = 8'hFF;
        pin_in_drive = 8'hFF;
        @(posedge clk);
        resetn <= 0;
        repeat(3) @(posedge clk);
        check_byte("Reset clears in_data", in_data, 8'h00);
        resetn <= 1;
        repeat(5) @(posedge clk);

        $display("\n=== Test 6: Sync delay ===");
        pin_in_drive = 8'h99;
        repeat(5) @(posedge clk);
        check_byte("Synced input", in_data, 8'h99);

        $display("\n========================================");
        $display("[GPIO TB] %0d passed, %0d failed", pass, fail);
        $display("========================================");
        $finish;
    end

    initial begin
        #(100_000);
        $display("[GPIO TB] TIMEOUT");
        $finish;
    end

endmodule
