current_dir := ${CURDIR}
TARGET := nexys4ddr
TOP := top
SOURCES := ${current_dir}/./top.v \
           /home/geek/opt/fpga/modules/picorv32@1.0.0/picorv32.v \
           /home/geek/opt/fpga/modules/apb@1.0.0/apb_interconnect.v \
           /home/geek/opt/fpga/modules/apb@1.0.0/apb_master.v \
           /home/geek/opt/fpga/modules/picorv32-apb-bridge@1.0.0/picorv32_apb_bridge.v \
           /home/geek/opt/fpga/modules/baud-rate-generator@1.0.0/baud_rate_generator.v \
           /home/geek/opt/fpga/modules/uart-tx@1.0.0/uart_tx.v \
           /home/geek/opt/fpga/modules/uart-rx@1.0.0/uart_rx.v \
           /home/geek/opt/fpga/modules/uart@1.0.0/uart.v \
           /home/geek/opt/fpga/modules/apb-uart@1.0.0/apb_uart.v \
           /home/geek/opt/fpga/modules/picorv32-core@1.0.0/picorv32_core.v \
           /home/geek/opt/fpga/modules/picorv32-soc@1.0.0/picorv32_soc.v
XDC := ${current_dir}/Nexys-A7-100T-Master.xdc

include ${current_dir}/common/common.mk
