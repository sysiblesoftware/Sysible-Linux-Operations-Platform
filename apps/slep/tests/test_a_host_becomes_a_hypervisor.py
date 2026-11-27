"""Making a plain Linux host into a hypervisor, and keeping its storage usable.

Two failures drove this. First, SLEP could drive a hypervisor but not MAKE one:
a freshly installed host had no storage pool, no network and usually no libvirt,
and the first sign of it was a Terraform apply dying partway through. Second, and
reported from a real host:

    error creating libvirt domain: internal error: process exited while connecting
    to monitor: qemu-system-x86_64: -blockdev {"driver":"file","filename":
    "/var/lib/libvirt/images/arch-base.qcow2","node-name":"libvirt-3-storage",
    "auto-read-only":true,...}: Could not open '...': Permission denied

Who fails there is the point: qemu-system-x86_64, not libvirtd. libvirtd is root
and creates the files perfectly. The VM process runs unprivileged and must open
its disk's whole backing chain -- and every VM is a COW clone over one shared base
image. The modes can look perfectly fine (0644, in a 0711 directory) and the open
still fail, because libvirt hands the base back to its original owner when another
VM stops.
"""
import re
import subprocess
from pathlib import Path

import pytest

from backend import hypervisor as h

HERE = Path(__file__).resolve().parents[1]
SCRIPTS = {"PROVISION_SH": h.PROVISION_SH, "DIAGNOSE_SH": h.DIAGNOSE_SH, "REPAIR_SH": h.REPAIR_SH}


# ------------------------------------------------------------ distro-agnostic

@pytest.mark.parametrize("id_,id_like,want", [
    ("debian", "", "debian"),
    ("ubuntu", "debian", "debian"),
    ("linuxmint", "ubuntu debian", "debian"),     # the user's example, by name
    ("sysible", "debian", "debian"),              # our own ISOs set exactly this
    ("pop", "ubuntu debian", "debian"),
    ("zorin", "ubuntu debian", "debian"),
    ("rocky", "rhel centos fedora", "rhel"),
    ("almalinux", "rhel centos fedora", "rhel"),
    ("fedora", "", "rhel"),
    ("opensuse-leap", "suse opensuse", "suse"),
    ("sles", "", "suse"),
    ("arch", "", "arch"),
    ("manjaro", "arch", "arch"),
    ("alpine", "", "alpine"),
])
def test_the_distro_does_not_have_to_be_named(id_, id_like, want):
    """'Being linux mint or sysible server it shouldn't matter what the remote host is.'
    A derivative reaches its parent through ID_LIKE without ever being listed."""
    assert h.family_for(id_, id_like) == want


def test_an_unknown_distro_is_said_so_not_guessed():
    assert h.family_for("plan9", "") == ""
    assert "Unrecognised distribution" in h.PROVISION_SH


def test_our_own_isos_still_set_the_id_like_this_detector_relies_on():
    """If a build ever drops ID_LIKE=debian, Sysible stops being detectable --
    by its own tooling. Pin it from here so that change cannot pass unnoticed."""
    found = []
    for repo in ("sysible-server", "sysible-workstation"):
        hook = Path("/home/user") / repo / "live-build/config/hooks/normal/9000-sysible-system.hook.chroot"
        if hook.exists():
            found.append(repo)
            assert "ID_LIKE=debian" in hook.read_text(encoding="utf-8"), (
                "%s no longer sets ID_LIKE=debian in /etc/os-release, so SLEP cannot "
                "identify its own distribution on a remote host" % repo)
    if not found:
        pytest.skip("ISO repos not checked out beside this one")


# ---------------------------------------------------------------- the scripts

@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_every_script_is_posix_sh(name, tmp_path):
    """/bin/sh is dash on Debian and ash on Alpine. A bashism here fails on the
    host, at the far end of an SSH connection, with nothing useful to show."""
    if not _has("dash"):
        pytest.skip("dash not available to check with")
    f = tmp_path / (name + ".sh")
    f.write_text(SCRIPTS[name])
    p = subprocess.run(["dash", "-n", str(f)], capture_output=True, text=True)
    assert p.returncode == 0, "%s is not valid POSIX sh:\n%s" % (name, p.stderr)


