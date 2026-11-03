"""Can an operator actually reach a restore point and press it?

The store, the relay and the agent all gained whole-machine restore points
before the console did, which means the feature existed and nobody could use
it: "I want to select a backup from flashback and reboot the VM into that
state" is a journey through a page, not a row in a database.

These drive a real browser, the way an operator does:

  * host -> restore point -> what it holds -> put the machine back;
  * a host with NO config history still offers the capture, because that is
    exactly the host someone wants a restore point of;
  * a capture still in flight is not offered as somewhere to go back to;
  * a read-only identity is told it needs operator access rather than handed
    a button that 403s;
  * the consequence is on the page NEXT TO the button, not hidden in a
    confirm() dialog — this reboots a server.

They skip themselves when playwright or a browser is missing, because that is
a missing tool and not a broken console.

Run: pip install -r ../requirements.txt pytest ; pytest    (from the flashback/ dir)
"""
import contextlib
import hashlib
import importlib
import importlib.util
import os
import socket
import sys
import threading
import time
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

HOST = "deb-web-1"
BARE = "deb-fresh-2"          # enrolled, never captured a config file
PATH = "/etc/hosts"
BODY = b"127.0.0.1 localhost\n"

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("playwright") is None,
    reason="playwright not installed",
)


def _free_port():
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seal(store, host, label, entries=None, roots="/etc:/srv", seal=True):
    """One restore point, through the same calls the agent makes."""
    ents = entries if entries is not None else [
        {"path": "/etc/hostname", "kind": "f", "mode": 0o644, "uid": 0, "gid": 0,
         "size": len(BODY), "sha256": hashlib.sha256(BODY).hexdigest()},
        {"path": "/etc/ssl", "kind": "d", "mode": 0o755, "uid": 0, "gid": 0},
        {"path": "/etc/localtime", "kind": "l", "target": "/usr/share/zoneinfo/UTC"},
    ]
    pid = store.open_restore_point(host, label, roots=roots)
    r = store.add_restore_point_entries(pid, ents)
    for sha in r["missing"]:
        store.put_blob(sha, BODY)
    if seal:
        store.seal_restore_point(pid)
    return pid


@contextlib.contextmanager
def _console(tmp_path, role="superuser", seed=None):
    """The console, served for real, in a browser."""
    import uvicorn
    from playwright.sync_api import sync_playwright

    os.environ.update(
        SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH="0",
        SYSIBLE_FLASHBACK_LOCAL_ROLE=role,
        SYSIBLE_FLASHBACK_DATA=str(tmp_path),
    )
    os.environ.pop("SLOP_CONTROLLER_UPSTREAM", None)
    for mod in ("backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    m.store.ingest_snapshot(HOST, HOST, [{"path": PATH, "content": BODY.decode()}])
    if seed:
        seed(m.store)

    port = _free_port()
    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=port, log_level="error")
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
                browser = p.chromium.launch(
                    **({"executable_path": chrome} if chrome else {}))
            except Exception as e:
                pytest.skip(f"no chromium to drive ({e}); "
                            "run `playwright install chromium` or set "
                            "SYSIBLE_TEST_CHROMIUM")
            pg = browser.new_page()
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            try:
                yield pg, m
            finally:
                browser.close()
    finally:
        server.should_exit = True
        t.join(timeout=10)


def _open_point(pg, nth=0):
    """Click a restore point and wait for the pane to be FINISHED.

    The manifest is fetched after the detail renders, so waiting on .rpdetail
    proves only that the heading is up — read the pane then and you catch
    "Loading the manifest…". That made this file flaky at about 1 run in 3.
    """
    pg.locator("#files .points button.rp").nth(nth).click()
    pg.wait_for_selector("#diff .rpdetail", timeout=5000)
    # Either the table arrived, or the point is one that has no restore pane at
    # all (an unsealed capture) — in which case the note is the finished state.
    pg.wait_for_function(
        "() => { const d = document.querySelector('#diff');"
        "  if (!d) return false;"
        "  if (d.querySelector('.mtable table')) return true;"
        "  const t = d.querySelector('.mtable');"
        "  return !!t && !/Loading/.test(t.textContent); }",
        timeout=5000)


def _open_host(pg, host=HOST):
    """Click the host row, the way an operator does."""
    row = pg.locator(f"#hosts .hostwrap .item", has_text=host).first
    assert row.count(), f"no row for {host}"
    row.click()
    pg.wait_for_selector("#files .points .rpcard", timeout=5000)


