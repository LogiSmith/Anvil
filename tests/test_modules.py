import contextlib, io, os
import shutil, sys, tempfile, unittest

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
        # A flat chain of allowed operators parses as a deeply nested BinOp
        # tree; without a node-count bound this blows the recursion stack
        # instead of raising ValueError.
        with self.assertRaises(ValueError):
            fetch.eval_arith("+".join(["1"] * 1000), {})


class TestEvalDefsyms(TempCase):
    """anvil.eval_defsyms must turn a bad expression into a clean exit via
    fail(), never a raised exception or a raw traceback -- including for the
    long-chain input that used to escape eval_arith as a RecursionError."""

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


if __name__ == "__main__":
    unittest.main()