def _has(prog):
    import shutil
    return shutil.which(prog) is not None


def test_the_password_is_read_from_stdin_exactly_once():
    """It must not be in argv (ps on the host shows that), and leaving it unread
    would hand it to whatever ran next on the same stdin."""
    for name, src in SCRIPTS.items():
        assert "read -r SUDOPW" in src, "%s does not consume the password" % name
        assert src.count("read -r SUDOPW") == 1, "%s reads stdin more than once" % name
        assert "SUDOPW=''" in src.split("read -r SUDOPW", 1)[1], (
            "%s never clears the password after using it" % name)


def test_a_cpu_without_virtualisation_is_refused_not_emulated():
    """Decided: refuse to build rather than fall back to TCG emulation."""
    assert "no hardware virtualisation" in h.PROVISION_SH
    assert "exit 12" in h.PROVISION_SH
    # and the refusal must come BEFORE anything is installed
    assert h.PROVISION_SH.index("exit 12") < h.PROVISION_SH.index("apt-get install")


def test_provisioning_covers_every_family_it_claims_to_detect():
    for fam in h.FAMILIES:
        assert re.search(r"^\s*%s\)" % fam, h.PROVISION_SH, re.M), (
            "family %r is detected but has no install branch" % fam)


def test_the_repair_and_the_check_resolve_the_qemu_identity_the_same_way():
    """The repair used to group-own volumes to `id -gn $QU` while the check read the
    group out of qemu.conf -- so the repair could hand a file to a group the VM
    process is not in and report success. One resolver, shared, or it drifts again."""
    assert h._QEMU_ID_SH in h.DIAGNOSE_SH
    assert h._QEMU_ID_SH in h.REPAIR_SH
    assert "id -gn" not in h.REPAIR_SH, (
        "the repair is resolving the group from the USER again instead of qemu.conf")


def test_the_probe_does_not_hand_the_vm_user_groups_it_does_not_have():
    """libvirt starts qemu with qemu.conf's user AND group and adds no supplementary
    groups. A probe via plain `su` gets the account's own groups, so a file can
    probe readable and still be denied in the real thing."""
    assert "--clear-groups" in h._QEMU_ID_SH
    assert "setpriv" in h._QEMU_ID_SH


def test_the_repair_verifies_by_opening_the_file_not_by_chmod_returning_zero():
    body = h.REPAIR_SH.split("fixed_vols", 1)[0]
    assert "as_qemu" in h.REPAIR_SH.split("# 2.", 1)[1].split("# 3.", 1)[0], (
        "the repair never re-probes as the VM process, so it can report success "
        "while the open is still denied -- which is exactly what it did")
    assert "STILL cannot read" in h.REPAIR_SH


def test_remember_owner_is_explained_not_just_toggled():
    """It is why this works once and then stops: stopping one VM restores its disks'
    original owner, handing a shared base image back to root while others need it."""
    for src in (h.DIAGNOSE_SH, h.REPAIR_SH):
        assert "remember_owner" in src
    assert "restores" in h.DIAGNOSE_SH or "restore" in h.DIAGNOSE_SH
    assert "qemu.conf.sysible-bak" in h.REPAIR_SH, "the repair edits qemu.conf without keeping a copy"


def test_the_pool_directory_is_made_searchable_not_readable():
    """o+x is all a path open needs; o+r on the DIRECTORY would publish a listing of
    every disk on the host. The volumes themselves are a separate decision below."""
    assert 'chmod o+x "$POOLDIR"' in h.REPAIR_SH
    assert 'chmod o+r "$POOLDIR"' not in h.REPAIR_SH
    assert "chmod 777" not in h.REPAIR_SH and "chmod a+rwx" not in h.REPAIR_SH


