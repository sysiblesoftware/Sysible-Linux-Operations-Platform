"""Declaring a state without writing YAML — and the YAML option beside it.

Reported: "I don't want baseline to be yaml based with the new states. It
should be more intuitive. We do need a yaml option though."

A state used to be one textarea of raw Salt SLS. That is the right thing to
SHIP — the host runs `salt-call --local` on real SLS and nothing about that
changed — but the wrong thing to ask for. So a state is now a list of items,
and backend/build.py turns the list into SLS.

The three things that make that safe rather than merely nicer:

  * The SLS is generated on the SERVER. It runs as root on every host the state
    is assigned to, so it is the thing that gets tested — not a second
    implementation in the browser that drifts from the one in the preview.
  * It is dumped by PyYAML, never formatted as text. A path with a colon, a
    value that reads as a boolean, a file body whose first line starts "- " —
    each produces a different document when a template builds the string, and
    the first anyone knows is a state that fails on the fleet.
  * A state REMEMBERS how it was written. One typed as YAML opens as YAML,
    because parsing SLS back into items is a guess, and what a guess silently
    drops is the part the operator cannot see.
"""
import importlib
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SECRET = "s" * 32
TOKEN = "t" * 64
OPERATOR = {"x-sysible-user": "alice", "x-sysible-role": "operator",
            "x-sysible-auth": SECRET}
AUDITOR = {"x-sysible-user": "zoe", "x-sysible-role": "auditor",
           "x-sysible-auth": SECRET}