# ---------------------------------------------------------------------------
# the journey
# ---------------------------------------------------------------------------
def test_a_host_shows_its_restore_points_and_what_one_holds(tmp_path):
    with _console(tmp_path, seed=lambda s: _seal(s, HOST, "before the upgrade")) as (pg, _):
        _open_host(pg)
        card = pg.inner_text("#files .points")
        # inner_text() is the RENDERED text and .col h3 is uppercased by CSS.
        assert "whole machine" in card.lower(), card
        assert "before the upgrade" in card, \
            "the host's restore point is not listed anywhere an operator can see"
        assert "3 files" in card, card

        _open_point(pg)
        detail = pg.inner_text("#diff")
        # What it holds, not just that it exists.
        assert "before the upgrade" in detail
        assert "/etc/hostname" in detail, \
            "the manifest is not shown, so the button that reboots a server is opaque"
        assert "/etc/localtime" in detail
        assert "/usr/share/zoneinfo/UTC" in detail, "a symlink's target is not shown"
        assert "0644" in detail, "the mode a file is restored with is not shown"
        assert "20 B" in detail, "a file's size is not shown"
        # The wire format is ONE LETTER per entry (f/l/d). An operator must not
        # be shown it, and matching 'file'/'link' against it silently blanked
        # every size and every symlink target.
        assert "symlink" in detail and "directory" in detail, detail
        row = [r for r in detail.splitlines() if r.startswith("/etc/localtime")]
        assert row and "0000" not in row[0], \
            "a symlink has no mode of its own, and 0000 says it does: " + str(row)
        assert "20 B" in detail, "a file's size is not shown"
        # The wire format is one letter per entry. The operator must not see it.
        assert "symlink" in detail and "directory" in detail, detail
        rows = [r for r in detail.splitlines() if r.startswith("/etc/localtime")]
        assert rows and "0000" not in rows[0], \
            "a symlink has no mode of its own, and 0000 says it does: " + str(rows)


def test_the_consequence_is_on_the_page_beside_the_button(tmp_path):
    """A confirm() dialog is not disclosure: it appears after the decision and
    nobody reads it. This reboots a machine."""
    with _console(tmp_path, seed=lambda s: _seal(s, HOST, "known good")) as (pg, _):
        _open_host(pg)
        _open_point(pg)
        warn = pg.inner_text("#diff .rpwarn").lower()
        assert "rewrites" in warn
        assert "reboot" in warn
        assert "removed" in warn, \
            "that files added since the capture are deleted is not stated"
        btn = pg.locator("#diff button.btn.danger")
        assert btn.count(), "no restore button for a writing identity"
        assert HOST in btn.inner_text(), \
            "the button does not name the host it rewrites"


def test_putting_a_machine_back_queues_it_and_says_so(tmp_path):
    with _console(tmp_path, seed=lambda s: _seal(s, HOST, "known good")) as (pg, m):
        _open_host(pg)
        _open_point(pg)
        pg.on("dialog", lambda d: d.accept())
        pg.locator("#diff button.btn.danger").click()
        pg.wait_for_selector("#msg:not([hidden])", timeout=5000)
        assert "known good" in pg.inner_text("#msg"), pg.inner_text("#msg")
        # The store really was asked, not just the page.
        job = m.store.pending_point_restore(HOST)
        assert job, "the click did not queue anything"
        assert job["reboot"] is True
        # And the card now says the machine is on its way back.
        pg.wait_for_selector("#files .points .rpjob", timeout=5000)
        banner = pg.inner_text("#files .points .rpjob")
        assert "known good" in banner
        assert "check-in" in banner or "stag" in banner.lower(), banner


def test_the_reboot_can_be_declined(tmp_path):
    """Some fleets reboot on a change window, not when the console says so."""
    with _console(tmp_path, seed=lambda s: _seal(s, HOST, "known good")) as (pg, m):
        _open_host(pg)
        _open_point(pg)
        pg.locator("#diff .rpreboot input").uncheck()
        pg.on("dialog", lambda d: d.accept())
        pg.locator("#diff button.btn.danger").click()
        pg.wait_for_selector("#msg:not([hidden])", timeout=5000)
        job = m.store.pending_point_restore(HOST)
        assert job, "nothing was queued"
        assert job["reboot"] is False, \
            "the reboot box was cleared and the host was queued to reboot anyway"


