# Module system

!!! note "Stub"
    Skeleton — headings below are the intended structure, to be filled in.

How reusable RTL modules are stored, resolved and authored.

## Concept

<!-- TODO: a module = versioned RTL block in modules/<name>@<version>/ with a
     module.json; reused across projects via config.json "modules" list -->

## Anatomy of a module

<!-- TODO: directory contents — module.json, .v/.sv sources, optional tb/,
     optional soc.json (+ link.ld, startup.S) for SoC modules -->

## Registry & versioning

<!-- TODO: modules.json catalog, name@version refs, latest resolution,
     self-healing load (drops missing dirs) -->

## Dependency resolution

<!-- TODO: walk resolve_deps — depth-first, depends[], circular-dep guard,
     path deps (./ ../), dedup. Reference architecture.md build flow. -->

## Authoring a new module

<!-- TODO: anvil init --module, fill module.json, anvil installmodule -->

## Path dependencies

<!-- TODO: how ./ and ../ deps work for local/in-development modules -->
