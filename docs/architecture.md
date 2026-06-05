# Architecture

How `anvil.py` is organised and what happens on each build.

## Overview

Anvil is a **single-file CLI** (`anvil.py`) that orchestrates the open-source
F4PGA toolchain. It is intentionally thin: it does not implement synthesis or
place-and-route itself — it shells out to F4PGA (via Conda) and to helper tools
(`sv2v`, `iverilog`, `openFPGALoader`). What Anvil *adds* on top is a small
**project model**:

- a **board registry** (`boards.json`) — part numbers and constraints per board,
- a **module system** (`modules.json` + `modules/`) — versioned, reusable RTL
  blocks with dependency resolution,
- a **SoC/firmware flow** — compile C/C++ into a RAM image baked into the bitstream.

Everything is driven by a per-project `config.json`. See
[File formats](file-formats.md) for the schemas.

## Code map

`anvil.py` is flat — plain functions, no classes. Grouped by role:

| Area | Functions | Notes |
|------|-----------|-------|
| Paths & constants | top of file (`SCRIPT_DIR`, `MODULES_DIR`, `CONDA_*`, …) | All tool locations live here |
| Registry & config | `load_boards`, `load_modules_registry`, `load_config`, `save_config` | `load_modules_registry` drops entries whose module dir is missing |
| Module resolution | `parse_module_ref`, `resolve_version`, `load_module_meta`, `resolve_deps`, `get_resolved_modules` | See [Module system](modules.md) |
| SoC detection | `find_soc_module`, `eval_defsyms` | A module is a SoC iff it has `soc.json` |
| Source collection | `get_v_files`, `get_sv_files`, `find_sv2v`, `sv2v_convert`, `collect_sources` | `.sv` → `.v` conversion happens here |
| Makefile generation | `build_makefile` | Writes the F4PGA-driving `Makefile` |
| Toolchain runners | `run`, `conda_run` | `conda_run` wraps a command in the F4PGA Conda env |
| Command handlers | `cmd_*` | One per CLI subcommand |
| Dispatch | `COMMANDS`, `usage`, `main` | `COMMANDS` maps name → `(handler, help)` |

### Command dispatch

`main()` reads `sys.argv[1:]`, looks the first token up in the `COMMANDS` dict,
and calls its handler with the rest of the args. Adding a command =
write `cmd_<name>(args)` and add one entry to `COMMANDS`. See
[Contributing](contributing.md).

## Build flow

`anvil build` = `cmd_compile` (firmware, if any) then `cmd_synth` (bitstream).
The synth path:

```
config.json
   │  get_resolved_modules()  ── walks each module's depends (resolve_deps)
   ▼
resolved modules  +  project-root sources  +  build/firmware/ram.v (if present)
   │  collect_sources()
   │     • native .v          → used as-is
   │     • .sv                → sv2v → build/converted/.../<name>.v
   ▼
list of .v paths
   │  build_makefile()  ── writes Makefile with SOURCES = those paths
   ▼
Makefile
   │  conda_run("make")  ── activates F4PGA Conda env, runs common/common.mk
   ▼
F4PGA pipeline:  synth → pack → place → route → fasm → bit
   ▼
build/<target>/top.bit
```

Key point: **`build_makefile` regenerates the `Makefile` on every synth** from
the resolved source list. The committed `Makefile` in a project is therefore an
artifact, not a hand-maintained file.

### Source collection details (`collect_sources`)

1. Project root: every `.v` used directly; every `.sv` converted into
   `build/converted/<rel>.v`.
2. If `build/firmware/ram.v` exists (from a SoC firmware build), it is appended.
3. For each resolved module: its `.v` files used directly, its `.sv` files
   converted into `build/converted/modules/<key>/<file>.v`.

`sv2v` is located via `find_sv2v()` — `PATH` first, then `~/opt/sv2v/sv2v`.

## SoC / firmware flow

When a resolved module contains a `soc.json`, `find_soc_module` marks the project
as a SoC build and `cmd_compile` runs:

```
firmware/src/*.{cpp,c,S}
   │  <compiler from soc.json>  (march/mabi, cflags, ldflags,
   │                             link.ld + startup.S from the SoC module,
   │                             --defsym from eval_defsyms(soc.json.defsyms, config.params))
   ▼
build/firmware/firmware.elf
   │  objcopy -O verilog
   ▼
build/firmware/firmware.mem
   │  programator.py  --depth (1 << params.ram_addr_bits)
   ▼
build/firmware/ram.v      ← later picked up by collect_sources()
```

`eval_defsyms` evaluates each `soc.json` defsym expression (e.g.
`"1 << (ram_addr_bits + 2)"`) with `config.params` as the variable scope, in a
sandboxed `eval` (no builtins).

## Testbench flow (`cmd_test`)

Independent of F4PGA — uses Icarus Verilog:

1. Split args into the testbench (`tb/…` or `*_tb.{v,sv}`) and extra sources.
2. Resolve sources: explicit args if given, else from `config.json`, else from a
   local `module.json`, else bare project root.
3. `sv2v`-convert any `.sv` (sources and tb), dedup paths.
4. `iverilog -g2012` → `vvp`; VCD written to `tb/<name>.vcd`.

## Generated vs. committed files

| Generated (git-ignored, under `build/`) | Committed |
|-----------------------------------------|-----------|
| `build/converted/` (sv2v output) | `config.json`, `top.sv`, `*.xdc` |
| `build/firmware/` (elf, mem, `ram.v`) | `firmware/`, `tb/` |
| `build/<target>/` (eblif … `bit`) | `common/common.mk` |

The `Makefile` is a special case: it is **regenerated on every synth** by
`build_makefile`, yet it sits at the project root (not under `build/`). The
bundled examples commit it for convenience, but treat it as an artifact — never
hand-edit it.
