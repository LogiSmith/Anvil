/* link.ld — Generic linker script for PicoRV32 SoC
 *
 * Static — same for all projects. Stack top defined via -Wl,--defsym=__stack_top=<value>.
 *
 * Memory layout:
 *   0x00000000 -                code, rodata, data, bss
 *                  __stack_top  ← grows down
 */

ENTRY(_start)

SECTIONS {
    .text 0x00000000 : {
        KEEP(*(.text.init))     /* startup code first */
        *(.text*)
        *(.rodata*)
    }

    .data : {
        *(.data*)
    }

    .bss : {
        _bss_start = .;
        *(.bss*)
        *(COMMON)
        _bss_end = .;
    }

    /* Discard unused sections */
    /DISCARD/ : {
        *(.eh_frame*)
        *(.note*)
        *(.comment)
    }
}
