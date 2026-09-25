#!/usr/bin/env python3
"""
anvil -- FPGA project toolchain for F4PGA + open-source RISC-V SoCs

Project structure:
    config.json          project config
    top.sv               user -- top-level (SystemVerilog or Verilog)
    *.xdc                user -- pin assignments
    firmware/            user -- C/C++ source (when SoC module added)
    |-- include/
    |-- src/
    tb/                  user -- testbenches
    build/               auto-generated (gitignore)
    |-- firmware/        firmware build artifacts (elf, mem, ram.v)
    |-- <target>/        FPGA build artifacts (eblif, fasm, bit)

Commands:
    anvil init --board <name>        Initialize new project
    anvil init --module <name>       Create a new module
    anvil compile                    Build firmware (C++ -> ram.v)
    anvil synth                      Synthesize bitstream
    anvil build                      compile + synth
    anvil program                    Flash bitstream to board
    anvil test tb/<tb>.v             Run testbench
    anvil clean                      Remove build/
    anvil status                     Show project info
    anvil doctor                     Check external tool dependencies
    anvil update                     Update the whole toolchain (Anvil + deps)
    anvil version                    Print Anvil version
    anvil boards                     List boards
    anvil modules                    List modules
    anvil addmodule <name> ...       Add module(s)
    anvil removemodule <name> ...    Remove module(s)
    anvil installmodule              Install current dir as module
"""

import subprocess
import sys
import os
import re
import shutil
import json
import time
import glob
import tempfile
import fetch

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR     = os.path.dirname(os.path.realpath(__file__))
PROGRAMATOR    = os.path.join(SCRIPT_DIR, "programator.py")
BOARDS_FILE    = os.path.join(SCRIPT_DIR, "boards.json")
MODULES_FILE   = os.path.join(SCRIPT_DIR, "modules.json")
MODULES_DIR    = os.path.join(SCRIPT_DIR, "modules")
XDC_DIR        = os.path.join(SCRIPT_DIR, "xdc")
EXAMPLES_DIR   = os.path.join(SCRIPT_DIR, "examples")
CONDA_SH       = os.path.expanduser("~/miniconda3/etc/profile.d/conda.sh")
CONDA_ENV      = "xc7"
FPGA_FAM       = "xc7"
F4PGA_INSTALL  = os.path.expanduser("~/opt/f4pga")
F4PGA_EXAMPLES = os.path.expanduser("~/f4pga-examples")
OPENFPGALOADER = "/usr/local/bin/openFPGALoader"
TOOLCHAIN_INSTALLER = "https://raw.githubusercontent.com/LogiSmith/toolchain-setup/main/install.sh"

CONFIG_FILE    = "config.json"
EXTERNAL_DIR   = "external"
TB_DIR         = "tb"
FW_DIR         = "firmware"
FW_SRC         = "firmware/src"
FW_INC         = "firmware/include"
BUILD_DIR      = "build"
BUILD_FW_DIR   = "build/firmware"
BUILD_CONVERTED = "build/converted"
SV2V_HOME      = os.path.expanduser("~/opt/sv2v/sv2v")

def load_boards():
    with open(BOARDS_FILE) as f:
        return json.load(f)

def load_modules_registry():
    with open(MODULES_FILE) as f:
        registry = json.load(f)

    return {
        name: info
        for name, info in registry.items()
        if any(
            os.path.isdir(os.path.join(MODULES_DIR, f"{name}@{ver}"))
            for ver in info.get("versions", [])
        )
    }

def load_config():
    if not os.path.exists(CONFIG_FILE):
        boards = load_boards()
        print("[ERROR] No config.json. Run: anvil init --board <name>")
        print(f"        Available: {', '.join(boards.keys())}")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    cfg, changed = migrate_config(cfg)
    if changed:
        save_config(cfg)
        print(f"[Anvil] config.json migrated to schema {SCHEMA}")
    return cfg

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

SCHEMA = "2.0"

def module_entry(version, source, path, digest):
    return {"version": version, "source": source, "path": path, "hash": digest}

def bundled_path(key):
    """A bundled module's location, written without the user's home directory."""
    return f"$ANVIL_HOME/modules/{key}"

def make_entry(key, mod_dir, meta):
    """A schema-2 entry for a module already resolved via load_module_meta/resolve_deps."""
    version = meta.get("version", "1.0.0")
    if is_path_dep(key):
        return module_entry(version, key, key, fetch.module_hash(mod_dir))
    return module_entry(version, "system", bundled_path(key), fetch.module_hash(mod_dir))

def entry_ref(name, entry):
    """The ref string that resolves `entry` again: its path if local or fetched, else name@version."""
    if is_path_dep(entry["path"]):
        return entry["path"]
    if entry.get("source") != "system":
        return "./" + entry["path"]   # external/<name>@<version> -- a path, not a bundled key
    return f"{name}@{entry['version']}"

def install_external(ref, staging):
    """Download and validate `ref` into a fresh directory under `staging`; nothing is installed yet.

    Returns (name, meta, staged_dir, resolved_url). The caller decides whether to keep it --
    Task 7 asks for consent once the whole dependency set is known.
    """
    _, _, subpath = fetch.split_ref(ref)
    work = tempfile.mkdtemp(dir=staging)
    archive, resolved = fetch.download_archive(ref, work)
    unpacked = os.path.join(work, "unpacked")
    os.makedirs(unpacked)
    fetch.extract(archive, unpacked)
    root = fetch.find_module_root(unpacked, subpath)
    meta = fetch.validate_module(root)
    return meta["name"], meta, root, resolved

def plan_external(refs, staging, existing):
    """Fetch every external ref and its dependencies breadth-first; nothing is installed yet."""
    queue, seen, out = [(r, None) for r in refs], {}, []
    while queue:
        ref, required_by = queue.pop(0)
        name, meta, staged, resolved = install_external(ref, staging)
        if name in existing and existing[name].get("source") != resolved:
            fail(f"module '{name}' is already in this project",
                 f"present:  {existing[name].get('source')}\n"
                 f"incoming: {resolved}")
        # two refs in the same request resolving to one name is the same risk, just not against config.json yet
        if name in seen and seen[name] != resolved:
            fail(f"module '{name}' resolves to two different sources in this request",
                 f"first:  {seen[name]}\nsecond: {resolved}")
        if name in seen:
            continue
        seen[name] = resolved
        out.append({
            "name": name,
            "version": meta.get("version", "0.0.0"),
            "source": resolved,
            "staged": staged,
            "required_by": required_by,
            "has_soc": os.path.isfile(os.path.join(staged, "soc.json")),
        })
        for dep in meta.get("depends", []):
            if fetch.classify(dep) != "name":   # bundled names keep going through resolve_deps
                queue.append((dep, name))
    return out

def ask_consent(prompt, assume_yes, refuse_msg):
    """The shared yes/no gate: --yes bypasses, a non-tty without it refuses, only an explicit y counts."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"[ERROR] {refuse_msg}")
        print("        stdin is not a terminal -- pass --yes if this is intended")
        sys.exit(1)
    try:
        answer = input(prompt)
    except EOFError:   # e.g. Ctrl-D -- absence of an answer is not a yes
        answer = ""
    return answer.strip().lower() == "y"

def confirm_external(plan, assume_yes):
    """Show every module the plan will install, then ask -- silence must never read as yes."""
    if not plan:
        return True

    print("\nThese external modules will be added:\n")
    for m in plan:
        why = f"   (required by {m['required_by']})" if m["required_by"] else ""
        print(f"  {m['name']}  {m['version']}{why}")
        # own line, never truncated: the name is self-declared by the module, the URL is what's judged
        print(f"      from  {m['source']}")
        if m["has_soc"]:
            print(yellow("      ⚠ ships soc.json -- chooses the compiler that runs"))
        print()
    print("Names are declared by the modules themselves; "
          "the URL is what you are trusting.")
    return ask_consent(f"Add these {len(plan)} external modules? [y/N] ", assume_yes,
                        "refusing to install external modules without confirmation")

def plan_force(names, current):
    """The current on-disk hash of each already-recorded module, without writing anything."""
    out = []
    for name in names:
        entry = current[name]
        source = entry.get("source", "system")
        if fetch.classify(source) == "url":
            mod_dir = entry["path"]
            if not os.path.isfile(os.path.join(mod_dir, "module.json")):
                fail(f"module '{name}' is missing",
                     f"{mod_dir} does not exist -- run anvil build to re-fetch it first")
            with open(os.path.join(mod_dir, "module.json")) as f:
                meta = json.load(f)
        else:
            meta, mod_dir, _ = load_module_meta(entry_ref(name, entry), base_dir=os.getcwd())
        out.append({
            "name": name,
            "version": meta.get("version", entry["version"]),
            "source": source,
            "path": entry["path"],   # unchanged -- re-recording never moves anything
            "before": entry["hash"],
            "after": fetch.module_hash(mod_dir),   # mod_dir is where the content is, for hashing only
            "has_soc": os.path.isfile(os.path.join(mod_dir, "soc.json")),   # can't detect "gained" one, so always flag
        })
    return out

def confirm_force(plan, assume_yes):
    """Re-recording a hash is the one path that bypasses a fresh add's prompt -- gate it the same way."""
    changed = [p for p in plan if p["before"] != p["after"]]
    for p in plan:
        if p["before"] == p["after"]:
            print(f"[Anvil] {p['name']} is unchanged -- hash already matches, nothing to do.")
    if not changed:
        return True

    print("\nThese modules will be re-recorded:\n")
    for p in changed:
        print(f"  {p['name']}  {p['version']}")
        print(f"      recorded  {p['before']}")
        print(f"      found     {p['after']}")
        if p["has_soc"]:
            print(yellow("      ⚠ ships soc.json -- chooses the compiler that runs"))
        print()
    return ask_consent(f"Re-record these {len(changed)} module(s)? [y/N] ", assume_yes,
                        "refusing to re-record module hashes without confirmation")

