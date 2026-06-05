current_dir := ${CURDIR}
TARGET := nexys4ddr
TOP := top
SOURCES := ${current_dir}/*.v \
           /home/geek/opt/fpga/modules/baud-rate-generator@1.0.0/baud_rate_generator.v \
           /home/geek/opt/fpga/modules/uart-tx@1.0.0/uart_tx.v \
           /home/geek/opt/fpga/modules/uart-rx@1.0.0/uart_rx.v \
           /home/geek/opt/fpga/modules/uart@1.0.0/uart.v
XDC := ${current_dir}/Nexys-A7-100T-Master.xdc

include ${current_dir}/common/common.mk
