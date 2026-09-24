// Example: Hello World via UART (9600 baud)

module top (
    input  logic clk,
    input  logic cpu_resetn,
    output logic uart_tx
);
    parameter TX_LIMIT = 10415;
    parameter RX_LIMIT = 651;

    logic       tx_done;
    logic       tx_busy;
    logic [7:0] tx_data  = 0;
    logic       tx_start = 0;

    uart #(
        .TX_LIMIT(TX_LIMIT),
        .RX_LIMIT(RX_LIMIT)
    ) u_uart (
        .clk      (clk),
        .resetn   (cpu_resetn),
        .tx_data  (tx_data),
        .tx_start (tx_start),
        .tx       (uart_tx),
        .tx_done  (tx_done),
        .tx_busy  (tx_busy),
        .rx       (1'b1),
        .rx_data  (),
        .rx_done  (),
        .rx_busy  ()
    );

    logic [7:0] msg [0:12];
    logic [3:0] idx  = 0;
    logic       sent = 0;

    initial begin
        msg[0]  = "H"; msg[1]  = "e"; msg[2]  = "l";
        msg[3]  = "l"; msg[4]  = "o"; msg[5]  = " ";
        msg[6]  = "W"; msg[7]  = "o"; msg[8]  = "r";
        msg[9]  = "l"; msg[10] = "d"; msg[11] = "!";
        msg[12] = 8'h0A;
    end

    always_ff @(posedge clk or negedge cpu_resetn) begin
        if (!cpu_resetn) begin
            idx      <= '0;
            sent     <= 1'b0;
            tx_start <= 1'b0;
            tx_data  <= '0;
        end else begin
            tx_start <= 1'b0;
            if (!sent) begin
                if (idx == 0 || tx_done) begin
                    if (idx <= 12) begin
                        tx_data  <= msg[idx];
                        tx_start <= 1'b1;
                        idx      <= idx + 1'b1;
                    end else begin
                        sent <= 1'b1;
                    end
                end
            end
        end
    end

endmodule
