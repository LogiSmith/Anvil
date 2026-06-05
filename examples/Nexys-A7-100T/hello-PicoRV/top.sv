// top.sv — soc-hello-v2 with FPRO-style 64-slot SoC
// Slot 0: APB UART
// All other slots: tied off

module top #(
    parameter TX_LIMIT = 10415,    // 100MHz / 9600 baud - 1
    parameter RX_LIMIT = 651       // 100MHz / (9600*16) - 1
) (
    input  logic clk,
    input  logic cpu_resetn,
    output logic uart_tx,
    input  logic uart_rx,
    output logic led0,
    output logic led1
);
    localparam int NUM_SLOTS = 64;

    logic [31:0] PADDR;
    logic        PENABLE;
    logic        PWRITE;
    logic [31:0] PWDATA;

    // Per-slot signals
    logic [NUM_SLOTS-1:0]    SpSEL;
    logic [NUM_SLOTS*32-1:0] SpRDATA;
    logic [NUM_SLOTS-1:0]    SpSLVERR;
    logic [NUM_SLOTS-1:0]    SpREADY;


    logic sync_resetn;

    picorv32_soc #(.NUM_SLOTS(NUM_SLOTS)) u_soc (
        .clk        (clk),
        .resetn     (cpu_resetn),
        .sync_resetn(sync_resetn),
        .PADDR      (PADDR),
        .PENABLE    (PENABLE),
        .PWRITE     (PWRITE),
        .PWDATA     (PWDATA),
        .SpSEL      (SpSEL),
        .SpRDATA    (SpRDATA),
        .SpREADY    (SpREADY),
        .SpSLVERR   (SpSLVERR)
    );

    logic [31:0] uart_prdata;
    logic        uart_pready;
    logic        uart_pslverr;

    apb_uart #(.TX_LIMIT(TX_LIMIT), .RX_LIMIT(RX_LIMIT)) u_uart (
        .clk    (clk),
        .resetn (sync_resetn),
        .PADDR  (PADDR),
        .PSEL   (SpSEL[0]),
        .PENABLE(PENABLE),
        .PWRITE (PWRITE),
        .PWDATA (PWDATA),
        .PRDATA (uart_prdata),
        .PREADY (uart_pready),
        .PSLVERR(uart_pslverr),
        .uart_tx(uart_tx),
        .uart_rx(uart_rx)
    );

    // Wire slot 0 to UART responses
    assign SpRDATA [0*32 +: 32] = uart_prdata;
    assign SpREADY [0]          = uart_pready;
    assign SpSLVERR[0]          = uart_pslverr;

    // Tie off unused slots [63:1]
    assign SpRDATA [NUM_SLOTS*32-1:32] = '0;
    assign SpREADY [NUM_SLOTS-1:1]     = {(NUM_SLOTS-1){1'b1}};  // ready=1 (no stall)
    assign SpSLVERR[NUM_SLOTS-1:1]     = '0;

    // led0 = heartbeat (counter blink)
    logic [25:0] counter = 0;
    always_ff @(posedge clk) counter <= counter + 1'b1;
    assign led0 = counter[25];

    // led1 = UART TX activity
    logic uart_tx_write;
    assign uart_tx_write = SpSEL[0] && PENABLE && PWRITE && (PADDR[6:2] == 5'd0);
    logic [22:0] tx_pulse = 0;
    always_ff @(posedge clk) begin
        if (uart_tx_write)     tx_pulse <= 23'h7FFFFF;
        else if (tx_pulse > 0) tx_pulse <= tx_pulse - 1'b1;
    end
    assign led1 = tx_pulse > 0;

endmodule
