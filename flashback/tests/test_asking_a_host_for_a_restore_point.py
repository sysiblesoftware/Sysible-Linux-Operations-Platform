"""Who may ask a host to capture its whole machine, and what is told back.

A config snapshot reads the paths an operator configured. A restore point
reads EVERY file under the captured roots — so it is the one capture that can
put a host's secrets into the store on an operator's behalf, and it carries
the operator gate the per-file snapshot does not need.

It also goes through the Controller, because agents are outbound-only. When
the Controller is not there, saying so beats a silent no-op: the whole
complaint about "Back up now" was that a request going nowhere looked exactly
like a button that did nothing.
"""
import importlib
import os
import sys
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "test-shared-secret"
SUPER = {"X-Sysible-Auth": SECRET, "X-Sysible-User": "root", "X-Sysible-Role": "superuser"}
OPERATOR = {"X-Sysible-Auth": SECRET, "X-Sysible-User": "ops", "X-Sysible-Role": "operator"}
AUDITOR = {"X-Sysible-Auth": SECRET, "X-Sysible-User": "eyes", "X-Sysible-Role": "auditor"}
HOST = "deb-web-1"


@pytest.fixture()
def cl(tmp_path):
    from starlette.testclient import TestClient
    os.environ.update(
        SYSIBLE_FLASHBACK_TRUST_GATEWAY_AUTH="1",
        SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_FLASHBACK_DATA=str(tmp_path),
    )
    os.environ.pop("SLOP_CONTROLLER_UPSTREAM", None)
    for mod in ("backend.identity", "backend.store", "backend.controller", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    with TestClient(m.app, base_url="http://slop.lan") as c:
        c.app_module = m
        yield c


def _asked(cl, monkeypatch):
    """Capture what the Flashback app sends the Controller."""
    seen = {}

    def _ok(identity, host_id):
        seen["user"] = identity.user
        seen["role"] = identity.role
        seen["host"] = host_id
        return True, "Capturing."
    monkeypatch.setattr(cl.app_module.controller, "request_restore_point", _ok)
    return seen


# ---------------------------------------------------------------------------
# who may ask
# ---------------------------------------------------------------------------
def test_an_operator_may_ask(cl, monkeypatch):
    seen = _asked(cl, monkeypatch)
    r = cl.post(f"/api/hosts/{HOST}/restore-point", headers=OPERATOR)
    assert r.status_code == 200, r.text
    assert r.json()["requested"] is True
    assert seen["host"] == HOST


def test_an_auditor_may_not(cl, monkeypatch):
    """Reading a machine's whole filesystem into the store is a write, and the
    one capture that can carry a host's secrets out of it."""
    called = _asked(cl, monkeypatch)
    r = cl.post(f"/api/hosts/{HOST}/restore-point", headers=AUDITOR)
    assert r.status_code == 403, r.text
    assert not called, "the Controller was asked anyway"


def test_the_identity_is_the_callers_own(cl, monkeypatch):
    """The Controller applies its OWN RBAC, so it has to be told who is
    asking — not handed a service identity that passes everything."""
    seen = _asked(cl, monkeypatch)
    cl.post(f"/api/hosts/{HOST}/restore-point", headers=OPERATOR)
    assert seen["user"] == "ops" and seen["role"] == "operator"


# ---------------------------------------------------------------------------
# what is told back
# ---------------------------------------------------------------------------
def test_a_missing_controller_is_reported_not_swallowed(cl, monkeypatch):
    monkeypatch.setattr(cl.app_module.controller, "request_restore_point",
                        lambda i, h: (False, "no Controller is configured"))
    r = cl.post(f"/api/hosts/{HOST}/restore-point", headers=OPERATOR)
    assert r.status_code == 502, r.text
    assert "Controller" in r.json()["detail"]


def test_asking_is_audited(cl, monkeypatch):
    """It reads every file on a machine. Who asked is part of the record."""
    _asked(cl, monkeypatch)
    cl.post(f"/api/hosts/{HOST}/restore-point", headers=OPERATOR)
    log = " ".join(str(e) for e in cl.app_module.store.list_audit(limit=20))
    assert "restore-point" in log and "ops" in log and HOST in log, log


# ---------------------------------------------------------------------------
# the host id is the only caller-controlled part of a URL carrying the
# platform shared secret
# ---------------------------------------------------------------------------
def test_a_host_id_that_is_not_one_never_reaches_the_controller(cl):
    """Unlike a FastAPI path parameter, this lands in a URL the client builds.
    The real guard lives in controller.py; this is the end-to-end proof that
    nothing routes around it."""
    import backend.controller as ctl
    ok, why = ctl.request_restore_point(
        type("I", (), {"user": "ops", "role": "operator"})(), "../../admin/users")
    assert ok is False
    assert "host" in why.lower(), why