def test_the_volume_fix_does_not_rely_on_a_group_libvirt_will_overwrite():
    """The first attempt chgrp'd each volume to qemu.conf's group and set g+r. libvirt
    re-chowns a disk when the domain starts -- on a real host to libvirt-qemu:
    libvirt-qemu, not kvm -- so the group was overwritten and the 0640 file was then
    readable by nobody that mattered. It kept failing in exactly the same way."""
    vol_loop = h.REPAIR_SH.split("# 2.", 1)[1].split("# 3.", 1)[0]
    # Strip comments: the explanation of why chgrp is wrong must not read as chgrp.
    code = "\n".join(l for l in vol_loop.splitlines() if not l.strip().startswith("#"))
    assert "chmod a+r" in code, "the volumes are not made world-readable"
    assert "chgrp" not in code, (
        "the repair is group-owning volumes again -- libvirt overwrites that group "
        "on domain start, so the fix does not survive")


def test_the_repair_clears_the_owner_libvirt_saved():
    """With remember_owner on, libvirt stores each file's original owner in
    trusted.libvirt.security.* and RESTORES it every time a domain starts -- which
    silently undoes a chown done by hand. That is why this kept coming back."""
    assert "setfattr -x" in h.REPAIR_SH
    for a in ("dac", "ref_dac", "selinux", "ref_selinux"):
        assert a in h.REPAIR_SH, "trusted.libvirt.security.%s is not cleared" % a


def test_the_check_reports_the_owner_libvirt_saved():
    assert "getfattr" in h.DIAGNOSE_SH
    assert "saved_owner" in h.DIAGNOSE_SH
    assert "saved_owner" in h.PERM_STEP_LABELS


# -------------------------------------------------------------- step reporting

def test_steps_are_parsed_and_junk_is_not():
    out = ("Reading package lists...\n"
           "SYSIBLE_STEP|os|ok|Linux Mint 22 -> debian\n"
           "E: some apt noise|with|pipes\n"
           "SYSIBLE_STEP|virt|failed|no vmx/svm\n")
    steps = h.parse_steps(out)
    assert [s["step"] for s in steps] == ["os", "virt"]
    assert steps[0]["label"] == "Identify the distribution"
    assert steps[1]["status"] == "failed"


def test_every_step_the_scripts_emit_has_a_human_label():
    """An unlabelled step shows the operator a bare key like 'fixed_vols'."""
    known = set(h.STEP_LABELS) | set(h.PERM_STEP_LABELS)
    for name, src in SCRIPTS.items():
        for key in re.findall(r"^\s*S ([a-z_]+) ", src, re.M):
            assert key in known, "step %r emitted by %s has no label" % (key, name)


# ------------------------------------------- the behaviour, actually exercised

@pytest.fixture
def pool(tmp_path):
    """A pool directory in the exact reported state: base image 0600 root:root."""
    d = tmp_path / "images"
    d.mkdir()
    (d / "arch-base.qcow2").write_bytes(b"qcow2")
    (d / "arch-base.qcow2").chmod(0o600)
    d.chmod(0o700)
    # every ancestor must be traversable or the probe reports the ancestors, not us
    p = tmp_path
    while str(p) != "/":
        p.chmod(p.stat().st_mode | 0o111)
        p = p.parent
    return d


def _run(script, pooldir, remember="0"):
    env = "POOLDIR='%s'\nSET_REMEMBER='%s'" % (pooldir, remember)
    p = subprocess.run(["sh", "-c", env + "\n" + script],
                       capture_output=True, text=True, input="", timeout=120)
    return h.parse_steps(p.stdout)


def _step(steps, key):
    for s in steps:
        if s["step"] == key:
            return s
    return None


@pytest.mark.skipif(not _has("setpriv"), reason="needs setpriv to probe as another user")
def test_the_check_finds_the_failure_that_was_reported(pool):
    import os
    if os.geteuid() != 0:
        pytest.skip("needs root to probe as the VM user")
    steps = _run(h.DIAGNOSE_SH, pool)
    vols = _step(steps, "volumes")
    assert vols and vols["status"] == "failed", steps
    assert "arch-base.qcow2" in vols["detail"]


