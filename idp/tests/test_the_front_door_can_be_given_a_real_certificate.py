"""SLOP terminates TLS for the whole platform, so its certificate is the one
that matters — and it was the only one with no way to replace it.

A browser only ever talks to the gateway: the apps live behind it on one origin,
addressed by path. So the gateway's certificate is the only one anybody's
browser judges, and the one producing the warning on every first visit. Caddy
mints it itself and the Caddyfile's advice was to "drop a real cert in front" —
put something else in front of the front door. Meanwhile the Controller, whose
certificate no browser ever sees, has had install / regenerate / trust-bundle
endpoints for ages.

THE DANGEROUS PART IS THE ORDER. A Caddy snippet naming files that are not
there does not degrade; Caddy refuses to start:

    Error: loading http app module: ... loading certificates:
    open /etc/caddy/tls/cert.pem: no such file or directory

That is :443 for the whole platform — including the page an operator would use
to undo it. Verified against a real caddy, which is also what the snippet test
below adapts, so "the snippet is valid" is a fact here and not a belief.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import tlsconfig as slop_tls  # noqa: E402

CADDY = shutil.which("caddy") or os.path.expanduser("~/go/bin/caddy")


@pytest.fixture()
def tlsdir(tmp_path, monkeypatch):
    """Point the module at a scratch directory, and make the snippet's paths
    resolve there too — the snippet normally names the GATEWAY's view."""
    d = tmp_path / "tls"
    d.mkdir()
    monkeypatch.setattr(slop_tls, "DIR", str(d))
    monkeypatch.setattr(slop_tls, "GATEWAY_DIR", str(d))
    return d


def _pair(tmp_path, cn="sysible.example", days=30):
    key, crt = tmp_path / f"{cn}.key", tmp_path / f"{cn}.crt"
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", str(days),
         "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={cn}"],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip(f"openssl unavailable: {r.stderr[-200:]}")
    return crt.read_text(), key.read_text()


# ------------------------------------------------------------- installing

def test_a_matching_pair_is_installed(tlsdir, tmp_path):
    cert, key = _pair(tmp_path)
    ok, msg = slop_tls.install(cert, key)
    assert ok, msg
    assert slop_tls.installed()
    assert (tlsdir / "cert.pem").exists() and (tlsdir / "key.pem").exists()


def test_the_private_key_is_not_world_readable(tlsdir, tmp_path):
    cert, key = _pair(tmp_path)
    assert slop_tls.install(cert, key)[0]
    mode = (tlsdir / "key.pem").stat().st_mode & 0o777
    assert not mode & 0o007, f"the private key is readable by anyone: {oct(mode)}"


def test_the_snippet_names_the_gateways_paths(tlsdir, tmp_path, monkeypatch):
    """The gateway sees a different mount point. Writing our own paths into the
    snippet names files that exist here and not there — which is the outage."""
    monkeypatch.setattr(slop_tls, "GATEWAY_DIR", "/etc/caddy/tls")
    cert, key = _pair(tmp_path)
    assert slop_tls.install(cert, key)[0]
    body = (tlsdir / slop_tls.SNIPPET_NAME).read_text()
    assert "/etc/caddy/tls/cert.pem" in body and "/etc/caddy/tls/key.pem" in body, body
    assert str(tlsdir) not in body, f"the snippet names this service's own paths: {body}"


# ------------------------------------------------- what must be refused

def test_a_key_that_belongs_to_another_certificate_is_refused(tlsdir, tmp_path):
    cert, _ = _pair(tmp_path, "one")
    _, key = _pair(tmp_path, "two")
    ok, msg = slop_tls.install(cert, key)
    assert not ok
    assert "does not belong" in msg, msg


def test_the_cert_and_key_swapped_round_is_refused(tlsdir, tmp_path):
    cert, key = _pair(tmp_path)
    ok, msg = slop_tls.install(key, cert)
    assert not ok, msg


def test_an_expired_certificate_is_refused(tlsdir, tmp_path, monkeypatch):
    """Browsers refuse it, so installing it only breaks the front door.

    The date is forced rather than minted: `openssl req` here has no
    -not_before, and skipping the test would leave the refusal unexercised.
    """
    cert, key = _pair(tmp_path)
    real = slop_tls._decode
    monkeypatch.setattr(slop_tls, "_decode",
                        lambda p: {**real(p), "notAfter": "Feb  1 00:00:00 2020 GMT"})
    ok, msg = slop_tls.install(cert, key)
    assert not ok and "expired" in msg.lower(), msg
    assert not slop_tls.installed(), "an expired certificate was installed anyway"


