"""Handing every image a network-config took Arch and openSUSE off the network.

REPORTED. Four VMs, all four running, two leases:

    virsh list --all              virsh net-dhcp-leases default
     1  arch-1    running          52:54:00:2e:d7:1c  192.168.122.55   ubuntu-1
     2  ubuntu-1  running          52:54:00:b6:6a:80  192.168.122.179  rocky-1
     3  suse-1    running
     4  rocky-1   running

So arch-1 and suse-1 booted perfectly and never asked for an address -- not a
CPU abort (that was a different fault, they are running now), and not DHCP
(ubuntu-1 and rocky-1 got leases from the same dnsmasq on the same bridge).

cloud-init renders a version-2 network-config through Netplan, networkd or
NetworkManager and nothing else. Debian/Ubuntu have Netplan; RHEL-family images
render it through their own stack. Arch ships no Netplan, and the openSUSE cloud
image has shipped with neither wicked nor NetworkManager -- so on those two the
file renders to nothing.

That by itself would be harmless. The fault is the second half: with NO
network-config supplied, cloud-init falls back to issuing DHCP on the first
interface by itself, and that fallback is what was bringing those images up. A
config they cannot render SUPPRESSES the fallback that was working. The file
added to make Rocky work is what took Arch and openSUSE out.

    https://docs.cloud-init.io/en/latest/reference/network-config.html
    https://docs.cloud-init.io/en/latest/reference/network-config-format-v2.html
"""
import re

import pytest

from backend import infra

UBUNTU = "https://cloud-images.ubuntu.com/noble/noble-server-cloudimg-amd64.img"
DEBIAN = "https://cloud.debian.org/images/cloud/bookworm/debian-12-generic-amd64.qcow2"
ROCKY = "https://dl.rockylinux.org/pub/rocky/9/images/Rocky-9-GenericCloud.latest.qcow2"
ALMA = "https://repo.almalinux.org/almalinux/9/cloud/AlmaLinux-9-GenericCloud.qcow2"
ARCH = "https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2"
SUSE = "https://download.opensuse.org/.../openSUSE-Leap-15.6.x86_64-NoCloud.qcow2"


def _ci_blocks(text):
    """{cloud-init resource name: whether it carries a network_config line}."""
    out = {}
    for blk in re.split(r'(?=resource "libvirt_cloudinit_disk")', text):
        m = re.match(r'resource "libvirt_cloudinit_disk" "(\w+)"', blk)
        if m:
            out[m.group(1)] = "network_config" in blk
    return out


# ------------------------------------------------------- which image needs it

@pytest.mark.parametrize("img", [ROCKY, ALMA, "rocky9.qcow2", "AlmaLinux-9.qcow2",
                                 "CentOS-Stream-9.qcow2", "rhel-9.4-x86_64.qcow2"])
def test_rhel_family_is_recognised(img):
    """It is the family that genuinely cannot DHCP without being told to."""
    assert infra.rhel_family_image(img), img


@pytest.mark.parametrize("img", [UBUNTU, DEBIAN, ARCH, SUSE, "alpine-3.20.qcow2", ""])
def test_everything_else_is_not_rhel_family(img):
    assert not infra.rhel_family_image(img), img


@pytest.mark.parametrize("img", [ARCH, SUSE, "manjaro-cloud.qcow2", "SLES-15-SP6.qcow2"])
def test_the_images_that_cannot_render_it_are_named(img):
    """The two that were reported with no lease, and their relatives. If this
    ever stops matching, the repair stops removing the file and the bug is back."""
    assert infra.cannot_render_v2_network_config(img), img


@pytest.mark.parametrize("img", [UBUNTU, DEBIAN, ROCKY, ALMA,
                                 "https://example.invalid/custom.qcow2", ""])
def test_everything_else_is_left_alone(img):
    """A POSITIVE list, not "anything unrecognised". This fault came from acting
    on an absence of knowledge; stripping the file from every image nothing
    vouches for is the same mistake pointed the other way -- and it would destroy
    and rebuild a working VM to do it."""
    assert not infra.cannot_render_v2_network_config(img), img


# --------------------------------------------------------------- generating

def test_a_new_groups_project_gives_it_only_to_the_family_that_needs_it():
    spec = {"provider": "libvirt", "groups": [
        {"name": "ubuntu", "base_image": UBUNTU, "count": 1},
        {"name": "rocky", "base_image": ROCKY, "count": 1},
        {"name": "arch", "base_image": ARCH, "count": 1},
        {"name": "suse", "base_image": SUSE, "count": 1},
    ]}
    have = _ci_blocks(infra.generate("libvirt", spec, "")["main.tf"])
    assert have == {"ci_ubuntu": False, "ci_rocky": True,
                    "ci_arch": False, "ci_suse": False}, have


@pytest.mark.parametrize("img,want", [(ROCKY, True), (ALMA, True),
                                      (UBUNTU, False), (ARCH, False), (SUSE, False)])
def test_a_new_single_image_project_decides_the_same_way(img, want):
    """The two layouts must agree, or the same image behaves differently
    depending on which wizard branch built it."""
    out = infra.generate("libvirt", {"provider": "libvirt", "base_image": img}, "")
    assert ("network_config" in out["main.tf"]) is want


