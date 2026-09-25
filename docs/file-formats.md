# File formats

Reference for every JSON file Anvil reads or writes. All are plain JSON, 2-space
indent by convention.

## `config.json` — project config

Per-project, in the project root. Written by `anvil init`, read by most commands.
On `init` the chosen board's fields from [`boards.json`](#boardsjson-board-registry)
are merged in (any key not already present is copied).

Schema **2.0**. `schema` and `version` are easy to confuse and are named apart
on purpose: `schema` is the format of this file; `version` is the *project's*
own version.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema` | string | yes | Config file format version. Currently `"2.0"` |
| `project` | string | yes | Project name (defaults to the directory name) |
| `version` | string | yes | The project's own version — not the config format's. Set to `1.0.0` by `anvil init` |
| `board` | string | yes | Board key, must exist in `boards.json` |
| `top` | string | no | Top module name (defaults to `top`) |
| `modules` | object | no | Modules used by this project, keyed by name — see [Modules](#modules) below |
| `params` | object | no | Build parameters; values are exposed to SoC `defsyms` (e.g. `ram_addr_bits`) |
| `description`, `target`, `vpr_device`, `partname`, `device`, `ofl_board`, `xdc` | string | yes\* | Copied from the board on `init`; consumed by the Makefile / programmer |

\* present after `anvil init`; they originate from `boards.json`.

### Modules

`modules` is an **object** keyed by module name — not a list. Order is no
longer meaningful; build order comes from dependency resolution (see
[Dependency resolution](modules.md#dependency-resolution)).

| Field | Meaning |
|-------|---------|
| `version` | Taken from the module's own `module.json` |
| `source` | `"system"` for a module bundled with Anvil, a local path exactly as the user typed it, or the fully **resolved** archive URL for a fetched module |
| `path` | Where the code actually is: `$ANVIL_HOME/…` for a bundled module, `external/…` for a fetched one, the path itself for a local one |
| `hash` | Content hash covering the module's `.v`/`.sv` files, `module.json` and `soc.json` — see [What the hash does not prove](modules.md#what-the-hash-does-not-prove) |

`$ANVIL_HOME` is the same placeholder `build_makefile` already writes into the
Makefile, so no user's home directory ends up in a file that gets shared or
committed.

```json
{
  "schema": "2.0",
  "project": "leds",
  "version": "1.0.0",
  "board": "Nexys-A7-50T",
  "params": {},
  "modules": {
    "apb": {
      "version": "1.0.0",
      "source": "system",
      "path": "$ANVIL_HOME/modules/apb@1.0.0",
      "hash": "sha256:4f5c4089097fedba77bf3a35b8fa3bb248fe62a840602907ed8dc546348b88ec"
    },
    "scratch": {
      "version": "0.1.0",
      "source": "../scratch-module",
      "path": "../scratch-module",
      "hash": "sha256:54a46c6b08551914820c9d1408b41cd31a2243d6da9ac9a4e80c259918604dfb"
    },
    "fifo": {
      "version": "1.2.0",
      "source": "http://127.0.0.1:46479/fifo.tar.gz",
      "path": "external/fifo@1.2.0",
      "hash": "sha256:8b4ad69065293f4c2fc5f96f30cb4d0e775f1becd84ecbda0de221629798135d"
    }
  },
  "description": "Digilent Nexys A7 50T (xc7a50t)",
  "target": "nexys_a7_50t",
  "vpr_device": "xc7a50t_test",
  "partname": "xc7a50tcsg324-1",
  "device": "artix7",
  "ofl_board": "nexys_a7_50",
  "xdc": "Nexys-A7-50T-Master.xdc"
}
```

Every field, path and hash above came from a real `anvil init` followed by
three `anvil addmodule` calls (one bundled, one local path, one URL); keys are
reordered here to match the table. `fifo`'s `source` is a local test server
used while writing this page — a real fetch resolves to a forge URL such as
`https://github.com/ana/fifo/archive/refs/tags/v1.2.0.tar.gz` (see
[Resolving a URL to an archive](modules.md#resolving-a-url-to-an-archive)).

!!! note "Schema-1 migration"
    A `config.json` without a `schema` key is schema 1, where `modules` is a
    list of strings (`name@version`, or a `./`/`../` path). `load_config()`
    rewrites it to 2.0 in place the first time it's loaded, computing a fresh
    `hash` for every module, and prints one line:

    ```
    $ anvil modules
    [Anvil] config.json migrated to schema 2.0
    [Anvil] Modules in 'uart-hw-test':
      + baud-rate-generator       baud-rate-generator module
      ...
    ```

    Nothing else about the project changes, and a schema-1 project can't
    contain a URL module — migration never fetches anything.

## `module.json` — module metadata

One per module directory (`modules/<name>@<version>/`). Created by
`anvil init --module`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Module name (matches the `<name>` in `<name>@<version>/`) |
| `description` | string | yes | One-line summary (shown by `anvil modules`) |
| `version` | string | no | Defaults to `1.0.0`; matches `<version>` in the dir name |
| `depends` | string[] | no | Module refs this module needs — resolved recursively. May be a bundled name (`apb`), a path dep (`./`, `../`), or a URL — the same three forms a project's own `modules` can use, see [Module sources](modules.md#module-sources) |

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
| `target` | string | Build target selector; must be unique across boards |
| `vpr_device` | string | F4PGA/VPR architecture device (e.g. `xc7a100t_test`) |
| `partname` | string | FPGA part (e.g. `xc7a100tcsg324-1`) |
| `device` | string | Device family for the bitstream (e.g. `artix7`) |
| `ofl_board` | string | openFPGALoader board id (used by `anvil program`) |
| `xdc` | string | Master XDC filename in `xdc/` |

```json
{
  "Nexys-A7-100T": {
    "description": "Digilent Nexys A7 100T (xc7a100t)",
    "target": "nexys4ddr",
    "vpr_device": "xc7a100t_test",
    "partname": "xc7a100tcsg324-1",
    "device": "artix7",
    "ofl_board": "nexys_a7_100",
    "xdc": "Nexys-A7-100T-Master.xdc"
  },
  "Nexys-A7-50T": {
    "description": "Digilent Nexys A7 50T (xc7a50t)",
    "target": "nexys_a7_50t",
    "vpr_device": "xc7a50t_test",
    "partname": "xc7a50tcsg324-1",
    "device": "artix7",
    "ofl_board": "nexys_a7_50",
    "xdc": "Nexys-A7-50T-Master.xdc"
  }
}
```

These five build fields are not just metadata: `anvil init` and `anvil synth`
generate the `TARGET` → device/part table in `common/common.mk` from this file,
so the registry is the only place a board is described. The rest of `common.mk`
(the build rules) is taken verbatim from the pinned `f4pga-examples` checkout.

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
