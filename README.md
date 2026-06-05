# Anvil

[![Docs](https://img.shields.io/badge/docs-mkdocs--material-blue)](https://logismith.github.io/Anvil/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

**Open-source FPGA CLI for the F4PGA toolchain and RISC-V SoCs.**

Anvil is a command-line tool for synthesizing, simulating and programming FPGAs
with the fully open-source [F4PGA](https://f4pga.org/) flow (Yosys + VPR). On top
it adds a small project model: a board registry, version-pinned reusable RTL
**modules**, and a RISC-V **SoC** flow that compiles your C/C++ firmware straight
into the bitstream. Runs on Linux (and Windows under WSL2).

## Documentation

- 📖 **Installation & usage** — <!-- TODO: link to organisation-level Docs -->
  *(user guide: installing the toolchain, getting started, tutorials)*
- 🛠️ **Developer docs** — <https://logismith.github.io/Anvil/>
  *(architecture, file-format reference, module system, contributing)*

## Quickstart

> Assumes the toolchain (F4PGA, sv2v, openFPGALoader) is installed — see the
> installation guide above.

```bash
mkdir blinky && cd blinky
anvil init --board Nexys-A7-100T   # scaffold project
# edit top.sv and the .xdc pin constraints
anvil build                        # firmware (if any) + synth → bitstream
anvil program                      # flash the board
```

## Repository layout

```
anvil.py        ← the CLI
programator.py  ← firmware .mem → Verilog RAM generator
boards.json     ← board registry
modules.json    ← module registry
modules/        ← reusable RTL modules (<name>@<version>/)
xdc/            ← per-board master pin constraints
examples/       ← example projects per board
docs/           ← developer documentation (MkDocs)
```

## Contributing

See the [contributing guide](https://logismith.github.io/Anvil/contributing/).

## License

Released under the [MIT License](LICENSE). © 2025 LogiSmith.