def test_the_config_file_is_still_written_for_the_projects_that_use_it():
    out = infra.generate("libvirt", {"provider": "libvirt", "base_image": ROCKY}, "")
    assert "network-config.cfg" in out, sorted(out)
    assert "dhcp4: true" in out["network-config.cfg"]


# ----------------------------------------------------------- repairing a project

def _old_style(pairs, with_cfg):
    """A groups-mode main.tf as the old generator wrote it."""
    out = []
    for slug, img in pairs:
        line = ('  network_config = file("${path.module}/network-config.cfg")\n'
                if slug in with_cfg else "")
        out.append(
            f'resource "libvirt_volume" "base_{slug}" {{\n'
            f'  name   = "{slug}-base.qcow2"\n'
            f'  source = "{img}"\n}}\n\n'
            f'resource "libvirt_cloudinit_disk" "ci_{slug}" {{\n'
            f'  count     = 1\n'
            f'  user_data = file("${{path.module}}/cloudinit.cfg")\n'
            f'{line}}}\n')
    return "\n".join(out)


ALL_FOUR = [("ubuntu", UBUNTU), ("rocky", ROCKY), ("arch", ARCH), ("suse", SUSE)]


def test_the_reported_project_is_repaired():
    """Every VM had the file. Only the two that cannot render it lose it."""
    text = _old_style(ALL_FOUR, with_cfg={s for s, _ in ALL_FOUR})
    out, notes = infra.migrate_libvirt_main_tf(text)
    have = _ci_blocks(out)
    assert not have["ci_arch"], "Arch still gets a config it cannot render"
    assert not have["ci_suse"], "openSUSE still gets a config it cannot render"
    assert any("removed" in n for n in notes), notes


def test_the_vms_that_were_working_are_not_touched():
    """ubuntu-1 and rocky-1 HAD leases. The domain is replace_triggered_by its
    cloud-init ISO, so stripping their line would destroy and rebuild two healthy
    VMs to tidy up something that was doing no harm."""
    text = _old_style(ALL_FOUR, with_cfg={s for s, _ in ALL_FOUR})
    out, _ = infra.migrate_libvirt_main_tf(text)
    have = _ci_blocks(out)
    assert have["ci_ubuntu"], "a working Ubuntu VM would be recreated for nothing"
    assert have["ci_rocky"], "a working Rocky VM would be recreated for nothing"


def test_a_rhel_project_that_lacks_it_still_gets_it():
    """The repair must keep working in the direction it was written for."""
    out, notes = infra.migrate_libvirt_main_tf(_old_style([("rocky", ROCKY)], with_cfg=set()))
    assert _ci_blocks(out) == {"ci_rocky": True}
    assert any("added" in n for n in notes), notes


def test_the_note_names_the_vms_it_changed():
    """"Repaired your infrastructure" with no subject is not a log line."""
    text = _old_style(ALL_FOUR, with_cfg={s for s, _ in ALL_FOUR})
    _, notes = infra.migrate_libvirt_main_tf(text)
    joined = " ".join(notes)
    assert "ci_arch" in joined and "ci_suse" in joined, joined


def test_the_repair_is_idempotent():
    text = _old_style(ALL_FOUR, with_cfg={s for s, _ in ALL_FOUR})
    once, _ = infra.migrate_libvirt_main_tf(text)
    twice, notes = infra.migrate_libvirt_main_tf(once)
    assert twice == once
    assert not notes, notes


def test_a_single_image_project_is_judged_from_variables_tf():
    """That layout keeps its image in a variable, so main.tf alone cannot say
    which image a cloud-init disk belongs to."""
    main = ('resource "libvirt_cloudinit_disk" "ci" {\n'
            '  count     = var.vm_count\n'
            '  user_data = file("${path.module}/cloudinit.cfg")\n'
            '  network_config = file("${path.module}/network-config.cfg")\n}\n')
    arch_vars = 'variable "base_image" {\n  type = string\n  default = "%s"\n}\n' % ARCH
    out, notes = infra.migrate_libvirt_main_tf(main, arch_vars)
    assert _ci_blocks(out) == {"ci": False}, "Arch keeps a config it cannot render"
    rocky_vars = 'variable "base_image" {\n  type = string\n  default = "%s"\n}\n' % ROCKY
    out2, _ = infra.migrate_libvirt_main_tf(main, rocky_vars)
    assert _ci_blocks(out2) == {"ci": True}, "Rocky lost the config it needs"


def test_an_unknown_image_keeps_whatever_it_has():
    """Not in either list. Adding would risk the reported bug; removing would
    recreate a VM that may be fine. Leave it be."""
    main = _old_style([("mystery", "https://example.invalid/custom.qcow2")],
                      with_cfg={"mystery"})
    out, notes = infra.migrate_libvirt_main_tf(main)
    assert _ci_blocks(out) == {"ci_mystery": True}
    assert not notes, notes


def test_the_runner_hands_the_migration_its_variables():
    """A helper that takes variables.tf and a caller that never passes it is the
    same as not having written it."""
    import inspect
    from backend.runners import terraform_runner
    src = inspect.getsource(terraform_runner)
    call = src[src.index("migrate_libvirt_main_tf("):][:400]
    assert "variables.tf" in src
    assert call.count(",") >= 1, "migrate_libvirt_main_tf is called with main.tf only"
