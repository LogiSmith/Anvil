current_dir := ${CURDIR}
ANVIL_HOME ?= $(HOME)/opt/anvil
TARGET := nexys4ddr
TOP := top
SOURCES := ${current_dir}/./top.v \
           $(ANVIL_HOME)/modules/picorv32@1.0.0/picorv32.v \
           $(ANVIL_HOME)/modules/apb@1.0.0/apb_interconnect.v \
           $(ANVIL_HOME)/modules/apb@1.0.0/apb_master.v \
           $(ANVIL_HOME)/modules/picorv32-apb-bridge@1.0.0/picorv32_apb_bridge.v \
           $(ANVIL_HOME)/modules/baud-rate-generator@1.0.0/baud_rate_generator.v \
           $(ANVIL_HOME)/modules/uart-tx@1.0.0/uart_tx.v \
           $(ANVIL_HOME)/modules/uart-rx@1.0.0/uart_rx.v \
           $(ANVIL_HOME)/modules/uart@1.0.0/uart.v \
           $(ANVIL_HOME)/modules/apb-uart@1.0.0/apb_uart.v \
           $(ANVIL_HOME)/modules/picorv32-core@1.0.0/picorv32_core.v \
           $(ANVIL_HOME)/modules/picorv32-soc@1.0.0/picorv32_soc.v
XDC := ${current_dir}/Nexys-A7-100T-Master.xdc

include ${current_dir}/common/common.mk
