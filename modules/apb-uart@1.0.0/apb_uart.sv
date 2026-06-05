// apb_uart
// APB slave wrapper around uart module
//
// Register map:
//   0x00  TX_DATA  [7:0]  write: send byte (stalls until UART ready)
//   0x04  RX_DATA  [7:0]  read: received byte (stalls until byte available)
//   0x08  STATUS   [0] tx_busy, [1] rx_ready
//
// Baud rate parameters (CLK_FREQ = 100 MHz):
//   9600   baud: TX_LIMIT=10415, RX_LIMIT=651
//   115200 baud: TX_LIMIT=867,   RX_LIMIT=53

`timescale 1ns/1ps

module apb_uart #(
    parameter TX_LIMIT = 16'd10415,
    parameter RX_LIMIT = 16'd651
) (
    input  logic        clk,
    input  logic        resetn,

    input  logic [31:0] PADDR,
    input  logic        PSEL,
    input  logic        PENABLE,
    input  logic        PWRITE,
    input  logic [31:0] PWDATA,
    output logic [31:0] PRDATA,
    output logic        PREADY,
    output logic        PSLVERR,

    output logic        uart_tx,
    input  logic        uart_rx
);
    assign PSLVERR = 1'b0;

    logic       tx_done;
    logic       tx_busy;
    logic [7:0] rx_data;
    logic       rx_done;
    logic       rx_busy;

    logic [7:0] tx_data  = '0;
    logic       tx_start = 1'b0;

    logic [7:0] rx_buf   = '0;
    logic       rx_ready = 1'b0;

    uart #(
        .DATA_WIDTH(8),
        .TX_LIMIT  (TX_LIMIT),
        .RX_LIMIT  (RX_LIMIT)
    ) u_uart (
        .clk      (clk),
        .resetn   (resetn),
        .tx_data  (tx_data),
        .tx_start (tx_start),
        .tx       (uart_tx),
        .tx_done  (tx_done),
        .tx_busy  (tx_busy),
        .rx       (uart_rx),
        .rx_data  (rx_data),
        .rx_done  (rx_done),
        .rx_busy  (rx_busy)
    );

    // RX buffer
    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            rx_buf   <= '0;
            rx_ready <= 1'b0;
        end else begin
            if (rx_done) begin
                rx_buf   <= rx_data;
                rx_ready <= 1'b1;
            end else if (PSEL && PENABLE && !PWRITE && PADDR[3:2] == 2'd1)
                rx_ready <= 1'b0;
        end
    end

    // APB register access
    always_ff @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            PREADY   <= 1'b0;
            PRDATA   <= '0;
            tx_start <= 1'b0;
            tx_data  <= '0;
        end else begin
            tx_start <= 1'b0;
            PREADY   <= 1'b0;

            if (PSEL && PENABLE) begin
                if (PWRITE && PADDR[3:2] == 2'd0) begin
                    // TX_DATA write — stall until not busy AND not just started
                    // tx_start check prevents race: tx_busy takes 1 cycle to assert
                    if (!tx_busy && !tx_start) begin
                        tx_data  <= PWDATA[7:0];
                        tx_start <= 1'b1;
                        PREADY   <= 1'b1;
                    end
                    // else: PREADY stays 0, CPU stalls
                end else if (!PWRITE && PADDR[3:2] == 2'd1) begin
                    // RX_DATA read — stall until byte available
                    if (rx_ready) begin
                        PRDATA <= {24'b0, rx_buf};
                        PREADY <= 1'b1;
                    end
                    // else: PREADY stays 0, CPU stalls
                end else begin
                    // All other accesses complete immediately
                    PREADY <= 1'b1;
                    if (!PWRITE) begin
                        unique case (PADDR[3:2])
                            2'd0:    PRDATA <= {24'b0, tx_data};
                            2'd2:    PRDATA <= {30'b0, rx_ready, tx_busy};
                            default: PRDATA <= 32'b0;
                        endcase
                    end
                end
            end
        end
    end

endmodule
