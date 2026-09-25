"""Can an operator actually GET at a backup?

Reported from a live fleet: three hosts, each showing over a thousand files
captured, and "there's no way to do anything. restore or view the files." Nothing
was broken — host → files → versions → download/diff/restore all worked. The row
that opens all of it just did not look like a control, while the two things that
DID (the select box and "Back up now") are both about capturing. So the console
offered three ways to take a backup and no visible way to read one back.

These tests cover both halves of that, because only one of them is code:

  * the PATH still works end to end, for the identity the console runs as;
  * the console still SAYS the path is there. An affordance nobody can see is the
    same to an operator as a feature that does not exist, so the words and the
    chevron are treated as behaviour, not decoration.

The browser test at the bottom is the real proof — it clicks the row the way an
operator does — and skips itself when playwright is not installed.

Run: pip install -r ../requirements.txt pytest ; pytest    (from the flashback/ dir)
"""
import importlib
import importlib.util
import os
import sys
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "test-shared-secret"
OPERATOR = {"X-Sysible-Auth": SECRET, "X-Sysible-User": "ops", "X-Sysible-Role": "operator"}
AUDITOR = {"X-Sysible-Auth": SECRET, "X-Sysible-User": "eyes", "X-Sysible-Role": "auditor"}

HOST = "deb-web-1"
PATH = "/etc/hosts"
V1 = "127.0.0.1 localhost\n"
V2 = "127.0.0.1 localhost\n10.0.0.5 db\n"