@pytest.fixture()
def cl(tmp_path):
    from starlette.testclient import TestClient
    os.environ.update(
        SYSIBLE_BASELINE_TRUST_GATEWAY_AUTH="1",
        SYSIBLE_SSO_SHARED_SECRET=SECRET,
        SYSIBLE_BASELINE_AGENT_TOKEN=TOKEN,
        SYSIBLE_BASELINE_DATA=str(tmp_path),
    )
    os.environ.pop("SYSIBLE_BASELINE_DB", None)
    for mod in ("backend.env", "backend.identity", "backend.store", "backend.app"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import backend.app as m
    m = importlib.reload(m)
    m.store.init_db()
    with TestClient(m.app, base_url="http://slop.lan") as c:
        c._mod = m
        yield c


WEB = [
    {"kind": "package", "names": "nginx\ncurl"},
    {"kind": "service", "name": "nginx", "enable": True},
    {"kind": "file", "path": "/etc/nginx/conf.d/x.conf",
     "contents": "server {\n  listen 80;\n}\n", "mode": "0644"},
]


# ---- the generator ---------------------------------------------------------
def test_a_list_of_things_becomes_a_salt_state():
    from backend import build
    doc = yaml.safe_load(build.to_sls(WEB))
    assert len(doc) == 3, f"three items, {len(doc)} states: {list(doc)}"
    mods = [next(iter(v)) for v in doc.values()]
    assert mods == ["pkg.installed", "service.running", "file.managed"], mods


def test_the_order_of_the_list_is_the_order_of_the_state():
    """Salt applies in file order. If the list and the file disagree, "install
    it, then start it" silently becomes "start it, then install it"."""
    from backend import build
    forward = list(yaml.safe_load(build.to_sls(WEB)))
    backward = list(yaml.safe_load(build.to_sls(list(reversed(WEB)))))
    assert forward == list(reversed(backward)), (forward, backward)


@pytest.mark.parametrize("item,field,value", [
    ({"kind": "file", "path": "/etc/odd: name.conf", "contents": "x"}, "name",
     "/etc/odd: name.conf"),
    ({"kind": "file", "path": "/e", "contents": "- not\n- a list"}, "contents",
     "- not\n- a list"),
    ({"kind": "sysctl", "name": "a.b", "value": "yes"}, "value", "yes"),
    ({"kind": "sysctl", "name": "a.b", "value": "10"}, "value", "10"),
    ({"kind": "dir", "path": "/o", "mode": "0755"}, "mode", "0755"),
    ({"kind": "line", "path": "/etc/hosts", "line": "10.0.0.1 db # primary"},
     "content", "10.0.0.1 db # primary"),
    ({"kind": "command", "command": "sh -c 'echo \"hi\" > /tmp/x'"}, "name",
     "sh -c 'echo \"hi\" > /tmp/x'"),
])
def test_a_value_survives_being_turned_into_yaml(item, field, value):
    """The whole reason this is dumped rather than formatted. Every one of
    these is a value that a string template turns into a DIFFERENT document:
    "yes" becomes True, "0755" becomes the integer 493, a colon splits a
    mapping, and a body starting "- " becomes a list."""
    from backend import build
    doc = yaml.safe_load(build.to_sls([item]))
    args = list(list(doc.values())[0].values())[0]
    flat = {k: v for a in args for k, v in a.items()}
    assert flat.get(field) == value, f"{field} came back as {flat.get(field)!r}"


def test_two_items_of_one_kind_do_not_collide():
    """Salt state IDs are keys in one mapping: a duplicate silently replaces
    the first, so one of the two declarations just does not happen."""
    from backend import build
    doc = yaml.safe_load(build.to_sls([
        {"kind": "service", "name": "nginx"},
        {"kind": "service", "name": "nginx"},
    ]))
    assert len(doc) == 2, f"one of the two was swallowed: {doc}"


def test_a_missing_required_field_is_refused_with_all_the_others():
    """One error per save makes a four-field mistake take four round trips."""
    from backend import build
    bad = build.normalise([{"kind": "file", "path": "", "contents": ""},
                           {"kind": "sysctl", "name": "", "value": ""}])
    msgs = build.problems(bad)
    assert len(msgs) >= 4, msgs
    assert any("path" in m.lower() for m in msgs), msgs


def test_an_unknown_kind_is_dropped_not_passed_through():
    """Whatever arrives here ends up in a file that runs as root."""
    from backend import build
    items = build.normalise([{"kind": "exploit", "cmd": "rm -rf /"},
                             {"kind": "group", "name": "ok"}])
    assert [i["kind"] for i in items] == ["group"], items
    assert "rm -rf" not in build.to_sls(items)


def test_an_unknown_field_is_dropped_too():
    from backend import build
    items = build.normalise([{"kind": "group", "name": "devs", "onlyif": "rm -rf /"}])
    assert "onlyif" not in items[0], items[0]
    assert "rm -rf" not in build.to_sls(items)


def test_the_catalog_describes_every_kind_it_can_build():
    """The console draws its forms from this. A kind the generator knows and
    the catalog omits is a kind nobody can reach; the reverse is a form whose
    fields are silently discarded."""
    from backend import build
    listed = {k["kind"] for k in build.catalog()}
    assert listed == set(build.KINDS), listed ^ set(build.KINDS)
    for entry in build.catalog():
        assert entry["fields"], f"{entry['kind']} has no fields"
        for f in entry["fields"]:
            assert f["type"] in ("text", "lines", "body", "bool", "choice"), f


# ---- the API ---------------------------------------------------------------
def test_saving_items_stores_generated_sls_and_the_list(cl):
    r = cl.post("/api/states/web", headers=OPERATOR, json={"items": WEB})
    assert r.status_code == 200, r.text
    got = cl.get("/api/states/web", headers=OPERATOR).json()["state"]
    assert got["source"] == "builder"
    assert len(got["items"]) == 3
    assert "pkg.installed" in got["sls"], got["sls"]


def test_the_preview_is_the_same_string_that_gets_saved(cl):
    """The YAML view is read-only for a builder state precisely so it can make
    this promise. If the preview came from somewhere else it would be a
    plausible-looking description of a different file."""
    prev = cl.post("/api/states/web/preview", headers=OPERATOR,
                   json={"items": WEB}).json()["sls"]
    cl.post("/api/states/web", headers=OPERATOR, json={"items": WEB})
    saved = cl.get("/api/states/web", headers=OPERATOR).json()["state"]["sls"]
    assert prev == saved


def test_a_preview_saves_nothing(cl):
    cl.post("/api/states/ghost/preview", headers=OPERATOR, json={"items": WEB})
    assert cl.get("/api/states/ghost", headers=OPERATOR).status_code == 404


def test_yaml_is_still_a_way_to_write_a_state(cl):
    sls = "nginx:\n  pkg.installed: []\n"
    assert cl.post("/api/states/hand", headers=OPERATOR,
                   json={"sls": sls}).status_code == 200
    got = cl.get("/api/states/hand", headers=OPERATOR).json()["state"]
    assert got["source"] == "yaml"
    assert got["sls"] == sls, "typed YAML was not stored as typed"
    assert got["items"] == []


def test_a_state_remembers_which_editor_wrote_it(cl):
    cl.post("/api/states/a", headers=OPERATOR, json={"items": WEB})
    cl.post("/api/states/b", headers=OPERATOR, json={"sls": "x:\n  test.nop: []\n"})
    by_name = {s["name"]: s["source"] for s in
               cl.get("/api/states", headers=OPERATOR).json()["states"]}
    assert by_name == {"a": "builder", "b": "yaml"}, by_name


def test_taking_a_state_over_as_yaml_leaves_the_earlier_versions_alone(cl):
    """Switching is one-way for the state going forward, but the versions
    written in the builder are still the versions that were written in it —
    "what did this say yesterday" has to keep working."""
    cl.post("/api/states/web", headers=OPERATOR, json={"items": WEB})
    taken = cl.post("/api/states/web/preview", headers=OPERATOR,
                    json={"items": WEB}).json()["sls"] + "\nextra_thing:\n  test.nop: []\n"
    cl.post("/api/states/web", headers=OPERATOR, json={"sls": taken})

    v1 = cl.get("/api/states/web", headers=OPERATOR, params={"version": 1}).json()["state"]
    v2 = cl.get("/api/states/web", headers=OPERATOR, params={"version": 2}).json()["state"]
    assert v1["source"] == "builder" and v1["items"], "v1 lost its item list"
    assert v2["source"] == "yaml" and v2["items"] == []


def test_asking_for_an_old_version_does_not_hand_back_the_newest(cl):
    """It used to: the route walked the version list and then returned
    latest_state() regardless, so "what did this say before" answered with what
    it says now — on a screen whose only purpose is telling them apart."""
    cl.post("/api/states/web", headers=OPERATOR, json={"items": WEB})
    cl.post("/api/states/web", headers=OPERATOR,
            json={"items": WEB + [{"kind": "group", "name": "later"}]})
    v1 = cl.get("/api/states/web", headers=OPERATOR, params={"version": 1}).json()["state"]
    v2 = cl.get("/api/states/web", headers=OPERATOR, params={"version": 2}).json()["state"]
    assert v1["version"] == 1 and v2["version"] == 2
    assert "later" not in v1["sls"], "version 1 came back with version 2's content"
    assert "later" in v2["sls"]


def test_bad_items_are_refused_before_anything_is_stored(cl):
    r = cl.post("/api/states/broken", headers=OPERATOR,
                json={"items": [{"kind": "file", "path": "", "contents": ""}]})
    assert r.status_code == 400, r.text
    assert cl.get("/api/states/broken", headers=OPERATOR).status_code == 404


def test_an_auditor_can_read_but_cannot_declare(cl):
    """The console is served to anyone the gateway authenticates and renders
    read-only for a role that cannot write. Every READ endpoint it calls used
    to demand an operator, so an auditor got a console where each panel said
    "This needs an operator account" — a read-only mode that could not read."""
    cl.post("/api/states/web", headers=OPERATOR, json={"items": WEB})
    for path in ("/api/states", "/api/kinds", "/api/states/web",
                 "/api/assignments", "/api/rules", "/api/runs"):
        assert cl.get(path, headers=AUDITOR).status_code == 200, \
            f"an auditor cannot read {path}"
    # ...and still cannot change anything.
    for method, path, body in (
            ("post", "/api/states/web", {"items": WEB}),
            ("post", "/api/states/web/preview", {"items": WEB}),
            ("post", "/api/assign", {"target": "h1", "state": "web"}),
            ("post", "/api/rules", {"name": "r", "action": "notify"}),
            ("delete", "/api/rules/r", None),
            ("post", "/api/hosts/h1/apply", {}),
    ):
        kw = {"headers": AUDITOR}
        if body is not None:
            kw["json"] = body
        r = getattr(cl, method)(path, **kw)
        assert r.status_code == 403, f"an auditor could {method.upper()} {path}"


def test_signing_out_is_still_required(cl):
    """Relaxing the read rule must not have relaxed it to nobody."""
    for path in ("/api/states", "/api/kinds", "/api/runs"):
        assert cl.get(path).status_code == 401, f"{path} is readable unauthenticated"


def test_what_the_host_runs_is_the_generated_sls(cl):
    """The builder changes how a state is written and nothing else: the agent
    endpoint still hands out a salt-call command built from SLS."""
    cl.post("/api/states/web", headers=OPERATOR, json={"items": WEB})
    cl.post("/api/assign", headers=OPERATOR, json={"target": "h1", "state": "web"})
    d = cl.get("/api/agent/desired", headers={"authorization": "Bearer " + TOKEN},
               params={"host_id": "h1"}).json()
    assert d["apply"], d
    cmd = d["apply"][0]["command"]
    assert "salt-call" in cmd and "nginx" in cmd, cmd
