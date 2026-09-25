"""install.sh's env-file writer, driven for real.

Found by functional testing: `_upsert_kv` replaced an existing line with

    sed "s|^$KEY=.*|$KEY=$VALUE|"

which reads the VALUE as part of a sed replacement. The values that go through it
are filesystem paths and URLs, where every one of sed's metacharacters is
perfectly legal:

    /opt/R&D/slop      `&` means "the whole match"  -> /opt/R<the whole line>D/slop
    /opt/a|b/slop      `|` closed the expression    -> sed errored and the OLD
                                                       value was silently kept
    /opt/back\\slash    the backslash was eaten

Only on the REPLACE branch, so a first install looked fine and the corruption
appeared on the re-run — including to SYSIBLE_SLOP_DIR, the checkout
Administration updates SLOP from.

These tests extract the shipping shell function and run it. What matters is that
a value comes back out byte-for-byte, so that is what they assert, one awkward
value at a time.

Run: pytest tests/    (from the repo root)
"""
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"


@pytest.fixture(scope="module")
def upsert_fn():
    """The real _upsert_kv, lifted out of install.sh."""
    src = INSTALL.read_text()
    start = src.index("_upsert_kv() {")
    depth, i = 0, start
    while True:                       # walk to the matching close brace
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return src[start:i + 1]


def run_sh(script, cwd):
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                       cwd=cwd, timeout=60)
    return r


AWKWARD = [
    ("/opt/slop", "an ordinary path"),
    ("/opt/R&D/slop", "& is sed's 'whole match'"),
    ("/opt/a|b/slop", "| closes a sed s||| expression"),
    ("/opt/back\\slash", "a backslash is an escape to sed and to awk -v"),
    ("https://host/x?a=1&b=2", "a URL with a query string"),
    ("/opt/with space/slop", "a space"),
    ("/opt/#hash", "a comment character"),
    ("/opt/'quote", "a single quote"),
    ('/opt/"dquote', "a double quote"),
]


@pytest.mark.parametrize("value,why", AWKWARD)
def test_a_value_survives_being_rewritten(upsert_fn, tmp_path, value, why):
    """The REPLACE branch — the one a re-install takes."""
    env = tmp_path / ".env"
    script = textwrap.dedent(f"""
        {upsert_fn}
        _upsert_kv '{env}' OTHER_KEY untouched
        _upsert_kv '{env}' SYSIBLE_SLOP_DIR /first/run
        _upsert_kv '{env}' SYSIBLE_SLOP_DIR "$1"
    """) + "\n"
    r = subprocess.run(["sh", "-s", value], input=script, capture_output=True,
                       text=True, cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stderr

    lines = env.read_text().splitlines()
    got = [l[len("SYSIBLE_SLOP_DIR="):] for l in lines if l.startswith("SYSIBLE_SLOP_DIR=")]
    assert got == [value], f"{why}: wrote {got!r}, expected [{value!r}]"
    assert "OTHER_KEY=untouched" in lines, "rewriting one key disturbed another"


def test_a_new_key_is_appended(upsert_fn, tmp_path):
    env = tmp_path / ".env"
    r = run_sh(f"{upsert_fn}\n_upsert_kv '{env}' NEWKEY firstvalue", tmp_path)
    assert r.returncode == 0, r.stderr
    assert env.read_text().splitlines() == ["NEWKEY=firstvalue"]


def test_the_file_is_created_private(upsert_fn, tmp_path):
    """It holds the SSO shared secret, which is the whole app-trust boundary."""
    env = tmp_path / ".env"
    r = run_sh(f"umask 022\n{upsert_fn}\n_upsert_kv '{env}' SECRET abc123", tmp_path)
    assert r.returncode == 0, r.stderr
    assert oct(env.stat().st_mode)[-3:] == "600", \
        "the env file is readable by other local users"


def test_no_temp_file_is_left_behind(upsert_fn, tmp_path):
    env = tmp_path / ".env"
    run_sh(f"{upsert_fn}\n_upsert_kv '{env}' A 1\n_upsert_kv '{env}' A 2", tmp_path)
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert not leftovers, f"left a temp file holding the secret: {leftovers}"


def test_the_rewrite_never_goes_through_sed_again(upsert_fn):
    """The specific mistake, named so it cannot come back: the value must not be
    interpolated into a sed (or awk -v) expression. awk -v is no fix either — it
    processes escape sequences, so the backslash case stays broken."""
    assert "sed" not in upsert_fn, \
        "the value is being interpolated into a sed expression again"
    assert "ENVIRON" in upsert_fn, \
        "the value should reach awk through the environment, not the expression"
    assert not re.search(r"awk\s+-v\s+\w*v=", upsert_fn), \
        "awk -v processes escapes in the value; use ENVIRON"


def test_install_sh_is_valid_posix_sh():
    r = subprocess.run(["sh", "-n", str(INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
