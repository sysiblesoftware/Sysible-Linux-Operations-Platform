"""`x = get("A") or get("A")` — a fallback to the thing it just read.

This has now happened four times in this codebase, and every one of them looked
like a working compatibility shim:

    _FLASHBACK = (os.getenv("SLOP_FLASHBACK_UPSTREAM")
                 or os.getenv("SLOP_FLASHBACK_UPSTREAM")   # <- same name
                 or "flashback:8080")

with a comment above it saying "new name first, pre-rebrand name honoured". Both
names were the same one. The second read could only ever return what the first
had already returned, so an operator who had pinned the old variable got the
default and no warning — and the code, the comment and the manual all agreed
with each other about a thing that was not happening.

It is invisible in review because the two sides are not adjacent on the line and
the names differ only in the middle. It is trivial to see in an AST.

Run: pytest tests/    (from the repo root)
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
APPS = ("baseline", "flashback", "visualizer", "updater", "idp")


def _sources():
    out = []
    for app in APPS:
        out += sorted((REPO / app).rglob("*.py"))
    return [p for p in out if "__pycache__" not in p.parts and "tests" not in p.parts]


def _same(a, b) -> bool:
    """Whether two expressions are the same expression, textually."""
    try:
        return ast.dump(a) == ast.dump(b)
    except Exception:                                    # pragma: no cover
        return False


FILES = _sources()


def test_there_are_sources_to_scan():
    assert len(FILES) > 10, f"only {len(FILES)} files found — this would pass vacuously"


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_nothing_falls_back_to_itself(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        for i, left in enumerate(node.values):
            for right in node.values[i + 1:]:
                # A bare name repeated in an `or` chain is usually harmless
                # (`a or a` is odd but not a lie about configuration). What
                # this is for is a repeated CALL or SUBSCRIPT — a second read
                # of the same key, dressed as an alternative to the first.
                if isinstance(left, (ast.Call, ast.Subscript)) and _same(left, right):
                    bad.append((getattr(node, "lineno", "?"), ast.unparse(left)))
    assert not bad, (
        f"{path.relative_to(REPO)} falls back to the value it just read: "
        + "; ".join(f"line {ln}: `{src} or {src}`" for ln, src in bad))


def test_a_dict_does_not_set_the_same_key_twice():
    """The same mistake in the other shape: the second value silently wins and
    the first line of the pair is dead."""
    bad = []
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            seen = {}
            for key in node.keys:
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if key.value in seen:
                    bad.append(f"{path.relative_to(REPO)}:{key.lineno} {key.value!r}")
                seen[key.value] = True
    assert not bad, "a dict literal sets the same key twice: " + ", ".join(bad)
