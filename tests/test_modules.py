import contextlib, io, os
import shutil, sys, tarfile, tempfile, unittest, zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch                                                    # noqa: E402
import anvil                                                    # noqa: E402

@contextlib.contextmanager
def capture():
    """Collect stdout as a string -- the stand-in for pytest's capsys."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf

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


if __name__ == "__main__":
    unittest.main()