@pytest.mark.skipif(not _has("setpriv"), reason="needs setpriv to probe as another user")
def test_the_repair_actually_lets_the_vm_process_open_the_backing_file(pool):
    """The whole point. An earlier version reported ok here while the open was
    still denied, because it checked chmod's exit code instead of the open."""
    import os
    if os.geteuid() != 0:
        pytest.skip("needs root to change ownership")
    assert _step(_run(h.DIAGNOSE_SH, pool), "volumes")["status"] == "failed"
    fixed = _step(_run(h.REPAIR_SH, pool), "fixed_vols")
    assert fixed and fixed["status"] == "ok", fixed
    after = _step(_run(h.DIAGNOSE_SH, pool), "volumes")
    assert after["status"] == "ok", after


@pytest.mark.skipif(not _has("setpriv"), reason="needs setpriv to probe as another user")
def test_the_repair_is_idempotent(pool):
    import os
    if os.geteuid() != 0:
        pytest.skip("needs root")
    _run(h.REPAIR_SH, pool)
    again = _run(h.REPAIR_SH, pool)
    assert [s["status"] for s in again] == ["ok"] * len(again), again


@pytest.mark.skipif(not _has("setpriv"), reason="needs setpriv to probe as another user")
def test_the_fix_survives_libvirt_restoring_the_saved_owner(pool):
    """The event that actually breaks this, and the reason a group-based fix fails.

    libvirt does not only chown a disk TO the qemu user -- with remember_owner it
    also chowns it BACK to the owner it saved (root:root) when a domain stops. A
    volume left 0640 root:kvm by a chgrp survives the first direction (the qemu
    process then owns the file) and dies on the second: group root, qemu's group
    kvm, other nothing. 0644 survives both, because it never depended on who owns
    the file at all.

    An earlier version of this test modelled only the chown TO libvirt-qemu, which
    hands that process owner permissions -- so it passed against the broken fix."""
    import os, subprocess as sp
    if os.geteuid() != 0:
        pytest.skip("needs root")
    _run(h.REPAIR_SH, pool)
    img = pool / "arch-base.qcow2"
    os.chown(str(img), 0, 0)          # libvirt restoring the owner it remembered
    r = sp.run(["setpriv", "--reuid", "libvirt-qemu", "--regid", "kvm",
                "--clear-groups", "cat", str(img)], capture_output=True)
    assert r.returncode == 0, (
        "after libvirt restored the saved owner (root:root) the VM process can no "
        "longer read the backing file -- this fix does not survive a VM stopping: %s"
        % (r.stderr or b"").decode()[:200])


# ------------------------------------------- the endpoints must be reachable

def test_every_hypervisor_endpoint_has_a_button():
    """An endpoint nothing calls is an endpoint that does not exist. SLEP has already
    shipped one unreachable feature this way (a pipeline dialog whose caller was
    never wired), so pin the wiring rather than trust it."""
    views = HERE / "webgui" / "frontend" / "src" / "views"
    ui = "\n".join(f.read_text(encoding="utf-8") for f in views.glob("*.jsx"))
    for route in ("infra/hypervisor-provision",
                  "infra/hypervisor-permissions",
                  "infra/hypervisor-fix-permissions"):
        assert route in ui, "%s is not called from any view -- nothing can reach it" % route


def test_the_backend_actually_serves_those_routes():
    from backend import app as backend_app
    paths = {getattr(r, "path", "") for r in backend_app.app.routes}
    for route in ("/infra/hypervisor-provision",
                  "/infra/hypervisor-permissions",
                  "/infra/hypervisor-fix-permissions"):
        assert route in paths, "%s is called from the UI but not served" % route


