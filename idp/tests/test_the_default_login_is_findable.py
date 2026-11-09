"""The default way in is documented, and it is the one that actually works.

Reported as "I also can't find the default slop username and password" — and it
was not findable. The bootstrap generated a random password, deliberately kept it
out of the container log (a log is replayed by `docker compose logs` for as long
as the service lives), and wrote it to a 0600 file. That reasoning was sound and
the file mechanism is still here. What was wrong was everything that told the
operator where to look: `.env.example` and `docker-compose.yml` both said the
password was "printed ONCE to the idp logs", which had not been true since it
moved to the file, and `docs/SSO.md` sent them to `docker logs` too.

So SLOP now ships a documented default — admin / sysible — and the generated
password is one value away (SLOP_ADMIN_PASSWORD=random) for anything exposed.

That default is PUBLIC by construction. These tests exist so that:
  - it keeps working, because a documented credential that does not log in is
    worse than no documented credential;
  - it keeps being printed, because hiding a value that is in the docs protects
    nothing and hiding it is what caused this;
  - the generated path survives, because it is the right setting for an exposed
    install and it is now reachable only by name;
  - the banner keeps saying it is public and should be changed.
"""
import importlib
import os
import sys
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

DEFAULT_USER = "admin"
DEFAULT_PW = "sysible"


def _fresh(tmp_path, **env):
    """The module reloaded against an empty data dir, with the bootstrap env set
    to exactly what a caller asks for and nothing left over from another test."""
    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    for k in ("SLOP_ADMIN_USER", "SLOP_ADMIN_PASSWORD", "SLOP_ADMIN_FORCE_CHANGE"):
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items()})
    import app as m
    return importlib.reload(m)


def _boot(mod, capsys=None):
    from starlette.testclient import TestClient
    with TestClient(mod.app, base_url="http://slop.lan"):
        pass
    return capsys.readouterr().out if capsys else ""


# The CSRF token is a double-submit COOKIE, not a hidden field, and the POST is
# origin-checked — the same helpers test_idp.py uses. Scraping the form for it
# found nothing, which looked exactly like "the default password is wrong".
_HDR = {"origin": "http://slop.lan", "referer": "http://slop.lan/login"}


def _login(mod, user, pw):
    from starlette.testclient import TestClient
    with TestClient(mod.app, base_url="http://slop.lan") as c:
        c.get("/login", follow_redirects=False)
        return c.post("/login",
                      data={"username": user, "password": pw,
                            "csrf": c.cookies.get("sysible_csrf")},
                      headers=_HDR, follow_redirects=False)


class TestTheDocumentedDefaultWorks:
    def test_the_default_credentials_sign_in(self, tmp_path):
        """The whole point. A documented credential that does not log in is
        worse than no documented credential at all."""
        mod = _fresh(tmp_path)
        _boot(mod)
        r = _login(mod, DEFAULT_USER, DEFAULT_PW)
        assert r.status_code == 302, \
            f"admin / {DEFAULT_PW} did not sign in (got {r.status_code})"

    def test_the_default_is_not_forced_to_change_on_first_login(self, tmp_path):
        """Chosen deliberately: forcing a change made the first login a puzzle
        rather than a login. A GENERATED password is still forced (below)."""
        mod = _fresh(tmp_path)
        _boot(mod)
        with mod._db() as c:
            row = c.execute("SELECT must_change FROM users WHERE username=?",
                            (DEFAULT_USER,)).fetchone()
        assert row is not None, "no bootstrap account was created"
        assert not row["must_change"], \
            "the shipped default is being forced to change at first login"

    def test_it_is_printed_so_it_can_be_found(self, tmp_path, capsys):
        """It is in the docs. Withholding it from the log protects nothing and is
        exactly what made this a support question."""
        mod = _fresh(tmp_path)
        out = _boot(mod, capsys)
        assert DEFAULT_USER in out
        assert DEFAULT_PW in out, "the shipped default password is not printed anywhere"

    def test_the_banner_says_it_is_public_and_how_to_replace_it(self, tmp_path, capsys):
        mod = _fresh(tmp_path)
        out = _boot(mod, capsys)
        assert "SHIPPED DEFAULT" in out and "public" in out, \
            "nothing tells the operator this credential is on every install"
        assert "SLOP_ADMIN_PASSWORD" in out, "the banner does not say how to replace it"
        assert "random" in out, "the banner does not mention the generated option"


