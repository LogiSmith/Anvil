"""Module sourcing for Anvil: expression evaluation, archives, hashing."""

import ast
import hashlib
import http.client
import json
import operator
import os
import re
import stat
import tarfile
import urllib.error
import urllib.parse
import urllib.request
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
    real = os.path.realpath(target)  # a path component may itself be a pre-existing symlink
    if real != dest and not real.startswith(dest + os.sep):
        raise UnsafeArchive(f"path escapes the destination through a symlink: {name}")
    return target  # not race-free: a symlink planted after this check still wins

def _check_budget(count, total):
    if count > MAX_ENTRIES:
        raise UnsafeArchive(f"archive has more than {MAX_ENTRIES} entries")
    if total > MAX_BYTES:
        raise UnsafeArchive(f"archive unpacks to more than {MAX_BYTES // (1024 * 1024)} MB")

def _extract_tar(path, dest):
    with tarfile.open(path) as tar:
        members, count, total = [], 0, 0
        m = tar.next()
        while m is not None:                   # not getmembers(): budget must reject before
            if m.issym() or m.islnk():          # the next() that skips (decompresses) a body
                raise UnsafeArchive(f"archive contains a link: {m.name}")
            if not (m.isreg() or m.isdir()):
                raise UnsafeArchive(f"archive contains a special file: {m.name}")
            _checked_target(m.name, dest)
            count += 1
            total += m.size
            _check_budget(count, total)
            members.append(m)
            m = tar.next()
        tar.extractall(dest, members=members, filter="data")

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
    """Unpack `archive_path` into `dest`; every member is validated before anything is written."""
    # zipfile has no safety filter at all, and tarfile's isn't the default until Python 3.14
    dest = os.path.realpath(dest)
    if tarfile.is_tarfile(archive_path):
        _extract_tar(archive_path, dest)
    elif zipfile.is_zipfile(archive_path):
        _extract_zip(archive_path, dest)
    else:
        raise UnsafeArchive(f"not a tar or zip archive: {archive_path}")

ARCHIVE_EXTS  = (".tar.gz", ".zip", ".tgz", ".tar.xz")
ARCHIVE_TYPES = ("application/gzip", "application/x-gzip", "application/zip",
                 "application/x-tar", "application/x-gtar")
ARCHIVE_MAGIC = (b"\x1f\x8b", b"PK\x03\x04", b"\xfd7zXZ\x00")
USER_AGENT    = "anvil"

class NoArchiveFound(Exception):
    """No candidate URL for a ref turned out to be an archive."""

def classify(ref):
    """A ref is a local path, a URL, or a bundled module name -- decided before any network call."""
    if ref.startswith(("./", "../", "/", "~")):
        return "path"
    if ref.startswith(("http://", "https://")):
        return "url"
    if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}/", ref):   # host.tld/...
        return "url"
    return "name"

def split_ref(ref):
    """Split `base@ref#subpath`; an `@` after the last `/` is a ref, not URL userinfo."""
    base, sub = (ref.split("#", 1) + [None])[:2]
    at = base.rfind("@")
    slash = base.rfind("/")
    if at > slash:
        return base[:at], base[at + 1:], sub
    return base, None, sub

def _as_url(base):
    """A bare `host/path` ref means https -- Anvil never silently downgrades it."""
    return base if base.startswith(("http://", "https://")) else "https://" + base

