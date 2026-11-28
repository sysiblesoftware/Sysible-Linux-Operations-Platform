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

# The three SPAs were left out of this for a long time, and that is precisely
# where it hurt: they are the apps an operator works in. A fix that had shipped
# four days earlier was reported missing, and a Terraform apply was traced to a
# DIFFERENT SLEP than the one being inspected — neither of which any screen could
# have told anybody. They build from their own paths, hence the map.
SPAS = {
    "slep": "apps/slep/deploy/Dockerfile",
    "connect": "apps/connect/deploy/Dockerfile",
    "controller": "apps/controller/Dockerfile",
}


def _dockerfile(app):
    return REPO / SPAS.get(app, "%s/Dockerfile" % app)


@pytest.mark.parametrize("app", BUILT + tuple(SPAS))
def test_every_built_image_accepts_the_revision(app):
    """The arg has to exist in the Dockerfile or compose cannot pass it."""
    df = _dockerfile(app).read_text(encoding="utf-8")
    assert re.search(r"^ARG SYSIBLE_REV", df, re.M), f"{app} takes no SYSIBLE_REV"
    assert re.search(r"^ENV SYSIBLE_REV=\$SYSIBLE_REV", df, re.M), \
        f"{app} never turns the build arg into an environment variable"


@pytest.mark.parametrize("app", BUILT + tuple(SPAS))
def test_the_revision_is_the_last_thing_in_the_dockerfile(app):
    """Not cosmetic: an ENV above the COPY invalidates it, so every new commit
    would reinstall every dependency to change one string."""
    lines = [ln for ln in _dockerfile(app).read_text().splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    tail = lines[-1]
    assert tail.startswith("ENV SYSIBLE_REV"), \
        f"{app}'s revision is not the last layer; it is followed by {tail!r}"


@pytest.mark.parametrize("app", BUILT + tuple(SPAS))
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
        out = build_info.chip()
    assert "deadbee" in out, out
    assert "class=rev" in out, out


@pytest.mark.parametrize("app", CONSOLES)
def test_an_unstamped_build_says_so_rather_than_guessing(app, app_module, monkeypatch):
    monkeypatch.delenv("SYSIBLE_REV", raising=False)
    with app_module(app, "backend.build_info") as build_info:
        out = build_info.chip()
    assert "version unknown" in out, out
    assert "build " not in out, f"it claims a build it does not have: {out}"


# ---- where it goes ---------------------------------------------------------
@pytest.mark.parametrize("app", CONSOLES)
def test_the_build_is_header_chrome_not_a_line_in_the_page(app):
    """Reported: "why is the product name in the middle of the page".

    The first attempt appended a footer after the body. Three things were wrong
    with that at once. The product name is already in the header, so the page
    said its own name twice. The line sat OUTSIDE the centred content column,
    hard against the left edge, in text the size of the content — so it read as
    content, not chrome. And Flashback's .wrap is height:calc(100vh - 49px), so
    anything after it is permanently below the fold.
    """
    src = (REPO / app / "backend" / "ui.py").read_text(encoding="utf-8")
    assert "build_info.chip()" in src, f"{app} does not show the build at all"
    assert "build_info.footer(" not in src, \
        f"{app} is back to appending a footer after the body"
    # In the header element, beside the signed-in user.
    assert "{build_info.chip()}</header>" in src, \
        f"{app} puts the build somewhere other than the header"


@pytest.mark.parametrize("app", CONSOLES)
def test_the_chip_is_styled_wherever_it_is_rendered(app):
    """An unstyled span inherits the body's size and colour, which is how the
    first version came out looking like a stray sentence."""
    src = (REPO / app / "backend" / "ui.py").read_text(encoding="utf-8")
    assert "build_info.CSS" in src, \
        f"{app}'s stylesheet does not carry the chip's rule, so it renders bare"


def test_every_console_styles_it_the_same_way():
    """Three consoles, three stylesheets, one piece of chrome. They drift the
    moment each owns a copy of the rule."""
    css = set()
    for app in CONSOLES:
        bi = (REPO / app / "backend" / "build_info.py").read_text(encoding="utf-8")
        assert "CSS = (" in bi, f"{app} has no shared chip rule"
        css.add(bi)
    assert len(css) == 1, "the consoles' build_info modules have drifted apart"


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


# ---------------------------------------------------------------------------
# THE SPAs MUST ACTUALLY ASK, AND ACTUALLY SHOW.
#
# Everything above this line checks the build is AVAILABLE: the Dockerfile takes
# the revision, build_info reports it, the console's ui.py renders a chip. For
# the three React consoles that covered the Dockerfile and the endpoint and
# stopped there — so Connect served /version from the day the feature shipped
# and no screen ever asked for it. SLEP and the Controller both displayed
# theirs; Connect alone was silent, and every test was green.
#
# That is the same gap this codebase keeps hitting: a symbol being present in
# the source is not the same as it being reached. Found by a QA sweep looking
# for routes nothing in the UI references.
_SPA_SRC = {
    "slep": "apps/slep/webgui/frontend/src",
    "connect": "apps/connect/webgui/frontend/src",
    "controller": "apps/controller/webgui/frontend/src",
}


def _spa_text(app):
    root = REPO / _SPA_SRC[app]
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in root.rglob("*.js*"))


@pytest.mark.parametrize("app", sorted(_SPA_SRC))
def test_the_spa_asks_its_backend_which_build_it_is(app):
    """Serving /version is half a feature. Something has to call it."""
    text = _spa_text(app)
    assert re.search(r"""api\(\s*['"]version['"]|/api/version|controllerVersion""", text), (
        "%s never asks its backend which build it is, so the endpoint it serves "
        "is answered to nobody" % app)


# What "rendered" means for each, because they render it differently: SLEP and
# Connect put a chip in the chrome, the Controller prints it in Settings. Named
# per app so the assertion is about JSX that is USED, not a component that merely
# exists — the first version of this test passed against a BuildChip that had
# been defined, renamed and never placed, which is the exact mistake it was
# written to catch.
_SPA_RENDERS = {
    "slep": r"<BuildChip\b",
    "connect": r"<BuildChip\b",
    # the OUTER guard, not an inner expression: `{ver.version && ...}` survives
    # inside a block whose own condition has been turned off, so matching it
    # would pass against a build nothing can display.
    "controller": r"\{ver && \(",
}


@pytest.mark.parametrize("app", sorted(_SPA_SRC))
def test_the_spa_renders_what_it_asked_for(app):
    """...and something has to put the answer on SCREEN. Fetching it into a
    state variable nothing reads is the same silence with more code."""
    text = _spa_text(app)
    assert re.search(_SPA_RENDERS[app], text), (
        "%s fetches its build but no JSX renders it" % app)