class TestTheHardenedPathIsStillThere:
    """It used to be what you got by leaving the password empty, so it is now
    reachable only by name — and that makes it easy to lose."""

    def test_random_still_generates_into_a_0600_file(self, tmp_path, capsys):
        mod = _fresh(tmp_path, SLOP_ADMIN_PASSWORD="random")
        out = _boot(mod, capsys)
        f = tmp_path / "initial-password"
        assert f.is_file(), "SLOP_ADMIN_PASSWORD=random did not generate a password"
        pw = f.read_text().strip()
        assert pw and pw != DEFAULT_PW
        assert oct(f.stat().st_mode)[-3:] == "600", oct(f.stat().st_mode)
        assert pw not in out, "the generated password was printed to the log"

    def test_a_generated_password_is_still_forced_to_change(self, tmp_path):
        """Nobody chose it and it exists only as a file on disk."""
        mod = _fresh(tmp_path, SLOP_ADMIN_PASSWORD="random")
        _boot(mod)
        with mod._db() as c:
            row = c.execute("SELECT must_change FROM users WHERE username=?",
                            (DEFAULT_USER,)).fetchone()
        assert row["must_change"], "a generated password is not forced to change"

    def test_the_default_is_not_used_when_one_is_configured(self, tmp_path):
        mod = _fresh(tmp_path, SLOP_ADMIN_PASSWORD="anoperatorschoice99")
        _boot(mod)
        assert _login(mod, DEFAULT_USER, "anoperatorschoice99").status_code == 302
        assert _login(mod, DEFAULT_USER, DEFAULT_PW).status_code != 302, \
            "the shipped default still works on an install that configured a password"

    def test_a_configured_password_is_not_printed(self, tmp_path, capsys):
        mod = _fresh(tmp_path, SLOP_ADMIN_PASSWORD="anoperatorschoice99")
        out = _boot(mod, capsys)
        assert "anoperatorschoice99" not in out, \
            "an operator's own password was printed to the container log"

    def test_force_change_can_be_turned_back_on(self, tmp_path):
        mod = _fresh(tmp_path, SLOP_ADMIN_FORCE_CHANGE="1")
        _boot(mod)
        with mod._db() as c:
            row = c.execute("SELECT must_change FROM users WHERE username=?",
                            (DEFAULT_USER,)).fetchone()
        assert row["must_change"], "SLOP_ADMIN_FORCE_CHANGE=1 no longer forces a change"

    def test_the_username_can_still_be_renamed(self, tmp_path):
        mod = _fresh(tmp_path, SLOP_ADMIN_USER="sysadmin")
        _boot(mod)
        assert _login(mod, "sysadmin", DEFAULT_PW).status_code == 302


class TestTheDocsSayTheSameThing:
    """The code was never the reason this was unfindable — the docs were, and
    they disagreed with the code for as long as the file mechanism existed."""

    @pytest.mark.parametrize("rel", [".env.example", "docker-compose.yml", "docs/SSO.md"])
    def test_no_file_still_sends_the_operator_to_the_log(self, rel):
        import pathlib
        p = pathlib.Path(HERE).parent / rel
        if not p.exists():
            pytest.skip(f"{rel} is not in this checkout")
        text = p.read_text()
        for stale in ("printed ONCE to the idp logs",
                      "printed to the\n      # idp logs",
                      "read\nthe one-time generated password from the IdP logs"):
            assert stale not in text, f"{rel} still points at the log for the password"

    @pytest.mark.parametrize("rel", [".env.example", "docs/SSO.md"])
    def test_the_default_is_written_down(self, rel):
        import pathlib
        p = pathlib.Path(HERE).parent / rel
        if not p.exists():
            pytest.skip(f"{rel} is not in this checkout")
        text = p.read_text()
        assert DEFAULT_PW in text, f"{rel} does not state the default password"
        assert "random" in text, f"{rel} does not offer the generated alternative"