# ---------------------------------------------------------------------------
# the states that are not a happy path
# ---------------------------------------------------------------------------
def test_a_host_whose_only_backup_is_a_restore_point(tmp_path):
    """The files column returns early for a host with no config history, and
    the restore-point card has to survive that — a host captured only as a
    whole machine is not a host with nothing."""
    with _console(tmp_path,
                  seed=lambda s: _seal(s, BARE, "day one")) as (pg, _):
        # The host list must not call it unbacked. It has a capture; it just is
        # not a config-file one, and "no backup captured yet" sends an operator
        # off hunting a broken agent.
        row = pg.locator("#hosts .hostwrap .item", has_text=BARE).first
        assert row.count(), "a host captured only as a restore point vanished"
        rowtext = row.inner_text()
        assert "no backup captured yet" not in rowtext.lower(), rowtext
        assert "restore point" in rowtext.lower(), rowtext

        _open_host(pg, BARE)
        col = pg.inner_text("#files")
        assert "whole machine" in col.lower()
        assert "day one" in col, col
        # And it still says the FILE history is empty, rather than lying.
        assert "Nothing captured for this host yet" in col, col


def test_a_host_with_no_restore_points_is_told_what_one_is(tmp_path):
    """The empty state has to teach, not just say zero."""
    with _console(tmp_path) as (pg, _):
        _open_host(pg)
        col = pg.inner_text("#files .points")
        assert "No restore points" in col, col
        assert "Capture one now" in col, \
            "a host with no restore point is offered no way to take one"
        assert "whole machine" in col.lower()
        assert "reboot" in col.lower(), \
            "the copy never says what a restore point is FOR"


def test_a_capture_in_flight_is_not_offered_as_somewhere_to_go_back_to(tmp_path):
    with _console(tmp_path,
                  seed=lambda s: _seal(s, HOST, "mid flight", seal=False)) as (pg, _):
        _open_host(pg)
        row = pg.locator("#files .points button.rp").first
        assert "capturing" in row.inner_text().lower(), row.inner_text()
        row.click()
        pg.wait_for_selector("#diff .rpdetail", timeout=5000)
        pg.wait_for_selector("#diff .mtable table", timeout=5000)
        detail = pg.inner_text("#diff")
        assert "not finished" in detail.lower(), detail
        assert not pg.locator("#diff button.btn.danger").count(), \
            "a half-captured machine was offered as a restore target"


def test_a_read_only_identity_is_told_why_not(tmp_path):
    with _console(tmp_path, role="auditor",
                  seed=lambda s: _seal(s, HOST, "known good")) as (pg, _):
        assert pg.get_attribute("body", "data-can-write") == "0"
        _open_host(pg)
        card = pg.inner_text("#files .points")
        assert "Capture one now" not in card, \
            "a read-only identity was offered a capture it cannot perform"
        _open_point(pg)
        detail = pg.inner_text("#diff")
        assert not pg.locator("#diff button.btn.danger").count()
        assert "operator access" in detail, \
            "the button is simply absent, which reads as a broken page"
        # It can still READ what the point holds — that is an audit's whole job.
        assert "/etc/hostname" in detail


def test_the_feature_is_discoverable_before_a_host_is_picked(tmp_path):
    """An affordance nobody can see is the same as one that does not exist."""
    with _console(tmp_path) as (pg, _):
        start = pg.inner_text("#diff")
        assert "restore point" in start.lower(), start
        assert "reboot" in start.lower(), \
            "the landing pane never says a whole machine can be put back"


def test_the_column_beside_a_restore_point_does_not_contradict_it(tmp_path):
    """Picking a whole machine used to leave the next column saying "Pick a
    file to see every version of it" — one column arguing with the one beside
    it, which is the complaint this console keeps earning."""
    with _console(tmp_path, seed=lambda s: _seal(s, HOST, "known good")) as (pg, _):
        _open_host(pg)
        _open_point(pg)
        col = pg.inner_text("#versions")
        assert "Pick a file" not in col, col
        assert "/etc" in col, "the column says nothing about what was captured"
        assert "symlink" in col or "directory" in col or "file" in col, col

        # And picking a config file afterwards puts the versions back, rather
        # than leaving the roots summary stranded there.
        pg.locator("#files .filelist .item").first.click()
        pg.wait_for_selector("#versions .item", timeout=5000)
        assert "What it captured" not in pg.inner_text("#versions")


def test_the_capture_button_cannot_fail_silently_without_a_controller(tmp_path):
    """A host cannot be reached inbound — the request goes via the Controller.
    With none wired the button can only 502, which reads as a broken console.
    It stays VISIBLE (so the feature is still discoverable) and disabled."""
    with _console(tmp_path) as (pg, _):
        _open_host(pg)
        cb = pg.locator("#files .points .rphead button.btn").first
        assert cb.count(), "the capture button is gone entirely"
        assert cb.is_disabled(), \
            "with no Controller this button can only ever 502"
        why = cb.get_attribute("title") or ""
        assert "Controller" in why, why