def find_by_local_path(current, base, arg):
    """The module in `current` whose path/source normalizes to `arg`, or None -- survives a deleted directory."""
    target = os.path.normpath(os.path.join(base, arg))
    for name, entry in current.items():
        for val in (entry.get("path"), entry.get("source")):
            if val and is_path_dep(val) and os.path.normpath(os.path.join(base, val)) == target:
                return name
    return None

def local_dir_missing(ref, base):
    return is_path_dep(ref) and not os.path.isdir(os.path.normpath(os.path.join(base, ref)))

def migrate_config(cfg):
    """Bring a config up to schema 2.0, returning (config, changed); never fetches -- schema 1 refs are never URLs."""
    if cfg.get("schema") == SCHEMA:
        return cfg, False

    out = dict(cfg)
    out["schema"] = SCHEMA
    out.setdefault("version", "1.0.0")

    mods = {}
    for ref in cfg.get("modules", []) or []:
        try:
            meta, mod_dir, key = load_module_meta(ref, base_dir=os.getcwd())
        except SystemExit:
            fail(f"config.json migration failed -- module '{ref}' could not be resolved")
        mods[meta["name"]] = make_entry(key, mod_dir, meta)
    out["modules"] = mods
    return out, True

def get_examples_for_board(board_name):
    board_dir = os.path.join(EXAMPLES_DIR, board_name)
    if not os.path.isdir(board_dir):
        return []
    return sorted([
        e for e in os.listdir(board_dir)
        if os.path.isdir(os.path.join(board_dir, e))
    ])

def get_example_description(board_name, example_name):
    """Read description from example's top.sv (or top.v) first comment line."""
    base = os.path.join(EXAMPLES_DIR, board_name, example_name)
    for fname in ("top.sv", "top.v"):
        top = os.path.join(base, fname)
        if os.path.exists(top):
            with open(top) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("//"):
                        return line.lstrip("/ ").strip()
            return ""
    return ""

def copy_example(board_name, example_name, dest):
    """Recursively copy example contents to destination project dir."""
    src_dir = os.path.join(EXAMPLES_DIR, board_name, example_name)
    if not os.path.isdir(src_dir):
        return False
    for item in os.listdir(src_dir):
        s = os.path.join(src_dir, item)
        d = os.path.join(dest, item)
        if os.path.isdir(s):
            if not os.path.exists(d):
                shutil.copytree(s, d)
        else:
            if not os.path.exists(d):
                shutil.copy(s, d)
    return True

def is_path_dep(dep):
    return dep.startswith("./") or dep.startswith("../")

def parse_module_ref(ref):
    if "@" in ref and not is_path_dep(ref):
        name, version = ref.split("@", 1)
        return name, version
    return ref, None

def resolve_version(name, requested_version, registry):
    if name not in registry:
        print(f"[ERROR] Unknown module: '{name}'")
        print(f"        Available: {', '.join(registry.keys())}")
        sys.exit(1)
    info     = registry[name]
    versions = info.get("versions", [])
    if requested_version is None:
        return info.get("latest", versions[-1])
    if requested_version not in versions:
        print(f"[ERROR] Version '{requested_version}' not found for '{name}'")
        sys.exit(1)
    return requested_version

def load_module_meta(dep, base_dir=None):
    if is_path_dep(dep):
        base    = base_dir or os.getcwd()
        mod_dir = os.path.normpath(os.path.join(base, dep))
        key     = dep
    else:
        name, version = parse_module_ref(dep)
        registry = load_modules_registry()
        ver = resolve_version(name, version, registry)
        mod_dir       = os.path.join(MODULES_DIR, f"{name}@{ver}")
        key           = f"{name}@{ver}"

    meta_path = os.path.join(mod_dir, "module.json")
    if not os.path.exists(meta_path):
        print(f"[ERROR] module.json not found: {meta_path}")
        sys.exit(1)
    with open(meta_path) as f:
        meta = json.load(f)
    return meta, mod_dir, key

def resolve_deps(dep, registry, resolved=None, seen=None, base_dir=None):
    if resolved is None: resolved = []
    if seen     is None: seen     = set()

    meta, mod_dir, key = load_module_meta(dep, base_dir)

    if key in seen:
        print(f"[ERROR] Circular dependency: {key}")
        sys.exit(1)
    seen.add(key)

    for child in meta.get("depends", []):
        if fetch.classify(child) == "url":
            continue   # already its own top-level config.json entry -- plan_external put it there
        resolve_deps(child, registry, resolved, seen, base_dir=mod_dir)

    if key not in [r[0] for r in resolved]:
        resolved.append((key, mod_dir, meta))

    seen.discard(key)
    return resolved

def get_resolved_modules(config):
    registry = load_modules_registry()
    modules  = config.get("modules", {})
    resolved = []
    for name, entry in modules.items():
        resolve_deps(entry_ref(name, entry), registry, resolved, base_dir=os.getcwd())
    check_url_deps_are_recorded(resolved, modules)
    return resolved

def check_url_deps_are_recorded(resolved, modules):
    """resolve_deps trusts a URL depends entry is already its own entry -- this is where that trust is checked."""
    sources = [e.get("source") for e in modules.values()]
    for _, _, meta in resolved:
        for dep in meta.get("depends", []):
            # candidates, not == -- a shorthand ref won't match the resolved URL plan_external recorded verbatim
            if fetch.classify(dep) == "url" and not any(c in sources for c in fetch.archive_candidates(dep)):
                fail(f"module '{meta['name']}' depends on '{dep}', which is not in this project",
                     "config.json and this module's own dependencies disagree")

def find_soc_module(resolved_modules):
    """A module is a SoC if it contains soc.json. Returns (mod_dir, soc_cfg, key) or (None, None, None)."""
    socs = []
    for (key, mod_dir, meta) in resolved_modules:
        soc_json = os.path.join(mod_dir, "soc.json")
        if os.path.exists(soc_json):
            with open(soc_json) as f:
                socs.append((mod_dir, json.load(f), key))

    if len(socs) > 1:
        names = ", ".join(s[2] for s in socs)
        print(f"[ERROR] Multiple SoC modules: {names}")
        print(f"        A project must have exactly one SoC module.")
        sys.exit(1)

    return socs[0] if socs else (None, None, None)

def eval_defsyms(defsyms, params):
    """Evaluate defsym expressions with project params as variables."""
    result = {}
    for name, expr in defsyms.items():
        try:
            result[name] = fetch.eval_arith(expr, params)
        except ValueError as e:
            fail(f"defsym '{name}' is not a valid expression",
                 f"{name} = {expr}\n{e}")
    return result

def get_v_files(directory):
    if not os.path.isdir(directory):
        return []
    return sorted([
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith(".v") and os.path.isfile(os.path.join(directory, f))
    ])

def get_sv_files(directory):
    if not os.path.isdir(directory):
        return []
    return sorted([
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith(".sv") and os.path.isfile(os.path.join(directory, f))
    ])

def get_tb_files(directory="."):
    return get_v_files(os.path.join(directory, TB_DIR))

