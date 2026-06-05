// uart_rx
// UART Receiver — 8N1 with 16x oversampling
// Samples in the middle of each bit for noise immunity
// Interface:
//   rx       : serial input (idle = 1)
//   data_out : received byte (valid when rx_done pulses)
//   rx_done  : 1-cycle pulse when byte received
//   rx_busy  : high while receiving

`timescale 1ns/1ps

module uart_rx #(
    parameter DATA_WIDTH = 8
) (
    input  logic                   clk,
    input  logic                   resetn,
    input  logic                   rx,
    input  logic                   sample_tick,  // 16x baud rate tick
    output logic [DATA_WIDTH-1:0]  data_out,
    output logic                   rx_done,
    output logic                   rx_busy
);
    typedef enum logic [1:0] {
        IDLE  = 2'b00,
        START = 2'b01,
        DATA  = 2'b10,
        STOP  = 2'b11
    } state_t;

    state_t                state;
    logic [DATA_WIDTH-1:0] shift_reg;
    logic [3:0]            s_count;   // sample tick counter (0-15)
    logic [3:0]            bit_count; // received bit counter

    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            state     <= IDLE;
            shift_reg <= '0;
            data_out  <= '0;
            rx_done   <= 1'b0;
            rx_busy   <= 1'b0;
            s_count   <= '0;
            bit_count <= '0;
        end else begin
            rx_done <= 1'b0; // default

            unique case (state)
                IDLE: begin
                    rx_busy <= 1'b0;
                    s_count <= '0;
                    if (!rx) begin
                        // falling edge — start bit detected
                        state   <= START;
                        rx_busy <= 1'b1;
                    end
                end

                START: begin
                    // Wait 8 ticks to sample middle of start bit
                    if (sample_tick) begin
                        if (s_count == 7) begin
                            s_count   <= '0;
                            bit_count <= '0;
                            state     <= DATA;
                        end else begin
                            s_count <= s_count + 1'b1;
                        end
                    end
                end

                DATA: begin
                    // Sample at tick 15 (middle of bit)
                    if (sample_tick) begin
                        if (s_count == 15) begin
                            s_count   <= '0;
                            shift_reg <= {rx, shift_reg[DATA_WIDTH-1:1]}; // LSB first
                            if (bit_count == DATA_WIDTH - 1) begin
                                state <= STOP;
                            end else begin
                                bit_count <= bit_count + 1'b1;
                            end
                        end else begin
                            s_count <= s_count + 1'b1;
                        end
                    end
                end

                STOP: begin
                    // Wait for stop bit
                    if (sample_tick) begin
                        if (s_count == 15) begin
                            data_out <= shift_reg;
                            rx_done  <= 1'b1;
                            state    <= IDLE;
                        end else begin
                            s_count <= s_count + 1'b1;
                        end
                    end
                end
            endcase
        end
    end

endmodule
