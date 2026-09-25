# Module system

How reusable RTL modules are stored, resolved and authored.

## Concept

A **module** is a versioned, self-contained RTL block. Anvil ships some of
them itself, in `modules/<name>@<version>/`; a project can also pull in a
module from a local path or a URL, landing in `external/<name>@<version>/` —
see [Module sources](#module-sources) below for how a reference is told apart
and fetched.

Wherever it comes from, a module is recorded in the project's
[`config.json`](file-formats.md#configjson-project-config) `modules` object,
keyed by its own name. Modules in turn declare their own `depends`, so adding
one pulls in its whole dependency tree — bundled, external or local, resolved
recursively. This keeps shared RTL (UART, APB, PicoRV32, …) in one place
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
The layout is the same wherever the module lives — `modules/`, `external/`, or
a local path — only the containing directory differs.

## Registry & versioning

[`modules.json`](file-formats.md#modulesjson-module-registry) is the catalog of
modules bundled with Anvil. A **bundled** reference is either:

- `name` — resolves to the registry's `latest` version, or
- `name@version` — an exact version.

`load_modules_registry()` filters out catalog entries whose directory is missing,
so the registry self-heals if it drifts (you get a clean *Unknown module* error
rather than a crash). Keep `modules.json` in sync with `modules/` —
`anvil installmodule` does this for you.

A module reference isn't always a bundled name, though — it can also be a
local path or a URL. See [Module sources](#module-sources) next.

## Module sources

A module reference is one of three things, told apart by the shape of the
string itself — Anvil never needs to be told which kind it's looking at:

1. **A local path** — starts with `./` or `../`. Used in place; nothing is
   downloaded or copied.

   ```bash
   anvil addmodule ../scratch-module
   ```

2. **A bundled name** — anything else that isn't a URL; resolves against
   `modules.json` as in [Registry & versioning](#registry-versioning) above.

   ```bash
   anvil addmodule uart
   ```

3. **A URL** — fetched as an archive.

   ```bash
   anvil addmodule https://example.org/fifo.tar.gz
   ```

### Resolving a URL to an archive

Anvil decides what a URL points to by *what the server actually returns*, not
by which host it is — this is what keeps a self-hosted forge working without
Anvil knowing it exists. `git` is never invoked: a plain git server has no
archive endpoint, and a direct archive link works there just as well.

1. If the ref carries no `@ref` — a plain URL, or a
   `.../releases/tag/<ref>` URL, which already has one built in — request it
   as given, then with each of `.tar.gz`, `.zip`, `.tgz`, `.tar.xz` appended
   in turn. This step is skipped entirely when `@ref` is present:
   `owner/repo@ref` isn't itself a URL, so there's nothing literal to try.
2. If the response to any of those is one of the archive content types
   (`application/gzip`, `application/x-gzip`, `application/zip`,
   `application/x-tar`), use it — first archive wins.
3. If the URL matches a known forge shape and carries a ref (`@ref`, or a
   `/releases/tag/<ref>` path), also try that forge's own archive URLs for
   it, in order:

   | You write | Anvil tries, in order |
   |---|---|
   | `github.com/<owner>/<repo>@<ref>` | `.../archive/refs/tags/<ref>.tar.gz`, then `.../archive/refs/heads/<ref>.tar.gz`, then `.../archive/<ref>.tar.gz` |
   | `github.com/<owner>/<repo>/releases/tag/<ref>` | the same three, using that `<ref>` |
   | `gitlab.com/<owner>/<repo>@<ref>` | `.../-/archive/<ref>/<repo>-<ref>.tar.gz` |

   The three GitHub candidates exist because `<ref>` might name a tag or a
   branch — Anvil doesn't ask which; it tries a tag path, then a branch path,
   then a bare ref, and uses whichever comes back as an archive first.

   Checked live against real repositories while writing this page:
   `github.com/pallets/flask@3.0.0` carries `@ref`, so step 1 was skipped and
   only these three were tried — it resolved on the first,
   `https://github.com/pallets/flask/archive/refs/tags/3.0.0.tar.gz`.
   `gitlab.com/gitlab-org/gitlab-test@v1.1.1` resolved the same way, to
   `https://gitlab.com/gitlab-org/gitlab-test/-/archive/v1.1.1/gitlab-test-v1.1.1.tar.gz`.

4. If nothing tried turned out to be an archive, fail — the error lists every
   URL that was attempted.

Adding a forge is a row in that table, not a new code path.

### `@ref` and `#subpath`

A source can carry a version and, separately, pick one module out of a
repository that holds several. The fragment is always last, after any `@ref`:

```
github.com/ana/rtl@v1.2.0#modules/fifo
https://example.org/rtl.tar.gz#modules/fifo
```

`@ref` is stripped first to build the archive URL above. Once the archive is
unpacked, Anvil looks for `module.json` at its root, or — if the archive has
exactly one top-level directory, which is what GitHub and GitLab produce —
inside that directory; any other shape is rejected, naming how many
directories were actually found. `#subpath` is then resolved inside that
tree; `module.json` must exist there, or the add fails naming the subpath
that was checked.

### Git and `.gitignore` at `init`

`anvil init` sets up version control for a new project, silently unless
something goes wrong:

- it runs `git init` in the project directory, unless that directory is
  already inside a git work tree — so running `anvil init` inside an existing
  repository never nests a second one;
- it writes a `.gitignore`, if one isn't already there, **regardless** of
  whether `git init` itself succeeded — even a machine with no `git` at all
  still gets one:

  ```gitignore
  build/
  external/
  *.vcd
  *.log
  __pycache__/
  ```

Anvil never edits or parses a `.gitignore` that's already present. If you'd
rather vendor fetched modules than have Anvil re-fetch them, delete the
`external/` line yourself — nothing here enforces it.

Since the write is unconditional, a project only ends up with no
`.gitignore` at all if it predates this behavior, or its `config.json` was
never produced by `anvil init` in the first place. Either way, the first
time a module lands in `external/`, Anvil warns once and still proceeds; a
missing `.gitignore` says nothing about whether the project is otherwise
fine:

```
⚠ no .gitignore in this project
  external/ and build/ are generated -- committing them is rarely wanted.
  A reasonable starting point:

      build/
      external/
      *.vcd
      *.log
      __pycache__/
```

### `external/`

A fetched module lands inside the project itself, one directory per module:

```
external/
├── fifo@1.2.0/
└── spi-master@0.3.1/
```

This is what makes a project self-contained: with `external/` present,
`anvil build` never touches the network. `external/` is one of the lines in
the `.gitignore` [`anvil init` writes](#git-and-gitignore-at-init), so it's
normal for it to be absent after a fresh clone — when that happens, `anvil
build`, `synth` and `test` re-fetch every module whose `source` is a URL,
using the recorded source, and check the fetched code against the recorded
`hash` before using it (see
[`config.json`](file-formats.md#configjson-project-config)). A module whose
`source` is a local path can't be re-fetched; if it's missing, the build fails
saying so instead.

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
- **URL deps** — a `depends` entry can be a URL too. `resolve_deps` doesn't walk
  it directly; a URL dependency is fetched up front (see
  [Module sources](#module-sources)) and recorded as its own top-level entry in
  `config.json`, which `resolve_deps` then trusts is already there.

The resolved set feeds `collect_sources()` →
[build flow](architecture.md#build-flow).

## Working with modules in a project

```bash
anvil modules                    # list available + which are in this project
anvil addmodule uart             # add uart + its depends, regenerate Makefile
anvil addmodule <url-or-path>    # same, for an external or local module
anvil addmodule --yes <ref>      # skip the confirmation prompt below
anvil addmodule --force <name>   # re-record an already-added module's hash
anvil removemodule uart          # remove (refuses if another module depends on it)
```

`addmodule` resolves the full dependency chain, appends new entries to
`config.modules`, and — if the added tree contains a SoC module — scaffolds a
`firmware/` template and sets a default `params.ram_addr_bits`.

`removemodule` refuses to drop a module that another kept module still depends
on, to avoid leaving the project unbuildable. It only edits `config.json`,
though — the module's own directory (`external/<name>@<version>/`, or
`modules/<name>@<version>/` for a bundled one) is left on disk; delete it by
hand if you want the space back.

### Consent before installing an external module

Adding a module from a URL or a path can pull in a whole dependency tree of
*other* external modules — a `depends` entry can itself be a URL. Before any
of it touches disk, `anvil addmodule` fetches every archive in that closure
into a temporary directory (fetching, not installing — nothing is written to
`external/` or `config.json` yet), then shows the whole set and asks:

```
These external modules will be added:

  fifo  1.2.0
      from  http://127.0.0.1:48287/fifo.tar.gz

  axi-lite  2.0.0   (required by fifo)
      from  http://127.0.0.1:48287/axi.tar.gz

  picorv-dma  0.4.0   (required by axi-lite)
      from  http://127.0.0.1:48287/dma.tar.gz
      ⚠ ships soc.json -- chooses the compiler that runs

Names are declared by the modules themselves; the URL is what you are trusting.
Add these 3 external modules? [y/N]
```

This is a real three-level `addmodule` run against a local test server, which
is why the `from` lines point at `127.0.0.1`; against a real forge they'd show
a resolved address such as `https://github.com/ana/fifo/archive/refs/tags/v1.2.0.tar.gz`,
per [Resolving a URL to an archive](#resolving-a-url-to-an-archive) above.

A module's `name` is whatever its own `module.json` says — chosen by whoever
published it, not by the URL you typed. That's why the URL gets its own line,
shown in full: the name is a label, the URL is the thing you're actually
trusting. Each entry also says who pulled it in (`required by …`), so an
unexpected module in the list can be traced instead of merely noticed. A
module carrying a `soc.json` is flagged, because that's the one that picks the
compiler `anvil compile` runs — see [the note on trust](#what-the-hash-does-not-prove)
below.

Answering anything but `y` aborts: `[Anvil] Aborted -- nothing installed.`
Nothing is written to `external/`, and `config.json` is untouched.

`--yes` skips the question (the listing above still prints). It's required
when stdin isn't a terminal — the toolchain installer, for instance, runs
`anvil` with stdin from `/dev/null` — because silence must never be read as
agreement:

```
[ERROR] refusing to install external modules without confirmation
        stdin is not a terminal -- pass --yes if this is intended
```

Consent is asked once, not on every build. `config.json` already records what
was agreed to, so `anvil build` re-fetching a missing `external/` (see
[`external/`](#external) above) just verifies the recorded hash instead of
asking again. A hash that doesn't match is an error, not a fresh prompt:

```
✗ module 'fifo' does not match its recorded hash
    recorded: sha256:0000000000000000000000000000000000000000000000000000000000000000
         found: sha256:a9da67ed484a9b2aec956c62f3450aa8cb39c24f1cd5ab2ff151b3dd149dfbab
      re-record with: anvil addmodule --force fifo
```

If the change was intentional, `anvil addmodule --force <name>` re-records the
new hash — showing the old and new hash and re-flagging `soc.json`, gated by
the same `[y/N]` prompt. If it wasn't, don't re-record it: work out why the
module's own source changed underneath you.

### What the hash does not prove

The hash proves a module's code has not changed **since it was added** — it is
a check for accidental drift, not a security boundary. It does not prove the
code is safe to run. `soc.json` supplies the compiler and `objcopy` that
`anvil compile` executes, so a fetched SoC module chooses which binary runs on
your machine, and Anvil does not sandbox that choice.

This is accepted deliberately, not an oversight: the trust model is the
publisher, the same as any other package manager — installing a package
already means running its build scripts. Anvil's job is to make sure the code
you agreed to is the code that's actually there, not to judge whether the
publisher is trustworthy. That judgment is the one the consent prompt above is
asking you to make.

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
