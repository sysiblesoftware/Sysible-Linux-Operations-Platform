"""Hypervisors were reachable only from inside a per-project wizard.

REPORTED, in as many words: "I don't see a hypervisor option on SLEP."

They were right. Managing a KVM host meant: open Infrastructure, create or edit
a project, choose the libvirt provider, then choose "Remote KVM host over SSH"
-- and only on that last branch did the buttons that install libvirt, repair the
storage permissions and explain a VM that never got an address appear. Nothing
in the rail said hypervisors existed, and every project re-typed the same host,
user and URI by hand.

A hypervisor is a HOST, not a property of one project. So it lives in the rail
beside Jump Hosts and Controllers, which are the same kind of thing: something
out on the network that SLEP reaches into.

One property is load-bearing and easy to lose later: NO SSH PASSWORD IS SAVED.
The managed key is the way in, and a password is typed at the moment it is
needed. A saved hypervisor is a convenience for typing, not a credential store
for operator-chosen hosts.
"""
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "webgui" / "frontend" / "src"
VIEW = SRC / "views" / "Hypervisors.jsx"
APP = SRC / "App.jsx"


@pytest.fixture(scope="module")
def view():
    return VIEW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app():
    return APP.read_text(encoding="utf-8")


# ------------------------------------------------------------ it is reachable

def test_the_rail_has_a_hypervisors_entry(app):
    """The whole report was that nothing named them."""
    assert re.search(r"\{\s*key:\s*'hypervisors'", app), "no rail entry"
    assert "label: 'Hypervisors'" in app


def test_it_sits_under_reach_with_the_other_remote_things(app):
    """Grouping is the difference between "a menu item exists" and "I would have
    looked there". Controllers, Credentials and Jump Hosts are all things SLEP
    reaches into; a hypervisor is another."""
    keys = re.findall(r"\{\s*key:\s*'(\w+)'.*?\}", app, re.S)
    assert "hypervisors" in keys, keys
    assert "jumphosts" in keys
    assert abs(keys.index("hypervisors") - keys.index("jumphosts")) == 1, (
        "Hypervisors is not next to Jump Hosts: %s" % keys)
    # and nothing between them re-opens a group heading
    between = app[app.index("key: 'jumphosts'"):app.index("key: 'hypervisors'")]
    assert "group:" not in between, between


def test_the_view_is_imported_and_routed(app):
    """A rail entry with no route renders an empty pane."""
    assert "import Hypervisors from './views/Hypervisors.jsx'" in app
    assert "view === 'hypervisors' ? <Hypervisors />" in app


def test_its_icon_exists(app):
    """NavIcon renders ICONS[name]; a missing key is a silently blank square."""
    assert re.search(r"^\s*hypervisor:\s*<", app, re.M), "no 'hypervisor' icon"


def test_the_icon_avoids_the_font_block_that_drew_as_tofu(app):
    """Same rule as every other glyph here: nothing from Miscellaneous Technical."""
    line = next(l for l in app.splitlines() if re.match(r"\s*hypervisor:\s*<", l))
    assert not any(0x2300 <= ord(c) <= 0x23FF for c in line), line


# ------------------------------------------------- it does the host-level jobs

@pytest.mark.parametrize("route", [
    "infra/hypervisor-provision",
    "infra/hypervisor-permissions",
    "infra/hypervisor-why-no-ip",
    "infra/hypervisor-fix-permissions",
])
def test_every_host_action_is_offered_here(view, route):
    """The point of the page. If an action still only exists inside the project
    wizard, the operator still has to go and find it there."""
    assert route in view, route


def test_the_actions_name_the_saved_hypervisor_instead_of_retyping_it(view):
    """The whole reason to save one. hypervisor_id is resolved server-side, which
    is also what applies the organization guard to it."""
    assert "hypervisor_id: hv.id" in view, (
        "the actions do not pass the saved hypervisor, so the host must be typed again")