def test_reading_the_expiry_date(tlsdir):
    """The parsing the refusal above depends on, in both forms OpenSSL emits."""
    assert slop_tls._expired("Feb  1 00:00:00 2020 GMT") is True
    assert slop_tls._expired("Feb  1 00:00:00 2099 GMT") is False
    assert slop_tls._expired("") is False          # unknown is not expired
    assert slop_tls._expired("nonsense") is False


def test_a_passphrase_protected_key_is_refused_with_the_cure(tlsdir, tmp_path):
    """The gateway starts unattended and cannot be prompted."""
    cert, _ = _pair(tmp_path)
    enc = ("-----BEGIN RSA PRIVATE KEY-----\n"
           "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,0123456789ABCDEF\n\nAAAA\n"
           "-----END RSA PRIVATE KEY-----\n")
    ok, msg = slop_tls.install(cert, enc)
    assert not ok
    assert "passphrase" in msg.lower() and "openssl" in msg.lower(), msg


@pytest.mark.parametrize("cert,key", [("", "x"), ("x", ""), ("not a cert", "not a key")])
def test_rubbish_is_refused(tlsdir, cert, key):
    assert not slop_tls.install(cert, key)[0]


def test_a_missing_shared_volume_is_reported_not_silently_worked_around(tmp_path, monkeypatch):
    """The upgrade case. A stack that predates this feature has no shared
    volume; creating the directory ourselves would make a private one inside
    this container, and every install would then report success while the
    gateway served its own certificate with nothing anywhere to explain it."""
    monkeypatch.setattr(slop_tls, "DIR", str(tmp_path / "never-mounted"))
    cert, key = _pair(tmp_path)
    ok, msg = slop_tls.install(cert, key)
    assert not ok
    assert "not mounted" in msg and "sysiblectl" in msg, msg
    assert not (tmp_path / "never-mounted").exists(), \
        "a private directory was created inside this container"


def test_a_refused_install_leaves_the_gateway_alone(tlsdir, tmp_path):
    """THE one that matters. Any half-written state here is a front door that
    will not come back up, and the page to undo it is behind that front door."""
    cert, _ = _pair(tmp_path, "one")
    _, key = _pair(tmp_path, "two")
    slop_tls.install(cert, key)
    assert not slop_tls.installed()
    assert not (tlsdir / slop_tls.SNIPPET_NAME).exists()
    assert list(tlsdir.iterdir()) == [], f"left behind: {list(tlsdir.iterdir())}"


def test_the_snippet_never_names_a_file_that_is_not_there(tlsdir, tmp_path):
    """The invariant the whole write order exists to keep.

    A snippet is an instruction to the gateway to load those files. If it is
    present and they are not, caddy does not fall back — it refuses to start,
    and that is :443 for the entire platform, including the page you would use
    to undo it. So the files go in first and the snippet last.

    Checked as the property rather than the sequence, because the sequence can
    be rearranged in ways that still pass a test that only watches the failure
    path: a mismatched pair is rejected before the writing starts, so it proves
    nothing about the order of the writes that a GOOD pair performs.
    """
    cert, key = _pair(tmp_path)
    assert slop_tls.install(cert, key)[0]
    body = (tlsdir / slop_tls.SNIPPET_NAME).read_text()
    named = re.findall(r"\S+\.pem", body)
    assert named, f"the snippet names no certificate at all: {body}"
    for path in named:
        assert os.path.exists(path), \
            f"the snippet tells the gateway to load {path}, which is not there"


def test_a_failed_install_does_not_disturb_one_already_working(tlsdir, tmp_path):
    good_cert, good_key = _pair(tmp_path, "good")
    assert slop_tls.install(good_cert, good_key)[0]
    bad_cert, _ = _pair(tmp_path, "a")
    _, bad_key = _pair(tmp_path, "b")
    assert not slop_tls.install(bad_cert, bad_key)[0]
    assert slop_tls.installed()
    assert (tlsdir / "cert.pem").read_text().strip() == good_cert.strip()


def test_a_write_failing_midway_leaves_the_front_door_up(tlsdir, tmp_path, monkeypatch):
    """Replacing a certificate that is already live, with the disk filling up
    between the two renames.

    It takes a full disk or a dying filesystem to stop a rename inside one
    directory, but the consequence if it happens while a snippet is live is a
    gateway pointed at a new certificate and the OLD key — a mismatched pair,
    which caddy refuses to start on. Without the snippet it falls back to its
    own certificate: the front door stays up, with the warning it had before
    anyone touched it. Up-and-warning beats down.
    """
    cert, key = _pair(tmp_path, "good")
    assert slop_tls.install(cert, key)[0]
    assert slop_tls.installed()

    cert2, key2 = _pair(tmp_path, "replacement")
    real_replace = os.replace
    seen = []

    def flaky(src, dst):
        seen.append(dst)
        if len(seen) == 2:                     # the key, after the certificate
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr(slop_tls.os, "replace", flaky)
    ok, msg = slop_tls.install(cert2, key2)
    assert not ok and "could not write" in msg.lower(), msg
    assert not slop_tls.installed(), (
        "the gateway is still told to load a certificate and key that no longer "
        "match — it will not start")


