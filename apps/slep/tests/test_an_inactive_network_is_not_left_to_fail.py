"""A network defined but INACTIVE must be fixable from SLEP, as a pool already was.

The storage half of this shipped and the network half did not. If the selected
POOL was inactive the wizard said so in red and offered "Start it now"; if the
selected NETWORK was inactive the dropdown appended "-- inactive" to the option
text and that was the whole of it. No warning, no button, and no endpoint to call
even by hand.

libvirt will not attach an interface to an inactive network, so the apply dies on
the first domain -- the same class of failure the pool button exists to prevent,
reached by the same wizard, two fields apart. An operator who loaded the host's
real network and picked it was given a dropdown that looked like it had worked.

So both halves now go through one helper and one UI handler, because two copies
is how they came apart in the first place.
"""
import re
import shutil
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
JSX = HERE / "webgui" / "frontend" / "src" / "views" / "Infrastructure.jsx"


def _body(src, fn):
    """Just that function: stop at the next top-level def/decorator, not at the
    next @app. -- the plain helpers in between would otherwise be read as part
    of the endpoint, and one of them runs virsh itself."""
    start = src.index(fn)
    rest = src[start + len(fn):]
    m = re.search(r"\n(?=@|def )", rest)
    return src[start:start + len(fn) + (m.start() if m else len(rest))]


# --------------------------------------------------------------- the contract

def test_both_kinds_can_be_started_and_both_need_an_operator():
    """Reading what is inactive is for anyone; changing the hypervisor's state
    is not."""
    import inspect
    from backend import app as backend_app
    paths = {getattr(r, "path", "") for r in backend_app.app.routes}
    src = inspect.getsource(backend_app)
    for route, fn in (("/infra/hypervisor-pool-start", "def infra_hypervisor_pool_start"),
                      ("/infra/hypervisor-net-start", "def infra_hypervisor_net_start")):
        assert route in paths, "%s is not served" % route
        sig = src[src.index(fn):src.index(fn) + 300]
        assert "require_operator" in sig, "%s is not operator-guarded" % fn


def test_the_two_halves_share_one_implementation():
    """They drifted once already. A copy per kind is how the network half ends up
    missing a fix the pool half got."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    assert src.count("def _virsh_activate") == 1
    for fn in ("def infra_hypervisor_pool_start", "def infra_hypervisor_net_start"):
        body = _body(src, fn)
        assert "_virsh_activate(" in body, "%s has its own copy" % fn
        assert "subprocess.run" not in body, "%s runs virsh itself instead of sharing" % fn


def test_the_object_kind_never_comes_from_the_request():
    """`kind` is interpolated straight into the virsh verb. It must be the
    endpoint's own literal, not anything a caller can set."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    body = _body(src, "def _virsh_activate")
    assert 'assert kind in ("pool", "net")' in body, "kind is not constrained"
    for fn in ("def infra_hypervisor_pool_start", "def infra_hypervisor_net_start"):
        call = _body(src, fn)
        m = re.search(r'_virsh_activate\([^,]+,\s*("pool"|"net")\s*,', call)
        assert m, "%s does not pass a literal kind: %s" % (fn, call[-200:])


