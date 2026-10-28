"""What a sweep over every console's controls turned up.

124 controls were clicked across the four app consoles and the five
Administration pages. Zero threw a JavaScript error, which is the good news.
These are the things that were wrong anyway — each one invisible from the
outside, because a control that is merely USELESS looks exactly like a control
that works until you press it.

Run: pytest tests/    (from the repo root)
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


# ---- the update count, written to an element that no longer existed --------
def test_the_updates_page_sets_the_count_on_the_tab_it_has_the_data_for():
    """paint() wrote it to #updpill — the standalone pill REPLACED by the tab
    badge two commits earlier. So on the one page that already has this number,
    nothing was set, and pressing Check now never moved it."""
    src = (REPO / "idp" / "app.py").read_text(encoding="utf-8")
    # The REFERENCE, not the word: the comment above the fix names the element
    # it used to write to, so `"updpill" not in src` matches that prose and
    # fails on the corrected file. Four assertions in this repo have already
    # been satisfied — or broken — by comment text.
    assert "getElementById('updpill')" not in src, \
        "the removed pill is being written to again"
    assert "querySelector('b')" not in src or "updn" in src
    m = re.search(r"function paint\(d\)\{.*?\n  \}", src, re.S)
    assert m, "paint() is gone"
    assert "setUpdCount(" in m.group(0), \
        "paint() does not set the badge, so the page it is on never updates it"
    assert "getElementById('updn')" in src, "nothing writes to the badge at all"


@pytest.mark.parametrize("page", ["admin", "admin/apps", "admin/settings", "admin/updates"])
def test_every_admin_page_shows_the_update_count(page):
    """It appeared on two of the four and nobody could see the rule. The page
    that HAS the data sets it from what it is painting; the others poll."""
    import ast
    src = (REPO / "idp" / "app.py").read_text(encoding="utf-8")
    fn = {"admin": "_admin_page", "admin/apps": "_apps_page",
          "admin/settings": "_config_page", "admin/updates": "_updates_page"}[page]
    # ast, not a slice between markers. Slicing from `def X(` to the next
    # `@app.` overlapped: _config_page is followed by two more defs before the
    # next decorator, so its "body" ran on through _apps_page and every page
    # looked like it polled. Deleting the poll from the Apps page did not fail
    # this test until it was parsed properly.
    node = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == fn)
    body = ast.unparse(node)
    has_poll = "_PILL_POLL" in body
    has_paint = "_UPDATES_JS" in body
    assert has_poll or has_paint, \
        f"{page} neither polls the count nor paints it — its badge stays empty"


# ---- a button that could never work ----------------------------------------
def test_the_log_button_is_only_offered_where_there_is_a_log(app_module):
    """Shown for all five apps; only SLEP and the Controller expose one, so on
    the other three it failed every time with "this app exposes no log
    endpoint". The list that drives the button is the list that answers it."""
    with app_module("visualizer", "backend.sources") as sources:
        assert set(sources.LOG_APPS) == {"slep", "controller"}, sources.LOG_APPS
        for app in sources.app_keys():
            if not sources.has_log(app):
                _, err = sources.fetch_log(app, None, "")
                assert err, f"{app} is not offered a log but fetch_log does not refuse"
    ui = (REPO / "visualizer" / "backend" / "ui.py").read_text(encoding="utf-8")
    assert "lb.hidden=!(meta&&meta.log)" in ui, \
        "the console shows the Log button without asking whether there is one"
    app_py = (REPO / "visualizer" / "backend" / "app.py").read_text(encoding="utf-8")
    assert '"log": sources.has_log(k)' in app_py, \
        "/api/apps does not tell the console which apps have a log"


# ---- an app the Visualizer claimed to cover and did not --------------------
def test_the_visualizer_can_read_baseline(app_module):
    """Its own description and the portal tile both say "every app ... Flashback
    and Baseline". Baseline was not in APPS at all — so the one app that CHANGES
    hosts was the one app whose work could not be read here."""
    with app_module("visualizer", "backend.sources") as sources:
        assert "baseline" in sources.APPS, sorted(sources.APPS)
        assert sources.APPS["baseline"][0] == "Sysible Baseline"
    portal = (REPO / "portal" / "index.html").read_text(encoding="utf-8")
    assert "Baseline" in portal, "the portal blurb no longer mentions it either"


def test_a_baseline_run_becomes_a_readable_event(app_module):
    runs = [
        {"id": 1, "host_id": "web-01", "state_name": "web", "state_version": 2,
         "started": 1700000000, "changed": 3, "failed": 0, "error": None, "preview": 0},
        {"id": 2, "host_id": "db-01", "state_name": "db", "state_version": 1,
         "started": 1700000001, "changed": 0, "failed": 1,
         "error": "could not finish", "preview": 0},
        {"id": 3, "host_id": "web-02", "state_name": "web", "state_version": 2,
         "started": 1700000002, "changed": 0, "failed": 0, "error": None, "preview": 1},
    ]
    with app_module("visualizer", "backend.sources") as sources:
        sources._get = lambda *a, **k: ({"runs": runs}, None)
        out = sources._baseline(None, 50)
    acts = [e["action"] for e in out["events"]]
    assert acts == ["baseline.changed", "baseline.failed", "baseline.preview"], acts
    assert "nothing applied" in out["events"][2]["detail"], out["events"][2]
    assert out["events"][0]["target"] == "web-01"


# ---- a set that protected nothing ------------------------------------------
def test_baselines_own_work_is_automation_not_a_person(app_module):
    """_SERVICE_ACTORS held "flashback" twice, under a comment saying one was an
    old spelling kept so history stayed classified. Both were the same name, so
    it protected nothing — and the name the rebrand actually used, "baseline",
    was missing. An actor this set does not recognise is taken to be a PERSON,
    so every event Baseline logs was being filed against a human."""
    with app_module("visualizer", "backend.sources") as sources:
        assert sources._source_for("baseline") == "automation", \
            "Baseline's own work is filed as a named human"
        assert sources._source_for("flashback") == "automation"
        assert sources._source_for("alice") == "user", "a real person became automation"
    # The duplicate itself: a literal that lists one name twice is a shim that
    # is not there.
    src = (REPO / "visualizer" / "backend" / "sources.py").read_text(encoding="utf-8")
    block = re.search(r"_SERVICE_ACTORS = \{(.*?)\}", src, re.S).group(1)
    names = re.findall(r'"([^"]*)"', block)
    assert len(names) == len(set(names)), f"duplicate actor names: {names}"


# ---- dead code ---------------------------------------------------------------
def test_the_env_scanner_does_not_compute_its_answer_twice():
    """It built key and value and then immediately overwrote both on the next
    two lines, and passed maxsplit positionally — which 3.13 deprecates."""
    src = (REPO / "flashback" / "backend" / "platform.py").read_text(encoding="utf-8")
    assert 're.split(r"([:=])"' not in src, "the dead first computation is back"
    assert 'maxsplit=1' in src, "maxsplit is passed positionally again"


def test_the_fleet_fetch_cannot_silently_drop_an_upstream():
    src = (REPO / "visualizer" / "backend" / "sources.py").read_text(encoding="utf-8")
    assert "zip(reads, results, strict=True)" in src, \
        "a length mismatch would truncate silently and lose an upstream's rows"
