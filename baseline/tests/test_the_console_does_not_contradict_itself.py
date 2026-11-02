"""One fact, one answer. The console used to give four.

REPORTED, with a screenshot of one host: the apply panel said "1 done", the
STATE card said "failing — the last run could not finish", both MUST SATISFY
rows said "as declared", and RECENT RUNS said "nothing changed". Every one of
those is on the screen at the same time, about the same run.

TWO BUGS, feeding each other.

The first is the dangerous one. A run row carries `ok`, `changed`, `failed` and
`error`. `error` is set when the run could not be carried out AT ALL — the
Controller unreachable, the agent refusing, the desired-state request failing
before salt-call ever started. The console read only `failed` and `changed`, so
a run that never ran came out as zero failed, zero changed — which it then
reported as "nothing changed" and "as declared". A desired-state tool telling
you a host is as declared when it could not even reach the host is the worst
single thing this product can say.

The second is why it was visible rather than merely wrong. The host's roll-up
came from the SERVER (store.drift_summary, which does count `error`), while
every row under it was derived in the browser from the runs (which did not). So
the summary and its own detail were computed from different code with different
rules, and the screen argued with itself.

The roll-up is now the worst of the host's per-state statuses — the same
function the rows use, one layer up — so it cannot disagree with them, in the
same way a state's status is the worst of its hosts.
"""
import importlib
import os
import sqlite3
import sys
import threading
import time
import warnings

import pytest

import console

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "s" * 32
PORT = 8887
OP = {"x-sysible-user": "alice", "x-sysible-role": "operator", "x-sysible-auth": SECRET}

FLEET = [
    # Its last run ERRORED: nothing failed, nothing changed, because nothing ran.
    {"id": "arch-01", "label": "arch-01", "environment": "Dev",
     "kind": "agent", "address": "192.168.100.201", "online": True},
    # Its last run was genuinely clean. The fix must not paint this one red.
    {"id": "arch-02", "label": "arch-02", "environment": "Dev",
     "kind": "agent", "address": "192.168.100.208", "online": True},
    # A real state failure, which is a different thing from not running.
    {"id": "arch-03", "label": "arch-03", "environment": "Dev",
     "kind": "agent", "address": "192.168.100.14", "online": True},
    # Failed AND carries a message. The real shape of a state that blew up.
    {"id": "arch-04", "label": "arch-04", "environment": "Dev",
     "kind": "agent", "address": "192.168.100.15", "online": True},
]

ERROR_TEXT = ("This controller is not wired to Sysible Baseline "
              "(SYSIBLE_BASELINE_URL unset), so nothing can be applied.")

# host, ok, changed, failed, error
RUNS = [
    ("arch-01", 0, 0, 0, ERROR_TEXT),
    ("arch-02", 1, 0, 0, None),
    ("arch-03", 0, 0, 2, None),
    ("arch-04", 0, 0, 1, "the state ran but did not finish"),
]


def _playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _playwright(), reason="playwright not installed")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("contradict")
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1", SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_GATEWAY_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN="t" * 64, SYSIBLE_BASELINE_DATA=str(tmp))
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    for mod in ("backend.env", "backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    # Saved and restored: m.controller is one module object for the whole pytest
    # process, so a plain assignment would outlive this file.
    _real = {k: getattr(m.controller, k) for k in ("list_targets",)}
    m.controller.list_targets = lambda _i: (FLEET, ["Dev"], None)

    m.store.save_state("ssh servers", "openssh-server:\n  pkg.installed: []\n",
                       "alice", source="yaml")
    m.store.assign("env:Dev", "ssh servers")

    db = sqlite3.connect(str(tmp / "baseline.db"))
    now = int(time.time())
    for host, ok, changed, failed, err in RUNS:
        db.execute(
            "INSERT INTO runs(host_id,state_name,state_version,preview,started,finished,"
            "ok,changed,failed,error,states_json,actions_json) "
            "VALUES(?,?,?,0,?,?,?,?,?,?,'[]','[]')",
            (host, "ssh servers", 1, now, now, ok, changed, failed, err))
    db.commit()
    db.close()

    cfg = uvicorn.Config(m.app, host="127.0.0.1", port=PORT, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "baseline did not come up"
    yield f"http://127.0.0.1:{PORT}"
    for k, v in _real.items():
        setattr(m.controller, k, v)
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    chrome = os.environ.get("SYSIBLE_TEST_CHROMIUM")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}),
                                  args=["--no-sandbox"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no chromium to drive ({e})")
        yield b
        b.close()


@pytest.fixture
def pg(browser, server):
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                              extra_http_headers=OP)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(server + "/", wait_until="domcontentloaded")
    console.ready(page, "hosts")
    yield page
    assert not errs, errs
    ctx.close()


