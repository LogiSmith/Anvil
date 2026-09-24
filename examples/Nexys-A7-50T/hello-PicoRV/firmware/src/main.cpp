#include "soc.hpp"

int main() {
    uart_puts("Hello from PicoRV32!\n");
    while (1);
    return 0;
}
