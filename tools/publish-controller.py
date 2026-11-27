#!/usr/bin/env python3
"""Publish SLOP's vendored Controller out to the standalone Sysible-Controller repo.

Decided: "only work in slop publish outward". SLOP's apps/controller is the source;
the standalone repo is a published copy. Before this existed the two were kept in
step by hand, which is to say not kept in step — a feature landed in one and was
missing from the other, and the only way to find out was for somebody to hit the
missing half. It has happened three times in one day.

    tools/publish-controller.py /path/to/Sysible-Controller [--check]

--check reports what WOULD change and exits non-zero if anything would, so it can
run as a drift alarm without touching the target.

No rsync: it is not installed everywhere (including the container this was written
in), and a publish step that depends on a tool the machine may not have is a
publish step that silently does not run.
"""
import argparse
import filecmp
import os
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "apps" / "controller"

# NEVER published. Build output, caches, and per-machine runtime state — a
# vendored copy of somebody's sysible.db or controller_secret.key is at best noise
# and at worst a leaked secret.
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
             "dist", "run", ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".key", ".db"}
SKIP_NAMES = {".env", ".DS_Store"}

# The standalone repo legitimately owns these. They are NOT drift:
#   README.md         carries the MIT licence pointer; SLOP's copy has no LICENSE
#                     file beside it to point at.
#   requirements.txt  same pins, different comment — each explains the pin in the
#                     terms of its own repo's CI history. The PINS are compared.
#   deploy/sysiblectl the standalone copy has no `first-run` verb, because that
#                     verb runs SLOP's install.sh and this repo has no install.sh;
#                     it points at the SLOP repo instead. Changes to it are ported
#                     by hand, and the test suite checks both copies carry them.
#   deploy/sysible_ctl is a symlink to sysiblectl in both.
OWNED_BY_STANDALONE = {"README.md", "requirements.txt",
                       "deploy/sysiblectl", "deploy/sysible_ctl"}


def publishable(root: Path):
    """Every file under root that should be published, as paths relative to it."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if name in SKIP_NAMES or p.suffix in SKIP_SUFFIX:
                continue
            if rel in OWNED_BY_STANDALONE:
                continue
            yield rel


def _pins(path: Path):
    """requirements.txt without comments or blank lines — the part that must match."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line.replace(" ", ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="path to the standalone Sysible-Controller checkout")
    ap.add_argument("--check", action="store_true",
                    help="report what would change and exit non-zero if anything would")
    a = ap.parse_args()

    dst = Path(a.target).resolve()
    if not (dst / ".git").exists():
        print("refusing: %s is not a git checkout" % dst, file=sys.stderr)
        return 2

    changed, added = [], []
    for rel in publishable(SRC):
        s, d = SRC / rel, dst / rel
        if not d.exists():
            added.append(rel)
        elif not filecmp.cmp(s, d, shallow=False):
            changed.append(rel)

    # Pins must match even though the comments around them need not: a vendored
    # copy of a vulnerable pin is still a vulnerable pin.
    rc = 0
    sp, dp = _pins(SRC / "requirements.txt"), _pins(dst / "requirements.txt")
    if sp != dp:
        print("DEPENDENCY PINS DIFFER between the two requirements.txt:", file=sys.stderr)
        for line in sorted(set(sp) ^ set(dp)):
            print("   %s" % line, file=sys.stderr)
        print("   Reconcile by hand — this file is owned by the standalone repo.",
              file=sys.stderr)
        rc = 1

    if a.check:
        if added or changed:
            print("The published Controller is behind SLOP:")
            for rel in added:
                print("   new      %s" % rel)
            for rel in changed:
                print("   changed  %s" % rel)
            return 1
        if rc:
            return rc
        print("Published Controller is in step with SLOP.")
        return 0

    for rel in added + changed:
        s, d = SRC / rel, dst / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        print("   %s %s" % ("added  " if rel in added else "updated", rel))
    if not (added or changed):
        print("Nothing to publish — already in step.")
    else:
        print("Published %d file(s) to %s" % (len(added) + len(changed), dst))
    print("Owned by the standalone repo, left alone: %s"
          % ", ".join(sorted(OWNED_BY_STANDALONE)))
    print("Review and commit in %s" % dst)
    return rc


if __name__ == "__main__":
    sys.exit(main())
