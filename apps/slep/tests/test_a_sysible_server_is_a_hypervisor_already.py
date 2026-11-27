"""Pointing SLEP at our OWN Server image must not fail with a riddle.

Sysible Server already installs the hypervisor: sysible-meta pulls in
libvirt-daemon-system, qemu-system and qemu-utils, and 9000-sysible-system
enables libvirtd. So "make this a hypervisor" has, on paper, nothing to do.

One thing is missing on purpose. 9100-sysible-users says it outright:

    # Default groups deliberately EXCLUDE docker and libvirt: membership in
    # either is root-equivalent (`docker run -v /:/mnt …`), so the default
    # account must not hold it out of the box.

That is the right call for a shipped image and it is not going to change. The
consequence is that a stock Sysible Server, used as a hypervisor, fails with

    error: Failed to connect socket to '/var/run/libvirt/libvirt-sock':
    Permission denied

and SLEP passed that straight through. "Permission denied" on a path is exactly
the wording that already sent an evening into file modes and ownership on the VM
disks, where the cause turned out to be AppArmor. Handing the same sentence over
a second time, for a cause that is neither modes nor AppArmor but one group
membership, is how that evening repeats.

So the failure is translated, and an unrecognised failure still shows its own
text rather than a confident wrong guess.
"""
import pytest

from backend import app as appmod

HINT = appmod._libvirt_connect_hint
URI = "qemu+ssh://sysible@server.lan/system"

SOCKET_DENIED = ("error: failed to connect to the hypervisor\n"
                 "error: Failed to connect socket to '/var/run/libvirt/libvirt-sock': "
                 "Permission denied")


def test_the_group_is_named_as_the_cause_not_the_file_permissions():
    """THE case against our own image. It must say the group, say the fix, and say
    plainly that this is not the file-permission problem it looks like."""
    h = HINT(URI, SOCKET_DENIED)
    assert h, "the most likely failure against Sysible Server is not explained"
    assert "libvirt` group" in h or "`libvirt` group" in h, h
    assert "usermod -aG libvirt sysible" in h, "the fix is not given as a command"
    assert "not a file-permission problem" in h, (
        "the wording that misled once must be ruled out explicitly")


def test_it_says_the_exclusion_is_deliberate_rather_than_a_bug():
    """An operator who reads 'your image forgot the group' files a bug. The image
    did not forget; it withholds a root-equivalent group on purpose."""
    h = HINT(URI, SOCKET_DENIED)
    assert "deliberate" in h, h
    assert "root-equivalent" in h, h


def test_it_names_the_account_from_the_uri_so_the_command_can_be_pasted():
    assert "usermod -aG libvirt deploybot" in HINT(
        "qemu+ssh://deploybot@10.0.0.9/system", SOCKET_DENIED)


def test_it_still_works_when_the_uri_carries_no_account():
    """qemu:///system has no user in it; the hint must not crash or interpolate
    'None' into a command."""
    h = HINT("qemu:///system", SOCKET_DENIED)
    assert "None" not in h
    assert "the SSH account" in h


def test_a_new_login_is_required_is_said():
    """usermod does not change the groups of a session that is already open, so an
    operator who adds the group and retries in the same shell sees no change."""
    h = HINT(URI, SOCKET_DENIED)
    assert "next SSH session" in h, h


@pytest.mark.parametrize("err,want", [
    ("error: Failed to connect socket to '/var/run/libvirt/libvirt-sock': "
     "No such file or directory", "not installed or not started"),
    ("error: Failed to connect socket to '/var/run/libvirt/libvirt-sock': "
     "Connection refused", "not installed or not started"),
    ("error: authentication unavailable: no polkit agent available to authenticate "
     "action 'org.libvirt.unix.manage'", "usermod -aG libvirt"),
    ("sysible@server.lan: Permission denied (publickey).", "Install key with password"),
    ("Host key verification failed.", "ssh-keyscan"),
    ("ssh: Could not resolve hostname server.lan: Name or service not known",
     "does not resolve"),
])
def test_each_failure_it_claims_to_know_is_actually_explained(err, want):
    h = HINT(URI, err)
    assert want in h, "%r produced %r" % (err[:50], h)


@pytest.mark.parametrize("err", [
    "error: internal error: something nobody has seen before",
    "error: Cannot write data: Broken pipe",
    "",
    None,
])
def test_an_unrecognised_failure_keeps_its_own_words(err):
    """A hint for a cause we have not identified is worse than no hint: it sends
    the operator somewhere specific and wrong."""
    assert HINT(URI, err) == ""


def test_the_socket_denial_is_not_confused_with_a_disk_denial():
    """'Permission denied' also appears in the VM-disk failure, which has an
    entirely different cause and its own button. Keying on the word alone would
    point that one at the libvirt group."""
    disk = ("error: internal error: process exited while connecting to monitor: "
            "qemu-system-x86_64: -blockdev {\"driver\":\"file\",\"filename\":"
            "\"/var/lib/libvirt/images/arch-base.qcow2\"}: Could not open "
            "'/var/lib/libvirt/images/arch-base.qcow2': Permission denied")
    assert HINT(URI, disk) == "", "a disk denial was answered with the group fix"


def test_the_failing_connection_actually_carries_the_hint(monkeypatch):
    """The helper existing is not the same as the endpoint using it."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda b: "/usr/bin/virsh")

    class R:
        returncode = 1
        stdout = ""
        stderr = SOCKET_DENIED
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: R())
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        import backend.db as db
        if db.count_admins() == 0:
            t = c.post("/setup", json={"username": "admin", "password": "changeme123"}).json()["token"]
        else:
            t = c.post("/login", json={"username": "admin", "password": "changeme123"}).json()["token"]
        c.headers.update({"Authorization": "Bearer " + t})
        d = c.post("/infra/test-hypervisor", json={"uri": URI}).json()
    assert d["ok"] is False
    assert "usermod -aG libvirt sysible" in d["output"], d["output"]
    assert "libvirt-sock" in d["output"], "the original error was replaced rather than explained"
