current_dir := ${CURDIR}
ANVIL_HOME ?= $(HOME)/opt/anvil
TARGET := nexys4ddr
TOP := top
SOURCES := ${current_dir}/*.v \
           $(ANVIL_HOME)/modules/baud-rate-generator@1.0.0/baud_rate_generator.v \
           $(ANVIL_HOME)/modules/uart-tx@1.0.0/uart_tx.v \
           $(ANVIL_HOME)/modules/uart-rx@1.0.0/uart_rx.v \
           $(ANVIL_HOME)/modules/uart@1.0.0/uart.v
XDC := ${current_dir}/Nexys-A7-100T-Master.xdc

include ${current_dir}/common/common.mk
