# Contributing

Guide for working on Anvil itself.

## Dev setup

Anvil runs straight from the source tree — there is no build step for the tool.

```bash
git clone git@github.com:LogiSmith/Anvil.git ~/opt/anvil
alias anvil="python3 ~/opt/anvil/anvil.py"     # add to ~/.bashrc
```

Runtime dependencies are external tools, located at the top of `anvil.py`
(`CONDA_*`, `F4PGA_INSTALL`, `SV2V_HOME`, `OPENFPGALOADER`): the F4PGA Conda
environment, `sv2v`, Icarus Verilog (`iverilog`/`vvp`) for `anvil test`, and
`openFPGALoader` for `anvil program`. Installing those belongs in the
**user/org docs**, not here.

## Code conventions

- **Single file.** All commands live in `anvil.py`, plain functions, no classes.
- **Handler pattern.** Each subcommand is `cmd_<name>(args)` and is registered in
  the `COMMANDS` dict (`name → (handler, help_text)`); `main()` dispatches on
  `sys.argv[1]`.
- **Errors.** Print `"[ERROR] <what> ..."` (optionally a hint line) then
  `sys.exit(1)`. Normal output is prefixed `"[Anvil] ..."` (or a stage tag like
  `"[TEST]"`, `"[SV2V]"`).
- **Paths & tool locations** are constants at the top of the file — add new ones
  there rather than inline.

## Adding a command

1. Write `cmd_<name>(args)`.
2. Add an entry to `COMMANDS`.
3. Update the module docstring's command list at the top of `anvil.py`.
4. Document it where relevant in these docs.

## Adding a module

See [Module system → Authoring](modules.md#authoring-a-new-module). In short:
`anvil init --module <name>`, write RTL + `module.json`, `anvil installmodule`.
Keep [`modules.json`](file-formats.md#modulesjson-module-registry) in sync —
`installmodule` handles this automatically.

## Adding a board

1. Add an entry to [`boards.json`](file-formats.md#boardsjson-board-registry).
2. Add the master constraints file `xdc/<board>-Master.xdc`.
3. Verify with `anvil boards`.

## Updating these docs

```bash
pip install -r docs/requirements.txt
python3 -m mkdocs serve              # live preview at http://127.0.0.1:8000
python3 -m mkdocs build --strict     # fail on broken links / nav issues
```

- Pages live in `docs/`; navigation is the `nav:` tree in `mkdocs.yml`.
- Run a `--strict` build before pushing — it catches dead internal links.
- The generated `site/` directory is git-ignored.