def test_making_a_hypervisor_and_changing_permissions_need_an_operator():
    """Reading what is wrong is for anyone who can see the project; installing
    packages and changing file ownership on someone's host is not."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    for fn in ("def infra_hypervisor_provision", "def infra_hypervisor_fix_permissions"):
        sig = src[src.index(fn):src.index(fn) + 400]
        assert "require_operator" in sig, "%s is not operator-guarded" % fn


def test_the_shared_ssh_validator_is_used_everywhere_not_copied():
    """Five endpoints take {host,user,port,password}. The guard that stops a Vault
    reference being used as a decryption oracle against an operator-chosen host
    lives in the validator -- a copy per endpoint is how it ends up enforced in
    four places out of five. Add an endpoint, add it here."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    assert src.count("def _hypervisor_ssh_target") == 1
    for fn in ("infra_install_hypervisor_key", "infra_hypervisor_provision",
               "infra_hypervisor_permissions", "infra_hypervisor_fix_permissions",
               "infra_hypervisor_why_no_ip"):
        body = src[src.index("def " + fn):]
        body = body[:body.index("\n@app.") if "\n@app." in body else 3000]
        assert "_hypervisor_ssh_target(" in body, "%s validates its own SSH target" % fn
    # and the guard itself exists exactly once
    assert src.count("may use a Vault variable as the") == 1


# ------------------------------------------- the cause it actually turned out to be

def test_the_check_reads_more_than_dmesg():
    """On a real host dmesg held NO denials while AppArmor was loaded and enforcing
    and auditd had every one of them. Reporting "no denial" from dmesg alone sent
    the investigation back to file permissions that were already correct."""
    for src in ("dmesg", "journalctl -k", "/var/log/audit/audit.log"):
        assert src in h.DIAGNOSE_SH, "the permission check does not look in %s" % src


def test_a_pool_the_vm_user_can_read_points_above_the_filesystem():
    """When every volume IS readable and the VMs still fail, the next place to look
    is the security module, and the check must say so rather than report all-clear."""
    assert "ABOVE the filesystem" in h.DIAGNOSE_SH


def test_the_repair_can_fix_the_apparmor_denial():
    """The denial, verbatim from a host:

        apparmor="DENIED" operation="open" profile="libvirt-<uuid>"
        name="/var/lib/libvirt/images/arch-base.qcow2" comm="qemu-system-x86"
        requested_mask="r" denied_mask="r" fsuid=64055 ouid=64055

    fsuid == ouid == libvirt-qemu: qemu was refused a file it owned."""
    r = h.REPAIR_SH
    assert "FIX_APPARMOR" in r
    assert "abstractions/libvirt-qemu" in r, "the repair does not touch the profile VMs include"
    assert "apparmor_parser -r" in r, "the profile is edited but never reloaded"
    assert "$POOLDIR/** rwk," in r, "the rule does not give qemu access to the pool"
    assert "apparmor" in h.PERM_STEP_LABELS


def test_the_apparmor_rule_covers_the_vm_disks_too():
    """Granting read only was wrong and moved the denial one level up the backing
    chain. The base images under this pool ARE read-only, but the same directory
    holds every VM's own disk, which qemu opens for write:

        -blockdev {"node-name":"libvirt-2-format","read-only":false,
        "backing":"libvirt-3-format"}: Could not open '.../ubuntu-1.qcow2':
        Permission denied

    i.e. the base opened and the VM's disk did not."""
    r = h.REPAIR_SH
    assert '"  $POOLDIR/** rwk,"' in r, "the rule does not let qemu write its own disks"


def test_the_apparmor_rule_is_still_scoped_to_the_pool():
    """Widening a security profile is not a thing to do casually. One pool, named
    explicitly -- never a wildcard over the filesystem, never a bare /** rule."""
    r = h.REPAIR_SH
    step = _apparmor_step()
    assert 'rule="  $POOLDIR/' in step, "the rule is not anchored to the pool directory"
    for reckless in ('"  /** ', '"  /**/** ', '/var/lib/** ', '/etc/** '):
        assert reckless not in step, "the rule reaches outside the pool: %r" % reckless
    # Scoped to the AppArmor step: .sysible-bak also appears in the qemu.conf
    # step, and a file-level assertion passed against a reverted backup here.
    assert ".sysible-bak" in _apparmor_step(), (
        "the AppArmor profile is edited without keeping a copy")


def test_both_ways_debian_ships_the_profile_are_handled():
    """Some Debian versions include a local/ override from the abstraction and some
    do not; writing to a local file that nothing includes would silently do nothing."""
    r = h.REPAIR_SH
    assert "local/abstractions/libvirt-qemu" in r
    assert "grep -q 'local/abstractions/libvirt-qemu'" in r, (
        "the repair assumes the local override is included rather than checking")


