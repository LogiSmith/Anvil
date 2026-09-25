import contextlib, functools, http.server, io, json, os, socket
import shutil, socketserver, struct, subprocess, sys, tarfile, tempfile, threading, unittest, zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch                                                    # noqa: E402
import anvil                                                    # noqa: E402

@contextlib.contextmanager
def capture():
    """Collect stdout as a string -- the stand-in for pytest's capsys."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf

@contextlib.contextmanager
def serve(directory):
    """A throwaway HTTP server over `directory`, yielding its base URL."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=directory)
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()

class TempCase(unittest.TestCase):
    """A TestCase with a scratch directory and a restored working directory."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEvalArith(unittest.TestCase):
    def test_evaluates_the_real_defsym(self):
        self.assertEqual(
            fetch.eval_arith("1 << (ram_addr_bits + 2)", {"ram_addr_bits": 11}),
            8192,
        )

    def test_all_allowed_operators(self):
        n = {"a": 12, "b": 5}
        self.assertEqual(fetch.eval_arith("a + b - 2", n), 15)
        self.assertEqual(fetch.eval_arith("a * b // 2", n), 30)
        self.assertEqual(fetch.eval_arith("a % b", n), 2)
        self.assertEqual(fetch.eval_arith("a >> 1", n), 6)
        self.assertEqual(fetch.eval_arith("a & b | 3", n), 7)
        self.assertEqual(fetch.eval_arith("a ^ b", n), 9)
        self.assertEqual(fetch.eval_arith("-a", n), -12)
        self.assertEqual(fetch.eval_arith("~a", n), -13)

    def test_rejects_the_popen_escape(self):
        evil = ("[c for c in ().__class__.__bases__[0].__subclasses__() "
                "if c.__name__=='Popen'][0](['sh','-c','touch /tmp/anvil_pwned'])")
        with self.assertRaises(ValueError):
            fetch.eval_arith(evil, {})
        self.assertFalse(os.path.exists("/tmp/anvil_pwned"))

    def test_rejects_attribute_access_and_calls(self):
        for expr in ["().__class__", "len('x')", "__import__('os')", "a.b", "[1,2]", "'s'"]:
            with self.assertRaises(ValueError):
                fetch.eval_arith(expr, {"a": 1})

    def test_rejects_unknown_name(self):
        with self.assertRaises(ValueError) as ctx:
            fetch.eval_arith("nope + 1", {"a": 1})
        self.assertIn("nope", str(ctx.exception))

    def test_rejects_resource_exhaustion(self):
        # 1 << 10**9 would allocate gigabytes before anyone notices
        with self.assertRaises(ValueError):
            fetch.eval_arith("1 << 1000000000", {})
        with self.assertRaises(ValueError):
            fetch.eval_arith("2 ** 64", {})       # ** is not in the allowed set at all

    def test_rejects_long_operator_chain(self):
        # a flat chain nests one BinOp per term -- unbounded, this overflows the stack
        with self.assertRaises(ValueError):
            fetch.eval_arith("+".join(["1"] * 1000), {})


class TestEvalDefsyms(TempCase):
    """eval_defsyms reports a bad expression via fail() and exits, never raises."""

    def test_bad_expression_reports_via_fail_and_exits(self):
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.eval_defsyms({"bad": "nope + 1"}, {})
        text = out.getvalue()
        self.assertIn("bad", text)
        self.assertIn("nope + 1", text)
        self.assertIn("nope", text)

    def test_long_operator_chain_reports_via_fail_and_exits(self):
        expr = "+".join(["1"] * 1000)
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.eval_defsyms({"huge": expr}, {})
        text = out.getvalue()
        self.assertIn("huge", text)
        self.assertIn("too large", text)


def _make_module(tmp_path, rtl="assign x = 1;\n", extra=None):
    d = os.path.join(tmp_path, "m")
    os.makedirs(os.path.join(d, "sub"), exist_ok=True)
    with open(os.path.join(d, "module.json"), "w") as f:
        f.write('{"name":"m","version":"1.0.0"}')
    with open(os.path.join(d, "top.v"), "w") as f:
        f.write(rtl)
    with open(os.path.join(d, "sub", "helper.sv"), "w") as f:
        f.write("module helper; endmodule\n")
    for name, body in (extra or {}).items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)
    return d


class TestModuleHash(TempCase):
    def test_hash_is_stable(self):
        d = _make_module(self.tmp)
        self.assertEqual(fetch.module_hash(d), fetch.module_hash(d))
        self.assertTrue(fetch.module_hash(d).startswith("sha256:"))

    def test_hash_ignores_files_anvil_does_not_read(self):
        d = _make_module(self.tmp)
        before = fetch.module_hash(d)
        for junk in ["README.md", "notes.log", "sim.vcd"]:
            p = os.path.join(d, junk)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("noise")
        self.assertEqual(fetch.module_hash(d), before)

    def test_skip_dirs_filters_rtl_in_build(self):
        d = _make_module(self.tmp)
        before = fetch.module_hash(d)
        p = os.path.join(d, "build", "synth.v")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("module synth; endmodule\n")
        self.assertEqual(fetch.module_hash(d), before)

    def test_skip_dirs_filters_rtl_in_git(self):
        d = _make_module(self.tmp)
        before = fetch.module_hash(d)
        p = os.path.join(d, ".git", "object.v")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("module object; endmodule\n")
        self.assertEqual(fetch.module_hash(d), before)

    def test_hash_changes_when_rtl_changes(self):
        a = _make_module(os.path.join(self.tmp, "a"))
        b = _make_module(os.path.join(self.tmp, "b"), rtl="assign x = 0;\n")
        self.assertNotEqual(fetch.module_hash(a), fetch.module_hash(b))

    def test_hash_changes_when_module_json_changes(self):
        d = _make_module(self.tmp)
        before = fetch.module_hash(d)
        with open(os.path.join(d, "module.json"), "w") as f:
            f.write('{"name":"m","version":"2.0.0"}')
        self.assertNotEqual(fetch.module_hash(d), before)

    def test_hash_independent_of_location(self):
        a = _make_module(os.path.join(self.tmp, "one"))
        b = _make_module(os.path.join(self.tmp, "two"))
        self.assertEqual(fetch.module_hash(a), fetch.module_hash(b))

    def test_hash_is_not_ambiguous_to_content(self):
        # Fixed-width digest encoding prevents NUL bytes in content from being mistaken for separators.
        a_dir = os.path.join(self.tmp, "a")
        b_dir = os.path.join(self.tmp, "b")
        os.makedirs(a_dir)
        os.makedirs(b_dir)
        with open(os.path.join(a_dir, "module.json"), "w") as f:
            f.write('{"name":"a","version":"1.0.0"}')
        with open(os.path.join(b_dir, "module.json"), "w") as f:
            f.write('{"name":"b","version":"1.0.0"}')
        with open(os.path.join(a_dir, "a.v"), "wb") as f:
            f.write(b'b\x00c.v\x00d')
        with open(os.path.join(b_dir, "a.v"), "wb") as f:
            f.write(b'b')
        with open(os.path.join(b_dir, "c.v"), "wb") as f:
            f.write(b'd')
        self.assertNotEqual(fetch.module_hash(a_dir), fetch.module_hash(b_dir))


def _tar_with(tmp_path, entries, name="a.tar.gz"):
    p = os.path.join(tmp_path, name)
    with tarfile.open(p, "w:gz") as t:
        for member_name, body in entries:
            info = tarfile.TarInfo(member_name)
            info.size = len(body)
            t.addfile(info, io.BytesIO(body))
    return p


class TestExtract(TempCase):
    def test_extracts_a_normal_tar(self):
        p = _tar_with(self.tmp, [("m/module.json", b"{}"), ("m/top.v", b"x")])
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        fetch.extract(p, dest)
        self.assertTrue(os.path.isfile(os.path.join(dest, "m", "top.v")))

    def test_rejects_parent_traversal(self):
        p = _tar_with(self.tmp, [("../escaped.txt", b"nope")])
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        with self.assertRaises(fetch.UnsafeArchive):
            fetch.extract(p, dest)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escaped.txt")))

    def test_rejects_absolute_path(self):
        p = _tar_with(self.tmp, [("/tmp/anvil_abs.txt", b"nope")])
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        with self.assertRaises(fetch.UnsafeArchive):
            fetch.extract(p, dest)

    def test_rejects_symlink(self):
        p = os.path.join(self.tmp, "s.tar.gz")
        with tarfile.open(p, "w:gz") as t:
            link = tarfile.TarInfo("evil")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            t.addfile(link)
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        with self.assertRaises(fetch.UnsafeArchive):
            fetch.extract(p, dest)

    def test_rejects_too_many_entries(self):
        p = _tar_with(self.tmp, [(f"m/f{i}.v", b"x") for i in range(fetch.MAX_ENTRIES + 1)])
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        with self.assertRaises(fetch.UnsafeArchive):
            fetch.extract(p, dest)

    def test_rejects_decompression_bomb(self):
        p = _tar_with(self.tmp, [("m/big.v", b"\0" * (fetch.MAX_BYTES + 1))])
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        with self.assertRaises(fetch.UnsafeArchive):
            fetch.extract(p, dest)

    def test_extracts_a_normal_zip(self):
        p = os.path.join(self.tmp, "a.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("m/module.json", "{}")
            z.writestr("m/top.v", "x")
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        fetch.extract(p, dest)
        self.assertTrue(os.path.isfile(os.path.join(dest, "m", "top.v")))

    def test_zip_traversal_rejected(self):
        p = os.path.join(self.tmp, "evil.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("../escaped.txt", "nope")
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        with self.assertRaises(fetch.UnsafeArchive):
            fetch.extract(p, dest)

    def test_rejects_zip_escape_via_preexisting_symlink(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        os.symlink(outside, os.path.join(dest, "link"), target_is_directory=True)
        p = os.path.join(self.tmp, "evil.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("link/evil.txt", "nope")
        with self.assertRaises(fetch.UnsafeArchive):
            fetch.extract(p, dest)
        self.assertFalse(os.path.exists(os.path.join(outside, "evil.txt")))

    def test_rejects_tar_escape_via_preexisting_symlink(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        os.symlink(outside, os.path.join(dest, "link"), target_is_directory=True)
        p = _tar_with(self.tmp, [("link/evil.txt", b"nope")], name="evil.tar.gz")
        with self.assertRaises(fetch.UnsafeArchive):
            fetch.extract(p, dest)
        self.assertFalse(os.path.exists(os.path.join(outside, "evil.txt")))


class TestClassify(unittest.TestCase):
    def test_bundled_name(self):
        self.assertEqual(fetch.classify("uart"), "name")

    def test_bundled_name_with_version_is_not_mistaken_for_a_url(self):
        self.assertEqual(fetch.classify("uart@1.0.0"), "name")

    def test_local_paths(self):
        self.assertEqual(fetch.classify("../moj-modul"), "path")
        self.assertEqual(fetch.classify("./m"), "path")
        self.assertEqual(fetch.classify("/home/ana/rtl/fifo"), "path")

    def test_forge_shorthand_is_a_url(self):
        self.assertEqual(fetch.classify("github.com/ana/fifo@v1.2.0"), "url")

    def test_monorepo_shorthand_with_subpath_is_a_url(self):
        self.assertEqual(fetch.classify("github.com/ana/rtl@v1#modules/fifo"), "url")

    def test_direct_archive_url(self):
        self.assertEqual(fetch.classify("https://x.si/f.tar.gz"), "url")


class TestSplitRef(unittest.TestCase):
    def test_ref_and_subpath(self):
        self.assertEqual(
            fetch.split_ref("github.com/ana/rtl@v1.2.0#modules/fifo"),
            ("github.com/ana/rtl", "v1.2.0", "modules/fifo"),
        )

    def test_plain_url_has_no_ref_or_subpath(self):
        self.assertEqual(fetch.split_ref("https://x.si/f.tar.gz"),
                          ("https://x.si/f.tar.gz", None, None))

    def test_ref_without_subpath(self):
        self.assertEqual(fetch.split_ref("github.com/ana/fifo@v1"),
                          ("github.com/ana/fifo", "v1", None))

    def test_subpath_with_nested_slashes(self):
        self.assertEqual(
            fetch.split_ref("github.com/ana/rtl@v1#a/b/c"),
            ("github.com/ana/rtl", "v1", "a/b/c"),
        )

    def test_url_with_port_has_no_ref(self):
        self.assertEqual(fetch.split_ref("https://x.si:8443/f.tar.gz"),
                          ("https://x.si:8443/f.tar.gz", None, None))


class TestArchiveCandidates(unittest.TestCase):
    def test_probes_extensions(self):
        c = fetch.archive_candidates("https://x.si/rtl/v1")
        self.assertEqual(c[0], "https://x.si/rtl/v1")
        self.assertIn("https://x.si/rtl/v1.tar.gz", c)
        self.assertIn("https://x.si/rtl/v1.zip", c)

    def test_rewrites_github_release_page(self):
        c = fetch.archive_candidates("https://github.com/LogiSmith/Anvil/releases/tag/1.1.5")
        self.assertIn("https://github.com/LogiSmith/Anvil/archive/refs/tags/1.1.5.tar.gz", c)

    def test_rewrites_github_at_ref(self):
        c = fetch.archive_candidates("github.com/ana/fifo@v1.2.0")
        self.assertIn("https://github.com/ana/fifo/archive/refs/tags/v1.2.0.tar.gz", c)

    def test_rewrites_gitlab_at_ref(self):
        c = fetch.archive_candidates("gitlab.com/ana/fifo@v1.2.0")
        self.assertIn("https://gitlab.com/ana/fifo/-/archive/v1.2.0/fifo-v1.2.0.tar.gz", c)


class TestLooksLikeArchive(unittest.TestCase):
    def test_trusts_gzip_magic_over_a_wrong_content_type(self):
        self.assertTrue(fetch._looks_like_archive(
            "https://x.si/m", "application/octet-stream", b"\x1f\x8b\x08\x00\x00\x00"))

    def test_trusts_xz_magic_bytes(self):
        self.assertTrue(fetch._looks_like_archive(
            "https://x.si/m", "application/octet-stream", b"\xfd7zXZ\x00"))

    def test_rejects_html_mislabeled_as_octet_stream(self):
        self.assertFalse(fetch._looks_like_archive(
            "https://x.si/m", "application/octet-stream", b"<html>"))

    def test_content_type_alone_is_enough(self):
        self.assertTrue(fetch._looks_like_archive("https://x.si/m", "application/zip", b"???\x00"))


class TestDownloadArchive(TempCase):
    def test_downloads_from_a_local_server(self):
        archive = os.path.join(self.tmp, "m.tar.gz")
        with tarfile.open(archive, "w:gz") as t:
            data = b'{"name":"m","version":"1.0.0"}'
            info = tarfile.TarInfo("m/module.json")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        with serve(self.tmp) as base_url:
            got, resolved = fetch.download_archive(f"{base_url}/m.tar.gz",
                                                     os.path.join(self.tmp, "dl"))
            self.assertTrue(os.path.isfile(got))
            self.assertEqual(resolved, f"{base_url}/m.tar.gz")

    def test_probes_extensions_before_succeeding(self):
        archive = os.path.join(self.tmp, "mod.tar.gz")
        with tarfile.open(archive, "w:gz") as t:
            data = b"x"
            info = tarfile.TarInfo("m/top.v")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        with serve(self.tmp) as base_url:
            got, resolved = fetch.download_archive(f"{base_url}/mod",
                                                     os.path.join(self.tmp, "dl"))
            self.assertTrue(os.path.isfile(got))
            self.assertEqual(resolved, f"{base_url}/mod.tar.gz")

    def test_accepts_a_tarball_the_server_mislabels(self):
        # extension-less file: SimpleHTTPRequestHandler serves it as octet-stream
        archive = os.path.join(self.tmp, "blob")
        with tarfile.open(archive, "w:gz") as t:
            data = b"x"
            info = tarfile.TarInfo("m/top.v")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        with serve(self.tmp) as base_url:
            got, resolved = fetch.download_archive(f"{base_url}/blob",
                                                     os.path.join(self.tmp, "dl"))
            self.assertTrue(os.path.isfile(got))
            self.assertEqual(resolved, f"{base_url}/blob")

    def test_rejects_an_html_page_served_as_octet_stream(self):
        with open(os.path.join(self.tmp, "ghost"), "w") as f:
            f.write("<html><body>not found</body></html>")
        with serve(self.tmp) as base_url:
            with self.assertRaises(fetch.NoArchiveFound) as ctx:
                fetch.download_archive(f"{base_url}/ghost", os.path.join(self.tmp, "dl"))
            self.assertIn("not an archive", str(ctx.exception))

    def test_reports_everything_it_tried(self):
        with serve(self.tmp) as base_url:
            with self.assertRaises(fetch.NoArchiveFound) as ctx:
                fetch.download_archive(f"{base_url}/ghost", os.path.join(self.tmp, "dl"))
            msg = str(ctx.exception)
            self.assertIn("/ghost", msg)
            self.assertIn(".tar.gz", msg)
            self.assertIn(".zip", msg)


class _TruncatingHandler(socketserver.BaseRequestHandler):
    """Claims a Content-Length it never delivers, then forces a TCP reset on close."""
    def handle(self):
        self.request.recv(4096)
        head = (b"HTTP/1.1 200 OK\r\nContent-Type: application/gzip\r\n"
                b"Content-Length: 1000000\r\n\r\n")
        self.request.sendall(head + b"\x1f\x8b\x08\x00\x00\x00")
        self.request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.request.close()


class TestDownloadArchiveDroppedConnection(TempCase):
    def test_dropped_connection_is_a_failed_candidate_not_a_crash(self):
        srv = socketserver.TCPServer(("127.0.0.1", 0), _TruncatingHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base_url = f"http://127.0.0.1:{srv.server_address[1]}"
            dest = os.path.join(self.tmp, "dl")
            with self.assertRaises(fetch.NoArchiveFound) as ctx:
                fetch.download_archive(f"{base_url}/m.tar.gz", dest)
            self.assertIn(base_url, str(ctx.exception))
            self.assertFalse(os.path.exists(os.path.join(dest, "archive")))
        finally:
            srv.shutdown()
            srv.server_close()


class TestDownloadArchivePermissions(TempCase):
    @unittest.skipIf(os.geteuid() == 0, "root ignores file mode bits")
    def test_unwritable_dest_dir_is_fatal_not_a_failed_candidate(self):
        archive = os.path.join(self.tmp, "m.tar.gz")
        with tarfile.open(archive, "w:gz") as t:
            data = b"x"
            info = tarfile.TarInfo("m/top.v")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        dest = os.path.join(self.tmp, "dl")
        os.makedirs(dest)
        os.chmod(dest, 0o500)
        try:
            with serve(self.tmp) as base_url:
                with self.assertRaises(OSError) as ctx:
                    fetch.download_archive(f"{base_url}/m.tar.gz", dest)
                self.assertIsInstance(ctx.exception, PermissionError)
        finally:
            os.chmod(dest, 0o700)


class _ShortBodyHandler(socketserver.BaseRequestHandler):
    """Claims a Content-Length it never delivers, then closes cleanly -- no reset, no exception."""
    def handle(self):
        self.request.recv(4096)
        head = (b"HTTP/1.1 200 OK\r\nContent-Type: application/gzip\r\n"
                b"Content-Length: 1000000\r\n\r\n")
        self.request.sendall(head + b"\x1f\x8b\x08\x00\x00\x00")
        self.request.close()


class TestDownloadArchiveTruncatedBody(TempCase):
    def test_short_body_is_a_failed_candidate_not_a_success(self):
        srv = socketserver.TCPServer(("127.0.0.1", 0), _ShortBodyHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base_url = f"http://127.0.0.1:{srv.server_address[1]}"
            dest = os.path.join(self.tmp, "dl")
            with self.assertRaises(fetch.NoArchiveFound) as ctx:
                fetch.download_archive(f"{base_url}/m.tar.gz", dest)
            self.assertIn(base_url, str(ctx.exception))
            self.assertFalse(os.path.exists(os.path.join(dest, "archive")))
        finally:
            srv.shutdown()
            srv.server_close()


class TestContentLength(unittest.TestCase):
    def test_absent_header_is_none(self):
        self.assertIsNone(fetch._content_length({}))

    def test_non_numeric_header_is_none(self):
        self.assertIsNone(fetch._content_length({"Content-Length": "abc"}))

    def test_negative_header_is_none(self):
        self.assertIsNone(fetch._content_length({"Content-Length": "-5"}))

    def test_valid_header_is_parsed(self):
        self.assertEqual(fetch._content_length({"Content-Length": "42"}), 42)


class _ChunkedHandler(socketserver.BaseRequestHandler):
    """Chunked framing plus a stray, wrong Content-Length -- chunked framing must win."""
    payload = b""  # set by the test before the server starts

    def handle(self):
        self.request.recv(4096)
        body = self.payload
        mid = len(body) // 2
        chunked = b"".join(f"{len(part):x}\r\n".encode() + part + b"\r\n"
                            for part in (body[:mid], body[mid:]) if part)
        chunked += b"0\r\n\r\n"
        header = (b"HTTP/1.1 200 OK\r\nContent-Type: application/gzip\r\n"
                  b"Content-Length: 1114\r\nTransfer-Encoding: chunked\r\n\r\n")
        self.request.sendall(header + chunked)
        self.request.close()


class TestDownloadArchiveChunkedWithStrayContentLength(TempCase):
    def test_chunked_body_succeeds_despite_a_stray_content_length(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            data = b"x"
            info = tarfile.TarInfo("m/top.v")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        payload = buf.getvalue()
        _ChunkedHandler.payload = payload
        srv = socketserver.TCPServer(("127.0.0.1", 0), _ChunkedHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base_url = f"http://127.0.0.1:{srv.server_address[1]}"
            dest = os.path.join(self.tmp, "dl")
            got, resolved = fetch.download_archive(f"{base_url}/m.tar.gz", dest)
            with open(got, "rb") as f:
                self.assertEqual(f.read(), payload)
            self.assertEqual(resolved, f"{base_url}/m.tar.gz")
        finally:
            srv.shutdown()
            srv.server_close()


class TestFindModuleRoot(TempCase):
    def test_at_archive_root(self):
        d = os.path.join(self.tmp, "u")
        os.makedirs(d)
        with open(os.path.join(d, "module.json"), "w") as f:
            f.write('{"name":"m","version":"1.0.0"}')
        self.assertEqual(fetch.find_module_root(d, None), d)

    def test_in_single_subdir(self):
        d = os.path.join(self.tmp, "u")
        inner = os.path.join(d, "repo-1.2.0")
        os.makedirs(inner)
        with open(os.path.join(inner, "module.json"), "w") as f:
            f.write('{"name":"m","version":"1.0.0"}')
        self.assertEqual(fetch.find_module_root(d, None), inner)

    def test_rejects_two_subdirs(self):
        d = os.path.join(self.tmp, "u")
        os.makedirs(os.path.join(d, "a"))
        os.makedirs(os.path.join(d, "b"))
        with self.assertRaises(fetch.InvalidModule) as ctx:
            fetch.find_module_root(d, None)
        self.assertIn("structure", str(ctx.exception).lower())

    def test_rejects_empty_archive(self):
        d = os.path.join(self.tmp, "u")
        os.makedirs(d)
        with self.assertRaises(fetch.InvalidModule) as ctx:
            fetch.find_module_root(d, None)
        self.assertIn("structure", str(ctx.exception).lower())

    def test_rejects_wrapper_with_no_module_json(self):
        d = os.path.join(self.tmp, "u")
        os.makedirs(os.path.join(d, "repo-1.0.0"))
        with self.assertRaises(fetch.InvalidModule) as ctx:
            fetch.find_module_root(d, None)
        self.assertIn("module.json", str(ctx.exception))

    def test_with_subpath(self):
        d = os.path.join(self.tmp, "u")
        inner = os.path.join(d, "repo-1", "modules", "fifo")
        os.makedirs(inner)
        with open(os.path.join(inner, "module.json"), "w") as f:
            f.write('{"name":"fifo","version":"1.0.0"}')
        self.assertEqual(fetch.find_module_root(d, "modules/fifo"), inner)

    def test_subpath_that_does_not_exist(self):
        d = os.path.join(self.tmp, "u")
        os.makedirs(os.path.join(d, "repo-1"))
        with open(os.path.join(d, "repo-1", "module.json"), "w") as f:
            f.write('{"name":"m","version":"1.0.0"}')
        with self.assertRaises(fetch.InvalidModule):
            fetch.find_module_root(d, "modules/nope")

    def test_subpath_with_dotdot_is_rejected(self):
        d = os.path.join(self.tmp, "u")
        os.makedirs(os.path.join(d, "repo-1"))
        with open(os.path.join(d, "repo-1", "module.json"), "w") as f:
            f.write('{"name":"m","version":"1.0.0"}')
        with self.assertRaises(fetch.InvalidModule) as ctx:
            fetch.find_module_root(d, "../../../../etc")
        self.assertIn("escapes", str(ctx.exception))


class TestValidateModule(TempCase):
    def test_requires_module_json(self):
        d = os.path.join(self.tmp, "m")
        os.makedirs(d)
        with self.assertRaises(fetch.InvalidModule):
            fetch.validate_module(d)

    def test_requires_rtl(self):
        d = os.path.join(self.tmp, "m")
        os.makedirs(d)
        with open(os.path.join(d, "module.json"), "w") as f:
            f.write('{"name":"m","version":"1.0.0"}')
        with self.assertRaises(fetch.InvalidModule) as ctx:
            fetch.validate_module(d)
        self.assertIn(".v", str(ctx.exception))

    def test_accepts_a_real_module(self):
        d = os.path.join(self.tmp, "m")
        os.makedirs(d)
        with open(os.path.join(d, "module.json"), "w") as f:
            f.write('{"name":"m","version":"1.0.0"}')
        with open(os.path.join(d, "top.v"), "w") as f:
            f.write("module m; endmodule")
        self.assertEqual(fetch.validate_module(d)["name"], "m")

    def test_rejects_invalid_json(self):
        d = os.path.join(self.tmp, "m")
        os.makedirs(d)
        with open(os.path.join(d, "module.json"), "w") as f:
            f.write("{not json")
        with self.assertRaises(fetch.InvalidModule):
            fetch.validate_module(d)

    def test_rejects_missing_name(self):
        d = os.path.join(self.tmp, "m")
        os.makedirs(d)
        with open(os.path.join(d, "module.json"), "w") as f:
            f.write('{"version":"1.0.0"}')
        with self.assertRaises(fetch.InvalidModule) as ctx:
            fetch.validate_module(d)
        self.assertIn("name", str(ctx.exception))

    def test_rejects_invalid_soc_json(self):
        d = os.path.join(self.tmp, "m")
        os.makedirs(d)
        with open(os.path.join(d, "module.json"), "w") as f:
            f.write('{"name":"m","version":"1.0.0"}')
        with open(os.path.join(d, "top.v"), "w") as f:
            f.write("module m; endmodule")
        with open(os.path.join(d, "soc.json"), "w") as f:
            f.write("{bad")
        with self.assertRaises(fetch.InvalidModule) as ctx:
            fetch.validate_module(d)
        self.assertIn("soc.json", str(ctx.exception))

    def test_accepts_valid_soc_json(self):
        d = os.path.join(self.tmp, "m")
        os.makedirs(d)
        with open(os.path.join(d, "module.json"), "w") as f:
            f.write('{"name":"m","version":"1.0.0"}')
        with open(os.path.join(d, "top.v"), "w") as f:
            f.write("module m; endmodule")
        with open(os.path.join(d, "soc.json"), "w") as f:
            f.write('{"ram":1}')
        self.assertEqual(fetch.validate_module(d)["name"], "m")


class TestInstallExternal(TempCase):
    def test_installs_a_valid_module(self):
        _tar_with(self.tmp, [
            ("repo-1.0.0/module.json", b'{"name":"fifo","version":"1.0.0"}'),
            ("repo-1.0.0/top.v", b"module fifo; endmodule"),
        ])
        staging = os.path.join(self.tmp, "staging")
        os.makedirs(staging)
        with serve(self.tmp) as base_url:
            name, meta, staged_dir, resolved = anvil.install_external(
                f"{base_url}/a.tar.gz", staging)
        self.assertEqual(anvil.EXTERNAL_DIR, "external")
        self.assertEqual(name, "fifo")
        self.assertEqual(meta["version"], "1.0.0")
        self.assertTrue(os.path.isfile(os.path.join(staged_dir, "top.v")))
        self.assertEqual(resolved, f"{base_url}/a.tar.gz")
        self.assertTrue(staged_dir.startswith(staging + os.sep))

    def test_subpath_selects_one_module_of_a_monorepo(self):
        _tar_with(self.tmp, [
            ("rtl-v1/modules/fifo/module.json", b'{"name":"fifo","version":"1.0.0"}'),
            ("rtl-v1/modules/fifo/top.v", b"module fifo; endmodule"),
            ("rtl-v1/modules/uart/module.json", b'{"name":"uart","version":"1.0.0"}'),
        ])
        staging = os.path.join(self.tmp, "staging")
        os.makedirs(staging)
        with serve(self.tmp) as base_url:
            name, meta, staged_dir, resolved = anvil.install_external(
                f"{base_url}/a.tar.gz#modules/fifo", staging)
        self.assertEqual(name, "fifo")
        self.assertTrue(staged_dir.endswith(os.path.join("modules", "fifo")))
        self.assertEqual(resolved, f"{base_url}/a.tar.gz#modules/fifo")

    def test_rejects_an_invalid_module_but_leaves_staging_usable(self):
        _tar_with(self.tmp, [("repo/module.json", b'{"name":"m","version":"1.0.0"}')])
        staging = os.path.join(self.tmp, "staging")
        os.makedirs(staging)
        with serve(self.tmp) as base_url:
            with self.assertRaises(fetch.InvalidModule):
                anvil.install_external(f"{base_url}/a.tar.gz", staging)
        self.assertTrue(os.path.isdir(staging))

    def test_missing_archive_raises_no_archive_found(self):
        staging = os.path.join(self.tmp, "staging")
        os.makedirs(staging)
        with serve(self.tmp) as base_url:
            with self.assertRaises(fetch.NoArchiveFound):
                anvil.install_external(f"{base_url}/does-not-exist.tar.gz", staging)
        self.assertTrue(os.path.isdir(staging))

    def test_subpath_with_dotdot_is_rejected(self):
        _tar_with(self.tmp, [
            ("repo/module.json", b'{"name":"m","version":"1.0.0"}'),
            ("repo/top.v", b"module m; endmodule"),
        ])
        staging = os.path.join(self.tmp, "staging")
        os.makedirs(staging)
        with serve(self.tmp) as base_url:
            with self.assertRaises(fetch.InvalidModule) as ctx:
                anvil.install_external(f"{base_url}/a.tar.gz#../../../etc", staging)
        self.assertIn("escapes", str(ctx.exception))

    def test_malicious_archive_raises_unsafe_archive_and_leaves_staging_usable(self):
        _tar_with(self.tmp, [("../escape.txt", b"nope")])
        staging = os.path.join(self.tmp, "staging")
        os.makedirs(staging)
        with serve(self.tmp) as base_url:
            with self.assertRaises(fetch.UnsafeArchive):
                anvil.install_external(f"{base_url}/a.tar.gz", staging)
        self.assertTrue(os.path.isdir(staging))


def _make_local_module(base, name="local-mod", version="1.0.0"):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "module.json"), "w") as f:
        json.dump({"name": name, "version": version}, f)
    with open(os.path.join(d, "top.v"), "w") as f:
        f.write("module m; endmodule\n")
    return d


class TestMigrateConfig(TempCase):
    def test_migrates_a_schema_1_config(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        _make_local_module(self.tmp, "local-mod")
        os.chdir(proj)

        old = {"project": "p", "board": "Nexys-A7-50T",
               "modules": ["uart@1.0.0", "../local-mod"]}
        new, changed = anvil.migrate_config(old)
        self.assertTrue(changed)
        self.assertEqual(new["schema"], "2.0")
        self.assertEqual(new["version"], "1.0.0")
        self.assertIsInstance(new["modules"], dict)
        self.assertEqual(new["modules"]["uart"]["source"], "system")
        self.assertTrue(new["modules"]["uart"]["path"].startswith("$ANVIL_HOME/modules/uart@"))
        self.assertEqual(new["modules"]["local-mod"]["source"], "../local-mod")
        self.assertEqual(new["modules"]["local-mod"]["path"], "../local-mod")

    def test_migration_is_idempotent(self):
        old = {"project": "p", "modules": ["uart@1.0.0"]}
        once, _ = anvil.migrate_config(old)
        twice, changed = anvil.migrate_config(once)
        self.assertFalse(changed)
        self.assertEqual(twice, once)

    def test_schema_2_config_is_left_alone(self):
        cfg = {"schema": "2.0", "project": "p", "version": "1.0.0", "modules": {}}
        out, changed = anvil.migrate_config(cfg)
        self.assertFalse(changed)
        self.assertEqual(out, cfg)

    def test_no_home_path_is_recorded(self):
        old = {"project": "p", "modules": ["uart@1.0.0"]}
        new, _ = anvil.migrate_config(old)
        self.assertNotIn(os.path.expanduser("~"), json.dumps(new))

    def test_migration_says_which_module_it_cannot_find(self):
        # An old project may name a bundled module no longer in the registry --
        # resolve_version exits the process; migration must explain itself.
        old = {"project": "p", "modules": ["module-that-was-removed@1.0.0"]}
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.migrate_config(old)
        text = out.getvalue()
        self.assertIn("module-that-was-removed", text)
        self.assertTrue("config.json" in text or "migrat" in text.lower())


class TestModulesSchemaIntegration(TempCase):
    """Every reader of config['modules'] must work against the schema-2 dict shape."""

    def test_load_config_migrates_in_place_with_one_notice(self):
        os.chdir(self.tmp)
        with open("config.json", "w") as f:
            json.dump({"project": "p", "board": "Nexys-A7-50T",
                       "target": "nexys_a7_50t", "modules": ["uart@1.0.0"]}, f)

        with capture() as out:
            cfg = anvil.load_config()
        self.assertEqual(out.getvalue().count("migrated"), 1)
        self.assertEqual(cfg["schema"], "2.0")
        self.assertIsInstance(cfg["modules"], dict)

        with open("config.json") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["schema"], "2.0")

        with capture() as out2:
            anvil.load_config()
        self.assertNotIn("migrated", out2.getvalue())

    def test_get_resolved_modules_reads_schema_2_dict(self):
        entry = anvil.module_entry("1.0.0", "system", anvil.bundled_path("uart@1.0.0"), "sha256:x")
        resolved = anvil.get_resolved_modules({"modules": {"uart": entry}})
        self.assertIn("uart@1.0.0", [k for k, _, _ in resolved])

    def test_addmodule_and_removemodule_use_schema_2_dict(self):
        # config.json is saved before build_makefile's sv2v step, which this
        # sandbox has no toolchain for; a SystemExit there is not what's under test.
        os.chdir(self.tmp)
        with open("config.json", "w") as f:
            json.dump({"schema": "2.0", "project": "p", "version": "1.0.0",
                       "board": "Nexys-A7-50T", "target": "nexys_a7_50t",
                       "xdc": "x.xdc", "modules": {}}, f)

        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_addmodule(["uart"])
        with open("config.json") as f:
            cfg = json.load(f)
        self.assertIn("uart", cfg["modules"])
        self.assertEqual(cfg["modules"]["uart"]["source"], "system")
        self.assertTrue(cfg["modules"]["uart"]["hash"].startswith("sha256:"))

        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_removemodule(["uart"])
        with open("config.json") as f:
            cfg = json.load(f)
        self.assertNotIn("uart", cfg["modules"])

    def test_cmd_init_scaffolds_schema_2_config(self):
        os.chdir(self.tmp)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_init(["--board", "Nexys-A7-50T", "--example", "uart-hello"])
        with open("config.json") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["schema"], "2.0")
        self.assertIsInstance(cfg["modules"], dict)
        self.assertIn("uart", cfg["modules"])
        self.assertNotIn(os.path.expanduser("~"), json.dumps(cfg))


class TestRemoveLocalModuleByPath(TempCase):
    """removemodule <path> must work with the ref used to add it, even after the directory is gone."""

    def _scaffold(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        _make_local_module(self.tmp, "gone-mod")
        os.chdir(proj)
        with open("config.json", "w") as f:
            json.dump({"schema": "2.0", "project": "p", "version": "1.0.0",
                       "board": "Nexys-A7-50T", "target": "nexys_a7_50t",
                       "xdc": "x.xdc", "modules": {}}, f)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_addmodule(["../gone-mod"])
        with open("config.json") as f:
            self.assertIn("gone-mod", json.load(f)["modules"])

    def test_removes_by_path_with_directory_present(self):
        self._scaffold()
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_removemodule(["../gone-mod"])
        with open("config.json") as f:
            self.assertNotIn("gone-mod", json.load(f)["modules"])

    def test_removes_by_path_after_directory_deleted(self):
        self._scaffold()
        shutil.rmtree(os.path.join(self.tmp, "gone-mod"))
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_removemodule(["../gone-mod"])
        with open("config.json") as f:
            self.assertNotIn("gone-mod", json.load(f)["modules"])

    def test_removes_a_module_when_an_unrelated_local_modules_directory_is_gone(self):
        # neither the target of removal nor the depends-on-me check should touch
        # a directory belonging to a module that isn't being removed.
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        _make_local_module(self.tmp, "modA")
        _make_local_module(self.tmp, "modB")
        os.chdir(proj)
        with open("config.json", "w") as f:
            json.dump({"schema": "2.0", "project": "p", "version": "1.0.0",
                       "board": "Nexys-A7-50T", "target": "nexys_a7_50t",
                       "xdc": "x.xdc", "modules": {}}, f)
        with capture():
            anvil.cmd_addmodule(["../modA"])
            anvil.cmd_addmodule(["../modB"])
        with open("config.json") as f:
            self.assertEqual(set(json.load(f)["modules"]), {"modA", "modB"})

        shutil.rmtree(os.path.join(self.tmp, "modB"))

        with capture():
            anvil.cmd_removemodule(["../modA"])
        with open("config.json") as f:
            cfg = json.load(f)
        self.assertNotIn("modA", cfg["modules"])
        self.assertIn("modB", cfg["modules"])

    def _scaffold_empty_project(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        os.chdir(proj)
        with open("config.json", "w") as f:
            json.dump({"schema": "2.0", "project": "p", "version": "1.0.0",
                       "board": "Nexys-A7-50T", "target": "nexys_a7_50t",
                       "xdc": "x.xdc", "modules": {}}, f)

    def test_removes_by_absolute_path_with_directory_present(self):
        self._scaffold_empty_project()
        mod_dir = _make_local_module(self.tmp, "mAbs")
        with capture():
            anvil.cmd_addmodule(["../mAbs"])

        with capture():
            anvil.cmd_removemodule([mod_dir])
        with open("config.json") as f:
            self.assertNotIn("mAbs", json.load(f)["modules"])

    def test_removes_by_absolute_path_after_directory_deleted(self):
        self._scaffold_empty_project()
        mod_dir = _make_local_module(self.tmp, "mAbs2")
        with capture():
            anvil.cmd_addmodule(["../mAbs2"])
        shutil.rmtree(mod_dir)

        with capture():
            anvil.cmd_removemodule([mod_dir])
        with open("config.json") as f:
            self.assertNotIn("mAbs2", json.load(f)["modules"])

    def test_unknown_argument_fails_and_leaves_config_untouched(self):
        self._scaffold_empty_project()
        with open("config.json") as f:
            before = json.load(f)

        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.cmd_removemodule(["no-such-module"])
        self.assertIn("no-such-module", out.getvalue())

        with open("config.json") as f:
            self.assertEqual(json.load(f), before)

    def test_one_unknown_argument_blocks_the_whole_removal(self):
        self._scaffold_empty_project()
        _make_local_module(self.tmp, "realmod")
        with capture():
            anvil.cmd_addmodule(["../realmod"])

        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.cmd_removemodule(["../realmod", "no-such-module"])
        self.assertIn("no-such-module", out.getvalue())

        with open("config.json") as f:
            self.assertIn("realmod", json.load(f)["modules"])


FIFO_URL = "https://github.com/ana/fifo/archive/refs/tags/v1.2.0.tar.gz"
AXI_URL  = "https://github.com/bob/axi/archive/refs/tags/v2.0.0.tar.gz"


class FakeTTY(io.StringIO):
    """A readable stream that claims to be a terminal, for simulating an interactive answer."""
    def isatty(self):
        return True


class StdinCase(unittest.TestCase):
    """Restores sys.stdin -- tests here replace it to simulate a terminal or a non-tty pipe."""
    def setUp(self):
        self._stdin = sys.stdin
    def tearDown(self):
        sys.stdin = self._stdin


class TestConfirmExternal(StdinCase):
    def _plan(self, **over):
        base = {"name": "m", "version": "1", "source": "u",
                "required_by": None, "has_soc": False}
        return [{**base, **over}]

    # the four properties that must be impossible to get by accident, first

    def test_non_tty_without_yes_is_an_error(self):
        sys.stdin = io.StringIO("")   # not a terminal
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.confirm_external(self._plan(), False)
        self.assertIn("--yes", out.getvalue())

    def test_assume_yes_returns_true_without_asking(self):
        sys.stdin = io.StringIO("")   # would raise if read
        with capture():
            self.assertTrue(anvil.confirm_external(self._plan(), True))

    def test_empty_plan_needs_no_consent(self):
        self.assertTrue(anvil.confirm_external([], False))

    def test_anything_but_an_explicit_y_is_a_no(self):
        for answer in ("", "yes", "Yes please", "sure", "n", "  \n"):
            sys.stdin = FakeTTY(answer)
            with capture():
                self.assertFalse(anvil.confirm_external(self._plan(), False), repr(answer))

    def test_uppercase_y_is_accepted(self):
        sys.stdin = FakeTTY("Y\n")
        with capture():
            self.assertTrue(anvil.confirm_external(self._plan(), False))

    # presentation properties the brief calls out as requirements, not choices

    def test_prompt_lists_every_module_and_who_pulled_it(self):
        plan = [
            {"name": "fifo", "version": "1.2.0", "source": FIFO_URL,
             "required_by": None, "has_soc": False},
            {"name": "axi-lite", "version": "2.0.0", "source": AXI_URL,
             "required_by": "fifo", "has_soc": True},
        ]
        with capture() as out:
            anvil.confirm_external(plan, assume_yes=True)
        text = out.getvalue()
        self.assertIn("fifo", text)
        self.assertIn("axi-lite", text)
        self.assertIn("required by fifo", text)
        self.assertIn("soc.json", text)

    def test_prompt_shows_the_full_resolved_url(self):
        plan = self._plan(name="uart", version="9.9.9", source=FIFO_URL)
        with capture() as out:
            anvil.confirm_external(plan, assume_yes=True)
        text = out.getvalue()
        self.assertIn(FIFO_URL, text)
        self.assertIn("trusting", text)


def _mod_meta(name, version="1.0.0", depends=None):
    meta = {"name": name, "version": version}
    if depends:
        meta["depends"] = depends
    return json.dumps(meta).encode()


class TestPlanExternal(TempCase):
    def _staging(self):
        staging = os.path.join(self.tmp, "staging")
        os.makedirs(staging)
        return staging

    def test_transitive_dependency_is_fetched_and_flagged(self):
        with serve(self.tmp) as base_url:
            axi_url = f"{base_url}/axi.tar.gz"
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("fifo", "1.2.0", depends=[axi_url])),
                ("pkg/top.v", b"module fifo; endmodule"),
            ], name="fifo.tar.gz")
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("axi-lite", "2.0.0")),
                ("pkg/top.v", b"module axi; endmodule"),
                ("pkg/soc.json", b'{"compiler":"gcc","objcopy":"objcopy"}'),
            ], name="axi.tar.gz")
            plan = anvil.plan_external([f"{base_url}/fifo.tar.gz"], self._staging(), {})
        by_name = {m["name"]: m for m in plan}
        self.assertEqual(set(by_name), {"fifo", "axi-lite"})
        self.assertIsNone(by_name["fifo"]["required_by"])
        self.assertEqual(by_name["axi-lite"]["required_by"], "fifo")
        self.assertTrue(by_name["axi-lite"]["has_soc"])
        self.assertFalse(by_name["fifo"]["has_soc"])
        self.assertEqual(by_name["axi-lite"]["source"], axi_url)

    def test_bundled_name_in_depends_is_not_queued(self):
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("fifo", "1.0.0", depends=["uart"])),
                ("pkg/top.v", b"module fifo; endmodule"),
            ], name="fifo.tar.gz")
            plan = anvil.plan_external([f"{base_url}/fifo.tar.gz"], self._staging(), {})
        self.assertEqual([m["name"] for m in plan], ["fifo"])

    def test_collision_with_an_existing_module_fails_naming_both_sources(self):
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("uart", "1.0.0")),
                ("pkg/top.v", b"module uart; endmodule"),
            ], name="uart.tar.gz")
            existing = {"uart": {"source": "system"}}
            with capture() as out:
                with self.assertRaises(SystemExit):
                    anvil.plan_external([f"{base_url}/uart.tar.gz"], self._staging(), existing)
        text = out.getvalue()
        self.assertIn("system", text)
        self.assertIn(f"{base_url}/uart.tar.gz", text)

    def test_collision_within_the_same_batch_fails_naming_both_sources(self):
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("shared", "1.0.0")),
                ("pkg/top.v", b"module a; endmodule"),
            ], name="a.tar.gz")
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("shared", "2.0.0")),
                ("pkg/top.v", b"module b; endmodule"),
            ], name="b.tar.gz")
            with capture() as out:
                with self.assertRaises(SystemExit):
                    anvil.plan_external(
                        [f"{base_url}/a.tar.gz", f"{base_url}/b.tar.gz"], self._staging(), {})
        text = out.getvalue()
        self.assertIn(f"{base_url}/a.tar.gz", text)
        self.assertIn(f"{base_url}/b.tar.gz", text)

    def test_cyclic_external_dependency_terminates(self):
        with serve(self.tmp) as base_url:
            a_url, b_url = f"{base_url}/a.tar.gz", f"{base_url}/b.tar.gz"
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("a", "1.0.0", depends=[b_url])),
                ("pkg/top.v", b"module a; endmodule"),
            ], name="a.tar.gz")
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("b", "1.0.0", depends=[a_url])),
                ("pkg/top.v", b"module b; endmodule"),
            ], name="b.tar.gz")
            plan = anvil.plan_external([a_url], self._staging(), {})
        self.assertEqual(sorted(m["name"] for m in plan), ["a", "b"])


def _scaffold_project(base):
    proj = os.path.join(base, "proj")
    os.makedirs(proj)
    with open(os.path.join(proj, "config.json"), "w") as f:
        json.dump({"schema": "2.0", "project": "p", "version": "1.0.0",
                   "board": "Nexys-A7-50T", "target": "nexys_a7_50t",
                   "xdc": "x.xdc", "modules": {}}, f)
    return proj


class TestCmdAddmoduleExternal(TempCase, StdinCase):
    """Exercises cmd_addmodule itself, not confirm_external/plan_external in isolation."""

    def setUp(self):
        TempCase.setUp(self)
        StdinCase.setUp(self)
        self._made = []
        self._real_mkdtemp = anvil.tempfile.mkdtemp
        def tracking_mkdtemp(*a, **k):
            d = self._real_mkdtemp(*a, **k)
            self._made.append(d)
            return d
        anvil.tempfile.mkdtemp = tracking_mkdtemp

    def tearDown(self):
        anvil.tempfile.mkdtemp = self._real_mkdtemp
        StdinCase.tearDown(self)
        TempCase.tearDown(self)

    def _serve_fifo(self, name="fifo", version="1.0.0", filename="fifo.tar.gz"):
        _tar_with(self.tmp, [
            ("pkg/module.json", _mod_meta(name, version)),
            ("pkg/top.v", b"module fifo; endmodule"),
        ], name=filename)

    def test_declining_installs_nothing_and_cleans_staging(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with open("config.json") as f:
            before = json.load(f)
        with serve(self.tmp) as base_url:
            self._serve_fifo()
            sys.stdin = FakeTTY("n\n")
            with capture():
                anvil.cmd_addmodule([f"{base_url}/fifo.tar.gz"])
        self.assertFalse(os.path.isdir("external"))
        with open("config.json") as f:
            self.assertEqual(json.load(f), before)
        self.assertTrue(self._made)
        for d in self._made:
            self.assertFalse(os.path.exists(d))

    def test_accepting_installs_and_records_a_sha256_hash(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._serve_fifo()
            sys.stdin = FakeTTY("y\n")
            with capture():   # a real failure here (e.g. get_resolved_modules choking on the
                anvil.cmd_addmodule([f"{base_url}/fifo.tar.gz"])   # new entry) must fail the test
        self.assertTrue(os.path.isfile(os.path.join("external", "fifo@1.0.0", "module.json")))
        with open("config.json") as f:
            entry = json.load(f)["modules"]["fifo"]
        self.assertEqual(entry["path"], os.path.join("external", "fifo@1.0.0"))
        self.assertEqual(entry["source"], f"{base_url}/fifo.tar.gz")
        self.assertTrue(entry["hash"].startswith("sha256:"))
        for d in self._made:
            self.assertFalse(os.path.exists(d))

    def test_yes_flag_skips_the_prompt(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._serve_fifo()
            sys.stdin = io.StringIO("")   # never read
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])
        with open("config.json") as f:
            self.assertIn("fifo", json.load(f)["modules"])

    def test_non_tty_without_yes_refuses_and_installs_nothing(self):
        # the toolchain installer runs anvil with stdin from /dev/null -- this is that case
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with open("config.json") as f:
            before = json.load(f)
        with serve(self.tmp) as base_url:
            self._serve_fifo()
            sys.stdin = io.StringIO("")
            with capture() as out:
                with self.assertRaises(SystemExit):
                    anvil.cmd_addmodule([f"{base_url}/fifo.tar.gz"])
        self.assertIn("--yes", out.getvalue())
        self.assertFalse(os.path.isdir("external"))
        with open("config.json") as f:
            self.assertEqual(json.load(f), before)
        for d in self._made:
            self.assertFalse(os.path.exists(d))

    def test_bundled_only_addmodule_never_touches_stdin(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        sys.stdin = io.StringIO("")   # would raise StopIteration if input() were ever called
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_addmodule(["uart"])
        with open("config.json") as f:
            self.assertIn("uart", json.load(f)["modules"])
        self.assertEqual(self._made, [])   # no staging dir at all -- no external ref involved

    def test_name_collision_with_a_bundled_module_names_both_sources(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_addmodule(["uart"])
        with serve(self.tmp) as base_url:
            self._serve_fifo(name="uart", filename="uart.tar.gz")
            sys.stdin = FakeTTY("y\n")
            with capture() as out:
                with self.assertRaises(SystemExit):
                    anvil.cmd_addmodule([f"{base_url}/uart.tar.gz"])
        text = out.getvalue()
        self.assertIn("system", text)
        self.assertIn(f"{base_url}/uart.tar.gz", text)
        with open("config.json") as f:
            self.assertEqual(json.load(f)["modules"]["uart"]["source"], "system")


class TestForceReRecordsHash(TempCase):
    def _init_config(self, path="config.json"):
        with open(path, "w") as f:
            json.dump({"schema": "2.0", "project": "p", "version": "1.0.0",
                       "board": "Nexys-A7-50T", "target": "nexys_a7_50t",
                       "xdc": "x.xdc", "modules": {}}, f)

    def test_without_force_a_local_edit_is_not_re_recorded(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        mod = _make_local_module(self.tmp, "edit-me")
        os.chdir(proj)
        self._init_config()
        with capture():
            anvil.cmd_addmodule(["../edit-me"])
        with open("config.json") as f:
            before = json.load(f)["modules"]["edit-me"]["hash"]

        with open(os.path.join(mod, "top.v"), "a") as f:
            f.write("// changed\n")

        with capture():
            anvil.cmd_addmodule(["../edit-me"])
        with open("config.json") as f:
            after = json.load(f)["modules"]["edit-me"]["hash"]
        self.assertEqual(before, after)

    def test_force_by_name_re_records_a_local_modules_hash(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        mod = _make_local_module(self.tmp, "edit-me2")
        os.chdir(proj)
        self._init_config()
        with capture():
            anvil.cmd_addmodule(["../edit-me2"])
        with open("config.json") as f:
            before = json.load(f)["modules"]["edit-me2"]["hash"]

        with open(os.path.join(mod, "top.v"), "a") as f:
            f.write("// changed\n")

        with capture():   # consent for --force is TestForceDisclosure's job; this test is the hash mechanics
            anvil.cmd_addmodule(["--yes", "--force", "edit-me2"])
        with open("config.json") as f:
            after = json.load(f)["modules"]["edit-me2"]["hash"]
        self.assertNotEqual(before, after)

    def test_force_by_name_re_hashes_an_external_module_in_place(self):
        # --force re-records what's on disk; it does not re-fetch, so a local
        # edit is picked up rather than silently discarded.
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        os.chdir(proj)
        self._init_config()
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("fifo", "1.0.0")),
                ("pkg/top.v", b"module fifo; endmodule"),
            ], name="fifo.tar.gz")
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])
            with open("config.json") as f:
                before = json.load(f)["modules"]["fifo"]
            self.assertEqual(before["source"], f"{base_url}/fifo.tar.gz")

            with open(os.path.join("external", "fifo@1.0.0", "top.v"), "a") as f:
                f.write("// edited after fetch\n")

            with capture():   # consent for --force is TestForceDisclosure's job; this test is the hash mechanics
                anvil.cmd_addmodule(["--yes", "--force", "fifo"])

        with open("config.json") as f:
            after = json.load(f)["modules"]["fifo"]
        self.assertNotEqual(after["hash"], before["hash"])
        self.assertEqual(fetch.module_hash(after["path"]), after["hash"])
        self.assertEqual(after["source"], f"{base_url}/fifo.tar.gz")   # source is preserved, not touched

    def test_force_on_a_missing_external_module_fails_cleanly(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        os.chdir(proj)
        self._init_config()
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("fifo", "1.0.0")),
                ("pkg/top.v", b"module fifo; endmodule"),
            ], name="fifo.tar.gz")
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])
        shutil.rmtree(os.path.join("external", "fifo@1.0.0"))

        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.cmd_addmodule(["--force", "fifo"])
        self.assertIn("fifo", out.getvalue())



class TestForceDisclosure(TempCase, StdinCase):
    """--force must disclose before it re-anchors trust, not just re-hash silently."""

    def setUp(self):
        TempCase.setUp(self)
        StdinCase.setUp(self)

    def tearDown(self):
        StdinCase.tearDown(self)
        TempCase.tearDown(self)

    def _install_fifo(self, base_url):
        _tar_with(self.tmp, [
            ("pkg/module.json", _mod_meta("fifo", "1.2.0")),
            ("pkg/top.v", b"module fifo; endmodule"),
        ], name="fifo.tar.gz")
        with capture():
            anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])

    def test_force_on_tampered_module_with_new_soc_json_discloses_and_requires_consent(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._install_fifo(base_url)
        with open("config.json") as f:
            before_hash = json.load(f)["modules"]["fifo"]["hash"]

        mod_dir = os.path.join("external", "fifo@1.2.0")
        with open(os.path.join(mod_dir, "top.v"), "a") as f:
            f.write("// tampered\n")
        with open(os.path.join(mod_dir, "soc.json"), "w") as f:
            f.write('{"compiler": "evil-cc", "objcopy": "objcopy"}')

        # non-tty, no --yes -- refusing must not have recorded anything either
        sys.stdin = io.StringIO("")
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.cmd_addmodule(["--force", "fifo"])
        text = out.getvalue()
        self.assertIn(before_hash, text)
        self.assertIn("soc.json", text)
        self.assertIn("--yes", text)
        with open("config.json") as f:
            self.assertEqual(json.load(f)["modules"]["fifo"]["hash"], before_hash)

        # explicit y -- records, and the transcript shows both hashes plus the flag
        sys.stdin = FakeTTY("y\n")
        with capture() as out:
            anvil.cmd_addmodule(["--force", "fifo"])
        text = out.getvalue()
        self.assertIn(before_hash, text)
        self.assertIn("soc.json", text)
        with open("config.json") as f:
            entry = json.load(f)["modules"]["fifo"]
        self.assertNotEqual(before_hash, entry["hash"])
        self.assertIn(entry["hash"], text)
        self.assertEqual(entry["source"], f"{base_url}/fifo.tar.gz")   # re-recorded, not re-fetched

    def test_force_on_unchanged_module_is_a_reported_no_op(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._install_fifo(base_url)
        with open("config.json") as f:
            before = json.load(f)

        sys.stdin = io.StringIO("")   # no prompt should even be needed -- nothing changed
        with capture() as out:
            anvil.cmd_addmodule(["--force", "fifo"])
        self.assertIn("unchanged", out.getvalue().lower())

        with open("config.json") as f:
            after = json.load(f)
        self.assertEqual(before, after)


class TestMidInstallFailure(TempCase):
    def test_failure_partway_through_names_installed_and_leftover_modules(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("aa", "1.0.0")),
                ("pkg/top.v", b"module aa; endmodule"),
            ], name="aa.tar.gz")
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("bb", "1.0.0")),
                ("pkg/top.v", b"module bb; endmodule"),
            ], name="bb.tar.gz")

            real_hash = fetch.module_hash
            calls = []
            def flaky_hash(mod_dir):
                calls.append(mod_dir)
                if len(calls) == 2:
                    raise RuntimeError("disk exploded")
                return real_hash(mod_dir)
            fetch.module_hash = flaky_hash
            try:
                with capture() as out:
                    with self.assertRaises(SystemExit):
                        anvil.cmd_addmodule(
                            ["--yes", f"{base_url}/aa.tar.gz", f"{base_url}/bb.tar.gz"])
            finally:
                fetch.module_hash = real_hash

        text = out.getvalue()
        self.assertIn("aa", text)
        self.assertIn("bb", text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(os.path.isdir(os.path.join("external", "aa@1.0.0")))
        with open("config.json") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["modules"], {})   # nothing written -- not even the successful half


class TestTransitiveExternalDependency(TempCase):
    """A URL dependency is its own top-level config.json entry; resolve_deps must not re-walk it."""

    def test_external_depending_on_external_resolves_end_to_end(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            axi_url = f"{base_url}/axi.tar.gz"
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("fifo", "1.0.0", depends=[axi_url])),
                ("pkg/top.v", b"module fifo; endmodule"),
            ], name="fifo.tar.gz")
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("axi", "1.0.0")),
                ("pkg/top.v", b"module axi; endmodule"),
            ], name="axi.tar.gz")
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])

        with open("config.json") as f:
            cfg = json.load(f)
        self.assertIn("fifo", cfg["modules"])
        self.assertIn("axi", cfg["modules"])

        sources = anvil.collect_sources(config=cfg)
        self.assertTrue(any(s.endswith(os.path.join("fifo@1.0.0", "top.v")) for s in sources), sources)
        self.assertTrue(any(s.endswith(os.path.join("axi@1.0.0", "top.v")) for s in sources), sources)

    def test_external_depending_on_external_depending_on_bundled_resolves(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            axi_url = f"{base_url}/axi.tar.gz"
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("fifo2", "1.0.0", depends=[axi_url])),
                ("pkg/top.v", b"module fifo2; endmodule"),
            ], name="fifo.tar.gz")
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("axi2", "1.0.0", depends=["picorv32"])),
                ("pkg/top.v", b"module axi2; endmodule"),
            ], name="axi.tar.gz")
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])

        with open("config.json") as f:
            cfg = json.load(f)
        self.assertIn("fifo2", cfg["modules"])
        self.assertIn("axi2", cfg["modules"])
        self.assertIn("picorv32", cfg["modules"])

        sources = anvil.collect_sources(config=cfg)
        self.assertTrue(any(s.endswith(os.path.join("fifo2@1.0.0", "top.v")) for s in sources), sources)
        self.assertTrue(any(s.endswith(os.path.join("axi2@1.0.0", "top.v")) for s in sources), sources)
        self.assertTrue(any(s.endswith("picorv32.v") for s in sources), sources)


class TestDependencyClosureConsistency(TempCase):
    """resolve_deps skips a URL depends entry on trust; get_resolved_modules must verify that trust."""

    def _serve_fifo_axi(self, base_url):
        axi_url = f"{base_url}/axi.tar.gz"
        _tar_with(self.tmp, [
            ("pkg/module.json", _mod_meta("fifo", "1.0.0", depends=[axi_url])),
            ("pkg/top.v", b"module fifo; endmodule"),
        ], name="fifo.tar.gz")
        _tar_with(self.tmp, [
            ("pkg/module.json", _mod_meta("axi", "1.0.0")),
            ("pkg/top.v", b"module axi; endmodule"),
        ], name="axi.tar.gz")

    def test_dependency_missing_from_config_fails_naming_both(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._serve_fifo_axi(base_url)
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])
        with open("config.json") as f:
            cfg = json.load(f)
        axi_url = cfg["modules"]["axi"]["source"]
        del cfg["modules"]["axi"]
        with open("config.json", "w") as f:
            json.dump(cfg, f)
        with open("config.json") as f:
            cfg = json.load(f)

        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.get_resolved_modules(cfg)
        text = out.getvalue()
        self.assertIn("fifo", text)
        self.assertIn(axi_url, text)

    def test_healthy_two_level_chain_is_silent_and_returns_both_sources(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._serve_fifo_axi(base_url)
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])
        with open("config.json") as f:
            cfg = json.load(f)

        with capture() as out:
            sources = anvil.collect_sources(config=cfg)
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(any(s.endswith(os.path.join("fifo@1.0.0", "top.v")) for s in sources), sources)
        self.assertTrue(any(s.endswith(os.path.join("axi@1.0.0", "top.v")) for s in sources), sources)

    def test_dependency_with_a_subpath_fragment_is_recognized_as_recorded(self):
        # source is now recorded with its #subpath fragment -- the check must compare fragment-aware too
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            shared_url = f"{base_url}/rtl.tar.gz#modules/shared"
            _tar_with(self.tmp, [
                ("rtl-1/modules/fifo/module.json", _mod_meta("fifo", "1.0.0", depends=[shared_url])),
                ("rtl-1/modules/fifo/top.v", b"module fifo; endmodule"),
                ("rtl-1/modules/shared/module.json", _mod_meta("shared", "1.0.0")),
                ("rtl-1/modules/shared/top.v", b"module shared; endmodule"),
            ], name="rtl.tar.gz")
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/rtl.tar.gz#modules/fifo"])
        with open("config.json") as f:
            cfg = json.load(f)

        with capture() as out:
            resolved = anvil.get_resolved_modules(cfg)   # must not raise
        self.assertEqual(out.getvalue(), "")
        self.assertIn("shared", [meta["name"] for _, _, meta in resolved])

    def test_bundled_only_project_is_unaffected(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        os.chdir(proj)
        with open("config.json", "w") as f:
            json.dump({"schema": "2.0", "project": "p", "version": "1.0.0",
                       "board": "Nexys-A7-50T", "target": "nexys_a7_50t",
                       "xdc": "x.xdc", "modules": {}}, f)
        with capture():
            anvil.cmd_addmodule(["picorv32"])
        with open("config.json") as f:
            cfg = json.load(f)

        with capture() as out:
            resolved = anvil.get_resolved_modules(cfg)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("picorv32@1.0.0", [k for k, _, _ in resolved])


def _archive_of(tmp_path, src_dir, name="a.tar.gz", wrapper="pkg"):
    """A tar.gz mirroring src_dir's files under one wrapper directory, at tmp_path/name."""
    p = os.path.join(tmp_path, name)
    with tarfile.open(p, "w:gz") as t:
        for root, _, files in os.walk(src_dir):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, src_dir)
                t.add(full, arcname=os.path.join(wrapper, rel))
    return p


