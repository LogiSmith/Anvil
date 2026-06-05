# Anvil — Developer Docs

Internal documentation for the **Anvil** codebase: how the CLI is built, how the
build flow works, and the schemas of the JSON files it reads and writes.

!!! info "Looking for install / usage docs?"
    End-user documentation — installing the full toolchain (F4PGA, sv2v,
    openFPGALoader), getting started and tutorials — lives in the
    [LogiSmith Docs](https://logismith.github.io/Docs/).
    This site documents the **code** of the `anvil` tool itself, for people
    working on or integrating with it.

---

## What lives here

| Section | What's inside |
|---------|---------------|
| [Architecture](architecture.md) | How `anvil.py` is structured and the end-to-end build flow: `config.json` → source collection → `sv2v` → generated `Makefile` → F4PGA |
| [File formats](file-formats.md) | Schema reference for `config.json`, `module.json`, `soc.json`, `boards.json`, `modules.json` |
| [Module system](modules.md) | How modules resolve internally: the registry, `depends` resolution, authoring & installing modules |
| [Contributing](contributing.md) | Dev setup and how to add a command, module, or board |

---

## Repository layout

```
anvil/
├── anvil.py          ← the CLI (all commands)
├── programator.py    ← firmware .mem → Verilog RAM generator
├── boards.json       ← board registry
├── modules.json      ← module registry
├── modules/          ← reusable RTL modules (<name>@<version>/)
├── xdc/              ← per-board master pin-constraint files
├── examples/         ← example projects per board
└── docs/             ← this documentation
```

---

## Building these docs locally

```bash
pip install mkdocs-material
mkdocs serve     # live preview at http://127.0.0.1:8000
mkdocs build     # static site → ./site (git-ignored)
```
