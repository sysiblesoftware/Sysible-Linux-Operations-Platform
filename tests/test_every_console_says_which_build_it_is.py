"""Every console prints the commit it was built from.

Three rounds of "is this my browser, my server, or your code?" ended with the
answer being "that code was never deployed" — and there was no way to see that
from the screen. A SLOP console is server-rendered: the whole page comes out of
the container, so a stale image and a current one are indistinguishable from
outside, and the only evidence was noticing that a sentence I had deleted two
commits ago was still on the page.

SYSIBLE_REV is baked in at build time and shown in the footer. Unset — a bare
`docker compose up` — it says "version unknown" rather than inventing one.

Run: pytest tests/    (from the repo root)
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BUILT = ("idp", "flashback", "baseline", "visualizer", "updater")
CONSOLES = ("flashback", "baseline", "visualizer")


@pytest.mark.parametrize("app", BUILT)
def test_every_built_image_accepts_the_revision(app):
    """The arg has to exist in the Dockerfile or compose cannot pass it."""
    df = (REPO / app / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG SYSIBLE_REV", df, re.M), f"{app} takes no SYSIBLE_REV"
    assert re.search(r"^ENV SYSIBLE_REV=\$SYSIBLE_REV", df, re.M), \
        f"{app} never turns the build arg into an environment variable"


@pytest.mark.parametrize("app", BUILT)
def test_the_revision_is_the_last_thing_in_the_dockerfile(app):
    """Not cosmetic: an ENV above the COPY invalidates it, so every new commit
    would reinstall every dependency to change one string."""
    lines = [ln for ln in (REPO / app / "Dockerfile").read_text().splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    tail = lines[-1]
    assert tail.startswith("ENV SYSIBLE_REV"), \
        f"{app}'s revision is not the last layer; it is followed by {tail!r}"


@pytest.mark.parametrize("app", BUILT)
def test_compose_passes_it_to_every_build(app):
    import yaml
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    build = compose["services"][app]["build"]
    assert isinstance(build, dict), f"{app} has no build args at all"
    assert build["args"]["SYSIBLE_REV"] == "${SYSIBLE_SLOP_REV:-}", build


@pytest.mark.parametrize("app", CONSOLES)
def test_a_console_prints_the_build_it_is_running(app, app_module, monkeypatch):
    monkeypatch.setenv("SYSIBLE_REV", "deadbee")
    with app_module(app, "backend.build_info") as build_info:
        out = build_info.footer("Sysible X")
    assert "deadbee" in out, out
    assert "class=foot" in out, out


@pytest.mark.parametrize("app", CONSOLES)
def test_an_unstamped_build_says_so_rather_than_guessing(app, app_module, monkeypatch):
    monkeypatch.delenv("SYSIBLE_REV", raising=False)
    with app_module(app, "backend.build_info") as build_info:
        out = build_info.footer("Sysible X")
    assert "version unknown" in out, out
    assert "build " not in out, f"it claims a build it does not have: {out}"


def test_the_updater_stamps_what_it_builds():
    """It is the thing that runs `compose up --build` on an operator's behalf,
    so if it does not pass the revision the console it just rebuilt still
    cannot say what it is."""
    src = (REPO / "updater" / "backend" / "jobs.py").read_text(encoding="utf-8")
    assert "SYSIBLE_SLOP_REV" in src, "the updater builds without stamping anything"
    assert "rev-parse" in src, "it never asks git what the revision is"


def test_the_cli_stamps_what_it_builds():
    ctl = REPO.parent / "Sysible-Controller" / "deploy" / "sysiblectl"
    if not ctl.exists():
        pytest.skip("the Controller checkout is not alongside this one")
    src = ctl.read_text(encoding="utf-8")
    assert src.count("SYSIBLE_SLOP_REV=") >= 2, \
        "sysiblectl has two `compose up --build` sites and must stamp both"


def test_the_portal_asks_the_idp_which_build_it_is():
    """The portal is static files on a bind mount — it has no image of its own
    to stamp, so it is the one surface that has to ask."""
    js = (REPO / "portal" / "app.js").read_text(encoding="utf-8")
    html = (REPO / "portal" / "index.html").read_text(encoding="utf-8")
    assert 'id="rev"' in html, "the portal footer has nowhere to put it"
    assert "me.rev" in js, "the portal never reads the revision from /auth/me"
    idp = (REPO / "idp" / "app.py").read_text(encoding="utf-8")
    assert '"rev": _REV' in idp, "/auth/me does not report the build"
