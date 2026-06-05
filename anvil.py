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
    anvil boards                     List boards
    anvil modules                    List modules
    anvil addmodule <name> ...       Add module(s)
    anvil removemodule <name> ...    Remove module(s)
    anvil installmodule              Install current dir as module
"""

import subprocess
import sys
import os
import shutil
import json
import time
import glob

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
F4PGA_INSTALL  = os.path.expanduser("~/opt/f4pga")
F4PGA_EXAMPLES = os.path.expanduser("~/f4pga-examples")
OPENFPGALOADER = "/usr/local/bin/openFPGALoader"

CONFIG_FILE    = "config.json"
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
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

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
        resolve_deps(child, registry, resolved, seen, base_dir=mod_dir)

    if key not in [r[0] for r in resolved]:
        resolved.append((key, mod_dir, meta))

    seen.discard(key)
    return resolved

def get_resolved_modules(config):
    registry = load_modules_registry()
    resolved = []
    for mod in config.get("modules", []):
        resolve_deps(mod, registry, resolved, base_dir=os.getcwd())
    return resolved

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
            value = eval(expr, {"__builtins__": {}}, params)
        except Exception as e:
            print(f"[ERROR] eval defsym '{name}' = '{expr}': {e}")
            sys.exit(1)
        result[name] = value
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
        print(f"[ERROR] sv2v failed for {sv_path}:")
        if result.stderr:
            print(result.stderr.rstrip())
        sys.exit(1)

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
        print("[ERROR] Command failed")
        sys.exit(1)
    return result

def conda_run(cmd):
    full = (
        f"source {CONDA_SH} && "
        f"conda activate {CONDA_ENV} && "
        f"export F4PGA_INSTALL_DIR={F4PGA_INSTALL} && "
        f"export FPGA_FAM=xc7 && "
        f"{cmd}"
    )
    run(f"bash -c '{full}'")

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
            "modules": [],
            "params":  {},
            **board
        }
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

    os.makedirs("common", exist_ok=True)
    src = os.path.join(F4PGA_EXAMPLES, "common", "common.mk")
    if os.path.exists(src):
        shutil.copy(src, "common/common.mk")

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
    if not args:
        print("[ERROR] Specify module(s): anvil addmodule <name> ...")
        sys.exit(1)

    registry = load_modules_registry()
    config   = load_config()
    current  = config.get("modules", [])

    to_add = []
    for mod in args:
        chain = resolve_deps(mod, registry, base_dir=os.getcwd())
        for (key, mod_dir, meta) in chain:
            if key not in current and key not in to_add:
                to_add.append(key)

    if not to_add:
        print("[Anvil] All requested modules already present.")
        return

    print(f"[Anvil] Resolving dependencies...")
    for key in to_add:
        print(f"  + {key}")

    config["modules"] = current + to_add
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
    current = config.get("modules", [])

    to_remove_names = set()
    for a in args:
        name, _ = parse_module_ref(a)
        to_remove_names.add(name)

    to_remove = set(args) | {m for m in current if parse_module_ref(m)[0] in to_remove_names}

    registry = load_modules_registry()
    for mod in current:
        if mod in to_remove:
            continue
        meta, _, _ = load_module_meta(mod)
        for dep in meta.get("depends", []):
            dep_key = resolve_deps(dep, registry, base_dir=os.getcwd())[0][0]
            if dep_key in to_remove:
                print(f"[ERROR] Cannot remove '{dep_key}' -- '{mod}' depends on it.")
                sys.exit(1)

    removed = [m for m in current if m in to_remove]
    config["modules"] = [m for m in current if m not in to_remove]
    save_config(config)
    build_makefile(config)
    print(f"[Anvil] Removed: {', '.join(removed)}")

def cmd_modules(args):
    registry = load_modules_registry()

    if os.path.exists(CONFIG_FILE):
        config  = load_config()
        current = config.get("modules", [])
        print(f"[Anvil] Modules in '{config['project']}':")
        if current:
            for m in current:
                meta, _, _ = load_module_meta(m)
                print(f"  + {m:<25} {meta.get('description', '')}")
        else:
            print("  (none)")
        print()

    print("[Anvil] Available modules:")
    for name, info in registry.items():
        ver        = info.get("latest", "?")
        key        = f"{name}@{ver}"
        meta, mod_dir, _ = load_module_meta(key)
        is_soc     = "[SOC]" if os.path.exists(os.path.join(mod_dir, "soc.json")) else "     "
        in_proj    = "+" if os.path.exists(CONFIG_FILE) and key in load_config().get("modules", []) else " "
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

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[ERROR] Firmware compilation failed")
        sys.exit(1)

    subprocess.run([cpu["objcopy"], "-O", "verilog", elf_out, mem_out], check=True)

    ram_addr_bits = params.get("ram_addr_bits", 11)
    depth = 1 << ram_addr_bits
    subprocess.run([
        "python3", PROGRAMATOR,
        mem_out,
        "--module-name", "ram",
        "--depth", str(depth),
        "-o", ram_out
    ], check=True)

    print(f"[Anvil] Firmware -> {ram_out}")

def cmd_synth(args):
    """Synthesize FPGA bitstream."""
    config = load_config()
    target = config["target"]

    build_makefile(config)

    print(f"[Anvil] Synthesizing for {config['board']}...")
    if config.get("modules"):
        print(f"[Anvil] Modules: {', '.join(config['modules'])}")

    t0 = time.time()
    conda_run(f"cd {os.getcwd()} && TARGET={target} make")
    elapsed = time.time() - t0

    bit = find_bitstream(target)
    if bit:
        print(f"[Anvil] Done in {elapsed:.1f}s -- {bit}")
    else:
        print("[ERROR] No .bit file produced")
        sys.exit(1)

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
    run(f"sudo {OPENFPGALOADER} -b {ofl_board} {bit}")
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
        print("[TEST] Compile error:")
        print(result.stderr)
        sys.exit(1)

    print(f"[TEST] Running...")
    result = subprocess.run(f"vvp {out}", shell=True, text=True)
    if result.returncode != 0:
        sys.exit(1)

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
    mods = config.get("modules", [])

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

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        usage()
        sys.exit(0)
    cmd = args[0]
    if cmd not in COMMANDS:
        print(f"[ERROR] Unknown command: {cmd}")
        usage()
        sys.exit(1)
    COMMANDS[cmd][0](args[1:])

if __name__ == "__main__":
    main()
