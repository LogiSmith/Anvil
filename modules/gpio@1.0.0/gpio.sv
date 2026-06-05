// gpio.sv — Generic GPIO core (SystemVerilog)
// Separated input/output ports — top-level decides if it uses tristate
// or separates into LED/switch pins.

`timescale 1ns/1ps

module gpio #(
    parameter int WIDTH = 32
) (
    input  logic             clk,
    input  logic             resetn,

    // Software interface
    input  logic [WIDTH-1:0] dir,        // 1=output, 0=input (informational only)
    input  logic [WIDTH-1:0] out_data,   // value to drive on output pins
    output logic [WIDTH-1:0] in_data,    // sampled input pin values

    // Pin interface (separated)
    output logic [WIDTH-1:0] pin_out,    // drive these to physical output pins
    output logic [WIDTH-1:0] pin_oe,     // output enable (use for tristate if needed)
    input  logic [WIDTH-1:0] pin_in      // read from physical input pins
);

    assign pin_oe  = dir;
    assign pin_out = out_data;

    // 2-stage synchronizer for inputs (metastability protection)
    logic [WIDTH-1:0] sync1, sync2;

    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            sync1   <= '0;
            sync2   <= '0;
            in_data <= '0;
        end else begin
            sync1   <= pin_in;
            sync2   <= sync1;
            in_data <= sync2;
        end
    end

endmodule
