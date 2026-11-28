"""A role is held per app, in an organization — not once for the whole platform.

ASKED FOR: "The SLOP RBAC needs an overhaul. User/Groups and organizations should
be determined at the SLOP level not individual app levels", and then: "I would
like there to [be] admin, operator, auditor roles but I can assign them different
for each app. For instance an admin of controller but not of flashback etc."

What was there: ONE `users.role` column, stamped onto X-Sysible-Role for every
app by /auth/verify. An operator was an operator of everything; `organizations`
lived inside SLEP, where the other apps could not see it; there were no groups.

Four decisions were settled before any of this was written, and each one is
pinned below because each is the kind of thing a later change erodes quietly:

  * Grants are per (subject, organization, app, role). A group grant reaches
    every member.
  * Platform administrator is its OWN privilege, deliberately not the same as
    being an administrator of every app.
  * The wire value stays `superuser`. Every app validates X-Sysible-Role against
    its own tuple and REFUSES a word it does not know, so renaming it would lock
    every app out until all of them shipped the new word together. The UI says
    "Admin"; the wire does not change.
  * Enforcement is OFF until somebody turns it on, and the upgrade NAMES every
    account that would lose access. Turning the model on is a revocation, and an
    upgrade that silently removes an operator's access to every app — then shows
    them a login that works and an app that 403s — is indefensible.
"""
import importlib
import os
import sys
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

ADMIN_PW = "adminpassword123"
HDR = {"origin": "http://slop.lan"}


@pytest.fixture()
def idp(tmp_path):
    from starlette.testclient import TestClient
    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path),
        SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin",
        SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0",
        SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    m = importlib.reload(m)
    with TestClient(m.app, base_url="http://slop.lan") as c:
        yield c, m


def _tok(c):
    c.get("/login", follow_redirects=False)
    return c.cookies.get("sysible_csrf")


def _login(c, user=  "admin", pw=ADMIN_PW):
    return c.post("/login", data={"username": user, "password": pw, "csrf": _tok(c)},
                  headers=HDR, follow_redirects=False)


def _mkuser(m, name, role="operator"):
    m._upsert_user(name, "password12345", role, must_change=False)


# ------------------------------------------------------- resolving a grant

def test_a_grant_applies_to_the_app_it_names_and_no_other(idp):
    c, m = idp
    _mkuser(m, "dana")
    m.set_grant("user", "dana", "controller", "superuser")
    assert m.effective_role("dana", "controller") == "superuser"
    assert m.effective_role("dana", "flashback") is None, (
        "the exact case that was asked for: admin of the Controller, not of Flashback")


def test_a_wildcard_grant_reaches_every_app(idp):
    c, m = idp
    _mkuser(m, "dana")
    m.set_grant("user", "dana", m.ANY_APP, "operator")
    assert all(m.effective_role("dana", a) == "operator" for a in m.APPS)


def test_the_most_privileged_matching_grant_wins(idp):
    """Operator everywhere plus admin on one app is a normal shape, and it must
    not matter which row is read first."""
    c, m = idp
    _mkuser(m, "dana")
    m.set_grant("user", "dana", m.ANY_APP, "operator")
    m.set_grant("user", "dana", "slep", "superuser")
    assert m.effective_role("dana", "slep") == "superuser"
    assert m.effective_role("dana", "connect") == "operator"


def test_a_group_grant_reaches_its_members(idp):
    c, m = idp
    _mkuser(m, "dana")
    m.create_group("platform")
    gid = m.list_groups()[0]["id"]
    m.add_group_member(gid, "dana")
    m.set_grant("group", "platform", "slep", "operator")
    assert m.effective_role("dana", "slep") == "operator"


def test_leaving_a_group_takes_the_access_with_it(idp):
    c, m = idp
    _mkuser(m, "dana")
    m.create_group("platform")
    gid = m.list_groups()[0]["id"]
    m.add_group_member(gid, "dana")
    m.set_grant("group", "platform", "slep", "operator")
    m.remove_group_member(gid, "dana")
    assert m.effective_role("dana", "slep") is None


def test_an_org_grant_does_not_leak_into_another_org(idp):
    c, m = idp
    _mkuser(m, "dana")
    m.create_org("acme")
    m.create_org("other")
    acme, other = [o["id"] for o in m.list_orgs()[:2]][0], [o["id"] for o in m.list_orgs()][-1]
    m.set_grant("user", "dana", "slep", "operator", org_id=acme)
    assert m.effective_role("dana", "slep", org_id=acme) == "operator"
    assert m.effective_role("dana", "slep", org_id=other) is None


