"""The platform's own compose mounts the server's /etc as a DIRECTORY.

The Controller resolves a host NAME with no address by reading the server's
/etc/hosts. In the platform stack that file reaches it through this compose
file, and HOW it is mounted decides whether the feature works for longer than
one afternoon.

A bind mount of a single FILE is bound to that file's INODE, not its name.
vim, sed -i and every other editor that writes a temporary file and renames
it over the original give /etc/hosts a new inode -- and the container goes on
reading the old one, with the content it had when the container was created,
forever. The mount still reports present. It still reads without error. It is
answering questions about a file that no longer exists, which from the console
looks exactly like "the Controller cannot see my hosts file" and was reported
as `lists 0 names` while the name sat in /etc/hosts on the server.

That cannot be fixed from inside the container, so it is fixed here. The
Controller repo pins the same property against its own compose file; this
pins it against the one the platform actually runs, because the two files are
edited separately and only this one is in front of an operator.
"""
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "docker-compose.yml"

# The files the Controller open()s under /host. Kept here rather than imported
# so that a change on the Controller side has to be a deliberate change here
# too -- this is the contract between two repositories.
OPENED = (
    "/host/etc/hosts",
    "/host/etc/resolv.conf",
    "/host/run/resolv.conf",
    "/host/run/systemd-resolve/resolv.conf",
)


@pytest.fixture(scope="module")
def host_mounts():
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    vols = doc["services"]["controller"]["volumes"]
    return [v for v in vols if "/host/" in v]


def test_the_controller_reads_something_from_the_server(host_mounts):
    assert host_mounts, (
        "the platform's Controller no longer sees the server's hosts file, so "
        "enrolling a host by name stops working")


@pytest.mark.parametrize("opened", OPENED)
def test_no_mount_target_is_a_file_the_controller_opens(host_mounts, opened):
    for v in host_mounts:
        target = v.split(":")[1]
        assert target != opened, (
            f"{v} mounts {target}, which the Controller open()s. A bind mount "
            f"of a FILE is bound to its inode: the first edit on the server "
            f"detaches this container from it, silently and for good. Mount "
            f"{Path(target).parent} instead.")


def test_the_servers_hosts_file_is_reachable_through_a_mount(host_mounts):
    """Tidy and useless is still useless: the directories have to actually
    contain the file the Controller reads."""
    targets = [v.split(":")[1].rstrip("/") for v in host_mounts]
    assert any("/host/etc/hosts".startswith(t + "/") for t in targets), \
        f"none of {targets} contains /host/etc/hosts"


def test_the_resolver_config_is_reachable_through_a_mount(host_mounts):
    """On a systemd-resolved host /etc/resolv.conf is the 127.0.0.53 stub,
    which inside a container is that container. The real upstream servers are
    under /run/systemd/resolve, and without them enrollment can read the
    server's hosts file but cannot ask the server's DNS."""
    targets = [v.split(":")[1].rstrip("/") for v in host_mounts]
    assert any("/host/run/systemd-resolve/resolv.conf".startswith(t + "/")
               for t in targets), \
        f"none of {targets} contains systemd-resolved's upstream list"


def test_every_host_mount_is_read_only(host_mounts):
    """Read-only is the whole claim the console makes about them, and /etc is
    a lot of server to hand a web-facing container any other way."""
    for v in host_mounts:
        assert v.rstrip().endswith(":ro"), f"{v} is not read-only"
