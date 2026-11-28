"""A generated bootstrap password must not be printed.

Connect generated an admin password on first run and printed it:

    [sysible-connect] first-run admin 'admin' password
    (you'll be asked to change it on first login): 8Kq2...

"Shown once" is not once. A container log is kept, and `docker compose logs`
replays it to anyone who can read it for as long as the service lives. The
must_change flag mitigates that only if somebody actually signs in — until then
it is a live admin credential sitting in a log, and Connect's admin opens a
terminal on every host in the fleet.

The IdP reached this conclusion first and left the reasoning in a comment beside
its own fix. Connect was still doing the thing that comment calls out as wrong,
which is the kind of gap that only shows up when something goes looking across
components rather than within one.

So: write it 0600 and print the PATH. Retrieving it becomes a deliberate act,
and the secret is never in the log at all. The file is deleted when the password
is changed, because the first password staying readable on disk after it stops
being the password is the same mistake one layer quieter.
"""
import json
import os
import stat

import pytest

from backend import auth


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A Connect data directory with no account yet."""
    monkeypatch.setattr(auth, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auth, "_AUTH_FILE", tmp_path / "auth.json")
    monkeypatch.delenv("SYSIBLE_CONNECT_PASSWORD", raising=False)
    monkeypatch.delenv("SYSIBLE_CONNECT_USER", raising=False)
    return tmp_path


def _bootstrap(capsys):
    auth.ensure_admin()
    return capsys.readouterr().out


def test_the_generated_password_is_not_printed(fresh, capsys):
    out = _bootstrap(capsys)
    pw = (fresh / "initial-password").read_text().strip()
    assert pw, "no bootstrap password file was written"
    assert pw not in out, (
        "the generated admin password was printed to the log, where "
        "`docker compose logs` replays it for the life of the service")


def test_the_log_says_where_to_find_it(fresh, capsys):
    """Silence would be worse than the leak: an operator who cannot find the
    password cannot sign in, and will reach for something less careful."""
    out = _bootstrap(capsys)
    assert "initial-password" in out, out
    assert "0600" in out
    assert "docker compose exec" in out, "no way to retrieve it is given"


def test_the_file_is_not_readable_by_anyone_else(fresh, capsys):
    _bootstrap(capsys)
    mode = stat.S_IMODE(os.stat(fresh / "initial-password").st_mode)
    assert mode == 0o600, "bootstrap password file is mode %o" % mode


def test_the_password_in_the_file_is_the_one_that_works(fresh, capsys):
    """A file containing the wrong password is worse than no file."""
    _bootstrap(capsys)
    pw = (fresh / "initial-password").read_text().strip()
    assert auth.verify(auth.current_user(), pw) is True


def test_changing_the_password_deletes_the_file(fresh, capsys):
    _bootstrap(capsys)
    assert (fresh / "initial-password").exists()
    auth.set_password("a-much-better-password-1")
    assert not (fresh / "initial-password").exists(), (
        "the first password is still readable on disk after being changed")


def test_an_operator_supplied_password_is_never_written_or_printed(fresh, capsys, monkeypatch):
    """SYSIBLE_CONNECT_PASSWORD is the operator's own secret. It is not
    generated, so there is nothing to hand back — and no reason to copy it
    anywhere."""
    monkeypatch.setenv("SYSIBLE_CONNECT_PASSWORD", "operator-chosen-123")
    out = _bootstrap(capsys)
    assert "operator-chosen-123" not in out
    assert not (fresh / "initial-password").exists()


def test_a_read_only_data_dir_still_lets_the_operator_in(fresh, capsys, monkeypatch):
    """The one case where the log is all there is. Better a printed password
    than an account nobody can sign into — but it must say why."""
    monkeypatch.setattr(auth, "_write_initial_password", lambda pw: None)
    out = _bootstrap(capsys)
    assert "could not write the password file" in out, out


def test_connect_now_matches_the_idp():
    """Both generate a bootstrap credential; both must handle it the same way.
    They drifted once already, in the direction of the less careful one."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[3]
    idp = (repo / "idp" / "app.py").read_text(encoding="utf-8")
    con = (repo / "apps" / "connect" / "backend" / "auth.py").read_text(encoding="utf-8")
    for fn in ("_write_initial_password", "_clear_initial_password"):
        assert fn in idp and fn in con, "%s exists in only one of the two" % fn
