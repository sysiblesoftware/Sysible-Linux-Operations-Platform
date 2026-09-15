"""The IdP ships its console JavaScript inside Python strings — parse it.

Administration is server-rendered HTML with several hundred lines of JS embedded
in module-level string constants. Nothing checked that JS: Python imports the
module happily whatever is inside the quotes, every test passes, the page renders,
and the browser silently drops the whole <script> on a syntax error. The symptom
is a console where nothing is clickable and the network tab is empty — with no
error anywhere on the server.

Two checks, deliberately: `node --check` is the real parser and runs wherever node
exists; the bracket balance is dependency-free and always runs, so a machine
without node still catches the mistake that actually happens when these strings
are edited (a block closed one brace too many or too few).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app.py")


def _js_constants():
    """Every module-level `_..._JS = r?\"\"\"…\"\"\"` block, by name."""
    src = open(APP, encoding="utf-8").read()
    found = dict(re.findall(r'^(_[A-Z0-9_]*JS) = r?"""(.*?)"""', src, re.S | re.M))
    return found


@pytest.fixture(scope="module")
def blocks():
    found = _js_constants()
    assert found, "no embedded JS constants found — this test would pass vacuously"
    return found


def test_the_console_javascript_is_found(blocks):
    assert "_UPDATES_JS" in blocks, \
        "the Software & services page's script is the largest embedded block"


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_every_embedded_block_parses(blocks):
    for name, js in blocks.items():
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            assert r.returncode == 0, f"{name} is not valid JavaScript:\n{r.stderr}"
        finally:
            os.unlink(path)


@pytest.mark.parametrize("pair", ["{}", "()", "[]"])
def test_brackets_balance_in_every_block(blocks, pair):
    """Dependency-free backstop for the mistake these strings actually invite."""
    open_c, close_c = pair
    for name, js in blocks.items():
        stripped = _without_literals(js)
        depth = 0
        for ch in stripped:
            if ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                assert depth >= 0, f"{name}: a '{close_c}' with no matching '{open_c}'"
        assert depth == 0, f"{name}: {depth} unclosed '{open_c}'"


def _without_literals(js: str) -> str:
    """Drop strings, template literals, regex-ish slashes and comments, so brackets
    inside them are not counted. Crude on purpose — it only has to be balanced."""
    out, i, n = [], 0, len(js)
    while i < n:
        c = js[i]
        if c in "\"'`":
            quote, i = c, i + 1
            while i < n and js[i] != quote:
                i += 2 if js[i] == "\\" else 1
            i += 1
        elif c == "/" and i + 1 < n and js[i + 1] == "/":
            while i < n and js[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and js[i + 1] == "*":
            i = js.find("*/", i + 2)
            i = n if i < 0 else i + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)
