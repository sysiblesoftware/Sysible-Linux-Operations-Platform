"""SLOP's Controller is the source; the standalone repo is a published copy.

Decided: "only work in slop publish outward". Before there was a publish step the
two trees were kept in step by hand, which is to say not kept in step — a feature
landed in one and was missing from the other, and the only way to find out was for
somebody to hit the missing half. That happened three times in a single day: the
airgap guards, a whole set of test files, and the Copy buttons.

These tests drive tools/publish-controller.py against a throwaway target, so they
do not need the standalone checkout to be present and never touch it.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "publish-controller.py"
SRC = ROOT / "apps" / "controller"


def _run(target, *args):
    p = subprocess.run([sys.executable, str(TOOL), str(target), *args],
                       capture_output=True, text=True, timeout=300)
    return p


@pytest.fixture()
def target(tmp_path):
    """A pretend standalone checkout, seeded with a full publish."""
    t = tmp_path / "Sysible-Controller"
    (t / ".git").mkdir(parents=True)
    # the files the standalone repo owns have to exist for the pin check
    (t / "requirements.txt").write_text((SRC / "requirements.txt").read_text(encoding="utf-8"),
                                        encoding="utf-8")
    _run(t)
    return t


def test_the_tool_exists_and_runs():
    assert TOOL.exists(), "there is no publish step, so the trees are kept in step by hand"
    assert _run(Path("/nonexistent")).returncode == 2


def test_it_refuses_a_target_that_is_not_a_checkout(tmp_path):
    p = _run(tmp_path)
    assert p.returncode == 2
    assert "not a git checkout" in p.stderr


def test_a_fresh_target_is_brought_fully_up_to_date(target):
    p = _run(target, "--check")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "in step" in p.stdout


def test_a_changed_file_is_detected_and_republished(target):
    victim = target / "webgui" / "frontend" / "src" / "components" / "CopyButton.jsx"
    assert victim.exists(), "the publish did not copy the frontend"
    victim.write_text(victim.read_text(encoding="utf-8") + "\n// drifted\n", encoding="utf-8")
    chk = _run(target, "--check")
    assert chk.returncode == 1, "drift went unnoticed"
    assert "CopyButton.jsx" in chk.stdout
    assert _run(target).returncode == 0
    assert "drifted" not in victim.read_text(encoding="utf-8")


def test_a_file_missing_downstream_is_detected(target):
    victim = target / "webgui" / "frontend" / "src" / "components" / "CopyButton.jsx"
    victim.unlink()
    chk = _run(target, "--check")
    assert chk.returncode == 1
    assert "new" in chk.stdout and "CopyButton.jsx" in chk.stdout


def test_check_changes_nothing(target):
    victim = target / "webgui" / "frontend" / "src" / "components" / "CopyButton.jsx"
    victim.write_text("drifted", encoding="utf-8")
    _run(target, "--check")
    assert victim.read_text(encoding="utf-8") == "drifted", "--check wrote to the target"


# ------------------------------------------------- what must never be published

def test_runtime_state_is_never_published(tmp_path):
    """A vendored copy of somebody's sysible.db or controller_secret.key is at best
    noise and at worst a leaked secret."""
    sys.path.insert(0, str(ROOT / "tools"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("pubctl", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    published = set(mod.publishable(SRC))
    for forbidden in ("backend/sysible.db", "run/controller_secret.key"):
        assert forbidden not in published, "%s would be published" % forbidden
    for rel in published:
        assert not rel.startswith("run/"), rel
        assert "__pycache__" not in rel and "node_modules" not in rel, rel
        assert not rel.endswith((".pyc", ".key", ".db", ".log")), rel
        assert "/dist/" not in rel and not rel.startswith("dist/"), rel


def test_the_frontend_and_the_tests_are_published(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("pubctl2", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    published = set(mod.publishable(SRC))
    assert any(r.startswith("webgui/frontend/src/") for r in published)
    assert any(r.startswith("tests/") for r in published)
    assert any(r.startswith("backend/") and r.endswith(".py") for r in published)


# ------------------------------------- what the standalone repo legitimately owns

def test_the_owned_files_are_real_and_explained():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pubctl3", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src_text = TOOL.read_text(encoding="utf-8")
    for rel in mod.OWNED_BY_STANDALONE:
        assert (SRC / rel).exists() or (SRC / rel).is_symlink(), (
            "%s is listed as owned downstream but does not exist upstream" % rel)
        assert rel.split("/")[-1] in src_text, (
            "%s is excluded from publishing with no comment saying why" % rel)


def test_the_dependency_pins_must_still_match(target):
    """Each repo may explain its pin in its own terms; a vendored copy of a
    vulnerable pin is still a vulnerable pin."""
    req = target / "requirements.txt"
    req.write_text(req.read_text(encoding="utf-8").replace("cryptography", "cryptografy"),
                   encoding="utf-8")
    p = _run(target, "--check")
    assert p.returncode == 1
    assert "PINS DIFFER" in p.stderr


def test_a_comment_only_difference_is_tolerated(target):
    req = target / "requirements.txt"
    req.write_text("# a different explanation entirely\n" + req.read_text(encoding="utf-8"),
                   encoding="utf-8")
    p = _run(target, "--check")
    assert p.returncode == 0, p.stdout + p.stderr