class TestEnsureModules(TempCase):
    """ensure_modules(config): every module present and hash-matching, or fail saying why."""

    def _cfg(self, **modules):
        return {"schema": "2.0", "modules": modules}

    def test_missing_url_module_is_refetched(self):
        proj = _scaffold_project(self.tmp)
        srv  = os.path.join(self.tmp, "srv")
        os.makedirs(srv)
        m = _make_module(srv)
        expected = fetch.module_hash(m)
        _archive_of(srv, m)
        os.chdir(proj)

        with serve(srv) as base_url:
            cfg = self._cfg(m={"version": "1.0.0", "source": f"{base_url}/a.tar.gz",
                                "path": os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0"),
                                "hash": expected})
            with capture() as out:
                anvil.ensure_modules(cfg)
        self.assertTrue(os.path.isfile(os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0", "module.json")))
        self.assertIn("m", out.getvalue())

    def test_hash_mismatch_is_an_error(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        d = _make_local_module(self.tmp, "m")
        recorded = fetch.module_hash(d)
        with open(os.path.join(d, "top.v"), "w") as f:
            f.write("module m; wire tampered; endmodule\n")   # edited after recording

        cfg = self._cfg(m={"version": "1.0.0", "source": "../m", "path": "../m", "hash": recorded})
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.ensure_modules(cfg)
        text = out.getvalue().lower()
        self.assertIn("hash", text)
        self.assertIn("m", text)

    def test_missing_local_module_cannot_be_refetched(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        cfg = self._cfg(m={"version": "1.0.0", "source": "../gone",
                            "path": "../gone", "hash": "sha256:00"})
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.ensure_modules(cfg)
        self.assertIn("../gone", out.getvalue())

    def test_missing_bundled_module_cannot_be_refetched(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        path = "$ANVIL_HOME/modules/doesnotexist@9.9.9"
        cfg = self._cfg(m={"version": "9.9.9", "source": "system",
                            "path": path, "hash": "sha256:00"})
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.ensure_modules(cfg)
        self.assertIn(path, out.getvalue())

    def test_present_module_with_matching_hash_is_left_alone(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        d = _make_local_module(self.tmp, "m")
        digest = fetch.module_hash(d)
        cfg = self._cfg(m={"version": "1.0.0", "source": "../m", "path": "../m", "hash": digest})
        with capture() as out:
            anvil.ensure_modules(cfg)   # must not raise
        self.assertEqual(out.getvalue(), "")

    def test_only_the_missing_module_among_several_is_refetched(self):
        proj = _scaffold_project(self.tmp)
        srv  = os.path.join(self.tmp, "srv")
        os.makedirs(srv)
        present = _make_local_module(self.tmp, "present")
        present_hash = fetch.module_hash(present)
        missing_src = _make_module(srv)
        missing_hash = fetch.module_hash(missing_src)
        _archive_of(srv, missing_src)
        os.chdir(proj)

        with serve(srv) as base_url:
            cfg = self._cfg(
                present={"version": "1.0.0", "source": "../present", "path": "../present",
                          "hash": present_hash},
                m={"version": "1.0.0", "source": f"{base_url}/a.tar.gz",
                   "path": os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0"), "hash": missing_hash},
            )
            with capture() as out:
                anvil.ensure_modules(cfg)
        text = out.getvalue()
        self.assertEqual(text.count("Fetching"), 1)
        self.assertIn("m", text)
        self.assertTrue(os.path.isfile(os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0", "top.v")))

    def test_refetch_failure_reports_cleanly(self):
        proj = _scaffold_project(self.tmp)
        srv  = os.path.join(self.tmp, "srv")
        os.makedirs(srv)
        os.chdir(proj)

        with serve(srv) as base_url:   # nothing served -- every candidate 404s
            cfg = self._cfg(m={"version": "1.0.0", "source": f"{base_url}/nope.tar.gz",
                                "path": os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0"),
                                "hash": "sha256:00"})
            with capture() as out:
                with self.assertRaises(SystemExit):
                    anvil.ensure_modules(cfg)
        self.assertIn("m", out.getvalue())
        self.assertFalse(os.path.isdir(anvil.EXTERNAL_DIR))

    def test_refetched_content_hashing_differently_is_still_caught(self):
        proj = _scaffold_project(self.tmp)
        srv  = os.path.join(self.tmp, "srv")
        os.makedirs(srv)
        m = _make_module(srv)
        _archive_of(srv, m)
        os.chdir(proj)

        with serve(srv) as base_url:
            cfg = self._cfg(m={"version": "1.0.0", "source": f"{base_url}/a.tar.gz",
                                "path": os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0"),
                                "hash": "sha256:" + "0" * 64})   # not what the archive contains
            with capture() as out:
                with self.assertRaises(SystemExit):
                    anvil.ensure_modules(cfg)
        text = out.getvalue().lower()
        self.assertIn("hash", text)
        self.assertTrue(os.path.isfile(os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0", "module.json")))

    def test_no_modules_is_a_no_op(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with capture() as out:
            anvil.ensure_modules(self._cfg())
        self.assertEqual(out.getvalue(), "")


class TestEnsureModulesViaCmdSynth(TempCase):
    """The real entry point: a student's clone has config.json but no external/."""

    def test_cmd_synth_restores_a_deleted_external_module(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)

        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("m", "1.0.0")),
                ("pkg/top.v", b"module m; endmodule"),
            ], name="m.tar.gz")
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/m.tar.gz"])

            self.assertTrue(os.path.isfile(os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0", "top.v")))
            shutil.rmtree(anvil.EXTERNAL_DIR)   # simulate a fresh clone: git-ignored, so absent

            # the re-fetch inside cmd_synth needs the server still up -- stays in this block
            with capture() as out:
                with contextlib.suppress(SystemExit):   # sandbox has no F4PGA toolchain -- expected
                    anvil.cmd_synth([])

        self.assertTrue(os.path.isfile(os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0", "top.v")))
        self.assertIn("Fetching m", out.getvalue())
        with open("Makefile") as f:
            self.assertIn("m@1.0.0", f.read())


class TestEnsureModulesViaCmdTest(TempCase):
    """cmd_test's config-present branch needs the same safety net as cmd_synth."""

    def test_missing_external_module_is_refetched_and_run_proceeds(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)

        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("m", "1.0.0")),
                ("pkg/top.v", b"module m; endmodule"),
            ], name="m.tar.gz")
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/m.tar.gz"])
            shutil.rmtree(anvil.EXTERNAL_DIR)   # simulate a fresh clone: git-ignored, so absent
            os.makedirs("tb")
            with open("tb/m_tb.v", "w") as f:
                f.write("module m_tb; endmodule\n")

            # the re-fetch inside cmd_test needs the server still up -- stays in this block
            with capture() as out:
                with contextlib.suppress(SystemExit):   # sandbox has no iverilog -- expected
                    anvil.cmd_test(["tb/m_tb.v"])

        self.assertTrue(os.path.isfile(os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0", "top.v")))
        self.assertIn("Fetching m", out.getvalue())
        self.assertNotIn("module.json not found", out.getvalue())

    def test_hash_mismatch_fails_the_same_way_cmd_synth_does(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        d = _make_local_module(self.tmp, "m")
        with capture():
            anvil.cmd_addmodule(["../m"])
        with open(os.path.join(d, "top.v"), "w") as f:
            f.write("module m; wire tampered; endmodule\n")   # edited after recording
        os.makedirs("tb")
        with open("tb/m_tb.v", "w") as f:
            f.write("module m_tb; endmodule\n")

        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.cmd_test(["tb/m_tb.v"])
        text = out.getvalue()
        self.assertIn("hash", text.lower())
        self.assertIn("m", text)
        self.assertIn("anvil addmodule --force m", text)

    def test_present_and_matching_is_silent_before_the_toolchain_boundary(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        d = _make_local_module(self.tmp, "m")
        with capture():
            anvil.cmd_addmodule(["../m"])   # local -- no network needed, no toolchain either
        os.makedirs("tb")
        with open("tb/m_tb.v", "w") as f:
            f.write("module m_tb; endmodule\n")

        with capture() as out:
            with contextlib.suppress(SystemExit):   # sandbox has no iverilog -- expected
                anvil.cmd_test(["tb/m_tb.v"])
        text = out.getvalue()
        self.assertNotIn("Fetching", text)
        self.assertIn("[TEST] Testbench:", text)

    def test_no_config_present_path_is_unaffected(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        os.chdir(proj)
        with open("top.v", "w") as f:
            f.write("module top; endmodule\n")
        os.makedirs("tb")
        with open("tb/top_tb.v", "w") as f:
            f.write("module top_tb; endmodule\n")

        with capture() as out:
            with contextlib.suppress(SystemExit):   # sandbox has no iverilog -- expected
                anvil.cmd_test(["tb/top_tb.v"])
        text = out.getvalue()
        self.assertIn("[TEST] Testbench:", text)
        self.assertNotIn("Fetching", text)


class TestMonorepoSubpathRoundTrip(TempCase, StdinCase):
    """A URL ref's #subpath must survive into `source`, or the module can never be re-fetched."""

    def setUp(self):
        TempCase.setUp(self)
        StdinCase.setUp(self)

    def tearDown(self):
        StdinCase.tearDown(self)
        TempCase.tearDown(self)

    def _serve_monorepo(self):
        _tar_with(self.tmp, [
            ("rtl-1/modules/fifo/module.json", _mod_meta("fifo", "1.0.0")),
            ("rtl-1/modules/fifo/top.v", b"module fifo; endmodule"),
            ("rtl-1/modules/uart/module.json", _mod_meta("uart", "1.0.0")),
        ], name="rtl.tar.gz")

    def test_source_is_recorded_with_the_subpath_fragment(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._serve_monorepo()
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/rtl.tar.gz#modules/fifo"])
        with open("config.json") as f:
            entry = json.load(f)["modules"]["fifo"]
        self.assertEqual(entry["source"], f"{base_url}/rtl.tar.gz#modules/fifo")

    def test_deleted_external_is_restored_by_ensure_modules(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._serve_monorepo()
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/rtl.tar.gz#modules/fifo"])
            with open("config.json") as f:
                cfg = json.load(f)
            shutil.rmtree(anvil.EXTERNAL_DIR)

            with capture() as out:
                anvil.ensure_modules(cfg)   # must not raise -- restore, then hash must match
        restored = os.path.join(anvil.EXTERNAL_DIR, "fifo@1.0.0")
        self.assertTrue(os.path.isfile(os.path.join(restored, "top.v")))
        self.assertIn("Fetching fifo", out.getvalue())
        self.assertEqual(fetch.module_hash(restored), cfg["modules"]["fifo"]["hash"])

    def test_consent_prompt_shows_the_full_reference_including_subpath(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._serve_monorepo()
            sys.stdin = FakeTTY("y\n")
            with capture() as out:
                anvil.cmd_addmodule([f"{base_url}/rtl.tar.gz#modules/fifo"])
        self.assertIn(f"{base_url}/rtl.tar.gz#modules/fifo", out.getvalue())

    def test_plain_module_source_has_no_stray_fragment(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("fifo", "1.0.0")),
                ("pkg/top.v", b"module fifo; endmodule"),
            ], name="fifo.tar.gz")
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])
        with open("config.json") as f:
            entry = json.load(f)["modules"]["fifo"]
        self.assertEqual(entry["source"], f"{base_url}/fifo.tar.gz")
        self.assertNotIn("#", entry["source"])


class TestScaffoldGit(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        anvil._warned_gitignore.clear()

    def test_creates_git_and_gitignore(self):
        # tempfile.mkdtemp() lives outside Anvil's own repo -- confirm that rather than assume it
        outside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                                  cwd=self.tmp, capture_output=True)
        self.assertNotEqual(outside.returncode, 0)
        anvil.scaffold_git(self.tmp)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, ".git")))
        with open(os.path.join(self.tmp, ".gitignore")) as f:
            body = f.read()
        for line in ["build/", "external/", "*.vcd", "*.log", "__pycache__/"]:
            self.assertIn(line, body)

    def test_does_not_nest_a_repo(self):
        subprocess.run(["git", "init", "-q", self.tmp], check=True)
        inner = os.path.join(self.tmp, "proj")
        os.makedirs(inner)
        anvil.scaffold_git(inner)
        self.assertFalse(os.path.isdir(os.path.join(inner, ".git")))   # outer repo stays in charge
        self.assertTrue(os.path.isfile(os.path.join(inner, ".gitignore")))

    def test_existing_gitignore_is_not_overwritten(self):
        with open(os.path.join(self.tmp, ".gitignore"), "w") as f:
            f.write("mine\n")
        anvil.scaffold_git(self.tmp)
        with open(os.path.join(self.tmp, ".gitignore")) as f:
            self.assertEqual(f.read(), "mine\n")

    def test_missing_git_binary_still_leaves_a_gitignore(self):
        real_run = anvil.subprocess.run
        def missing(cmd, *a, **k):
            if cmd[0] == "git":
                raise FileNotFoundError("git")
            return real_run(cmd, *a, **k)
        anvil.subprocess.run = missing
        try:
            anvil.scaffold_git(self.tmp)   # must not raise
        finally:
            anvil.subprocess.run = real_run
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".git")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, ".gitignore")))


class TestWarnIfNoGitignore(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        anvil._warned_gitignore.clear()

    def test_warns_once_when_no_gitignore(self):
        with capture() as out:
            anvil.warn_if_no_gitignore(self.tmp)
        text = out.getvalue()
        self.assertIn("no .gitignore", text)
        self.assertIn("external/", text)
        with capture() as out2:
            anvil.warn_if_no_gitignore(self.tmp)
        self.assertEqual(out2.getvalue(), "")   # warned once, not every time

    def test_silent_when_gitignore_exists(self):
        with open(os.path.join(self.tmp, ".gitignore"), "w") as f:
            f.write("external/\n")
        with capture() as out:
            anvil.warn_if_no_gitignore(self.tmp)
        self.assertEqual(out.getvalue(), "")

    def test_unrelated_gitignore_content_is_taken_at_face_value(self):
        # the design refuses to parse gitignore patterns -- presence alone silences the warning
        with open(os.path.join(self.tmp, ".gitignore"), "w") as f:
            f.write("*.o\n")
        with capture() as out:
            anvil.warn_if_no_gitignore(self.tmp)
        self.assertEqual(out.getvalue(), "")

    def test_dedup_is_keyed_by_project_not_global(self):
        other = tempfile.mkdtemp()
        try:
            with capture():
                anvil.warn_if_no_gitignore(self.tmp)
            with capture() as out:
                anvil.warn_if_no_gitignore(other)
            self.assertIn("no .gitignore", out.getvalue())
        finally:
            shutil.rmtree(other, ignore_errors=True)


class TestCmdInitScaffoldsGit(TempCase):
    def test_git_init_and_gitignore_appear_after_a_plain_init(self):
        os.chdir(self.tmp)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_init(["--board", "Nexys-A7-50T"])
        self.assertTrue(os.path.isdir(".git"))
        self.assertTrue(os.path.isfile(".gitignore"))

    def test_second_init_does_not_touch_an_edited_gitignore(self):
        os.chdir(self.tmp)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_init(["--board", "Nexys-A7-50T"])
        with open(".gitignore", "a") as f:
            f.write("mine.local\n")
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_init(["--board", "Nexys-A7-50T"])
        self.assertIn("mine.local", open(".gitignore").read())

    def test_does_not_nest_inside_the_enclosing_repo(self):
        subprocess.run(["git", "init", "-q", self.tmp], check=True)
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        os.chdir(proj)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_init(["--board", "Nexys-A7-50T"])
        self.assertFalse(os.path.isdir(".git"))


class TestCmdAddmoduleWarnsAboutGitignore(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        anvil._warned_gitignore.clear()

    def _serve_fifo(self, name="fifo", filename="fifo.tar.gz"):
        _tar_with(self.tmp, [
            (f"pkg/module.json", _mod_meta(name, "1.0.0")),
            ("pkg/top.v", b"module fifo; endmodule"),
        ], name=filename)

    def test_warns_when_installing_external_module_without_gitignore(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            self._serve_fifo()
            with capture() as out:
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])
        self.assertIn("no .gitignore", out.getvalue())

    def test_no_warning_once_a_gitignore_is_present(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with open(".gitignore", "w") as f:
            f.write("external/\n")
        with serve(self.tmp) as base_url:
            self._serve_fifo()
            with capture() as out:
                anvil.cmd_addmodule(["--yes", f"{base_url}/fifo.tar.gz"])
        self.assertNotIn("no .gitignore", out.getvalue())

    def test_bundled_only_addmodule_never_warns(self):
        # no external/ write happens for a bundled module -- nothing to warn about yet
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with capture() as out, contextlib.suppress(SystemExit):
            anvil.cmd_addmodule(["uart"])
        self.assertNotIn("no .gitignore", out.getvalue())

    def test_declining_the_install_does_not_warn(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        real_stdin = sys.stdin
        with serve(self.tmp) as base_url:
            self._serve_fifo()
            sys.stdin = FakeTTY("n\n")
            try:
                with capture() as out:
                    anvil.cmd_addmodule([f"{base_url}/fifo.tar.gz"])
            finally:
                sys.stdin = real_stdin
        self.assertNotIn("no .gitignore", out.getvalue())


class TestEnsureModulesWarnsAboutGitignore(TempCase):
    """A fresh clone restoring external/ is the likeliest way to hit the missing-.gitignore case."""

    def setUp(self):
        TempCase.setUp(self)
        anvil._warned_gitignore.clear()

    def test_warns_when_ensure_modules_refetches_into_external(self):
        proj = _scaffold_project(self.tmp)
        srv  = os.path.join(self.tmp, "srv")
        os.makedirs(srv)
        m = _make_module(srv)
        expected = fetch.module_hash(m)
        _archive_of(srv, m)
        os.chdir(proj)
        with serve(srv) as base_url:
            cfg = {"schema": "2.0", "modules": {
                "m": {"version": "1.0.0", "source": f"{base_url}/a.tar.gz",
                      "path": os.path.join(anvil.EXTERNAL_DIR, "m@1.0.0"), "hash": expected}}}
            with capture() as out:
                anvil.ensure_modules(cfg)
        self.assertIn("no .gitignore", out.getvalue())

    def test_warns_once_across_addmodule_then_a_later_restore(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta("m", "1.0.0")),
                ("pkg/top.v", b"module m; endmodule"),
            ], name="m.tar.gz")
            with capture() as out1:
                anvil.cmd_addmodule(["--yes", f"{base_url}/m.tar.gz"])
            self.assertIn("no .gitignore", out1.getvalue())
            with open("config.json") as f:
                cfg = json.load(f)
            shutil.rmtree(anvil.EXTERNAL_DIR)   # simulate a fresh clone: git-ignored, so absent
            with capture() as out2:
                anvil.ensure_modules(cfg)
        self.assertNotIn("no .gitignore", out2.getvalue())   # same project -- already warned

    def test_a_module_present_locally_never_touches_external_and_never_warns(self):
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        d = _make_local_module(self.tmp, "m")
        digest = fetch.module_hash(d)
        cfg = {"schema": "2.0", "modules": {
            "m": {"version": "1.0.0", "source": "../m", "path": "../m", "hash": digest}}}
        with capture() as out:
            anvil.ensure_modules(cfg)   # already present -- external/ is never written
        self.assertEqual(out.getvalue(), "")


class TestScaffoldGitInitFailure(TempCase):
    def _fail_git_init(self, stderr="fatal: could not create work tree dir: Permission denied\n"):
        real_run = anvil.subprocess.run
        def faulty(cmd, *a, **k):
            if cmd[:2] == ["git", "init"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=stderr)
            return real_run(cmd, *a, **k)
        anvil.subprocess.run = faulty
        return real_run

    def test_failed_git_init_warns_but_gitignore_is_still_written(self):
        real_run = self._fail_git_init()
        try:
            with capture() as out:
                anvil.scaffold_git(self.tmp)
        finally:
            anvil.subprocess.run = real_run
        self.assertIn("git init failed", out.getvalue())
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, ".git")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, ".gitignore")))

    def test_cmd_init_still_completes_a_usable_project_when_git_init_fails(self):
        os.chdir(self.tmp)
        real_run = self._fail_git_init()
        try:
            with capture() as out, contextlib.suppress(SystemExit):
                anvil.cmd_init(["--board", "Nexys-A7-50T"])
        finally:
            anvil.subprocess.run = real_run
        self.assertIn("git init failed", out.getvalue())
        self.assertFalse(os.path.isdir(".git"))
        self.assertTrue(os.path.isfile("config.json"))
        self.assertTrue(os.path.isfile(".gitignore"))
        self.assertTrue(os.path.isfile("top.sv") or os.path.isfile("top.v"))


class TestAddmoduleAbsolutePath(TempCase):
    def test_absolute_path_is_treated_as_a_local_module(self):
        # a module directory outside the project, referenced absolutely
        mod = os.path.join(self.tmp, "mod")
        os.makedirs(mod)
        with open(os.path.join(mod, "module.json"), "w") as f:
            f.write('{"name":"absmod","version":"1.0.0"}')
        with open(os.path.join(mod, "absmod.v"), "w") as f:
            f.write("module absmod; endmodule\n")
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with capture():
            anvil.cmd_addmodule([mod, "--yes"])
        with open("config.json") as f:
            cfg = json.load(f)
        self.assertIn("absmod", cfg["modules"])
        self.assertEqual(cfg["modules"]["absmod"]["source"], mod)
        # path stays a cwd-relative alias -- like any other local module, it resolves from proj/
        self.assertEqual(os.path.normpath(os.path.join(proj, cfg["modules"]["absmod"]["path"])), mod)

    def test_absolute_path_walks_its_own_bundled_dependency(self):
        mod = os.path.join(self.tmp, "mod2")
        os.makedirs(mod)
        with open(os.path.join(mod, "module.json"), "w") as f:
            json.dump({"name": "absmod2", "version": "1.0.0", "depends": ["uart"]}, f)
        with open(os.path.join(mod, "top.v"), "w") as f:
            f.write("module absmod2; endmodule\n")
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_addmodule([mod, "--yes"])
        with open("config.json") as f:
            cfg = json.load(f)
        self.assertIn("absmod2", cfg["modules"])
        self.assertIn("uart", cfg["modules"])

    def test_absolute_path_without_module_json_is_a_sensible_error(self):
        # the old bug reported this as an unknown registry name -- it is a directory on disk
        empty = os.path.join(self.tmp, "not-a-module")
        os.makedirs(empty)
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with capture() as out:
            with self.assertRaises(SystemExit):
                anvil.cmd_addmodule([empty])
        text = out.getvalue()
        self.assertNotIn("Unknown module", text)
        self.assertIn("module.json not found", text)


class TestRemoveModuleDeletesFetchedDirectory(TempCase):
    def _add_fetched(self, proj, name, filename):
        os.chdir(proj)
        with serve(self.tmp) as base_url:
            _tar_with(self.tmp, [
                ("pkg/module.json", _mod_meta(name)),
                ("pkg/top.v", b"module m; endmodule"),
            ], name=filename)
            with capture():
                anvil.cmd_addmodule(["--yes", f"{base_url}/{filename}"])

    def test_a_fetched_module_is_removed_from_disk(self):
        proj = _scaffold_project(self.tmp)
        self._add_fetched(proj, "fifo", "fifo.tar.gz")
        with open("config.json") as f:
            path = json.load(f)["modules"]["fifo"]["path"]
        self.assertTrue(os.path.isdir(path))

        with capture():
            anvil.cmd_removemodule(["fifo"])

        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.isdir(anvil.EXTERNAL_DIR))   # the parent survives
        with open("config.json") as f:
            self.assertNotIn("fifo", json.load(f)["modules"])

    def test_a_local_path_module_is_never_deleted_from_disk(self):
        # its directory is the user's own, outside external/
        proj = _scaffold_project(self.tmp)
        mymod = _make_local_module(self.tmp, "mymod")
        os.chdir(proj)
        with capture():
            anvil.cmd_addmodule(["../mymod"])

        with capture():
            anvil.cmd_removemodule(["../mymod"])

        self.assertTrue(os.path.isdir(mymod))
        with open("config.json") as f:
            self.assertNotIn("mymod", json.load(f)["modules"])

    def test_a_bundled_module_is_never_deleted_from_disk(self):
        # it lives in the Anvil installation and is shared by every project
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_addmodule(["uart"])
        bundled_dir = os.path.join(anvil.MODULES_DIR, "uart@1.0.0")
        self.assertTrue(os.path.isdir(bundled_dir))

        # this sandbox has no sv2v toolchain; a SystemExit from build_makefile is not under test here (see TestModulesSchemaIntegration)
        with capture(), contextlib.suppress(SystemExit):
            anvil.cmd_removemodule(["uart"])

        self.assertTrue(os.path.isdir(bundled_dir))
        with open("config.json") as f:
            self.assertNotIn("uart", json.load(f)["modules"])

    def test_directory_already_gone_is_not_an_error(self):
        proj = _scaffold_project(self.tmp)
        self._add_fetched(proj, "fifo", "fifo.tar.gz")
        with open("config.json") as f:
            path = json.load(f)["modules"]["fifo"]["path"]
        shutil.rmtree(path)

        with capture():
            anvil.cmd_removemodule(["fifo"])

        with open("config.json") as f:
            self.assertNotIn("fifo", json.load(f)["modules"])

    def test_failed_deletion_is_a_warning_not_a_failure(self):
        proj = _scaffold_project(self.tmp)
        self._add_fetched(proj, "fifo", "fifo.tar.gz")
        real_rmtree = anvil.shutil.rmtree
        anvil.shutil.rmtree = lambda *a, **k: (_ for _ in ()).throw(OSError("simulated"))
        try:
            with capture() as out:
                anvil.cmd_removemodule(["fifo"])
        finally:
            anvil.shutil.rmtree = real_rmtree

        self.assertIn("WARN", out.getvalue())
        with open("config.json") as f:
            self.assertNotIn("fifo", json.load(f)["modules"])

    def test_one_failed_deletion_does_not_block_the_others(self):
        proj = _scaffold_project(self.tmp)
        self._add_fetched(proj, "fifo", "fifo.tar.gz")
        self._add_fetched(proj, "axi", "axi.tar.gz")
        with open("config.json") as f:
            cfg = json.load(f)
        fifo_path = os.path.abspath(cfg["modules"]["fifo"]["path"])
        axi_path  = os.path.abspath(cfg["modules"]["axi"]["path"])

        real_rmtree = anvil.shutil.rmtree
        def faulty(path, *a, **k):
            if os.path.abspath(path) == fifo_path:
                raise OSError("simulated")
            return real_rmtree(path, *a, **k)
        anvil.shutil.rmtree = faulty
        try:
            with capture() as out:
                anvil.cmd_removemodule(["fifo", "axi"])
        finally:
            anvil.shutil.rmtree = real_rmtree

        self.assertTrue(os.path.isdir(fifo_path))
        self.assertFalse(os.path.exists(axi_path))
        self.assertIn("WARN", out.getvalue())
        with open("config.json") as f:
            cfg2 = json.load(f)
        self.assertNotIn("fifo", cfg2["modules"])
        self.assertNotIn("axi", cfg2["modules"])

    def test_two_entries_sharing_a_directory_the_survivor_keeps_it(self):
        # a corrupted-by-hand config -- deleting must still never orphan a module that is staying
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        shared = os.path.join("external", "shared@1.0.0")
        os.makedirs(shared)
        with open(os.path.join(shared, "module.json"), "w") as f:
            json.dump({"name": "shared", "version": "1.0.0"}, f)
        with open("config.json") as f:
            cfg = json.load(f)
        cfg["modules"]["modA"] = {"version": "1.0.0", "source": "https://example.test/a.tar.gz",
                                   "path": shared, "hash": "sha256:aaaa"}
        cfg["modules"]["modB"] = {"version": "1.0.0", "source": "https://example.test/b.tar.gz",
                                   "path": shared, "hash": "sha256:bbbb"}
        with open("config.json", "w") as f:
            json.dump(cfg, f)

        with capture():
            anvil.cmd_removemodule(["modA"])

        self.assertTrue(os.path.isdir(shared))
        with open("config.json") as f:
            cfg2 = json.load(f)
        self.assertNotIn("modA", cfg2["modules"])
        self.assertIn("modB", cfg2["modules"])

    def test_a_symlinked_module_directory_pointing_outside_external_is_not_deleted(self):
        # a crafted or stale path must not delete whatever it resolves to outside external/
        proj = _scaffold_project(self.tmp)
        os.chdir(proj)
        real_target = os.path.join(self.tmp, "real-target")
        os.makedirs(real_target)
        with open(os.path.join(real_target, "keepme"), "w") as f:
            f.write("do not delete")
        os.makedirs("external")
        link = os.path.join("external", "evil@1.0.0")
        os.symlink(real_target, link)

        with open("config.json") as f:
            cfg = json.load(f)
        cfg["modules"]["evil"] = {"version": "1.0.0", "source": "https://example.test/evil.tar.gz",
                                   "path": link, "hash": "sha256:cccc"}
        with open("config.json", "w") as f:
            json.dump(cfg, f)

        with capture():
            anvil.cmd_removemodule(["evil"])

        self.assertTrue(os.path.isfile(os.path.join(real_target, "keepme")))
        self.assertTrue(os.path.islink(link))
        with open("config.json") as f:
            self.assertNotIn("evil", json.load(f)["modules"])


if __name__ == "__main__":
    unittest.main()
