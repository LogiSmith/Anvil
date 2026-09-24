// Example: Switches to LEDs
// Nexys A7 50T — SW[15:0] -> LED[15:0]

module top (
    input  logic [15:0] sw,
    output logic [15:0] led
);

    assign led = sw;

endmodule
