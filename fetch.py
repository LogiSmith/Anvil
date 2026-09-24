"""Module sourcing for Anvil: expression evaluation, archives, hashing.

Deliberately free of CLI and config concerns so it can be tested without a
project or a network.
"""

import ast
import operator

MAX_SHIFT = 64          # a shift past this is a memory bomb, not a build parameter

_BINOPS = {
    ast.Add: operator.add,   ast.Sub: operator.sub,   ast.Mult: operator.mul,
    ast.Div: operator.floordiv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,   ast.LShift: operator.lshift,
    ast.RShift: operator.rshift, ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_, ast.BitXor: operator.xor,
}
_UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert}

def eval_arith(expr, names):
    """Evaluate an integer arithmetic expression over `names`.

    No eval(): only integer literals, the given names, and the operators
    + - * / // % << >> & | ^ ~ with parentheses. `**` is excluded on purpose --
    it turns a one-line config value into a memory bomb.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"not a valid expression: {e.msg}")

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