def test_an_org_wide_grant_is_not_narrower_than_one_org(idp):
    """"operator everywhere" must still answer yes when asked about one org."""
    c, m = idp
    _mkuser(m, "dana")
    m.create_org("acme")
    acme = m.list_orgs()[0]["id"]
    m.set_grant("user", "dana", "slep", "operator")          # org_id NULL
    assert m.effective_role("dana", "slep", org_id=acme) == "operator"


def test_a_user_with_nothing_holds_nothing(idp):
    c, m = idp
    _mkuser(m, "nobody")
    assert all(m.effective_role("nobody", a) is None for a in m.APPS)


def test_an_unknown_app_is_never_granted(idp):
    c, m = idp
    _mkuser(m, "dana")
    m.set_grant("user", "dana", m.ANY_APP, "superuser")
    assert m.effective_role("dana", "wordpress") is None


@pytest.mark.parametrize("kind,subject,app,role", [
    ("user", "ghost", "slep", "operator"),       # no such user
    ("group", "ghosts", "slep", "operator"),     # no such group
    ("user", "dana", "wordpress", "operator"),   # no such app
    ("user", "dana", "slep", "wizard"),          # no such role
])
def test_a_grant_that_cannot_mean_anything_is_refused(idp, kind, subject, app, role):
    """`app` reaches SQL and `role` reaches X-Sysible-Role, where an app refuses a
    word it does not know. Both are validated where the grant is made."""
    c, m = idp
    _mkuser(m, "dana")
    with pytest.raises(ValueError):
        m.set_grant(kind, subject, app, role)


# ----------------------------------------------------- the platform admin

def test_platform_admin_is_not_the_same_as_admin_of_everything(idp):
    """Settled deliberately. Someone who administers SLOP — creating orgs and
    granting roles — need not be an administrator inside any app."""
    c, m = idp
    _mkuser(m, "dana")
    with m._db() as db:
        db.execute("UPDATE users SET platform_admin=1 WHERE username='dana'")
    assert all(m.effective_role("dana", a) is None for a in m.APPS), (
        "being a platform admin silently granted access to every app")


def test_a_platform_admin_is_refused_at_the_gate_like_anyone_else(idp):
    """The separation has to hold where it is ENFORCED, not only in the resolver.
    A mutation that waved platform admins past the per-app check in /auth/verify
    left the resolver test above perfectly green, which is exactly the shape of
    hole that makes an authorization test worthless."""
    c, m = idp
    _mkuser(m, "dana", role="auditor")
    with m._db() as db:
        db.execute("UPDATE users SET platform_admin=1 WHERE username='dana'")
    m._set_setting("rbac_enforced", "1")
    _login(c, "dana", "password12345")
    assert c.get("/auth/verify", headers={"X-Sysible-App": "/slep"}).status_code == 403, (
        "a platform admin was let into an app it holds no grant for")
    # and granting one lets them in, so the refusal is about the grant and not
    # about the flag
    m.set_grant("user", "dana", "slep", "operator")
    r = c.get("/auth/verify", headers={"X-Sysible-App": "/slep"})
    assert r.status_code == 204 and r.headers["X-Sysible-Role"] == "operator"


# ------------------------------------------------------------- migration

def test_the_upgrade_carries_administrators_across_and_nobody_else(idp):
    """"Admins only carry across"; everyone else is re-granted deliberately."""
    c, m = idp
    _mkuser(m, "opsy", role="operator")
    _mkuser(m, "reader", role="auditor")
    with m._db() as db:
        db.execute("DELETE FROM platform_settings WHERE key='rbac_migrated'")
        db.execute("DELETE FROM grants")
    m.migrate_rbac()
    assert m.effective_role("admin", "slep") == "superuser", "the bootstrap admin lost access"
    assert m.effective_role("opsy", "slep") is None
    assert m.effective_role("reader", "slep") is None


def test_the_upgrade_only_adds_rows(idp):
    """It runs on a live platform. Nothing may be revoked by an upgrade; the
    revocation is the separate switch."""
    c, m = idp
    assert m.rbac_enforced() is False, "an upgrade turned enforcement on by itself"


