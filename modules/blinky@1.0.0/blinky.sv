module blinky (
    input  logic        clk,
    output logic [15:0] led
);
    logic [23:0] counter = 0;
    always_ff @(posedge clk) begin
        counter <= counter + 1'b1;
        if (counter == 0)
            led <= led + 1'b1;
    end
endmodule