# ---------------------------------------------------------------- removing

def test_revert_removes_the_snippet_and_the_key(tlsdir, tmp_path):
    cert, key = _pair(tmp_path)
    assert slop_tls.install(cert, key)[0]
    ok, msg = slop_tls.revert()
    assert ok, msg
    assert not slop_tls.installed()
    assert not (tlsdir / "key.pem").exists(), "the private key was left on disk"


def test_reverting_when_nothing_is_installed_says_so(tlsdir):
    ok, msg = slop_tls.revert()
    assert not ok and "already" in msg.lower(), msg


# ------------------------------------------------------------- describing

def test_the_page_can_say_what_is_installed(tlsdir, tmp_path):
    cert, key = _pair(tmp_path, "front.example")
    assert slop_tls.install(cert, key)[0]
    d = slop_tls.describe()
    assert "front.example" in d["subject"], d
    assert d["not_after"] and d["expired"] is False, d


def test_the_page_lists_the_names_the_certificate_covers(tlsdir, tmp_path):
    """Which addresses stop warning is the question an operator is actually
    asking, and it is in the SAN, not the subject."""
    key, crt = tmp_path / "san.key", tmp_path / "san.crt"
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "5",
         "-keyout", str(key), "-out", str(crt), "-subj", "/CN=front.example",
         "-addext", "subjectAltName=DNS:front.example,DNS:www.front.example,IP:10.0.0.5"],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip("this openssl cannot add a SAN")
    assert slop_tls.install(crt.read_text(), key.read_text())[0]
    names = slop_tls.describe()["names"]
    assert names == ["front.example", "www.front.example", "10.0.0.5"], names


def test_a_certificate_with_no_san_still_describes(tlsdir, tmp_path):
    """Degrade, do not raise: the page must render for an odd certificate."""
    cert, key = _pair(tmp_path)
    assert slop_tls.install(cert, key)[0]
    d = slop_tls.describe()
    assert d["names"] == [] and d["subject"], d


def test_a_snippet_with_no_certificate_is_reported_as_broken(tlsdir):
    """The state the gateway cannot start in. A tidy blank page here would hide
    exactly the thing an operator needs to see."""
    (tlsdir / slop_tls.SNIPPET_NAME).write_text("tls /etc/caddy/tls/cert.pem /etc/caddy/tls/key.pem\n")
    assert slop_tls.describe() == {"broken": True}


# ------------------------------------------- and caddy agrees it is valid

@pytest.mark.skipif(not os.path.exists(CADDY), reason="no caddy binary to adapt the config")
def test_caddy_accepts_what_we_write(tlsdir, tmp_path):
    """The claim the whole feature rests on, checked against a real caddy
    rather than asserted: the snippet we generate adapts, and an EMPTY
    directory adapts too — so a platform with no certificate installed still
    starts."""
    conf = tmp_path / "Caddyfile"

    conf.write_text(f":8443 {{\n\timport {tlsdir}/*.caddy\n\trespond 204\n}}\n")
    empty = subprocess.run([CADDY, "validate", "--config", str(conf), "--adapter", "caddyfile"],
                           capture_output=True, text=True, timeout=120)
    assert empty.returncode == 0, f"an empty TLS directory breaks the gateway:\n{empty.stderr[-600:]}"

    cert, key = _pair(tmp_path, "adapted.example")
    assert slop_tls.install(cert, key)[0]
    got = subprocess.run([CADDY, "validate", "--config", str(conf), "--adapter", "caddyfile"],
                         capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, f"caddy rejects the snippet we write:\n{got.stderr[-600:]}"


# ---------------------------------------------------------------------------
# Through Administration, which is where an operator meets it
# ---------------------------------------------------------------------------
import importlib  # noqa: E402

ADMIN_PW = "adminpassword123"
HDR = {"origin": "http://slop.lan"}


@pytest.fixture()
def admin(tmp_path, monkeypatch):
    """A signed-in superuser, and the app module, with TLS writing to tmp."""
    from starlette.testclient import TestClient

    tlsd = tmp_path / "tlsvol"
    tlsd.mkdir()
    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
        SLOP_TLS_DIR=str(tlsd), SLOP_TLS_GATEWAY_DIR="/etc/caddy/tls",
    )
    # tlsconfig reads SLOP_TLS_DIR at import, so it has to be reloaded too --
    # reloading only `app` left it pointing at whatever directory the first
    # import saw, and the install wrote somewhere this test was not looking.
    importlib.reload(slop_tls)
    import app as m
    m = importlib.reload(m)
    # The gateway restart goes through the updater sidecar, which is not here.
    monkeypatch.setattr(m.updates, "configured", lambda: False)
    with TestClient(m.app, base_url="http://slop.lan") as c:
        c.get("/login", follow_redirects=False)
        tok = c.cookies.get("sysible_csrf")
        c.post("/login", data={"username": "admin", "password": ADMIN_PW, "csrf": tok},
               headers=HDR, follow_redirects=False)
        yield c, m, tlsd