def _forge_candidates(url, at):
    """The archive URLs a forge Anvil knows serves for `at`; empty for every other host."""
    out = []
    def add(u):
        if u not in out:
            out.append(u)

    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?"
                 r"(?:/releases/tag/(.+))?/?$", url)
    if m:
        owner, repo, tag = m.group(1), m.group(2), m.group(3) or at
        if tag:
            add(f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.tar.gz")
            add(f"https://github.com/{owner}/{repo}/archive/refs/heads/{tag}.tar.gz")
            add(f"https://github.com/{owner}/{repo}/archive/{tag}.tar.gz")

    m = re.match(r"^https?://gitlab\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if m and at:
        owner, repo = m.group(1), m.group(2)
        add(f"https://gitlab.com/{owner}/{repo}/-/archive/{at}/{repo}-{at}.tar.gz")

    return out

def _unknown_forge_host(ref):
    """The host of `ref` when its `@ref` shorthand reached no forge Anvil knows, else None."""
    base, at, _ = split_ref(ref)
    url = _as_url(base)
    if at is None or _forge_candidates(url, at):
        return None
    return urllib.parse.urlsplit(url).netloc

def archive_candidates(ref):
    """Every URL to try for `ref`, in order: as given, extension probes, then forge rewrites."""
    base, at, _ = split_ref(ref)
    url = _as_url(base)

    out = []
    def add(u):
        if u not in out:
            out.append(u)

    def add_with_probes(u):
        add(u)
        if not u.endswith(ARCHIVE_EXTS):
            for ext in ARCHIVE_EXTS:
                add(u + ext)

    if at is None:
        add_with_probes(url)
    for u in _forge_candidates(url, at):
        add(u)
    if not out:
        # an unknown host either bakes the `@ref` into a filename or is a typo -- both beat trying nothing
        add_with_probes(f"{url}@{at}")
    return out

def _looks_like_archive(url, ctype, head):
    """Content-Type picks the candidate; the first bytes confirm it either way."""
    if head.startswith(ARCHIVE_MAGIC):
        return True
    if ctype.split(";")[0].strip().lower() in ARCHIVE_TYPES:
        return True
    return url.endswith(ARCHIVE_EXTS)

def _content_length(headers):
    """The advertised body size, or None if absent, not a plain integer, or negative."""
    try:
        n = int(headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None

def download_archive(ref, dest_dir):
    """Fetch the first real archive among `archive_candidates(ref)`. Returns (path, resolved_url)."""
    os.makedirs(dest_dir, exist_ok=True)
    out = os.path.join(dest_dir, "archive")
    tmp = out + ".part"
    tried = []
    for url in archive_candidates(ref):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                ctype = r.headers.get("Content-Type", "")
                # a stray Content-Length alongside chunked framing is out of spec but real;
                # http.client already discards it (HTTPResponse.length is None when chunked)
                length = None if getattr(r, "chunked", False) else _content_length(r.headers)
                head = r.read(6)
                if not _looks_like_archive(url, ctype, head):
                    tried.append(f"{url} -> {ctype or 'unknown type'}, not an archive")
                    continue
                written = len(head)
                with open(tmp, "wb") as f:
                    f.write(head)
                    while True:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                # a clean close short of Content-Length raises nothing -- count it ourselves
                if length is not None and written != length:
                    tried.append(f"{url} -> got {written} of {length} bytes, connection closed early")
                    continue
                os.replace(tmp, out)  # atomic: a reader sees a complete archive or none
                return out, url  # resolved, not the shorthand: it's the only part a user can judge
        # what a failed candidate looks like -- a local filesystem error is fatal, not this
        except (urllib.error.HTTPError, urllib.error.URLError,
                ConnectionError, TimeoutError, http.client.HTTPException) as e:
            if isinstance(e, urllib.error.HTTPError):
                tried.append(f"{url} -> HTTP {e.code}")
                e.close()
            elif isinstance(e, urllib.error.URLError):
                tried.append(f"{url} -> {e.reason}")
            else:
                tried.append(f"{url} -> {e}")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    msg = "no archive found for " + ref + "\n    tried:\n      " + "\n      ".join(tried)
    host = _unknown_forge_host(ref)
    if host:
        msg += f"\n    no forge rewrite matched this {host} URL -- a direct archive URL always works"
    raise NoArchiveFound(msg)

class InvalidModule(Exception):
    """An unpacked archive is not a usable Anvil module."""

def find_module_root(unpacked, subpath=None):
    """Where module.json lives: the archive root, or the single directory a forge wraps it in."""
    if os.path.isfile(os.path.join(unpacked, "module.json")):
        base = unpacked
    else:
        entries = [e for e in os.listdir(unpacked)
                   if os.path.isdir(os.path.join(unpacked, e))]
        if len(entries) != 1:
            raise InvalidModule(
                "unexpected archive structure: expected module.json at the root or in a "
                f"single directory, found {len(entries)} directories")
        base = os.path.join(unpacked, entries[0])
        if not subpath and not os.path.isfile(os.path.join(base, "module.json")):
            raise InvalidModule(f"no module.json in '{entries[0]}'")

    if not subpath:
        return base

    # subpath is user input applied after the wrapper is stripped -- ".." must not reach outside
    root = os.path.normpath(os.path.join(base, subpath))
    if root != unpacked and not root.startswith(unpacked + os.sep):
        raise InvalidModule(f"subpath escapes the archive: {subpath}")
    if not os.path.isfile(os.path.join(root, "module.json")):
        raise InvalidModule(f"no module.json at subpath '{subpath}'")
    return root

def validate_module(mod_dir):
    """Parsed module.json if `mod_dir` is a usable module, else raise explaining what is missing."""
    meta_path = os.path.join(mod_dir, "module.json")
    if not os.path.isfile(meta_path):
        raise InvalidModule("no module.json")
    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except json.JSONDecodeError as e:
        raise InvalidModule(f"module.json is not valid JSON: {e}")
    if not isinstance(meta, dict):
        raise InvalidModule("module.json must be a JSON object")
    for field in ("name", "version"):
        if not meta.get(field):
            raise InvalidModule(f"module.json has no '{field}'")
    if not any(n.endswith((".v", ".sv"))
               for _, _, names in os.walk(mod_dir) for n in names):
        raise InvalidModule("no .v or .sv files in the module")
    soc = os.path.join(mod_dir, "soc.json")
    if os.path.isfile(soc):
        try:
            with open(soc) as f:
                json.load(f)
        except json.JSONDecodeError as e:
            raise InvalidModule(f"soc.json is not valid JSON: {e}")
    return meta
