// baud_rate_generator
// Generic configurable baud rate tick generator
// Generates one tick pulse every (limit+1) clock cycles
//
// Usage:
//   For TX (1x): limit = CLK_FREQ / BAUD_RATE - 1
//   For RX (16x oversampling): limit = CLK_FREQ / (BAUD_RATE * 16) - 1
//
// Examples at 100 MHz:
//   9600   baud TX:  limit = 10415
//   115200 baud TX:  limit = 867
//   115200 baud RX:  limit = 53

`timescale 1ns/1ps

module baud_rate_generator #(
    parameter COUNTER_WIDTH = 16    // max limit = 2^16-1 = 65535
) (
    input  logic                     clk,
    input  logic                     resetn,
    input  logic [COUNTER_WIDTH-1:0] limit,    // configurable at runtime
    output logic                     tick
);
    logic [COUNTER_WIDTH-1:0] count;

    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            count <= '0;
            tick  <= 1'b0;
        end else begin
            if (count >= limit) begin
                count <= '0;
                tick  <= 1'b1;
            end else begin
                count <= count + 1'b1;
                tick  <= 1'b0;
            end
        end
    end

endmodule
