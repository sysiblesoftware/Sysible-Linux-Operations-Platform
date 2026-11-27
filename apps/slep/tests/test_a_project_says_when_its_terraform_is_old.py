"""A project's Terraform is written once and never updated. Say so.

The .tf is generated at project creation and then lives in the project directory.
Updating SLEP does not touch it, so a fix to the generator reaches NEW projects
only and an existing one keeps whatever it was born with — silently.

That cost an evening. A project created before the host-passthrough CPU was added
builds VMs that abort in glibc at boot on Rocky/Alma 9+, Arch and openSUSE, while
Debian and Ubuntu boot fine on the same emulated CPU. The visible symptom is not a
boot error: it is the provider timing out with "couldn't retrieve IP address of
domain", which sends you to investigate DHCP, bridges and the guest agent, none of
which are wrong. Four of four VMs failed on permissions; then two of four failed on
this, and the split by distro was the only clue.
"""
from pathlib import Path

import pytest

from backend import db, infra

HERE = Path(__file__).resolve().parents[1]
VIEWS = HERE / "webgui" / "frontend" / "src" / "views"


# --------------------------------------------------------------- the stamp

def test_generated_terraform_says_which_generator_wrote_it():
    files = infra.generate("libvirt", {"name_prefix": "t", "vm_count": 1})
    assert infra.tf_generator_version(files["main.tf"]) == infra.TF_GENERATOR_VERSION


def test_the_stamp_is_applied_once_for_every_provider():
    """A provider added later must not ship unstamped and read as 'ancient'."""
    import inspect
    src = inspect.getsource(infra.generate)
    assert '_tf_header()' in src, "the stamp is not applied in generate()"
    for prov in infra._RENDERERS:
        try:
            files = infra.generate(prov, {"name_prefix": "t", "vm_count": 1})
        except Exception:
            continue          # a provider needing more options is not this test's business
        if files.get("main.tf"):
            assert infra.tf_generator_version(files["main.tf"]) == infra.TF_GENERATOR_VERSION, prov


def test_an_unstamped_file_reads_as_predating_the_stamp():
    assert infra.tf_generator_version('resource "libvirt_domain" "vm" {}') == 1
    assert infra.tf_generator_version("") == 1
    assert infra.tf_generator_version(None) == 1


def test_the_header_says_regenerating_rewrites_the_file():
    """Someone will hand-edit main.tf. They should know what Edit VMs does to it."""
    h = infra._tf_header()
    assert "Regenerate Terraform" in h and "rewrites this file" in h
    assert "Destroy the VMs first" in h


def test_every_bump_explains_itself():
    """'Your Terraform is old' is useless without 'and here is what it is missing'."""
    for v, text in infra.TF_GENERATOR_CHANGES.items():
        assert isinstance(v, int) and v > 1, v
        assert len(text) > 60, "change %s has no real explanation" % v
    assert max(infra.TF_GENERATOR_CHANGES) <= infra.TF_GENERATOR_VERSION, (
        "a change is listed for a version the generator never reaches")


def test_the_cpu_fix_is_the_change_that_is_described():
    text = " ".join(infra.TF_GENERATOR_CHANGES.values())
    assert "host-passthrough" in text
    assert "couldn't retrieve IP address" in text, (
        "the note does not name the symptom, which is what an operator actually sees")


def test_what_a_project_is_missing_is_computed_from_its_own_version():
    assert len(infra.tf_missing_changes(1)) == len(infra.TF_GENERATOR_CHANGES)
    assert infra.tf_missing_changes(infra.TF_GENERATOR_VERSION) == []
    assert infra.tf_missing_changes(99) == []


# ------------------------------------------------------------ on the screen

@pytest.fixture()
def two_projects(client):
    old = client.post("/projects", json={"name": "old-tf"}).json()["id"]
    new = client.post("/projects", json={"name": "new-tf"}).json()["id"]
    db.set_infra(old, "libvirt")
    db.set_infra(new, "libvirt")
    (db.project_dir(old) / "main.tf").write_text('resource "libvirt_domain" "vm" {}\n')
    (db.project_dir(new) / "main.tf").write_text(
        infra._tf_header() + 'resource "libvirt_domain" "vm" {}\n')
    return old, new


def test_the_list_says_which_generator_each_project_has(client, two_projects):
    old, new = two_projects
    rows = {r["project_id"]: r for r in client.get("/infra").json()["infra"]}
    assert rows[old]["tf_generator"] == 1
    assert rows[new]["tf_generator"] == infra.TF_GENERATOR_VERSION
    assert rows[old]["tf_current"] == infra.TF_GENERATOR_VERSION


def test_only_the_old_project_is_flagged(client, two_projects):
    old, new = two_projects
    rows = {r["project_id"]: r for r in client.get("/infra").json()["infra"]}
    assert rows[old]["tf_missing"], "an old project is not flagged"
    assert rows[new]["tf_missing"] == [], "a current project is flagged anyway"


def test_a_project_with_no_terraform_is_not_flagged(client):
    """Not every project has infrastructure; absence is not staleness."""
    pid = client.post("/projects", json={"name": "no-tf"}).json()["id"]
    db.set_infra(pid, "libvirt")
    row = next(r for r in client.get("/infra").json()["infra"] if r["project_id"] == pid)
    assert row["tf_generator"] is None
    assert row["tf_missing"] == []


def test_an_unreadable_terraform_never_breaks_the_list(client, monkeypatch):
    pid = client.post("/projects", json={"name": "bad-tf"}).json()["id"]
    db.set_infra(pid, "libvirt")
    (db.project_dir(pid) / "main.tf").write_bytes(b"\xff\xfe\x00binary")
    rows = client.get("/infra").json()["infra"]
    assert any(r["project_id"] == pid for r in rows), "the list died on one bad file"


def test_the_row_shows_it_and_says_what_to_do():
    ui = (VIEWS / "Infrastructure.jsx").read_text(encoding="utf-8")
    # The GUARD, not just the words. Gating the block off with `{false && (` left
    # every string in the file and passed a substring check -- the fourth time in
    # one session that "present in the source" was mistaken for "rendered".
    assert "{(r.tf_missing || []).length > 0 && (" in ui, (
        "the notice is in the file but its render condition is gone")
    assert "tf_missing" in ui, "the flag is computed but never shown"
    assert "generated by an older SLEP" in ui
    assert "Edit VMs" in ui, "the notice does not say how to fix it"
    assert "Destroy" in ui, "the notice does not warn that regenerating rewrites main.tf"
    css = (VIEWS.parent / "styles.css").read_text(encoding="utf-8")
    assert ".tf-stale" in css, "the notice has no styling, so it reads as body text"


def test_the_notice_lists_what_is_missing_rather_than_just_saying_old():
    ui = (VIEWS / "Infrastructure.jsx").read_text(encoding="utf-8")
    assert "r.tf_missing.map" in ui, "the notice never renders the actual changes"
