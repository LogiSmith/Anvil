// uart_tx
// UART Transmitter — 8N1 (8 data bits, no parity, 1 stop bit)
// Interface:
//   wr_data  : byte to transmit
//   wr_uart  : 1-cycle pulse to start transmission
//   tx       : serial output (idle = 1)
//   tx_done  : 1-cycle pulse when transmission complete
//   tx_busy  : high while transmitting

`timescale 1ns/1ps

module uart_tx #(
    parameter DATA_WIDTH = 8
) (
    input  logic                  clk,
    input  logic                  resetn,
    input  logic [DATA_WIDTH-1:0] wr_data,
    input  logic                  wr_uart,
    input  logic                  baud_tick,
    output logic                  tx,
    output logic                  tx_done,
    output logic                  tx_busy
);
    typedef enum logic [1:0] {
        IDLE  = 2'b00,
        START = 2'b01,
        DATA  = 2'b10,
        STOP  = 2'b11
    } state_t;

    state_t                  state;
    logic [DATA_WIDTH-1:0]   shift_reg;
    logic [3:0]              bit_count;

    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            state     <= IDLE;
            tx        <= 1'b1;
            tx_done   <= 1'b0;
            tx_busy   <= 1'b0;
            shift_reg <= '0;
            bit_count <= '0;
        end else begin
            tx_done <= 1'b0; // default

            unique case (state)
                IDLE: begin
                    tx      <= 1'b1;
                    tx_busy <= 1'b0;
                    if (wr_uart) begin
                        shift_reg <= wr_data;
                        tx_busy   <= 1'b1;
                        state     <= START;
                    end
                end

                START: begin
                    tx <= 1'b0; // start bit
                    if (baud_tick) begin
                        bit_count <= '0;
                        state     <= DATA;
                    end
                end

                DATA: begin
                    tx <= shift_reg[0]; // LSB first
                    if (baud_tick) begin
                        shift_reg <= {1'b0, shift_reg[DATA_WIDTH-1:1]};
                        if (bit_count == DATA_WIDTH - 1) begin
                            state <= STOP;
                        end else begin
                            bit_count <= bit_count + 1'b1;
                        end
                    end
                end

                STOP: begin
                    tx <= 1'b1; // stop bit
                    if (baud_tick) begin
                        tx_done <= 1'b1;
                        state   <= IDLE;
                    end
                end
            endcase
        end
    end

endmodule