def _csrf(c):
    c.get("/login", follow_redirects=False)
    return c.cookies.get("sysible_csrf")


def test_administration_has_a_tls_section(admin):
    c, _m, _d = admin
    r = c.get("/admin/tls")
    assert r.status_code == 200, r.text
    assert "/admin/tls" in c.get("/admin").text, "the TLS tab is not in the section strip"


def test_the_page_explains_that_this_is_the_certificate_browsers_see(admin):
    """The reason it belongs here rather than in each app: the apps' own
    certificates are behind the gateway and no browser ever judges them."""
    c, _m, _d = admin
    body = c.get("/admin/tls").text.lower()
    assert "browser" in body and "gateway" in body, body[:400]


def test_only_a_superuser_reaches_it(admin, tmp_path):
    c, m, _d = admin
    c.post("/logout", data={"csrf": _csrf(c)}, headers=HDR, follow_redirects=False)
    r = c.get("/admin/tls", follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403), r.status_code


def test_installing_a_good_pair_through_the_page(admin, tmp_path):
    c, m, tlsd = admin
    cert, key = _pair(tmp_path, "front.example")
    r = c.post("/admin/tls/install",
               data={"cert": cert, "key": key, "csrf": _csrf(c)}, headers=HDR)
    assert r.status_code == 200, r.text
    assert (tlsd / "cert.pem").exists() and (tlsd / m.tlsconfig.SNIPPET_NAME).exists()
    assert "front.example" in c.get("/admin/tls").text


def test_a_bad_pair_through_the_page_changes_nothing(admin, tmp_path):
    c, m, tlsd = admin
    cert, _ = _pair(tmp_path, "one")
    _, key = _pair(tmp_path, "two")
    r = c.post("/admin/tls/install",
               data={"cert": cert, "key": key, "csrf": _csrf(c)}, headers=HDR)
    assert r.status_code == 400, r.status_code
    assert list(tlsd.iterdir()) == [], f"left behind: {list(tlsd.iterdir())}"


def test_without_an_updater_it_says_what_to_run(admin, tmp_path):
    """The IdP holds no Docker socket on purpose. With no updater deployed the
    page must name the command rather than silently not restart anything."""
    c, _m, _d = admin
    cert, key = _pair(tmp_path, "front.example")
    r = c.post("/admin/tls/install",
               data={"cert": cert, "key": key, "csrf": _csrf(c)}, headers=HDR)
    assert "sysiblectl" in r.text and "restart" in r.text, r.text[-600:]


def test_the_restart_is_asked_of_the_updater_not_done_here(admin, tmp_path, monkeypatch):
    c, m, _d = admin
    calls = []
    monkeypatch.setattr(m.updates, "configured", lambda: True)
    monkeypatch.setattr(m.updates, "module_action",
                        lambda *a, **k: (calls.append(a), ({}, None))[1])
    cert, key = _pair(tmp_path, "front.example")
    c.post("/admin/tls/install", data={"cert": cert, "key": key, "csrf": _csrf(c)},
           headers=HDR)
    assert calls and calls[0][:3] == ("slop", "gateway", "restart"), calls


def test_removing_it_through_the_page(admin, tmp_path):
    c, m, tlsd = admin
    cert, key = _pair(tmp_path, "front.example")
    c.post("/admin/tls/install", data={"cert": cert, "key": key, "csrf": _csrf(c)},
           headers=HDR)
    r = c.post("/admin/tls/remove", data={"csrf": _csrf(c)}, headers=HDR)
    assert r.status_code == 200, r.text
    assert not (tlsd / m.tlsconfig.SNIPPET_NAME).exists()
    assert not (tlsd / "key.pem").exists()


def test_a_post_without_the_csrf_token_is_refused(admin, tmp_path):
    c, _m, tlsd = admin
    cert, key = _pair(tmp_path, "front.example")
    r = c.post("/admin/tls/install", data={"cert": cert, "key": key, "csrf": "wrong"},
               headers=HDR)
    assert r.status_code == 403, r.status_code
    assert list(tlsd.iterdir()) == []
