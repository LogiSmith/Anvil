"""Module sourcing for Anvil: expression evaluation, archives, hashing."""

import ast
import hashlib
import operator
import os
import stat
import tarfile
import zipfile

MAX_SHIFT = 64   # a shift past this is a memory bomb, not a build parameter
MAX_NODES = 200  # bounds recursion before it happens -- the interpreter's own limit varies

# ast.Pow ("**") is intentionally absent -- it turns a one-line value into a memory bomb
_BINOPS = {
    ast.Add: operator.add,   ast.Sub: operator.sub,   ast.Mult: operator.mul,
    ast.Div: operator.floordiv, ast.FloorDiv: operator.floordiv,  # results must stay integer
    ast.Mod: operator.mod,   ast.LShift: operator.lshift,
    ast.RShift: operator.rshift, ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_, ast.BitXor: operator.xor,
}
_UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert}

def eval_arith(expr, names):
    """Evaluate an integer arithmetic expression over `names`, raising ValueError otherwise."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"not a valid expression: {e.msg}")

    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_NODES:
        raise ValueError(f"expression is too large ({node_count} nodes, max {MAX_NODES})")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int):
                raise ValueError(f"only whole numbers are allowed, got {node.value!r}")
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"unknown name {node.id!r}")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, (ast.LShift, ast.RShift)) and right > MAX_SHIFT:
                raise ValueError(f"shift of {right} is too large (max {MAX_SHIFT})")
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise ValueError("division by zero")
            return _BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
            return _UNOPS[type(node.op)](walk(node.operand))
        raise ValueError(f"{type(node).__name__} is not allowed in an expression")

    return walk(tree)

HASHED_SUFFIXES = (".v", ".sv")
HASHED_NAMES = ("module.json", "soc.json")
SKIP_DIRS = {"build", ".git", "__pycache__"}

def hashed_files(mod_dir):
    # Paths Anvil actually reads, relative to `mod_dir`, sorted.
    found = []
    for root, dirs, names in os.walk(mod_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in names:
            if n.endswith(HASHED_SUFFIXES) or n in HASHED_NAMES:
                rel = os.path.relpath(os.path.join(root, n), mod_dir)
                found.append(rel.replace(os.sep, "/"))
    return sorted(found)

def module_hash(mod_dir):
    # SHA-256 over the module's source, independent of where it sits.
    # Each file hashes to a fixed 32-byte digest; no ambiguity between file content and separators.
    h = hashlib.sha256()
    for rel in hashed_files(mod_dir):
        h.update(hashlib.sha256(rel.encode("utf-8")).digest())
        file_hash = hashlib.sha256()
        with open(os.path.join(mod_dir, rel), "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                file_hash.update(chunk)
        h.update(file_hash.digest())
    return "sha256:" + h.hexdigest()

MAX_ENTRIES = 5000
MAX_BYTES = 200 * 1024 * 1024

class UnsafeArchive(Exception):
    """An archive member would write outside the destination, or the archive is too large."""

def _checked_target(name, dest):
    """Resolve an archive member's path under `dest`, or raise."""
    if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
        raise UnsafeArchive(f"absolute path in archive: {name}")
    target = os.path.normpath(os.path.join(dest, name))
    if target != dest and not target.startswith(dest + os.sep):
        raise UnsafeArchive(f"path escapes the destination: {name}")
    return target

def _check_budget(count, total):
    if count > MAX_ENTRIES:
        raise UnsafeArchive(f"archive has more than {MAX_ENTRIES} entries")
    if total > MAX_BYTES:
        raise UnsafeArchive(f"archive unpacks to more than {MAX_BYTES // (1024 * 1024)} MB")

def _extract_tar(path, dest):
    with tarfile.open(path) as tar:
        members, count, total = [], 0, 0
        for m in tar.getmembers():
            if m.issym() or m.islnk():         # a link can point anywhere; forbid both kinds
                raise UnsafeArchive(f"archive contains a link: {m.name}")
            if not (m.isreg() or m.isdir()):
                raise UnsafeArchive(f"archive contains a special file: {m.name}")
            _checked_target(m.name, dest)
            count += 1
            total += m.size
            _check_budget(count, total)
            members.append(m)
        try:
            tar.extractall(dest, members=members, filter="data")
        except TypeError:  # filter= predates Anvil's Python floor on some builds
            tar.extractall(dest, members=members)

def _extract_zip(path, dest):
    with zipfile.ZipFile(path) as zf:
        infos, count, total = [], 0, 0
        for zi in zf.infolist():
            mode = zi.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise UnsafeArchive(f"archive contains a link: {zi.filename}")
            _checked_target(zi.filename, dest)
            count += 1
            total += zi.file_size
            _check_budget(count, total)
            infos.append(zi)
        zf.extractall(dest, members=infos)

def extract(archive_path, dest):
    """Unpack `archive_path` into `dest`; every member is validated before anything is written.

    zipfile has no safety filter at all, and tarfile's is not the default on Python 3.12,
    so neither library's own protection is relied on.
    """
    dest = os.path.abspath(dest)
    if tarfile.is_tarfile(archive_path):
        _extract_tar(archive_path, dest)
    elif zipfile.is_zipfile(archive_path):
        _extract_zip(archive_path, dest)
    else:
        raise UnsafeArchive(f"not a tar or zip archive: {archive_path}")