def test_changing_a_host_needs_more_than_reading_it(view):
    """Reporting what is wrong is for anyone who can see the page; installing
    packages and changing file ownership on someone's host is not."""
    assert "canWrite()" in view
    m = re.search(r"ACTIONS\.filter\(\(a\) => ([^)]+)\)", view)
    assert m, "the action list is not filtered by write access"


def test_the_destructive_action_is_marked_as_such(view):
    """Three of the four change nothing. Exactly one does, and it is the one
    gated on write access."""
    rows = re.findall(r"\['infra/hypervisor-[\w-]+', '[^']*', (true|false),", view)
    assert rows.count("true") == 1, rows
    assert rows.count("false") == 3, rows


# ----------------------------------------------------------- the safety rules

def test_no_password_is_ever_saved(view):
    """A saved hypervisor must not become a store of SSH passwords to hosts an
    operator chose. The password field exists only in the Manage dialog, where it
    is used for one call."""
    form = view[view.index("function HypervisorForm"):view.index("const ACTIONS")]
    assert "password" not in form.lower() or "No password is stored" in form, form[:400]
    body = re.search(r"if \(hv\) await api\('hypervisors/' \+ hv\.id.*?\n", view, re.S)
    assert body, "cannot find the save call"
    saved = view[view.index("const body = {"):view.index("if (hv) await api")]
    assert "password" not in saved, "the save payload carries a password: " + saved


def test_the_form_refuses_to_assemble_a_uri_with_no_host(view):
    """qemu+ssh://user@/system is unparseable; libvirt falls back to its default
    and the operator gets "connect to host localhost port 22: Connection refused",
    which names neither the real problem nor the empty field."""
    assert "host.trim() ?" in view, "the URI is assembled without checking the host"
    assert re.search(r"disabled=\{![^}]*uri\}", view), (
        "Save is not disabled when no URI could be assembled")


def test_forgetting_one_says_what_it_does_not_do(view):
    """"Delete" next to a hypervisor reads as "destroy my VMs". It removes a row
    here and nothing else, and a project keeps working because its URI lives in
    its own variables.tf."""
    assert "Forget" in view
    assert "Nothing on the host is touched" in view


def test_the_placeholder_is_an_example_not_a_value(view):
    """A bare dotted-quad reads as a real address because of its shape. Caught by
    the existing guard when this file was written; pinned here so the next edit
    does not put one back."""
    for ph in re.findall(r'placeholder="([^"]*)"', view):
        assert not re.fullmatch(r"\s*\d{1,3}(\.\d{1,3}){3}\s*", ph), ph


# ------------------------------------------------------------ the backend half

def test_the_backend_serves_what_the_view_calls():
    from backend import app as backend_app
    paths = {getattr(r, "path", "") for r in backend_app.app.routes}
    for route in ("/hypervisors", "/hypervisors/{hid}"):
        assert route in paths, "%s is called from the view but not served" % route


def test_saving_and_changing_one_needs_an_operator():
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    for fn in ("def hypervisors_create", "def hypervisors_update", "def hypervisors_delete"):
        sig = src[src.index(fn):src.index(fn) + 300]
        assert "require_operator" in sig, "%s is not operator-guarded" % fn
    listing = src[src.index("def hypervisors_list"):src.index("def hypervisors_list") + 300]
    assert "current_user" in listing, "reading the list should not need an operator"


def test_one_organizations_hypervisor_cannot_be_driven_by_another():
    """hypervisor_id is resolved server-side precisely so the org guard applies.
    Trusting the id would let a saved host be borrowed across organizations."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    body = src[src.index("def _hypervisor_ssh_target"):]
    body = body[:body.index("\ndef ")]
    assert "hypervisor_id" in body, "the shared validator does not resolve a saved hypervisor"
    assert "_guard_object_org" in body, "a saved hypervisor is used without an org check"