def _apparmor_step():
    """The AppArmor step only. Splitting on the first FIX_APPARMOR lands on the
    variable assignment at the top of the script, which is steps 1 and 2."""
    return h.REPAIR_SH[h.REPAIR_SH.index("# 4. AppArmor"):]


def test_the_repair_is_idempotent_on_the_profile():
    step = _apparmor_step()
    assert "S apparmor already" in step, (
        "re-running the repair would append the same rule to the profile again")
    assert "grep -qF" in step, "nothing checks whether the rule is already present"


def test_fixing_the_denial_is_on_by_default():
    """A button called "Fix permissions" that leaves the actual denial in place is
    not fixing permissions."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    body = src[src.index("def infra_hypervisor_fix_permissions"):]
    body = body[:body.index("\n@app.")]
    assert 'body.get("fix_apparmor") is False' in body, (
        "fix_apparmor is opt-in; on a real host this was the actual cause")
    ui = (HERE / "webgui" / "frontend" / "src" / "views" / "Infrastructure.jsx").read_text(encoding="utf-8")
    assert "const [apparmor, setApparmor] = useState(true)" in ui, (
        "the AppArmor checkbox starts off, so the default repair leaves the denial")
    assert "fix_apparmor: apparmor" in ui, "the UI never sends the flag"


@pytest.mark.skipif(not _has("dash") and not _has("sh"), reason="no POSIX shell")
def test_the_apparmor_rule_is_really_written(tmp_path):
    """Exercised, not read for the right words: gating the whole step off went
    unnoticed by every assertion above, because they only check the script's text."""
    abs_file = tmp_path / "libvirt-qemu"
    abs_file.write_text("# a packaged abstraction with no local include\n  /dev/null rw,\n")
    pool = tmp_path / "images"
    pool.mkdir()
    (pool / "base.qcow2").write_bytes(b"x")
    env = ("POOLDIR='%s'\nFIX_APPARMOR=1\nAPPARMOR_ABS='%s'\nAPPARMOR_ABS_LOCAL='%s'"
           % (pool, abs_file, tmp_path / "local" / "libvirt-qemu"))
    out = subprocess.run(["sh", "-c", env + "\n" + h.REPAIR_SH],
                         capture_output=True, text=True, input="", timeout=120)
    step = _step(h.parse_steps(out.stdout), "apparmor")
    assert step and step["status"] == "ok", (out.stdout, out.stderr[-300:])
    assert "%s/** rwk," % pool in abs_file.read_text(), "the rule never reached the profile"
    assert (tmp_path / "libvirt-qemu.sysible-bak").exists(), "no backup was kept"
    # and again: no second copy of the rule
    out2 = subprocess.run(["sh", "-c", env + "\n" + h.REPAIR_SH],
                          capture_output=True, text=True, input="", timeout=120)
    assert _step(h.parse_steps(out2.stdout), "apparmor")["status"] == "already"
    assert abs_file.read_text().count("/** rwk,") == 1, "the rule was appended twice"


def test_the_local_override_is_used_when_the_abstraction_includes_it(tmp_path):
    abs_file = tmp_path / "libvirt-qemu"
    abs_file.write_text("#include <local/abstractions/libvirt-qemu>\n")
    local = tmp_path / "local" / "libvirt-qemu"
    pool = tmp_path / "images"
    pool.mkdir()
    env = ("POOLDIR='%s'\nFIX_APPARMOR=1\nAPPARMOR_ABS='%s'\nAPPARMOR_ABS_LOCAL='%s'"
           % (pool, abs_file, local))
    out = subprocess.run(["sh", "-c", env + "\n" + h.REPAIR_SH],
                         capture_output=True, text=True, input="", timeout=120)
    assert _step(h.parse_steps(out.stdout), "apparmor")["status"] == "ok", out.stdout
    assert local.exists() and "/** rwk," in local.read_text(), (
        "the rule went to the abstraction even though it includes a local override")
    assert "/** rwk," not in abs_file.read_text(), "the packaged abstraction was edited anyway"
