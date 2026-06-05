// apb_gpio.sv — APB wrapper for GPIO core
//
// Register map (4-byte aligned):
//   0x00  DIR   (R/W)  [WIDTH-1:0]  — direction (1=output, 0=input)
//   0x04  OUT   (R/W)  [WIDTH-1:0]  — output value (drives when dir=1)
//   0x08  IN    (RO)   [WIDTH-1:0]  — input value (sampled pin state)

`timescale 1ns/1ps

module apb_gpio #(
    parameter WIDTH = 32
) (
    input  logic        clk,
    input  logic        resetn,

    // APB slave
    input  logic [31:0] PADDR,
    input  logic        PSEL,
    input  logic        PENABLE,
    input  logic        PWRITE,
    input  logic [31:0] PWDATA,
    output logic [31:0] PRDATA,
    output logic        PREADY,
    output logic        PSLVERR,

    // Pin interface
    output logic [WIDTH-1:0] pin_out,
    output logic [WIDTH-1:0] pin_oe,
    input  logic [WIDTH-1:0] pin_in
);
    assign PSLVERR = 1'b0;

    // Internal registers
    logic [WIDTH-1:0] dir_reg;
    logic [WIDTH-1:0] out_reg;
    logic [WIDTH-1:0] in_reg;

    // Register write logic
    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            dir_reg <= '0;
            out_reg <= '0;
            PREADY  <= 1'b0;
            PRDATA  <= '0;
        end else begin
            PREADY <= 1'b0;
            if (PSEL && PENABLE) begin
                PREADY <= 1'b1;
                if (PWRITE) begin
                    unique case (PADDR[3:2])
                        2'd0:    dir_reg <= PWDATA[WIDTH-1:0];
                        2'd1:    out_reg <= PWDATA[WIDTH-1:0];
                        // 2'd2 (IN) is read-only — writes ignored
                        default: ;
                    endcase
                end else begin
                    unique case (PADDR[3:2])
                        2'd0:    PRDATA <= {{(32-WIDTH){1'b0}}, dir_reg};
                        2'd1:    PRDATA <= {{(32-WIDTH){1'b0}}, out_reg};
                        2'd2:    PRDATA <= {{(32-WIDTH){1'b0}}, in_reg};
                        default: PRDATA <= 32'h0;
                    endcase
                end
            end
        end
    end

    // GPIO core
    gpio #(.WIDTH(WIDTH)) u_gpio (
        .clk      (clk),
        .resetn   (resetn),
        .dir      (dir_reg),
        .out_data (out_reg),
        .in_data  (in_reg),
        .pin_out  (pin_out),
        .pin_oe   (pin_oe),
        .pin_in   (pin_in)
    );

endmodule
