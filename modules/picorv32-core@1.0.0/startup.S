# startup.S — Generic PicoRV32 startup
#
# Static — same for all projects.
# __stack_top symbol provided via linker --defsym at build time.

.section .text.init
.global _start

_start:
    # Set stack pointer (from linker defsym)
    la sp, __stack_top

    # Zero out BSS section
    la t0, _bss_start
    la t1, _bss_end
1:
    bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:

    # Call main()
    call main

    # If main returns, hang forever
hang:
    j hang