def test_the_two_endpoints_read_the_field_the_ui_sends():
    """One takes {pool}, the other {network}. Reading the wrong key turns the fix
    into a 400 that says a name is required while the UI plainly sent one."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    for fn, key in (("def infra_hypervisor_pool_start", '"pool"'),
                    ("def infra_hypervisor_net_start", '"network"')):
        body = _body(src, fn)
        assert 'body.get(%s' % key in body, "%s does not read %s" % (fn, key)


# ----------------------------------------------------------------- behaviour

def _calls(monkeypatch, returncode=0, stderr=""):
    """Record the virsh argv instead of running it."""
    import backend.app as appmod
    seen = []
    monkeypatch.setattr(shutil, "which", lambda b: "/usr/bin/virsh")

    class R:
        pass
    def fake_run(cmd, *a, **k):
        seen.append(cmd)
        r = R(); r.returncode = returncode; r.stdout = ""; r.stderr = stderr
        return r
    monkeypatch.setattr(appmod.subprocess, "run", fake_run)
    return seen


@pytest.mark.parametrize("kind,verb", [("pool", "pool"), ("net", "net")])
def test_it_starts_the_thing_and_makes_it_survive_a_reboot(kind, verb, monkeypatch):
    """A pool or network started by hand is inactive again after the host reboots,
    and the next apply fails for the same reason. Autostart is the other half."""
    import backend.app as appmod
    seen = _calls(monkeypatch)
    out = appmod._virsh_activate("qemu:///system", kind, "homelab")
    assert out["ok"] is True
    assert [c[3] for c in seen] == ["%s-start" % verb, "%s-autostart" % verb], seen
    assert all(c[:3] == ["virsh", "-c", "qemu:///system"] for c in seen)
    assert all(c[4] == "homelab" for c in seen)


@pytest.mark.parametrize("kind,noun", [("pool", "pool"), ("net", "network")])
def test_an_already_active_one_is_success_not_an_error(kind, noun, monkeypatch):
    """Two operators clicking the same link, or a click after it came up on its
    own, must not read as a failure."""
    import backend.app as appmod
    _calls(monkeypatch, returncode=1, stderr="error: Requested operation is not valid: network is already active")
    out = appmod._virsh_activate("qemu:///system", kind, "homelab")
    assert out["ok"] is True, out
    assert noun in out["output"]


@pytest.mark.parametrize("kind", ["pool", "net"])
def test_without_virsh_it_says_what_to_run_by_hand(kind, monkeypatch):
    """SLEP in a container often has no virsh. A dead button is worse than a
    sentence naming the command."""
    import backend.app as appmod
    monkeypatch.setattr(shutil, "which", lambda b: None)
    out = appmod._virsh_activate("qemu:///system", kind, "homelab")
    assert out["ok"] is False
    assert "%s-start homelab" % kind in out["output"], out


@pytest.mark.parametrize("kind", ["pool", "net"])
def test_a_nameless_request_is_refused_not_run(kind):
    """An empty name would make the argv `virsh net-start ''`."""
    from fastapi import HTTPException
    import backend.app as appmod
    with pytest.raises(HTTPException) as e:
        appmod._virsh_activate("qemu:///system", kind, "")
    assert e.value.status_code == 400


# ------------------------------------------------------------------- the UI

def test_the_wizard_warns_about_an_inactive_network_not_just_a_pool():
    """The reported gap: the pool got red text and a link, the network got the
    word 'inactive' inside a dropdown option."""
    ui = JSX.read_text(encoding="utf-8")
    m = re.search(r"netObj && !netObj\.active && \((.{0,1200}?)\n      \)\}", ui, re.S)
    assert m, "nothing renders a warning for an inactive network"
    block = m.group(1)
    assert "INACTIVE" in block, block[:200]
    assert "Start it now" in block, "the network warning offers no fix"
    assert "hypervisor-net-start" in ui, "no view calls the net-start endpoint"


def test_the_pool_warning_was_not_lost_in_the_process():
    ui = JSX.read_text(encoding="utf-8")
    m = re.search(r"poolObj && !poolObj\.active && \((.{0,1200}?)\n      \)\}", ui, re.S)
    assert m, "the pool warning is gone"
    assert "Start it now" in m.group(1)
    assert "hypervisor-pool-start" in ui


def test_one_ui_handler_serves_both_so_they_cannot_drift_again():
    ui = JSX.read_text(encoding="utf-8")
    assert "const activate = async (kind, name)" in ui, "the two kinds have separate handlers"
    assert "startPool" not in ui, "the old pool-only handler is still referenced"
    # The spinner must distinguish the two, or starting a network spins the pool.
    # Pin the value WRITTEN as well as the two values COMPARED: setting a bare
    # name while the links compare a prefixed one means neither ever spins, and
    # asserting only the comparisons does not notice.
    assert "setStarting(kind + ':' + name)" in ui, (
        "the spinner key written does not identify which kind is starting")
    assert "starting === 'net:' + net" in ui and "starting === 'pool:' + pool" in ui, (
        "a link compares against a different key than activate() writes, so it never spins")


def test_a_failed_start_is_shown_rather_than_swallowed():
    """`ok: false` used to be dropped on the floor: the list reloaded, the thing
    was still inactive, and nothing said why."""
    ui = JSX.read_text(encoding="utf-8")
    m = re.search(r"const activate = async \(kind, name\) => \{(.{0,900}?)\n  \}", ui, re.S)
    assert m, "activate() not found"
    assert "r.ok === false" in m.group(1), "a refused start is never surfaced"
