# File formats

Reference for every JSON file Anvil reads or writes. All are plain JSON, 2-space
indent by convention.

## `config.json` — project config

Per-project, in the project root. Written by `anvil init`, read by most commands.
On `init` the chosen board's fields from [`boards.json`](#boardsjson-board-registry)
are merged in (any key not already present is copied).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | yes | Project name (defaults to the directory name) |
| `board` | string | yes | Board key, must exist in `boards.json` |
| `top` | string | no | Top module name (defaults to `top`) |
| `modules` | string[] | no | Module refs (`name` or `name@version`) — order preserved |
| `params` | object | no | Build parameters; values are exposed to SoC `defsyms` (e.g. `ram_addr_bits`) |
| `description`, `target`, `partname`, `device`, `ofl_board`, `xdc` | string | yes\* | Copied from the board on `init`; consumed by the Makefile / programmer |

\* present after `anvil init`; they originate from `boards.json`.

```json
{
  "project": "uart-hw-test",
  "board": "Nexys-A7-100T",
  "top": "top",
  "modules": [
    "baud-rate-generator@1.0.0",
    "uart-tx@1.0.0",
    "uart-rx@1.0.0",
    "uart@1.0.0"
  ],
  "params": { "ram_addr_bits": 11 },
  "target": "nexys4ddr",
  "partname": "xc7a100tcsg324-1",
  "device": "artix7",
  "ofl_board": "nexys_a7_100",
  "xdc": "Nexys-A7-100T-Master.xdc"
}
```

## `module.json` — module metadata

One per module directory (`modules/<name>@<version>/`). Created by
`anvil init --module`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Module name (matches the `<name>` in `<name>@<version>/`) |
| `description` | string | yes | One-line summary (shown by `anvil modules`) |
| `version` | string | no | Defaults to `1.0.0`; matches `<version>` in the dir name |
| `depends` | string[] | no | Module refs this module needs — resolved recursively. May be registry refs (`apb`) or path deps (`./`, `../`) |

```json
{
  "name": "uart",
  "description": "Full UART — TX + RX with baud rate generator",
  "version": "1.0.0",
  "depends": ["baud-rate-generator", "uart-tx", "uart-rx"]
}
```

## `soc.json` — SoC build config

Present **only** in SoC modules. Its mere presence is what marks a module as a
SoC ([`find_soc_module`](architecture.md#soc-firmware-flow)); a project may have
exactly one. Drives the firmware compile in `cmd_compile`. The SoC module dir
must also provide `link.ld` and `startup.S`.

| Field | Type | Description |
|-------|------|-------------|
| `cpu.compiler` | string | C/C++ compiler (e.g. `riscv64-unknown-elf-g++`) |
| `cpu.objcopy` | string | objcopy used to emit the `.mem` |
| `cpu.march` / `cpu.mabi` | string | `-march` / `-mabi` flags (e.g. `rv32i` / `ilp32`) |
| `cflags` | string[] | Extra compiler flags |
| `ldflags` | string[] | Extra linker flags |
| `defsyms` | object | `name → expression`; each expression is evaluated with `config.params` as variables and passed as `-Wl,--defsym` |

```json
{
  "cpu": {
    "compiler": "riscv64-unknown-elf-g++",
    "objcopy":  "riscv64-unknown-elf-objcopy",
    "march":    "rv32i",
    "mabi":     "ilp32"
  },
  "cflags":  ["-fno-exceptions", "-fno-rtti", "-Os", "-Wall"],
  "ldflags": ["-nostdlib", "-nostartfiles"],
  "defsyms": { "__stack_top": "1 << (ram_addr_bits + 2)" }
}
```

!!! warning "defsym expressions are `eval`'d"
    `defsyms` values are Python expressions evaluated in a sandbox (no builtins)
    with `config.params` as the only names in scope. Keep them simple arithmetic.

## `boards.json` — board registry

Global, in the repo root. One entry per supported board.

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | Human-readable board name |
| `target` | string | Build target selector used by `common/common.mk` |
| `partname` | string | FPGA part (e.g. `xc7a100tcsg324-1`) |
| `device` | string | Device family for the bitstream (e.g. `artix7`) |
| `ofl_board` | string | openFPGALoader board id (used by `anvil program`) |
| `xdc` | string | Master XDC filename in `xdc/` |

```json
{
  "Nexys-A7-100T": {
    "description": "Digilent Nexys A7 100T (xc7a100t)",
    "target": "nexys4ddr",
    "partname": "xc7a100tcsg324-1",
    "device": "artix7",
    "ofl_board": "nexys_a7_100",
    "xdc": "Nexys-A7-100T-Master.xdc"
  }
}
```

## `modules.json` — module registry

Global, in the repo root. The catalog of available modules; must stay in sync
with the `modules/` directory.

| Field | Type | Description |
|-------|------|-------------|
| `<name>.description` | string | Shown by `anvil modules` |
| `<name>.versions` | string[] | Available versions |
| `<name>.latest` | string | Default version when a ref omits `@version` |

```json
{
  "uart": {
    "description": "Full UART — TX + RX with baud rate generator",
    "versions": ["1.0.0"],
    "latest": "1.0.0"
  }
}
```

!!! note "Self-healing load"
    `load_modules_registry()` drops any entry whose `<name>@<version>` directory
    does not exist, so a registry that has drifted out of sync degrades to a
    clean *Unknown module* error instead of crashing every command. The registry
    is updated automatically by `anvil installmodule`. See
    [Module system](modules.md).
