`timescale 1ns/1ps

module apb_gpio_tb;

    parameter int WIDTH = 8;

    logic         clk = 0;
    logic         resetn = 0;
    logic [31:0]  PADDR = 0;
    logic         PSEL = 0;
    logic         PENABLE = 0;
    logic         PWRITE = 0;
    logic [31:0]  PWDATA = 0;
    logic [31:0]  PRDATA;
    logic         PREADY;
    logic         PSLVERR;

    logic [WIDTH-1:0] pin_out;
    logic [WIDTH-1:0] pin_oe;
    logic [WIDTH-1:0] pin_in_drive = 0;

    always #5 clk = ~clk;

    apb_gpio #(.WIDTH(WIDTH)) u (
        .clk(clk), .resetn(resetn),
        .PADDR(PADDR), .PSEL(PSEL), .PENABLE(PENABLE), .PWRITE(PWRITE),
        .PWDATA(PWDATA), .PRDATA(PRDATA), .PREADY(PREADY), .PSLVERR(PSLVERR),
        .pin_out(pin_out), .pin_oe(pin_oe), .pin_in(pin_in_drive)
    );

    int pass = 0, fail = 0;

    task automatic apb_write(logic [3:0] addr, logic [31:0] data);
        @(posedge clk);
        PADDR   <= {28'h0, addr};
        PWDATA  <= data;
        PWRITE  <= 1;
        PSEL    <= 1;
        PENABLE <= 0;
        @(posedge clk);
        PENABLE <= 1;
        wait(PREADY);
        @(posedge clk);
        PSEL    <= 0;
        PENABLE <= 0;
        PWRITE  <= 0;
    endtask

    task automatic apb_read(logic [3:0] addr, output logic [31:0] data);
        @(posedge clk);
        PADDR   <= {28'h0, addr};
        PWRITE  <= 0;
        PSEL    <= 1;
        PENABLE <= 0;
        @(posedge clk);
        PENABLE <= 1;
        wait(PREADY);
        data = PRDATA;
        @(posedge clk);
        PSEL    <= 0;
        PENABLE <= 0;
    endtask

    task automatic check32(string name, logic [31:0] actual, logic [31:0] expected);
        if (actual === expected) begin
            $display("[PASS] %0s = 0x%08x", name, actual);
            pass++;
        end else begin
            $display("[FAIL] %0s: expected 0x%08x, got 0x%08x", name, expected, actual);
            fail++;
        end
    endtask

    logic [31:0] rd;

    initial begin
        $dumpfile("tb/apb_gpio_tb.vcd");
        $dumpvars(0, apb_gpio_tb);

        repeat(3) @(posedge clk);
        resetn <= 1;
        repeat(3) @(posedge clk);

        $display("\n=== Test 1: Reset state ===");
        apb_read(4'h0, rd); check32("DIR after reset",  rd, 32'h0);
        apb_read(4'h4, rd); check32("OUT after reset",  rd, 32'h0);

        $display("\n=== Test 2: Write/read DIR ===");
        apb_write(4'h0, 32'hFF);
        apb_read (4'h0, rd); check32("DIR readback", rd, 32'hFF);

        $display("\n=== Test 3: Write OUT, verify pin_out drives ===");
        apb_write(4'h4, 32'hA5);
        @(posedge clk);
        check32("pin_out", {{24{1'b0}}, pin_out}, 32'hA5);
        check32("pin_oe (= DIR)", {{24{1'b0}}, pin_oe}, 32'hFF);

        $display("\n=== Test 4: Read IN ===");
        pin_in_drive = 8'h33;
        repeat(5) @(posedge clk);   // wait for sync
        apb_read(4'h8, rd); check32("IN reflects pin_in", rd, 32'h33);

        $display("\n=== Test 5: Mixed dir, IN reads regardless ===");
        apb_write(4'h0, 32'h0F);    // 4 lower output, 4 upper input
        apb_write(4'h4, 32'hCC);    // out value
        pin_in_drive = 8'hAA;
        repeat(5) @(posedge clk);
        apb_read(4'h8, rd); check32("IN reads all 8 pins", rd, 32'hAA);
        check32("pin_oe = 0x0F", {{24{1'b0}}, pin_oe}, 32'h0F);
        check32("pin_out = 0xCC", {{24{1'b0}}, pin_out}, 32'hCC);

        $display("\n=== Test 6: Write to IN address (should be ignored) ===");
        apb_write(4'h8, 32'hDEADBEEF);   // try to write to IN
        pin_in_drive = 8'h77;
        repeat(5) @(posedge clk);
        apb_read(4'h8, rd); check32("IN still reads pin_in", rd, 32'h77);

        $display("\n========================================");
        $display("[APB GPIO TB] %0d passed, %0d failed", pass, fail);
        $display("========================================");
        $finish;
    end

    initial begin
        #(100_000);
        $display("[APB GPIO TB] TIMEOUT");
        $finish;
    end

endmodule
