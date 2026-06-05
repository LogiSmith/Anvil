# Module system

How reusable RTL modules are stored, resolved and authored.

## Concept

A **module** is a versioned, self-contained RTL block living in
`modules/<name>@<version>/`. Projects reference modules by name in their
[`config.json`](file-formats.md#configjson-project-config) `modules` list;
modules in turn declare their own `depends`, so adding one pulls in its whole
dependency tree. This keeps shared RTL (UART, APB, PicoRV32, …) in one place
instead of being copy-pasted between projects.

## Anatomy of a module

```
modules/uart@1.0.0/
├── module.json     ← metadata + depends   (required)
├── uart.sv         ← RTL source (.sv or .v; any number)
└── tb/             ← optional testbenches
```

A **SoC** module additionally carries:

```
├── soc.json        ← marks it as a SoC + firmware build config
├── link.ld         ← linker script
└── startup.S       ← startup code
```

See [File formats](file-formats.md) for `module.json` and `soc.json` schemas.

## Registry & versioning

[`modules.json`](file-formats.md#modulesjson-module-registry) is the catalog of
available modules. A module **reference** is either:

- `name` — resolves to the registry's `latest` version, or
- `name@version` — an exact version.

`load_modules_registry()` filters out catalog entries whose directory is missing,
so the registry self-heals if it drifts (you get a clean *Unknown module* error
rather than a crash). Keep `modules.json` in sync with `modules/` —
`anvil installmodule` does this for you.

## Dependency resolution

`resolve_deps()` walks the tree **depth-first**:

```
for each module ref in config.modules:
    load module.json
    recurse into its depends[]  ← children resolved before the parent
    append (key, dir, meta) if not already resolved   ← natural dedup
```

Properties:

- **Topological order** — a dependency always appears before the module that
  needs it in the final source list.
- **Dedup** — a module shared by several parents is included once.
- **Circular-dependency guard** — a cycle aborts with
  `[ERROR] Circular dependency: <key>`.
- **Path deps** — a ref starting with `./` or `../` is resolved relative to the
  *depending* module's directory, not the registry. Useful for local,
  in-development sub-modules.

The resolved set feeds `collect_sources()` →
[build flow](architecture.md#build-flow).

## Working with modules in a project

```bash
anvil modules                 # list available + which are in this project
anvil addmodule uart          # add uart + its depends, regenerate Makefile
anvil removemodule uart       # remove (refuses if another module depends on it)
```

`addmodule` resolves the full dependency chain, appends new entries to
`config.modules`, and — if the added tree contains a SoC module — scaffolds a
`firmware/` template and sets a default `params.ram_addr_bits`.

`removemodule` refuses to drop a module that another kept module still depends
on, to avoid leaving the project unbuildable.

## Authoring a new module

```bash
mkdir my_block && cd my_block
anvil init --module my_block   # writes module.json + my_block.sv stub
# write RTL, add tb/ if you want, list depends[] in module.json
anvil installmodule            # copies dir into modules/ + updates modules.json
```

`installmodule` copies the current directory into
`modules/<name>@<version>/` (ignoring `build/`, `*.vcd`, `__pycache__`, `.git`)
and registers it. After that, any project can `anvil addmodule <name>`.

To make the module a **SoC**, add a `soc.json` (plus `link.ld` and `startup.S`)
before installing — see [SoC fields](file-formats.md#socjson-soc-build-config).