def _claims(pg, host):
    """Every claim the console makes about one host, from both ends: the verdict
    in the Hosts list, and the three sections of its detail."""
    console.goto(pg, "hosts")
    assert console.rows(pg).count(), "no host rows at all"
    verdict = pg.eval_on_selector(
        f".grid .r[data-host='{host}'] .c:last-child", "e => e.textContent.trim()")
    console.open_host(pg, host)
    out = pg.evaluate("""() => {
      const sec = (word) => [...document.querySelectorAll('#detail .sec')]
        .find(s => new RegExp(word, 'i').test((s.querySelector('h4')||{}).innerText||''));
      const st = sec('state'), ms = sec('must satisfy'), rr = sec('recent runs');
      return {
        rollup: st ? (st.querySelector('.k')||{}).innerText || '' : '',
        rows: ms ? [...ms.querySelectorAll('.ln')].map(l => (l.querySelector('.t')||{}).innerText||'') : [],
        runs: rr ? [...rr.querySelectorAll('.ln .k')].map(k => k.innerText) : [],
      };
    }""")
    out["listed"] = verdict
    return out


# ---------------------------------------------------------------------------
# a run that could not run is not compliance
# ---------------------------------------------------------------------------
def test_an_errored_run_is_not_reported_as_as_declared(pg):
    c = _claims(pg, "arch-01")
    assert "as declared" not in " ".join(c["rows"]).lower(), (
        "the host could not be reached and the console called it compliant: "
        f"{c['rows']}")


def test_an_errored_run_is_not_reported_as_nothing_changed(pg):
    c = _claims(pg, "arch-01")
    assert c["runs"], "no run shown at all"
    assert "nothing changed" not in " ".join(c["runs"]).lower(), (
        f"a run that never ran is described as a clean one: {c['runs']}")


def test_the_reason_it_could_not_run_is_on_the_screen(pg):
    """The error text is the only thing that says what to DO."""
    _claims(pg, "arch-01")
    assert "SYSIBLE_BASELINE_URL" in console.detail(pg), console.detail(pg)


# ---------------------------------------------------------------------------
# …and the summary agrees with the detail under it
# ---------------------------------------------------------------------------
RANK = ["failing", "could not run", "out of date", "drifted", "never applied",
        "as declared"]


@pytest.mark.parametrize("host", [h["id"] for h in FLEET])
def test_the_rollup_never_contradicts_its_own_rows(pg, host):
    c = _claims(pg, host)
    assert c["rollup"], f"no roll-up for {host}"
    assert c["rows"], f"no per-state rows for {host}"
    worst = min(c["rows"], key=lambda label: RANK.index(label.strip().lower())
                if label.strip().lower() in RANK else len(RANK))
    assert c["rollup"].strip().lower() == worst.strip().lower(), (
        f"{host}: the summary says {c['rollup']!r} and its own rows say {c['rows']!r}")


def test_a_genuinely_clean_host_is_still_green(pg):
    """The fix must not be 'call everything failing'."""
    c = _claims(pg, "arch-02")
    assert c["rollup"].strip().lower() == "as declared", c
    assert c["listed"].strip().lower() == "as declared", c


def test_a_failure_that_also_carries_a_message_is_still_a_failure(pg):
    """The distinction is load-bearing and I got it backwards first.

    `failed` means salt-call RAN and that many states did not come out right.
    `error` is a message that may accompany that ("motd blew up") or stand
    alone. Checking `error` first turned every ordinary state failure into
    "could not run" — two existing tests went red, which is what they are for.
    """
    c = _claims(pg, "arch-04")
    assert c["rollup"].strip().lower() == "failing", c
    assert "could not run" not in " ".join(c["rows"] + c["runs"]).lower(), c
    # …and its message is still the detail shown.
    assert "ran but did not finish" in console.detail(pg), console.detail(pg)


def test_a_real_state_failure_still_reads_as_failing(pg):
    c = _claims(pg, "arch-03")
    assert c["rollup"].strip().lower() == "failing", c
    assert c["listed"].strip().lower() == "failing", c


def test_the_list_matches_the_detail_it_opens(pg):
    """The verdict in the Hosts list is the first thing anyone reads, and it
    came from a different computation than the detail it opens."""
    bad = []
    for h in FLEET:
        c = _claims(pg, h["id"])
        if c["listed"].strip().lower() != c["rollup"].strip().lower():
            bad.append((h["id"], c["listed"], c["rollup"]))
    assert not bad, bad
