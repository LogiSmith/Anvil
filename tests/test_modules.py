import contextlib, functools, http.server, io, json, os, socket
import shutil, socketserver, struct, sys, tarfile, tempfile, threading, unittest, zipfile

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


if __name__ == "__main__":
    unittest.main()
