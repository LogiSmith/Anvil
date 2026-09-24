// soc.hpp -- Memory-mapped peripheral defines (FPRO-style)
#pragma once

#define IO_BASE     0xC0000000
#define SLOT_ADDR(slot, reg)  (IO_BASE + ((slot) << 7) + ((reg) << 2))

// Slot 0 = UART
#define UART_TX     (*(volatile unsigned int*)SLOT_ADDR(0, 0))
#define UART_RX     (*(volatile unsigned int*)SLOT_ADDR(0, 1))
#define UART_STATUS (*(volatile unsigned int*)SLOT_ADDR(0, 2))

inline void uart_putc(char c) { UART_TX = c; }

inline void uart_puts(const char* s) {
    while (*s) uart_putc(*s++);
}
