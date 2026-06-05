# File formats

!!! note "Stub"
    Skeleton — one section per JSON schema, to be filled in with fields,
    types, required/optional, and a minimal + full example each.

Reference for every JSON file Anvil reads or writes.

## `config.json` — project config

Per-project, in the project root. Written by `anvil init`, read by most commands.

<!-- TODO: fields — project, board, top, modules[], params{}, plus the board
     fields copied from boards.json (target, partname, device, ofl_board, xdc) -->

## `module.json` — module metadata

One per module directory (`modules/<name>@<version>/`).

<!-- TODO: fields — name, description, version, depends[] -->

## `soc.json` — SoC build config

Present only in SoC modules; its presence is what marks a module as a SoC.

<!-- TODO: fields — cpu{compiler,objcopy,march,mabi}, cflags[], ldflags[],
     defsyms{} (note: defsym values are expressions eval'd against config params) -->

## `boards.json` — board registry

Global, in the repo root.

<!-- TODO: fields per board — description, target, partname, device, ofl_board, xdc -->

## `modules.json` — module registry

Global, in the repo root. Catalog of available modules; must stay in sync with
the `modules/` directory.

<!-- TODO: fields per module — description, versions[], latest.
     Note: load_modules_registry() drops entries with no matching module dir. -->
