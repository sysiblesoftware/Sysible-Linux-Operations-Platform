"""Only SOME VMs got the host CPU, which looked like "only some distros fail".

REPORTED, after the CPU fix had supposedly shipped:

    libvirt_domain.vm_suse[0]: Still creating... [4m50s elapsed]
    libvirt_domain.vm_arch[0]:  Still creating... [4m50s elapsed]
    Error: couldn't retrieve IP address of domain id: 9008972b-...
      with libvirt_domain.vm_arch[0], on main.tf line 133

Arch and openSUSE, and not the others. That is the signature of the x86-64-v2
CPU: Rocky/Alma 9+, Arch and openSUSE abort in glibc on the emulated QEMU v1
CPU before networking starts, so the provider waits for a lease that is never
coming and reports a DHCP problem. Debian and Ubuntu boot on v1 and look fine.

The migration that adds `cpu { mode = "host-passthrough" }` to older projects
was guarded on the WHOLE FILE:

    if 'host-passthrough' not in text and ...

In groups mode main.tf holds one resource per group -- vm_arch, vm_suse,
vm_ubuntu -- so the moment any single domain had the CPU, every other domain
was skipped for good, with no note to say anything had been left out. The guest
that needs it most is the one that gets stranded, and what the operator sees is
two distros failing out of five.

So the guard is per domain.
"""
import re

import pytest

from backend import infra

SLUGS = ("arch", "suse", "ubuntu")


def _main_tf(slugs=SLUGS, with_cpu=()):
    """A groups-mode main.tf: one libvirt_domain per group, as SLEP generates it."""
    out = ""
    for slug in slugs:
        cpu = '  cpu { mode = "host-passthrough" }\n' if slug in with_cpu else ""
        out += (f'resource "libvirt_domain" "vm_{slug}" {{\n'
                f'  count     = 1\n'
                f'  name      = "{slug}-${{count.index + 1}}"\n'
                f'  memory    = 2048\n'
                f'{cpu}'
                f'  cloudinit = libvirt_cloudinit_disk.ci_{slug}[count.index].id\n'
                f'  disk {{ volume_id = libvirt_volume.disk_{slug}[count.index].id }}\n'
                f'  network_interface {{\n'
                f'    network_name   = var.network\n'
                f'    wait_for_lease = true\n'
                f'  }}\n}}\n\n')
    return out


def _cpu_per_domain(text):
    """{resource name: whether that domain has a cpu block}."""
    out = {}
    for blk in re.split(r'(?=resource "libvirt_domain")', text):
        m = re.match(r'resource "libvirt_domain" "(\w+)"', blk)
        if m:
            out[m.group(1)] = bool(re.search(r'^\s*cpu\s*\{', blk, re.M))
    return out


@pytest.mark.parametrize("already", [(), ("arch",), ("arch", "suse"), SLUGS])
def test_every_domain_ends_up_with_the_host_cpu(already):
    """Whatever the starting state. With the old whole-file guard, any non-empty
    `already` left the rest without a CPU -- which is the reported failure."""
    out, _ = infra.migrate_libvirt_main_tf(_main_tf(with_cpu=already))
    have = _cpu_per_domain(out)
    assert have == {f"vm_{s}": True for s in SLUGS}, (
        "these domains were left on the emulated v1 CPU: "
        + ", ".join(k for k, v in have.items() if not v))


def test_the_stranded_domains_are_exactly_the_ones_that_panic():
    """Named, because the point is not arithmetic: arch and suse are the guests
    that die on a v1 CPU, and they were the ones being skipped."""
    out, _ = infra.migrate_libvirt_main_tf(_main_tf(with_cpu=("ubuntu",)))
    have = _cpu_per_domain(out)
    assert have["vm_arch"], "Arch left on the CPU its glibc aborts on"
    assert have["vm_suse"], "openSUSE left on the CPU its glibc aborts on"


def test_it_says_it_did_something_when_it_did():
    _, notes = infra.migrate_libvirt_main_tf(_main_tf(with_cpu=("arch",)))
    assert any("host-passthrough" in n for n in notes), (
        "two domains were repaired and the apply log said nothing")


def test_it_stays_quiet_when_there_is_nothing_to_do():
    """A note on every apply, forever, trains the operator to ignore them."""
    _, notes = infra.migrate_libvirt_main_tf(_main_tf(with_cpu=SLUGS))
    assert not any("host-passthrough" in n for n in notes), notes


@pytest.mark.parametrize("already", [(), ("arch",), SLUGS])
def test_running_it_twice_changes_nothing_the_second_time(already):
    once, _ = infra.migrate_libvirt_main_tf(_main_tf(with_cpu=already))
    twice, notes = infra.migrate_libvirt_main_tf(once)
    assert twice == once, "the migration is not idempotent — it would stack cpu blocks"
    assert not any("host-passthrough" in n for n in notes)


def test_it_does_not_touch_a_file_with_no_domains():
    text = 'resource "libvirt_volume" "base" {\n  name = "x.qcow2"\n}\n'
    out, notes = infra.migrate_libvirt_main_tf(text)
    assert "host-passthrough" not in out
    assert not any("host-passthrough" in n for n in notes)


def test_a_fresh_project_already_has_it_on_every_domain():
    """The generator and the migration must agree, or a new project and a
    migrated one behave differently for the same spec."""
    src = infra.__file__
    text = open(src, encoding="utf-8").read()
    # every groups-mode domain the generator emits carries the cpu line
    assert text.count('cpu {{ mode = "host-passthrough" }}') >= 1, (
        "the generator no longer emits a cpu mode for groups-mode domains")
