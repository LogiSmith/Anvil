# Architecture

!!! note "Stub"
    Skeleton — headings below are the intended structure, to be filled in.

How `anvil.py` is organised and what happens on each build.

## Overview

<!-- TODO: one-paragraph mental model — anvil is a thin orchestrator over F4PGA
     that adds a project model (config + modules + SoC) on top -->

## Code map

<!-- TODO: group the functions in anvil.py by area, with line refs
     - paths/constants
     - registry & config loading  (load_boards, load_modules_registry, load_config)
     - module resolution          (parse_module_ref, resolve_deps, get_resolved_modules)
     - source collection & sv2v   (collect_sources, sv2v_convert)
     - makefile generation        (build_makefile)
     - command handlers           (cmd_*)
-->

## Build flow

<!-- TODO: end-to-end pipeline diagram / steps:
     config.json → resolve modules → collect sources (.v + sv2v'd .sv + firmware ram.v)
     → generate Makefile → conda_run(make) → F4PGA (synth→pack→place→route→fasm→bit) -->

## SoC / firmware flow

<!-- TODO: how cmd_compile fits in — soc.json, compiler invocation, programator.py,
     ram.v injected into sources -->

## Generated vs source files

<!-- TODO: clarify what is auto-generated (Makefile, build/) vs committed -->