def test_running_the_migration_twice_changes_nothing(idp):
    c, m = idp
    before = [dict(r) for r in m.list_grants()]
    m.migrate_rbac()
    assert [dict(r) for r in m.list_grants()] == before


def test_the_report_names_everyone_who_would_lose_access(idp):
    """The whole reason the switch is separate. An operator must not discover
    this at a 403."""
    c, m = idp
    _mkuser(m, "opsy", role="operator")
    rows = {r["username"]: r for r in m.access_report()}
    assert rows["opsy"]["loses"], "the report does not mention an account that loses everything"
    assert set(rows["opsy"]["loses"]) == set(m.APPS)
    assert rows["admin"]["loses"] == [], "the admin carried across but is reported as losing access"
    assert set(rows["admin"]["keeps"]) == set(m.APPS)


# ---------------------------------------------------- what /auth/verify does

def test_with_enforcement_off_nothing_changes_at_all(idp):
    """Byte for byte the old behaviour: one global role, stamped for every app."""
    c, m = idp
    _mkuser(m, "opsy", role="operator")
    _login(c, "opsy", "password12345")
    for app in ("/slep", "/controller", "/flashback"):
        r = c.get("/auth/verify", headers={"X-Sysible-App": app})
        assert r.status_code == 204, app
        assert r.headers["X-Sysible-Role"] == "operator", app


def test_with_enforcement_on_the_role_is_the_one_granted_for_that_app(idp):
    c, m = idp
    _mkuser(m, "dana", role="auditor")
    m.set_grant("user", "dana", "slep", "superuser")
    m.set_grant("user", "dana", "connect", "operator")
    m._set_setting("rbac_enforced", "1")
    _login(c, "dana", "password12345")
    assert c.get("/auth/verify", headers={"X-Sysible-App": "/slep"}
                 ).headers["X-Sysible-Role"] == "superuser"
    assert c.get("/auth/verify", headers={"X-Sysible-App": "/connect"}
                 ).headers["X-Sysible-Role"] == "operator"
    assert c.get("/auth/verify", headers={"X-Sysible-App": "/flashback"}).status_code == 403


def test_the_wire_value_is_still_superuser(idp):
    """Every app validates X-Sysible-Role against its own tuple and refuses a word
    it does not know. The UI says "Admin"; changing the wire would lock every app
    out until all of them shipped the new word together."""
    c, m = idp
    assert m.ROLES == ("superuser", "operator", "auditor")
    assert m.ROLE_LABELS["superuser"] == "Admin"
    _mkuser(m, "dana", role="auditor")
    m.set_grant("user", "dana", "slep", "superuser")
    m._set_setting("rbac_enforced", "1")
    _login(c, "dana", "password12345")
    r = c.get("/auth/verify", headers={"X-Sysible-App": "/slep"})
    assert r.headers["X-Sysible-Role"] == "superuser", r.headers


def test_the_portal_stays_open_to_someone_holding_nothing(idp):
    """The portal is a launcher that shows what you can and cannot reach. Locking
    it would leave the only page that explains the situation behind the lock."""
    c, m = idp
    _mkuser(m, "nobody", role="auditor")
    m._set_setting("rbac_enforced", "1")
    _login(c, "nobody", "password12345")
    assert c.get("/auth/verify").status_code == 204, "the portal itself refused a signed-in user"


@pytest.mark.parametrize("hdr", ["/slep", "slep", "/slep/", "/slep/projects?x=1", "SLEP"])
def test_the_app_is_read_from_the_mount_prefix_however_it_arrives(idp, hdr):
    c, m = idp
    assert m.app_from_path(hdr) == "slep", hdr


@pytest.mark.parametrize("hdr", ["", "/", "/nonsense", "/admin", "../slep"])
def test_anything_that_is_not_an_app_reads_as_the_portal(idp, hdr):
    """The safe direction: the portal is open, an app is not."""
    c, m = idp
    assert m.app_from_path(hdr) == ""


# ------------------------------------------------- the browser must not loop

def test_someone_without_a_grant_gets_a_page_not_a_redirect_loop(idp):
    """The gateway answers a 403 from /auth/verify by redirecting to /login, and
    /login redirects a signed-in user onward — so without this the browser
    ping-pongs between the two forever. Exactly the trap the must_change case
    already carried a comment about."""
    c, m = idp
    _mkuser(m, "nobody", role="auditor")
    m._set_setting("rbac_enforced", "1")
    _login(c, "nobody", "password12345")
    r = c.get("/login?next=/slep", follow_redirects=False)
    assert r.status_code == 403, f"expected the no-access page, got {r.status_code}"
    assert "No access" in r.text
    assert "slep" in r.text.lower()