@pytest.fixture()
def cl(tmp_path):
    from starlette.testclient import TestClient

    os.environ.update(
        SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH="1",
        SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_FLASHBACK_DATA=str(tmp_path),
    )
    os.environ.pop("SLOP_CONTROLLER_UPSTREAM", None)
    for mod in ("backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    # Two versions of one file, the way an agent would have delivered them.
    # init_db() explicitly: the app does it on startup, but the seed has to land
    # BEFORE the client enters, or there is no schema to write into yet.
    m.store.init_db()
    m.store.ingest_snapshot(HOST, HOST, [{"path": PATH, "content": V1}])
    m.store.ingest_snapshot(HOST, HOST, [{"path": PATH, "content": V2}])
    with TestClient(m.app, base_url="http://slop.lan") as c:
        yield c


def _versions(cl):
    r = cl.get(f"/api/hosts/{HOST}/versions", params={"path": PATH}, headers=OPERATOR)
    assert r.status_code == 200, r.text
    return r.json()


# ---- the path is really there ---------------------------------------------
def test_a_backed_up_host_leads_all_the_way_to_a_restore(cl):
    """The exact journey the console's columns make: host → file → version → act."""
    hosts = cl.get("/api/hosts", headers=OPERATOR).json()["hosts"]
    ours = [h for h in hosts if h["host_id"] == HOST]
    assert ours and ours[0]["backed_up"] and ours[0]["files"] == 1

    files = cl.get(f"/api/hosts/{HOST}/files", headers=OPERATOR).json()
    assert [f["path"] for f in files] == [PATH]
    assert files[0]["versions"] == 2

    vers = _versions(cl)
    assert len(vers) == 2, "a changed file must keep BOTH versions — that is the product"

    # Newest first, so the first row the console labels "current (newest)" is.
    newest, older = vers[0], vers[1]
    assert cl.get(f"/api/hosts/{HOST}/download",
                  params={"path": PATH, "sha": older["sha256"]},
                  headers=OPERATOR).content == V1.encode()

    diff = cl.get(f"/api/hosts/{HOST}/diff",
                  params={"path": PATH, "a": older["sha256"], "b": newest["sha256"]},
                  headers=OPERATOR).json()["diff"]
    assert "+10.0.0.5 db" in diff

    r = cl.post(f"/api/hosts/{HOST}/restore",
                json={"path": PATH, "sha": older["sha256"]}, headers=OPERATOR)
    assert r.status_code == 200, r.text
    queued = cl.get(f"/api/hosts/{HOST}/restores", headers=OPERATOR).json()
    assert queued and queued[0]["path"] == PATH


def test_an_auditor_can_read_it_all_and_restore_nothing(cl):
    older = _versions(cl)[1]["sha256"]
    assert cl.get(f"/api/hosts/{HOST}/files", headers=AUDITOR).status_code == 200
    assert cl.get(f"/api/hosts/{HOST}/download",
                  params={"path": PATH, "sha": older}, headers=AUDITOR).status_code == 200
    assert cl.post(f"/api/hosts/{HOST}/restore",
                   json={"path": PATH, "sha": older}, headers=AUDITOR).status_code == 403


# ---- ...and the console says so --------------------------------------------
def test_the_console_names_what_an_operator_can_do_with_a_backup(cl):
    """Browse, diff, download, restore — all four were unreachable in practice
    because the page never mentioned any of them."""
    page = cl.get("/", headers=OPERATOR).text
    assert "Click a host" in page, "the page must say the host row is the way in"
    for word in ("download", "restore", "diff"):
        assert word in page.lower(), f"the console never mentions {word}"


def test_a_host_row_is_visibly_a_drill_down(cl):
    page = cl.get("/", headers=OPERATOR).text
    assert "class='drill'" in page or "'drill'" in page, "the chevron affordance is gone"
    assert "Browse '+what+" in page, "the row lost the title naming what it opens"
    assert ".hostwrap .item:hover{background" in page, \
        "the hover must mark the CLICKABLE region, not the whole row"


def test_a_failed_read_is_reported_not_silently_blank(cl):
    """A column that empties itself and says nothing is indistinguishable from a
    click that did nothing at all — which is how this was reported."""
    page = cl.get("/", headers=OPERATOR).text
    assert "Could not read this host" in page
    assert "Could not read this file" in page
    assert "catch(e){return;}" not in page, \
        "a swallowed error leaves the operator staring at an empty column"


# ---- the real thing, in a real browser -------------------------------------
@pytest.mark.skipif(
    importlib.util.find_spec("playwright") is None,
    reason="playwright not installed",
)
def test_clicking_a_host_row_opens_its_files_and_offers_restore(tmp_path):
    """No amount of string-matching proves a row is clickable. This clicks it."""
    import threading
    import time

    import uvicorn
    from playwright.sync_api import sync_playwright

    os.environ.update(
        SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH="0",      # standalone local identity
        SYSIBLE_FLASHBACK_DATA=str(tmp_path),
    )
    os.environ.pop("SLOP_CONTROLLER_UPSTREAM", None)
    for mod in ("backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    m.store.ingest_snapshot(HOST, HOST, [{"path": PATH, "content": V1}])
    m.store.ingest_snapshot(HOST, HOST, [{"path": PATH, "content": V2}])

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=8799, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "test server did not come up"
    try:
        chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
            except Exception as e:
                # playwright installed but no browser downloaded. That is a missing
                # tool, not a broken console — skip and say so, rather than
                # reporting a failure the code cannot cause.
                pytest.skip(f"no chromium to drive ({e}); "
                            "run `playwright install chromium` or set SYSIBLE_TEST_CHROMIUM")
            page = browser.new_page()
            page.goto("http://127.0.0.1:8799/", wait_until="networkidle")

            row = page.locator("#hosts .hostwrap .item").first
            assert row.count(), "no host row rendered"
            assert row.locator(".drill").count(), "the row shows no drill-down affordance"

            row.click()
            page.wait_for_selector("#files .item", timeout=5000)
            assert PATH in page.inner_text("#files")

            page.locator("#files .item").first.click()
            page.wait_for_selector("#versions .item", timeout=5000)

            page.locator("#versions .item").first.click()
            page.wait_for_selector("#diff .toolbar", timeout=5000)
            detail = page.inner_text("#diff")
            assert "Download this version" in detail
            assert "Restore this version" in detail, \
                "a superuser reached a version and was offered no way to restore it"
            browser.close()
    finally:
        server.should_exit = True
        t.join(timeout=10)
