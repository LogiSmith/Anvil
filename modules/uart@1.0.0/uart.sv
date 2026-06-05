// uart
// Full UART — TX + RX with shared baud rate generator
// TX uses 1x baud tick, RX uses 16x oversample tick
// Both generated from same baud_rate_generator with different limits
//
// Baud rate configuration (100 MHz clock):
//   tx_limit = CLK_FREQ / BAUD_RATE - 1
//   rx_limit = CLK_FREQ / (BAUD_RATE * 16) - 1
//
// Default: 115200 baud @ 100 MHz
//   tx_limit = 867
//   rx_limit = 53

`timescale 1ns/1ps

module uart #(
    parameter DATA_WIDTH = 8,
    parameter TX_LIMIT   = 867,   // 115200 baud TX @ 100 MHz
    parameter RX_LIMIT   = 53     // 115200 baud RX 16x @ 100 MHz
) (
    input  logic                  clk,
    input  logic                  resetn,

    // TX interface
    input  logic [DATA_WIDTH-1:0] tx_data,
    input  logic                  tx_start,
    output logic                  tx,
    output logic                  tx_done,
    output logic                  tx_busy,

    // RX interface
    input  logic                  rx,
    output logic [DATA_WIDTH-1:0] rx_data,
    output logic                  rx_done,
    output logic                  rx_busy
);
    logic baud_tick_tx;
    logic baud_tick_rx;

    // Reset TX baud counter when tx_start fires — ensures start bit is always a full period
    // Without this, the counter phase is random and start bit could be < 1 period
    logic baud_tx_resetn;
    assign baud_tx_resetn = resetn && !tx_start;

    // TX baud generator (1x)
    baud_rate_generator #(.COUNTER_WIDTH(16)) u_baud_tx (
        .clk    (clk),
        .resetn (baud_tx_resetn),
        .limit  (TX_LIMIT[15:0]),
        .tick   (baud_tick_tx)
    );

    // RX baud generator (16x oversampling)
    baud_rate_generator #(.COUNTER_WIDTH(16)) u_baud_rx (
        .clk    (clk),
        .resetn (resetn),
        .limit  (RX_LIMIT[15:0]),
        .tick   (baud_tick_rx)
    );

    // TX
    uart_tx #(.DATA_WIDTH(DATA_WIDTH)) u_tx (
        .clk       (clk),
        .resetn    (resetn),
        .wr_data   (tx_data),
        .wr_uart   (tx_start),
        .baud_tick (baud_tick_tx),
        .tx        (tx),
        .tx_done   (tx_done),
        .tx_busy   (tx_busy)
    );

    // RX
    uart_rx #(.DATA_WIDTH(DATA_WIDTH)) u_rx (
        .clk         (clk),
        .resetn      (resetn),
        .rx          (rx),
        .sample_tick (baud_tick_rx),
        .data_out    (rx_data),
        .rx_done     (rx_done),
        .rx_busy     (rx_busy)
    );

endmodule