def find_sv2v():
    """Locate sv2v binary: PATH first, then ~/opt/sv2v/sv2v fallback."""
    if shutil.which("sv2v"):
        return "sv2v"
    if os.path.exists(SV2V_HOME):
        return SV2V_HOME
    print("[ERROR] sv2v not found in PATH or ~/opt/sv2v/sv2v")
    print("        Install: https://github.com/zachjs/sv2v/releases")
    sys.exit(1)

def sv2v_convert(sv_path, out_path):
    """Convert one .sv to Verilog. Creates parent dir on demand."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    result = subprocess.run(
        [find_sv2v(), sv_path, "-w", out_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        rel = os.path.relpath(sv_path, os.getcwd())
        fail(f"sv2v failed on {rel}", (result.stderr or "") + (result.stdout or ""))

def ensure_modules(config):
    """Every module on disk and hash-matching before a build reads it, or fail saying why.

    Re-fetching does not ask again: config.json already records what was agreed to,
    and a matching hash is the proof the same code came back.
    """
    for name, entry in config.get("modules", {}).items():
        path  = entry["path"]
        local = path.replace("$ANVIL_HOME", SCRIPT_DIR) if path.startswith("$ANVIL_HOME") else path
        if not os.path.isdir(local):
            if fetch.classify(entry["source"]) != "url":
                fail(f"module '{name}' is missing",
                     f"{path} does not exist and '{entry['source']}' cannot be re-fetched")
            print(f"[Anvil] Fetching {name} from {entry['source']} ...")
            staging = tempfile.mkdtemp()
            try:
                _, _, staged, _ = install_external(entry["source"], staging)
                os.makedirs(EXTERNAL_DIR, exist_ok=True)
                shutil.move(staged, local)
            except (fetch.NoArchiveFound, fetch.InvalidModule, fetch.UnsafeArchive) as e:
                fail(f"could not re-fetch module '{name}'", f"source: {entry['source']}\n{e}")
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        got = fetch.module_hash(local)
        if got != entry["hash"]:
            fail(f"module '{name}' does not match its recorded hash",
                 f"recorded: {entry['hash']}\n     found: {got}\n"
                 f"  re-record with: anvil addmodule --force {name}")

def collect_sources(config=None, module_meta=None):
    """
    Discover RTL sources for synth or simulation.

    Returns a flat list of .v paths. Any .sv input is converted via sv2v
    into build/converted/ with mirrored layout:
        ./foo.sv                   -> build/converted/foo.v
        <module_dir>/foo.sv        -> build/converted/modules/<key>/foo.v

    Pass `config` for project-level builds (uses config.modules).
    Pass `module_meta` for module-only test mode (uses module.json depends).
    """
    sources    = []
    sv2v_count = 0

    # Project root: native .v + converted .sv
    for vf in get_v_files("."):
        sources.append(vf)
    for svf in get_sv_files("."):
        rel = os.path.relpath(svf, ".")
        out = os.path.join(BUILD_CONVERTED, rel[:-3] + ".v")
        sv2v_convert(svf, out)
        sources.append(out)
        sv2v_count += 1

    # Optional firmware-generated ram.v
    ram_v = os.path.join(BUILD_FW_DIR, "ram.v")
    if os.path.exists(ram_v):
        sources.append(ram_v)

    # Modules: from project config or from current module's deps
    resolved = []
    if config is not None:
        resolved = get_resolved_modules(config)
    elif module_meta is not None:
        registry = load_modules_registry()
        for dep in module_meta.get("depends", []):
            if dep.strip():
                resolve_deps(dep.strip(), registry, resolved, base_dir=os.getcwd())

    for (key, mod_dir, meta) in resolved:
        for vf in get_v_files(mod_dir):
            sources.append(vf)
        for svf in get_sv_files(mod_dir):
            name = os.path.basename(svf)
            out  = os.path.join(BUILD_CONVERTED, "modules", key, name[:-3] + ".v")
            sv2v_convert(svf, out)
            sources.append(out)
            sv2v_count += 1

    if sv2v_count > 0:
        print(f"[SV2V] Converted {sv2v_count} .sv file(s) -> {BUILD_CONVERTED}/")
    return sources

def board_mk_block(boards):
    """Render the TARGET -> device/part selection chain that common.mk needs.

    Mirrors the upstream F4PGA chain, but generated from boards.json so a new
    board only has to be described in one place.
    """
    lines = []
    seen  = set()
    for name, b in boards.items():
        missing = [k for k in ("target", "vpr_device", "device", "partname", "ofl_board")
                   if not b.get(k)]
        if missing:
            print(f"[WARN] Board '{name}' is missing {', '.join(missing)} -- skipped in common.mk")
            continue
        if b["target"] in seen:
            print(f"[WARN] Board '{name}' reuses target '{b['target']}' -- skipped in common.mk")
            continue
        seen.add(b["target"])
        lines += [
            f"{'else ' if lines else ''}ifeq ($(TARGET),{b['target']})",
            f"  DEVICE := {b['vpr_device']}",
            f"  BITSTREAM_DEVICE := {b['device']}",
            f"  PARTNAME := {b['partname']}",
            f"  OFL_BOARD := {b['ofl_board']}",
        ]
    if not lines:
        return None
    return lines + ["else", "  $(error Unsupported board type: $(TARGET))", "endif"]

def write_common_mk():
    """Write common/common.mk -- upstream F4PGA build rules, Anvil's board table.

    The upstream board chain is replaced rather than extended: boards.json is
    the single source of truth, so a target can never resolve to a stale part.
    Returns False when f4pga-examples is unavailable (any existing file stands).
    """
    src = os.path.join(F4PGA_EXAMPLES, "common", "common.mk")
    if not os.path.exists(src):
        return False

    with open(src) as f:
        upstream = f.read().splitlines()

    block = board_mk_block(load_boards())
    start = next((i for i, l in enumerate(upstream)
                  if l.startswith("ifeq ($(TARGET),")), None)
    end   = None
    if start is not None:
        end = next((i for i in range(start + 1, len(upstream))
                    if upstream[i].strip() == "endif"), None)

    os.makedirs("common", exist_ok=True)
    dst = os.path.join("common", "common.mk")

    if block is None or start is None or end is None:
        print("[WARN] common.mk: upstream board table not recognized -- copied unchanged")
        shutil.copy(src, dst)
        return True

    with open(dst, "w") as f:
        f.write("\n".join(upstream[:start] + block + upstream[end + 1:]) + "\n")
    return True

def build_makefile(config):
    target = config["target"]
    xdc    = config["xdc"]

    raw_sources = collect_sources(config=config)


    home = os.path.expanduser("~")
    if SCRIPT_DIR == home or SCRIPT_DIR.startswith(home + os.sep):
        anvil_home_default = os.path.join("$(HOME)", os.path.relpath(SCRIPT_DIR, home))
    else:
        anvil_home_default = SCRIPT_DIR


    def to_make_path(s):
        if s == MODULES_DIR or s.startswith(MODULES_DIR + os.sep):
            rel = os.path.relpath(s, MODULES_DIR)
            return f"$(ANVIL_HOME)/modules/{rel}"
        if os.path.isabs(s):
            return s
        return f"${{current_dir}}/{s}"

    sources = [to_make_path(s) for s in raw_sources]

    if not sources:
        sources = ["${current_dir}/*.v"]

    sources_str = " \\\n           ".join(sources)
    with open("Makefile", "w") as f:
        f.write(
            f"current_dir := ${{CURDIR}}\n"
            f"ANVIL_HOME ?= {anvil_home_default}\n"
            f"TARGET := {target}\n"
            f"TOP := top\n"
            f"SOURCES := {sources_str}\n"
            f"XDC := ${{current_dir}}/{xdc}\n"
            f"\n"
            f"include ${{current_dir}}/common/common.mk\n"
        )

def run(cmd, shell=True, check=True):
    result = subprocess.run(cmd, shell=shell, text=True)
    if check and result.returncode != 0:
        print(red("\n✗ command failed"))
        sys.exit(1)
    return result

# ─── Output formatting ────────────────────────────────────────────────────────
# Colour only on a real terminal: piping to a file, or running under the
# toolchain installer, must stay free of escape codes.
_COLOR = (sys.stdout.isatty()
          and not os.environ.get("NO_COLOR")
          and os.environ.get("TERM") != "dumb")

def _paint(code, s):
    return f"\033[{code}m{s}\033[0m" if _COLOR else s

def green(s):  return _paint("32", s)
def yellow(s): return _paint("33", s)
def red(s):    return _paint("31", s)

# file.ext:line  -- the shape yosys, VPR and gcc all use to point at source.
_SRC_REF = re.compile(r"([^\s:()'\"]+\.(?:sv|v|xdc|cpp|cc|c|hpp|h|S|ld|hs|tcl)):(\d+)")

def project_refs(line, root):
    """Source references in `line` that point inside the project.

    Everything else -- yosys' own cells_map.v, VPR's C++ sources, the conda
    env -- belongs to the toolchain and is noise the user cannot act on.
    """
    refs = []
    for m in _SRC_REF.finditer(line):
        path = os.path.expanduser(m.group(1))
        full = os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))
        if (full == root or full.startswith(root + os.sep)) and os.path.isfile(full):
            refs.append(f"{os.path.relpath(full, root)}:{m.group(2)}")
    return refs

_FILE_TOKEN  = re.compile(r"[^\s'\",]+\.(?:sv|v|xdc|sdc|pcf|cpp|cc|c|S)\b")
def command_files(cmdline, root):
    """Project files named on a stage's command line.

    Nothing is inferred: these are the inputs the stage was actually handed,
    and a token only counts once it resolves to a file that exists in the
    project. sv2v outputs are reported next to the source they came from,
    since that is the file the user edits.
    """
    out = []
    for tok in _FILE_TOKEN.findall(cmdline or ""):
        path = os.path.expanduser(tok)
        full = os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))
        if not (full == root or full.startswith(root + os.sep)):
            continue
        if not os.path.isfile(full):
            continue
        rel = os.path.relpath(full, root)
        conv = os.path.join(BUILD_CONVERTED, "")
        if rel.startswith(conv) and rel.endswith(".v"):
            src = os.path.basename(rel)[:-2] + ".sv"
            if os.path.isfile(os.path.join(root, src)):
                rel = f"{rel}  (generated from {src})"
        if rel not in out:
            out.append(rel)
    return out

def shorten(line, root):
    """Drop the project prefix so paths read as the user typed them."""
    return line.replace(root + os.sep, "")

def own_warnings(lines, root):
    """Warning lines that name a file inside the project, deduplicated."""
    seen, out = set(), []
    for line in lines:
        s = line.strip()
        if "warning:" not in s.lower():
            continue
        if not project_refs(s, root):
            continue
        s = shorten(s, root)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out

def diagnostics(lines, root=None):
    """Everything the tools themselves flagged, in the order they said it.

    No filtering by file and no per-error knowledge: whatever a tool called a
    warning or an error is shown. On a failure the cause is almost always a
    warning emitted just before the fatal line -- yosys' `get_ports`, for one,
    only warns that a port is missing and then lets the next command die on the
    empty result. Dropping those loses the explanation.
    """
    seen, out = set(), []
    cont = 0                      # indented lines continuing the last diagnostic
    for line in lines:
        s = line.strip()
        if not s or s.startswith("make:") or "***" in s:
            cont = 0
            continue
        # "        Set ram_addr_bits to 4 ..." and gcc's caret art carry the
        # actionable half of a message and match no keyword of their own.
        if cont and line[:1].isspace() and out:
            out.append(line.rstrip())   # keep the indent: gcc's carets align to it
            cont -= 1
            continue
        cont = 0
        low = s.lower()
        if not ("warn" in low or "error" in low
                or re.match(r"^\w*(Error|Exception):", s)):
            continue
        if root:
            refs = _SRC_REF.findall(s)
            if refs and not project_refs(s, root):
                continue
        if s not in seen:
            seen.add(s)
            out.append(s)
            cont = 2
    return out

def is_warning(line):
    low = line.lower()
    return "warn" in low and "error" not in low

def error_lines(lines):
    """Lines that look like the actual failure, newest-last, deduplicated.

    `make: *** [...] Error 1` is deliberately skipped: it only says which
    recipe died, which the stage name already tells the user, and it would
    crowd out the message that explains why. If a run produces nothing but
    that line, the caller's tail dump still shows it.
    """
    seen, out = set(), []
    for line in lines:
        s = line.strip()
        low = s.lower()
        if s.startswith("make:") or "***" in s:
            continue
        hit = (low.startswith("error") or "error:" in low
               or re.match(r"^\w*(Error|Exception):", s))
        if hit and s not in seen:
            seen.add(s)
            out.append(s)
    return out

def note_warnings(output, root=None):
    """Show a short tool's warnings even when it exits 0.

    programator.py warns that the firmware overflows the RAM depth and then
    returns success -- silently truncating it. Capturing a step's output must
    not be what hides that.
    """
    root = root or os.getcwd()
    for line in diagnostics((output or "").splitlines(), root):
        if is_warning(line):
            print(yellow(f"  ⚠ {shorten(line, root)}"))

def fail(title, output=None, log_path=None, root=None):
    """Report a failed stage the same way everywhere, then exit.

    For stages that produce little enough output to keep in memory; the ones
    that write a log use report() instead. Both print the same shape, so a
    failure looks the same whichever tool produced it.
    """
    root = root or os.getcwd()
    print(red(f"\n✗ {title}"))
    if isinstance(output, str):
        lines = output.splitlines()
    else:
        lines = list(output or [])
    shown = diagnostics(lines, root) or [l for l in lines if l.strip()][-10:]
    for d in shown[:12]:
        d = shorten(d, root)
        print(yellow(f"    {d}") if is_warning(d) else red(f"    {d}"))
    if log_path:
        print(f"  full log: {os.path.relpath(log_path, root)}")
    sys.exit(1)

def run_logged(cmd, log_path, verbose=False, on_line=None):
    """Run `cmd`, tee every line to `log_path`, and return (rc, lines).

    Quiet by default -- `on_line` gets each line so the caller can report
    progress. With verbose=True the raw output is echoed through untouched.
    """
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    lines = []
    proc = subprocess.Popen(
        cmd, shell=isinstance(cmd, str), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
    )
    with open(log_path, "w") as log:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            log.write(line + "\n")
            if verbose:
                print(line)
            elif on_line:
                on_line(line)
    proc.stdout.close()
    return proc.wait(), lines

def report(rc, lines, log_path, root, stage=None, config=None, what="Build",
           stage_cmd=None, since=0):
    """Print the warning/error summary. Returns True when the run succeeded."""
    if rc == 0:
        # A symbol, not the word: the tool's line already says "warning:".
        for w in own_warnings(lines, root):
            print(yellow(f"  ⚠ {w}"))
        return True

    label = f"{stage} failed" if stage else f"{what} failed"
    print(red(f"\n✗ {label}"))

    # Everything the failing stage flagged -- warnings included, since the
    # fatal line is often just the consequence of one of them.
    diag = diagnostics(lines[since:] if since else lines, root)
    for d in diag[:12]:
        d = shorten(d, root)
        print(yellow(f"    {d}") if is_warning(d) else red(f"    {d}"))
    if len(diag) > 12:
        print(f"    ... {len(diag) - 12} more in the log")
    if not diag:                      # nothing flagged -- never hide the failure
        for line in [l for l in lines if l.strip()][-15:]:
            print(f"    {shorten(line, root)}")

    # The tools name no file, so report the stage's actual inputs rather than
    # inferring a culprit from the message text.
    files = command_files(stage_cmd, root)
    if files:
        print("  files this stage was given:")
        for f in files:
            print(f"    {f}")

    print(f"  full log: {os.path.relpath(log_path, root)}")
    return False

def conda_cmd(cmd):
    """Wrap `cmd` so it runs inside the activated F4PGA conda environment."""
    full = (
        f"source {CONDA_SH} && "
        f"conda activate {CONDA_ENV} && "
        f"export F4PGA_INSTALL_DIR={F4PGA_INSTALL} && "
        f"export FPGA_FAM={FPGA_FAM} && "
        f"{cmd}"
    )
    return f"bash -c '{full}'"

def conda_run(cmd):
    run(conda_cmd(cmd))

def find_bitstream(target):
    build = os.path.join(os.getcwd(), BUILD_DIR, target)
    if os.path.isdir(build):
        for f in os.listdir(build):
            if f.endswith(".bit"):
                return os.path.join(build, f)
    return None

def get_usb_devices():
    result = subprocess.run([OPENFPGALOADER, "--scan-usb"], capture_output=True, text=True)
    devices = []
    for line in result.stdout.splitlines()[1:]:
        if line.strip():
            devices.append(line.strip())
    return devices

MAIN_CPP_TEMPLATE = '''// main.cpp -- entry point
#include "soc.hpp"

int main() {
    uart_puts("Hello from PicoRV32!\\n");
    while (1);
    return 0;
}
'''

SOC_HPP_TEMPLATE = '''// soc.hpp -- Memory-mapped peripheral defines
#pragma once

// FPRO-style address layout:
//   0xC0000000 + (slot << 7) + (reg << 2)
//   slot: 0-63 (6 bits)
//   reg:  0-31 (5 bits, 32-bit reg = 4 bytes)

#define IO_BASE     0xC0000000
#define SLOT_ADDR(slot, reg)  (IO_BASE + ((slot) << 7) + ((reg) << 2))

// Slot 0 = UART (assign in top.sv)
#define UART_TX     (*(volatile unsigned int*)SLOT_ADDR(0, 0))
#define UART_RX     (*(volatile unsigned int*)SLOT_ADDR(0, 1))
#define UART_STATUS (*(volatile unsigned int*)SLOT_ADDR(0, 2))

inline void uart_putc(char c) { UART_TX = c; }

inline void uart_puts(const char* s) {
    while (*s) uart_putc(*s++);
}
'''

TOP_SV_TEMPLATE = '''module top (
    // Add your ports here
);

endmodule
'''

def cmd_init(args):
    if "--module" in args:
        cmd_initmodule(args)
        return

    boards = load_boards()
    board_name   = None
    example_name = None

    if "--board" in args:
        idx = args.index("--board")
        if idx + 1 < len(args):
            board_name = args[idx + 1]

    if "--example" in args:
        idx = args.index("--example")
        if idx + 1 < len(args):
            example_name = args[idx + 1]

    if not board_name or board_name not in boards:
        print("[ERROR] Specify a board: anvil init --board <name> [--example <name>]")
        print(f"        Available: {', '.join(boards.keys())}")
        sys.exit(1)

    board = boards[board_name]
    name  = os.path.basename(os.getcwd())
    xdc   = board["xdc"]

    print(f"[Anvil] Initializing project: {name}")
    print(f"[Anvil] Board: {board_name} -- {board['description']}")

    # Validate example if specified
    if example_name:
        example_dir = os.path.join(EXAMPLES_DIR, board_name, example_name)
        if not os.path.isdir(example_dir):
            available = get_examples_for_board(board_name)
            print(f"[ERROR] Example '{example_name}' not found for {board_name}")
            print(f"        Available: {', '.join(available) if available else 'none'}")
            sys.exit(1)
        print(f"[Anvil] Example: {example_name}")
        copy_example(board_name, example_name, ".")

    # Load config from example if present, else create fresh
    if example_name and os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        config["project"] = name
        config["board"]   = board_name
        for k, v in board.items():
            if k not in config:
                config[k] = v
    else:
        config = {
            "project": name,
            "board":   board_name,
            "params":  {},
            **board
        }
    config, _ = migrate_config(config)
    save_config(config)

    xdc_src = os.path.join(XDC_DIR, xdc)
    if not os.path.exists(xdc):
        if os.path.exists(xdc_src):
            shutil.copy(xdc_src, xdc)
            print(f"[Anvil] Copied XDC: {xdc}")
        else:
            with open(xdc, "w") as f:
                f.write(f"# XDC constraints for {board_name}\n")

    if not os.path.exists("top.sv") and not os.path.exists("top.v"):
        with open("top.sv", "w") as f:
            f.write(TOP_SV_TEMPLATE)

    os.makedirs(TB_DIR, exist_ok=True)

    if not write_common_mk() and not os.path.exists(os.path.join("common", "common.mk")):
        print(f"[WARN] common/common.mk not written -- {F4PGA_EXAMPLES} is missing")
        print("       `anvil synth` will fail until the toolchain is installed (anvil update)")

    # If example brought modules, scaffold firmware/ template if SoC detected and not present
    if config.get("modules"):
        resolved = get_resolved_modules(config)
        soc_dir, soc_cfg, soc_key = find_soc_module(resolved)
        if soc_dir and not os.path.exists(FW_DIR):
            os.makedirs(FW_SRC, exist_ok=True)
            os.makedirs(FW_INC, exist_ok=True)
            with open(os.path.join(FW_SRC, "main.cpp"), "w") as f:
                f.write(MAIN_CPP_TEMPLATE)
            with open(os.path.join(FW_INC, "soc.hpp"), "w") as f:
                f.write(SOC_HPP_TEMPLATE)
            print(f"[Anvil] SoC '{soc_key}' detected -- created firmware/ template")

    build_makefile(config)

    print("[Anvil] Done!")
    if example_name:
        print(f"  Example '{example_name}' loaded -- ready to build!")
    else:
        print(f"  Files: config.json, top.sv, {xdc}, Makefile, tb/, common/")
        print(f"  Next: anvil addmodule <module>")

def cmd_initmodule(args):
    module_name = None
    if "--module" in args:
        idx = args.index("--module")
        if idx + 1 < len(args):
            module_name = args[idx + 1]

    if not module_name:
        print("[ERROR] Specify module name: anvil init --module <name>")
        sys.exit(1)

    print(f"[Anvil] Creating module: {module_name}")

    meta = {
        "name":        module_name,
        "description": f"{module_name} module",
        "version":     "1.0.0",
        "depends":     []
    }
    with open("module.json", "w") as f:
        json.dump(meta, f, indent=2)

    sv_file = f"{module_name}.sv"
    v_file  = f"{module_name}.v"
    if not os.path.exists(sv_file) and not os.path.exists(v_file):
        with open(sv_file, "w") as f:
            f.write(f"// {module_name} module\n\nmodule {module_name}(\n);\n\nendmodule\n")
        created = sv_file
    else:
        created = sv_file if os.path.exists(sv_file) else v_file

    os.makedirs(TB_DIR, exist_ok=True)

    print("[Anvil] Done!")
    print(f"  module.json, {created}, tb/")
    print(f"  When ready: anvil installmodule")

def cmd_addmodule(args):
    assume_yes = "--yes" in args
    force      = "--force" in args
    refs = [a for a in args if a not in ("--yes", "--force")]
    if not refs:
        print("[ERROR] Specify module(s): anvil addmodule <name> ...")
        sys.exit(1)

    registry = load_modules_registry()
    config   = load_config()
    current  = config.get("modules", {})

    to_add, added_keys = {}, []

    # --force only re-records the single named entry; it never walks that module's own
    # dependency chain, so no *other* entry's hash or soc.json status changes unannounced
    force_names = [r for r in refs if force and r in current]
    other_refs  = [r for r in refs if r not in force_names]

    if force_names:
        plan = plan_force(force_names, current)
        if not confirm_force(plan, assume_yes):
            print("[Anvil] Aborted -- nothing re-recorded.")
            return
        for p in plan:
            if p["before"] == p["after"]:
                continue
            to_add[p["name"]] = module_entry(p["version"], p["source"], p["path"], p["after"])
            added_keys.append(f"{p['name']}@{p['version']}")

    bundled_refs, external_refs = [], []
    for ref in other_refs:
        (external_refs if fetch.classify(ref) == "url" else bundled_refs).append(ref)

    for mod in bundled_refs:
        chain = resolve_deps(mod, registry, base_dir=os.getcwd())
        for (key, mod_dir, meta) in chain:
            name = meta["name"]
            if name not in current and name not in to_add:
                to_add[name] = make_entry(key, mod_dir, meta)
                added_keys.append(key)

    if external_refs:
        staging = tempfile.mkdtemp()
        try:
            plan = plan_external(external_refs, staging, {**current, **to_add})
            if not confirm_external(plan, assume_yes):
                print("[Anvil] Aborted -- nothing installed.")
                return
            os.makedirs(EXTERNAL_DIR, exist_ok=True)
            installed = []
            for i, m in enumerate(plan):
                dest = os.path.join(EXTERNAL_DIR, f"{m['name']}@{m['version']}")
                try:
                    if os.path.isdir(dest):
                        shutil.rmtree(dest)
                    shutil.move(m["staged"], dest)
                    digest = fetch.module_hash(dest)
                    with open(os.path.join(dest, "module.json")) as f:
                        dep_meta = json.load(f)
                except Exception as e:
                    # config.json is never written on this path -- the successful half is
                    # only orphaned on disk, never loaded by a later build
                    lines = ([f"  {d}" for d in installed]
                             if installed else ["nothing was installed before the failure"])
                    if installed:
                        lines.insert(0, "installed before the failure -- still on disk, remove by hand if unwanted:")
                    lines.append(f"failed on '{m['name']}': {e}")
                    skipped = [p["name"] for p in plan[i + 1:]]
                    if skipped:
                        lines.append(f"not attempted: {', '.join(skipped)}")
                    fail(f"installing '{m['name']}' failed -- config.json was not written", lines)
                to_add[m["name"]] = module_entry(m["version"], m["source"], dest, digest)
                added_keys.append(f"{m['name']}@{m['version']}")
                installed.append(dest)
                for dep in dep_meta.get("depends", []):
                    if fetch.classify(dep) == "name":   # bundled deps of a fetched module
                        for (key, mod_dir, meta) in resolve_deps(dep, registry, base_dir=os.getcwd()):
                            name = meta["name"]
                            if name not in current and name not in to_add:
                                to_add[name] = make_entry(key, mod_dir, meta)
                                added_keys.append(key)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    if not to_add:
        print("[Anvil] All requested modules already present.")
        return

    print(f"[Anvil] Resolving dependencies...")
    for key in added_keys:
        print(f"  + {key}")

    config["modules"] = {**current, **to_add}
    save_config(config)

    resolved = get_resolved_modules(config)
    soc_dir, soc_cfg, soc_key = find_soc_module(resolved)
    if soc_dir and not os.path.exists(FW_DIR):
        os.makedirs(FW_SRC, exist_ok=True)
        os.makedirs(FW_INC, exist_ok=True)
        with open(os.path.join(FW_SRC, "main.cpp"), "w") as f:
            f.write(MAIN_CPP_TEMPLATE)
        with open(os.path.join(FW_INC, "soc.hpp"), "w") as f:
            f.write(SOC_HPP_TEMPLATE)
        print(f"[Anvil] SoC '{soc_key}' detected -- created firmware/ template")

        if "ram_addr_bits" not in config.get("params", {}):
            config.setdefault("params", {})["ram_addr_bits"] = 11
            save_config(config)
            print(f"[Anvil] Default params.ram_addr_bits = 11 (8KB RAM)")

    build_makefile(config)
    print(f"[Anvil] Added {len(to_add)} module(s).")

def cmd_removemodule(args):
    if not args:
        print("[ERROR] Specify module(s): anvil removemodule <name> ...")
        sys.exit(1)

    config  = load_config()
    current = config.get("modules", {})
    base    = os.getcwd()

    to_remove = set()
    unknown = []
    for a in args:
        match = find_by_local_path(current, base, a)
        if match is None:
            name = parse_module_ref(a)[0]
            if name in current:
                match = name
        if match is None:
            unknown.append(a)
        else:
            to_remove.add(match)

    if unknown:
        fail(f"cannot remove {', '.join(unknown)} -- not in this project's modules")

    registry = load_modules_registry()
    for name, entry in current.items():
        if name in to_remove:
            continue
        ref = entry_ref(name, entry)
        if local_dir_missing(ref, base):
            continue   # gone from disk -- cannot be asserting a dependency on anything
        meta, _, _ = load_module_meta(ref, base_dir=base)
        for dep in meta.get("depends", []):
            _, _, dep_meta = resolve_deps(dep, registry, base_dir=os.getcwd())[0]
            if dep_meta["name"] in to_remove:
                print(f"[ERROR] Cannot remove '{dep_meta['name']}' -- '{name}' depends on it.")
                sys.exit(1)

    removed = [n for n in current if n in to_remove]
    config["modules"] = {n: e for n, e in current.items() if n not in to_remove}
    save_config(config)

    stale = [n for n, e in config["modules"].items() if local_dir_missing(entry_ref(n, e), base)]
    if stale:
        print(f"[WARN] Makefile not regenerated -- module director{'y' if len(stale) == 1 else 'ies'} missing: {', '.join(stale)}")
    else:
        build_makefile(config)
    print(f"[Anvil] Removed: {', '.join(removed)}")

def cmd_modules(args):
    registry = load_modules_registry()

    if os.path.exists(CONFIG_FILE):
        config  = load_config()
        current = config.get("modules", {})
        print(f"[Anvil] Modules in '{config['project']}':")
        if current:
            for name, entry in current.items():
                meta, _, _ = load_module_meta(entry_ref(name, entry))
                print(f"  + {name:<25} {meta.get('description', '')}")
        else:
            print("  (none)")
        print()

    print("[Anvil] Available modules:")
    for name, info in registry.items():
        ver        = info.get("latest", "?")
        key        = f"{name}@{ver}"
        meta, mod_dir, _ = load_module_meta(key)
        is_soc     = "[SOC]" if os.path.exists(os.path.join(mod_dir, "soc.json")) else "     "
        in_proj    = "+" if os.path.exists(CONFIG_FILE) and name in load_config().get("modules", {}) else " "
        print(f"  [{in_proj}] {is_soc} {key:<30} {info['description']}")

def cmd_installmodule(args):
    if not os.path.exists("module.json"):
        print("[ERROR] No module.json. Run: anvil init --module <name>")
        sys.exit(1)

    with open("module.json") as f:
        meta = json.load(f)

    module_name = meta["name"]
    version     = meta.get("version", "1.0.0")
    folder      = f"{module_name}@{version}"
    dst         = os.path.join(MODULES_DIR, folder)

    v_files  = get_v_files(".")
    sv_files = get_sv_files(".")
    print(f"[Anvil] Found {len(v_files)} .v + {len(sv_files)} .sv file(s)")
    if os.path.exists("soc.json"):
        print(f"[Anvil] SoC module detected (soc.json)")

    if os.path.exists(dst):
        if input(f"[Anvil] '{folder}' exists. Overwrite? [y/N] ").lower() != "y":
            print("[Anvil] Aborted.")
            sys.exit(0)
        shutil.rmtree(dst)

    shutil.copytree(
        os.getcwd(), dst,
        ignore=shutil.ignore_patterns("build", "*.vcd", "__pycache__", ".git")
    )
    print(f"[Anvil] Installed: {folder}")

    registry = load_modules_registry()
    if module_name not in registry:
        registry[module_name] = {
            "description": meta["description"],
            "versions":    [version],
            "latest":      version
        }
    else:
        if version not in registry[module_name]["versions"]:
            registry[module_name]["versions"].append(version)
        registry[module_name]["latest"] = version

    with open(MODULES_FILE, "w") as f:
        json.dump(registry, f, indent=2)

    print(f"[Anvil] Use with: anvil addmodule {module_name}")

def cmd_compile(args):
    """Build firmware: C++ -> ELF -> mem -> ram.v"""
    config   = load_config()
    resolved = get_resolved_modules(config)
    soc_dir, soc_cfg, soc_key = find_soc_module(resolved)

    if not soc_dir:
        print("[Anvil] No SoC module in project -- skipping firmware build")
        print("       Add one with: anvil addmodule <soc-module>")
        return

    if not os.path.isdir(FW_SRC):
        print(f"[ERROR] No {FW_SRC}/ directory found")
        sys.exit(1)

    src_files = (
        sorted(glob.glob(os.path.join(FW_SRC, "*.cpp"))) +
        sorted(glob.glob(os.path.join(FW_SRC, "*.c"))) +
        sorted(glob.glob(os.path.join(FW_SRC, "*.S")))
    )
    if not src_files:
        print(f"[ERROR] No source files in {FW_SRC}/")
        sys.exit(1)

    os.makedirs(BUILD_FW_DIR, exist_ok=True)

    cpu     = soc_cfg["cpu"]
    cflags  = soc_cfg.get("cflags", [])
    ldflags = soc_cfg.get("ldflags", [])
    params  = config.get("params", {})
    defsyms = eval_defsyms(soc_cfg.get("defsyms", {}), params)

    elf_out = os.path.join(BUILD_FW_DIR, "firmware.elf")
    mem_out = os.path.join(BUILD_FW_DIR, "firmware.mem")
    ram_out = os.path.join(BUILD_FW_DIR, "ram.v")

    print(f"[Anvil] Compiling firmware (SoC: {soc_key})")
    print(f"  Sources: {', '.join(os.path.basename(f) for f in src_files)}")
    for k, v in defsyms.items():
        print(f"  Defsym:  {k} = 0x{v:x}")

    cmd = [cpu["compiler"]]
    cmd += [f"-march={cpu['march']}", f"-mabi={cpu['mabi']}"]
    cmd += cflags
    cmd += ldflags
    cmd += ["-I", FW_INC]
    cmd += ["-T", os.path.join(soc_dir, "link.ld")]
    cmd += [os.path.join(soc_dir, "startup.S")]
    cmd += src_files
    for name, val in defsyms.items():
        cmd += [f"-Wl,--defsym={name}={val}"]
    cmd += ["-o", elf_out]

    root     = os.getcwd()
    log_path = os.path.join(root, BUILD_FW_DIR, "compile.log")
    rc, lines = run_logged(cmd, log_path,
                           verbose="--verbose" in args or "-v" in args)
    if not report(rc, lines, log_path, root, what="Firmware compilation"):
        sys.exit(1)

    r = subprocess.run([cpu["objcopy"], "-O", "verilog", elf_out, mem_out],
                       text=True, capture_output=True)
    if r.returncode != 0:
        fail("objcopy failed", (r.stderr or "") + (r.stdout or ""))
    note_warnings((r.stderr or "") + (r.stdout or ""))

    ram_addr_bits = params.get("ram_addr_bits", 11)
    depth = 1 << ram_addr_bits
    r = subprocess.run([
        "python3", PROGRAMATOR,
        mem_out,
        "--module-name", "ram",
        "--depth", str(depth),
        "-o", ram_out
    ], text=True, capture_output=True)
    if r.returncode != 0:
        fail("ram generation failed", (r.stderr or "") + (r.stdout or ""))
    note_warnings((r.stderr or "") + (r.stdout or ""))

    print(f"[Anvil] Firmware -> {ram_out}")

def cmd_synth(args):
    """Synthesize FPGA bitstream."""
    config = load_config()
    target = config["target"]

    ensure_modules(config)   # external/ is git-ignored -- a fresh clone must restore it here

    # Regenerate both: a project scaffolded by an older Anvil still has a
    # common.mk that predates its board.
    write_common_mk()
    build_makefile(config)

    print(f"[Anvil] Synthesizing for {config['board']}...")
    if config.get("modules"):
        print(f"[Anvil] Modules: {', '.join(config['modules'])}")

    root     = os.getcwd()
    verbose  = "--verbose" in args or "-v" in args
    log_path = os.path.join(root, BUILD_DIR, target, "synth.log")

    # The make recipes echo each stage's command; that is what marks progress.
    stage_of = {
        "symbiflow_synth":           "synth",
        "symbiflow_pack":            "pack",
        "symbiflow_place":           "place",
        "symbiflow_route":           "route",
        "symbiflow_write_fasm":      "fasm",
        "symbiflow_write_bitstream": "bitstream",
    }
    state = {"stage": None, "cmd": None, "at": 0, "n": 0}

    def watch(line):
        state["n"] += 1
        for marker, name in stage_of.items():
            if marker in line and state["stage"] != name:
                if state["stage"]:
                    print(green("ok"))
                print(f"  {name:<10}", end="", flush=True)
                state["stage"] = name
                state["cmd"]   = line     # names this stage's input files
                state["at"]    = state["n"] - 1
                break

    t0 = time.time()
    rc, lines = run_logged(
        conda_cmd(f"cd {root} && TARGET={target} make"),
        log_path, verbose=verbose, on_line=watch,
    )
    elapsed = time.time() - t0

    if not verbose and state["stage"]:
        print(red("failed") if rc != 0 else green("ok"))

    if not report(rc, lines, log_path, root, stage=state["stage"],
                  config=config, stage_cmd=state["cmd"], since=state["at"]):
        sys.exit(1)

    bit = find_bitstream(target)
    if not bit:
        print(red("\n✗ no .bit file produced"))
        print(f"  full log: {os.path.relpath(log_path, root)}")
        sys.exit(1)
    print(green(f"\n✓ Done in {elapsed:.1f}s -- {os.path.relpath(bit, root)}"))

def cmd_build(args):
    cmd_compile(args)
    cmd_synth(args)

def cmd_rebuild(args):
    """Clean + compile + synth"""
    cmd_clean(args)
    cmd_compile(args)
    cmd_synth(args)

def cmd_program(args):
    config = load_config()
    target = config["target"]
    ofl_board = config["ofl_board"]

    bit = find_bitstream(target)
    if not bit:
        print("[ERROR] No bitstream. Run: anvil synth")
        sys.exit(1)
    print(f"[Anvil] Bitstream: {bit}")

    devices = get_usb_devices()
    if not devices:
        print("[ERROR] No FPGA devices found")
        sys.exit(1)

    if len(devices) == 1:
        print(f"[Anvil] Device: {devices[0]}")
    else:
        print("[Anvil] Multiple devices:")
        for i, d in enumerate(devices):
            print(f"  [{i}] {d}")
        int(input("Select: "))

    print(f"[Anvil] Programming {ofl_board}...")
    r = subprocess.run(f"sudo {OPENFPGALOADER} -b {ofl_board} {bit}",
                       shell=True, text=True, capture_output=True)
    print(r.stdout, end="")
    if r.returncode != 0:
        fail("programming failed", (r.stderr or "") + (r.stdout or ""))
    print(f"[Anvil] Done! UART on /dev/ttyUSB1")

def cmd_test(args):
    if not args:
        print("[ERROR] Usage: anvil test [src.{v,sv} ...] tb/<tb>.{v,sv}")
        sys.exit(1)

    tb_file = None
    src_extra = []
    for a in args:
        base = os.path.basename(a)
        if (a.startswith(f"{TB_DIR}/")
                or base.endswith("_tb.v")
                or base.endswith("_tb.sv")):
            tb_file = a
        else:
            src_extra.append(a)

    if not tb_file or not os.path.exists(tb_file):
        print("[ERROR] Testbench not found. Must be in tb/ or end with _tb.{v,sv}")
        sys.exit(1)

    print(f"[TEST] Testbench: {tb_file}")

    # Source discovery
    if src_extra:
        src_files = []
        for s in src_extra:
            if s.endswith(".sv"):
                rel = os.path.relpath(s, ".")
                out = os.path.join(BUILD_CONVERTED, rel[:-3] + ".v")
                sv2v_convert(s, out)
                src_files.append(out)
            else:
                src_files.append(s)
    elif os.path.exists(CONFIG_FILE):
        src_files = collect_sources(config=load_config())
    elif os.path.exists("module.json"):
        with open("module.json") as mf:
            mod_meta = json.load(mf)
        src_files = collect_sources(module_meta=mod_meta)
    else:
        src_files = collect_sources()

    # Convert tb if it's .sv
    tb_compile_path = tb_file
    if tb_file.endswith(".sv"):
        rel = os.path.relpath(tb_file, ".")
        tb_compile_path = os.path.join(BUILD_CONVERTED, rel[:-3] + ".v")
        sv2v_convert(tb_file, tb_compile_path)

    # Dedup: tb may already be in src_files via project-root discovery.
    # Normalize paths so "./x.v" and "x.v" collapse to one.
    seen = set()
    all_files = []
    for f in src_files + [tb_compile_path]:
        key = os.path.normpath(f)
        if key not in seen:
            seen.add(key)
            all_files.append(f)
    print(f"[TEST] Compiling {len(all_files)} file(s)...")
    for f in all_files:
        print(f"  {f}")

    tb_base = os.path.basename(tb_file)
    tb_name = tb_base.rsplit(".", 1)[0]   # strip .v or .sv
    out = f"/tmp/anvil_test_{tb_name}.vvp"
    vcd_file = os.path.join(TB_DIR, f"{tb_name}.vcd")

    result = subprocess.run(
        f"iverilog -g2012 -o {out} {' '.join(all_files)}",
        shell=True, text=True, capture_output=True
    )
    if result.returncode != 0:
        fail("testbench compile failed", (result.stderr or "") + (result.stdout or ""))

    print(f"[TEST] Running...")
    result = subprocess.run(f"vvp {out}", shell=True, text=True)
    if result.returncode != 0:
        fail("testbench run failed")

    if os.path.exists(vcd_file):
        print(f"[TEST] VCD: {vcd_file}")
    print("[TEST] Done.")

def cmd_clean(args):
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
        print("[Anvil] Cleaned build/")
    else:
        print("[Anvil] Nothing to clean.")

def cmd_status(args):
    boards = load_boards()
    if not os.path.exists(CONFIG_FILE):
        print("[Anvil] No project here. Run: anvil init --board <name>")
        print(f"       Boards: {', '.join(boards.keys())}")
        return

    config = load_config()
    bit = find_bitstream(config["target"])
    mods = config.get("modules", {})

    resolved = get_resolved_modules(config)
    soc_dir, soc_cfg, soc_key = find_soc_module(resolved)

    has_fw = os.path.isdir(FW_SRC)
    fw_built = os.path.exists(os.path.join(BUILD_FW_DIR, "ram.v"))

    print(f"[Anvil] Project   : {config['project']}")
    print(f"       Board     : {config['board']} -- {config['description']}")
    print(f"       Modules   : {', '.join(mods) if mods else 'none'}")
    print(f"       SoC       : {soc_key or 'none'}")
    print(f"       Params    : {config.get('params', {})}")
    print(f"       Firmware  : {'present' if has_fw else 'none'} {'(built)' if fw_built else ''}")
    print(f"       Bitstream : {bit or 'not built'}")

def _which(*names):
    """First resolvable binary among names (PATH lookup), else None."""
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

def cmd_doctor(args):
    """Check that the external tools Anvil relies on are available."""
    rows     = []   # (status, label, detail, hint)
    failures = 0

    def add(status, label, detail, hint=""):
        nonlocal failures
        if status == "FAIL":
            failures += 1
        rows.append((status, label, detail, hint))

    # Informational
    add("OK", "Python", sys.version.split()[0])

    # Required: .sv -> .v conversion (every build)
    sv2v = shutil.which("sv2v") or (SV2V_HOME if os.path.exists(SV2V_HOME) else None)
    if sv2v:
        add("OK", "sv2v", sv2v)
    else:
        add("FAIL", "sv2v", "not found",
            "PATH or ~/opt/sv2v/sv2v -- https://github.com/zachjs/sv2v/releases")

    # Required: synthesis / place & route
    if os.path.exists(CONDA_SH):
        if os.path.isdir(F4PGA_INSTALL):
            add("OK", "F4PGA / Conda", f"{CONDA_SH} (env: {CONDA_ENV})")
        else:
            add("WARN", "F4PGA / Conda", f"{CONDA_SH} (env: {CONDA_ENV})",
                f"install dir missing: {F4PGA_INSTALL}")
    else:
        add("FAIL", "F4PGA / Conda", "conda.sh not found",
            f"expected {CONDA_SH} -- see the F4PGA setup guide")

    # Required per board: the VPR arch defs the board's device resolves to.
    # A toolchain installed before a board was added will be missing its device.
    arch_dir = os.path.join(F4PGA_INSTALL, FPGA_FAM, "share", "f4pga", "arch")
    wanted   = sorted({b["vpr_device"] for b in load_boards().values() if b.get("vpr_device")})
    if wanted and os.path.isdir(arch_dir):
        missing = [d for d in wanted if not os.path.isdir(os.path.join(arch_dir, d))]
        if missing:
            add("WARN", "F4PGA arch defs", f"missing: {', '.join(missing)}",
                "boards on those devices cannot be synthesized -- run `anvil update`")
        else:
            add("OK", "F4PGA arch defs", ", ".join(wanted))

    # Optional: anvil test
    if _which("iverilog") and _which("vvp"):
        add("OK", "Icarus Verilog", _which("iverilog"))
    else:
        add("WARN", "Icarus Verilog", "iverilog/vvp not found",
            "needed for `anvil test` -- e.g. apt install iverilog")

    # Optional: anvil program
    ofl = (OPENFPGALOADER if os.path.exists(OPENFPGALOADER) else None) or _which("openFPGALoader")
    if ofl:
        add("OK", "openFPGALoader", ofl)
    else:
        add("WARN", "openFPGALoader", "not found",
            "needed for `anvil program` -- build from source")

    # Optional: SoC firmware (anvil compile). Default toolchain; soc.json may override.
    if _which("riscv64-unknown-elf-g++") and _which("riscv64-unknown-elf-objcopy"):
        add("OK", "RISC-V toolchain", _which("riscv64-unknown-elf-g++"))
    else:
        add("WARN", "RISC-V toolchain", "riscv64-unknown-elf-g++ not found",
            "needed for SoC firmware (anvil compile)")

    print("[Anvil] Environment check\n")
    for status, label, detail, hint in rows:
        print(f"  [{status:>4}]  {label:<17} {detail}")
        if hint:
            print(f"           -> {hint}")
    print()
    if failures:
        print(f"[Anvil] {failures} required tool(s) missing -- synth will not work until fixed.")
        sys.exit(1)
    print("[Anvil] All required tools present.")

def read_version():
    vfile = os.path.join(SCRIPT_DIR, "VERSION")
    if os.path.exists(vfile):
        with open(vfile) as f:
            return f.read().strip()
    return "unknown"

def git_short_commit():
    try:
        r = subprocess.run(
            ["git", "-C", SCRIPT_DIR, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""

def cmd_version(args):
    """Print the installed Anvil version."""
    commit = git_short_commit()
    print(f"anvil {read_version()}" + (f" ({commit})" if commit else ""))

def cmd_update(args):
    """Update the whole toolchain (Anvil + dependencies) via the toolchain-setup
    installer, which is idempotent and update-aware. The long integration build
    is skipped by default (doctor still runs); extra args pass through, e.g.
    `anvil update --minimal`."""
    flags = ("--no-test " + " ".join(args)).strip()
    cmd = f"curl -fsSL {TOOLCHAIN_INSTALLER} | bash -s -- {flags}"
    print("[Anvil] Updating toolchain (Anvil + dependencies) via toolchain-setup...")
    run(cmd)

def cmd_examples(args):
    boards     = load_boards()
    board_name = None

    if "--board" in args:
        idx = args.index("--board")
        if idx + 1 < len(args):
            board_name = args[idx + 1]

    if not board_name:
        print("[ERROR] Specify a board: anvil examples --board <name>")
        print(f"        Available: {', '.join(boards.keys())}")
        sys.exit(1)

    if board_name not in boards:
        print(f"[ERROR] Unknown board: {board_name}")
        print(f"        Available: {', '.join(boards.keys())}")
        sys.exit(1)

    examples = get_examples_for_board(board_name)
    if not examples:
        print(f"[Anvil] No examples found for {board_name}")
        return

    print(f"[Anvil] Examples for {board_name}:")
    for ex in examples:
        desc = get_example_description(board_name, ex)
        desc_str = f" -- {desc}" if desc else ""
        print(f"  {ex:<20}{desc_str}")
    print()
    print(f"  Usage: anvil init --board {board_name} --example <name>")

def cmd_boards(args):
    boards = load_boards()
    print("[Anvil] Supported boards:")
    for name, b in boards.items():
        print(f"  {name:<20} {b['description']}")

COMMANDS = {
    "init":          (cmd_init,          "init --board <name>          Initialize project"),
    "compile":       (cmd_compile,       "                              Build firmware (C++ -> ram.v)"),
    "synth":         (cmd_synth,         "                              Synthesize bitstream"),
    "build":         (cmd_build,         "                              compile + synth"),
    "rebuild":       (cmd_rebuild,       "                              clean + compile + synth"),
    "program":       (cmd_program,       "                              Flash board"),
    "test":          (cmd_test,          "test tb/<tb>.v                Run testbench"),
    "clean":         (cmd_clean,         "                              Remove build/"),
    "status":        (cmd_status,        "                              Show project info"),
    "doctor":        (cmd_doctor,        "                              Check external tool dependencies"),
    "update":        (cmd_update,        "                              Update the whole toolchain"),
    "version":       (cmd_version,       "                              Print Anvil version"),
    "boards":        (cmd_boards,        "                              List boards"),
    "examples":      (cmd_examples,      "examples --board <name>       List examples for board"),
    "modules":       (cmd_modules,       "                              List modules"),
    "addmodule":     (cmd_addmodule,     "addmodule <name> ...          Add module(s)"),
    "removemodule":  (cmd_removemodule,  "removemodule <name> ...       Remove module(s)"),
    "installmodule": (cmd_installmodule, "                              Install current dir as module"),
}

def usage():
    print("Usage: anvil <command> [args]")
    print()
    for name, (_, desc) in COMMANDS.items():
        print(f"  {name:<14} {desc}")
    print()
    print("  While a build succeeds you see only warnings from your own files. When a")
    print("  stage fails you see everything that stage flagged, error or warning. The")
    print("  full tool output always goes to build/; --verbose (-v) streams it live.")

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        usage()
        sys.exit(0)
    if args[0] in ("-v", "--version"):
        cmd_version(args[1:])
        sys.exit(0)
    cmd = args[0]
    if cmd not in COMMANDS:
        print(f"[ERROR] Unknown command: {cmd}")
        usage()
        sys.exit(1)
    COMMANDS[cmd][0](args[1:])

if __name__ == "__main__":
    main()