def test_the_no_access_page_says_what_the_account_can_open(idp):
    c, m = idp
    _mkuser(m, "dana", role="auditor")
    m.set_grant("user", "dana", "connect", "operator")
    m._set_setting("rbac_enforced", "1")
    _login(c, "dana", "password12345")
    r = c.get("/login?next=/slep", follow_redirects=False)
    assert "Connect" in r.text, r.text[-600:]


def test_a_user_who_does_hold_the_grant_is_sent_through(idp):
    c, m = idp
    _mkuser(m, "dana", role="auditor")
    m.set_grant("user", "dana", "slep", "operator")
    m._set_setting("rbac_enforced", "1")
    _login(c, "dana", "password12345")
    r = c.get("/login?next=/slep", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/slep", r.headers


# ------------------------------------------------------------ the gateway

def test_the_gateway_tells_the_idp_which_app_is_being_asked_about():
    """A role is granted per app, so /auth/verify cannot answer without it. It is
    stamped explicitly rather than inferred from X-Forwarded-Uri, which is a
    forward_auth default this file does not state."""
    cf = open(os.path.join(os.path.dirname(HERE), "gateway", "Caddyfile"),
              encoding="utf-8").read()
    assert cf.count("header_up X-Sysible-App {args[0]}") == 2, (
        "both app snippets must stamp the app on the auth subrequest")


def test_a_client_cannot_claim_which_app_it_is_asking_about():
    """Asserting "/visualizer" while fetching /controller would pick the wrong
    grant. Stripped in every snippet that strips the other identity headers."""
    cf = open(os.path.join(os.path.dirname(HERE), "gateway", "Caddyfile"),
              encoding="utf-8").read()
    assert cf.count("request_header -X-Sysible-App") == cf.count("request_header -X-Sysible-User")


# ------------------------------------------------------------- the admin UI

def test_access_administration_needs_a_platform_admin(idp):
    c, m = idp
    _mkuser(m, "opsy", role="operator")
    _login(c, "opsy", "password12345")
    assert c.get("/admin/access", follow_redirects=False).status_code == 403


def test_a_platform_admin_can_open_it(idp):
    c, m = idp
    _login(c)
    r = c.get("/admin/access", follow_redirects=False)
    assert r.status_code == 200
    assert "Grants" in r.text and "Organizations" in r.text and "Groups" in r.text


def test_the_switch_warns_before_it_is_flipped(idp):
    """It must not read as a tidy-up. The page names the accounts that lose
    access, with the count, before offering the button."""
    c, m = idp
    _mkuser(m, "opsy", role="operator")
    _login(c)
    r = c.get("/admin/access")
    assert "would remove access" in r.text
    assert "opsy" in r.text


def test_every_mutation_needs_the_csrf_token(idp):
    c, m = idp
    _login(c)
    for path, data in (("/admin/access/orgs", {"name": "acme"}),
                       ("/admin/access/groups", {"name": "platform"}),
                       ("/admin/access/enforce", {"on": "1"})):
        r = c.post(path, data=data, headers=HDR, follow_redirects=False)
        assert r.status_code == 403, f"{path} accepted a request with no token"


def test_the_ui_calls_it_admin_while_the_wire_says_superuser(idp):
    c, m = idp
    _login(c)
    r = c.get("/admin/access")
    assert ">Admin<" in r.text or "Admin</option>" in r.text, (
        "the page shows the raw wire value to the operator")


# ------------------------------------------------- revocation takes effect now

def test_revoking_a_grant_locks_the_app_immediately(idp):
    """Not at next sign-in. /auth/verify re-resolves on EVERY request, so the
    session that was already open stops working the moment the grant goes.

    This is the half of an authorization change that is easy to get wrong and
    impossible to notice: the person being removed keeps working, and whoever
    removed them has no reason to check."""
    c, m = idp
    _mkuser(m, "dana", role="auditor")
    m.set_grant("user", "dana", "slep", "operator")
    m._set_setting("rbac_enforced", "1")
    _login(c, "dana", "password12345")
    assert c.get("/auth/verify", headers={"X-Sysible-App": "/slep"}).status_code == 204

    gid = [g["id"] for g in m.list_grants()
           if g["subject"] == "dana" and g["app"] == "slep"][0]
    m.revoke_grant(gid)

    assert c.get("/auth/verify", headers={"X-Sysible-App": "/slep"}).status_code == 403, (
        "a revoked grant left an open session still able to reach the app")


def test_removing_someone_from_a_group_locks_them_out_immediately(idp):
    """Same again through the indirection a group adds, because that is where
    an implementation is most likely to have cached something."""
    c, m = idp
    _mkuser(m, "dana", role="auditor")
    m.create_group("platform")
    gid = m.list_groups()[0]["id"]
    m.add_group_member(gid, "dana")
    m.set_grant("group", "platform", "slep", "operator")
    m._set_setting("rbac_enforced", "1")
    _login(c, "dana", "password12345")
    assert c.get("/auth/verify", headers={"X-Sysible-App": "/slep"}).status_code == 204
    m.remove_group_member(gid, "dana")
    assert c.get("/auth/verify", headers={"X-Sysible-App": "/slep"}).status_code == 403, (
        "leaving the group left an open session still able to reach the app")


def test_a_role_downgrade_is_also_immediate(idp):
    """The quieter half: not losing access, but losing WRITE access. An operator
    demoted to auditor must stop being stamped operator on the next request."""
    c, m = idp
    _mkuser(m, "dana", role="auditor")
    m.set_grant("user", "dana", "slep", "superuser")
    m._set_setting("rbac_enforced", "1")
    _login(c, "dana", "password12345")
    r = c.get("/auth/verify", headers={"X-Sysible-App": "/slep"})
    assert r.headers["X-Sysible-Role"] == "superuser"
    m.set_grant("user", "dana", "slep", "auditor")        # same row, lower role
    r = c.get("/auth/verify", headers={"X-Sysible-App": "/slep"})
    assert r.headers["X-Sysible-Role"] == "auditor", (
        "a demotion did not reach an already-open session")


def test_setting_a_grant_twice_leaves_one_row(idp):
    """The mechanism behind the demotion bug, pinned directly.

    SQLite treats NULLs as DISTINCT in a UNIQUE index, so `ON CONFLICT
    (subject_kind, subject, org_id, app) DO UPDATE` never fired for an ORG-WIDE
    grant — org_id IS NULL, the common case — and quietly inserted a second row
    beside the first. Since effective_role takes the most privileged match, the
    old row kept winning and a demotion did nothing at all."""
    c, m = idp
    _mkuser(m, "dana")
    m.set_grant("user", "dana", "slep", "superuser")
    m.set_grant("user", "dana", "slep", "auditor")
    rows = [g for g in m.list_grants() if g["subject"] == "dana" and g["app"] == "slep"]
    assert len(rows) == 1, "a second grant row was inserted instead of replacing: %r" % (
        [(r["org_id"], r["role"]) for r in rows],)
    assert rows[0]["role"] == "auditor"
    assert m.effective_role("dana", "slep") == "auditor"


def test_the_same_holds_for_a_grant_scoped_to_an_organization(idp):
    """The non-NULL half worked all along; it must keep working."""
    c, m = idp
    _mkuser(m, "dana")
    m.create_org("acme")
    org = m.list_orgs()[0]["id"]
    m.set_grant("user", "dana", "slep", "superuser", org_id=org)
    m.set_grant("user", "dana", "slep", "auditor", org_id=org)
    rows = [g for g in m.list_grants() if g["subject"] == "dana" and g["app"] == "slep"]
    assert len(rows) == 1 and rows[0]["role"] == "auditor"


def test_an_org_grant_and_an_org_wide_grant_are_different_rows(idp):
    """...and the fix must not collapse them. "operator everywhere" and
    "superuser in acme" are two separate, legitimate grants."""
    c, m = idp
    _mkuser(m, "dana")
    m.create_org("acme")
    org = m.list_orgs()[0]["id"]
    m.set_grant("user", "dana", "slep", "operator")               # org-wide
    m.set_grant("user", "dana", "slep", "superuser", org_id=org)  # in acme only
    rows = [g for g in m.list_grants() if g["subject"] == "dana" and g["app"] == "slep"]
    assert len(rows) == 2, "the two grants were collapsed into one"
    assert m.effective_role("dana", "slep") == "operator"
    assert m.effective_role("dana", "slep", org_id=org) == "superuser"
