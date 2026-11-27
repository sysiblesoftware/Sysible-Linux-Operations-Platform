"""Why a VM never got an address -- said once, instead of five guesses.

REPORTED, verbatim from an apply that had just started working:

    Error: couldn't retrieve IP address of domain id: 7a1c...
    Please check following: 1) is DHCP enabled on this network? 2) is there any
    IP address available for this network? 3) is the domain running? 4) is the
    network attached? 5) ...

Every one of those five was a red herring. DHCP was enabled, addresses were
free, the network was attached. The VM had died in glibc before it ever brought
a NIC up:

    Fatal glibc error: CPU does not support x86-64-v2

and the only place that sentence existed was /var/log/libvirt/qemu/<name>.log --
qemu's own stderr. SLEP could not show it, because the generated domain's serial
console is a pty and a pty is gone with the process. The operator spent the
evening on DHCP for a CPU-feature abort, and only Debian and Ubuntu came up,
which made it look like a flaky network rather than one missing HCL block.

So the question "why did this VM get no IP?" is answered from four places at
once -- domain state, the network's leases, that log, and any kernel denial --
and then one hint names the single most likely cause. Nothing is written.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

from backend import hypervisor as h

HERE = Path(__file__).resolve().parents[1]

# Real `virsh` output, column for column. The columns are the point: an earlier
# stub of mine had a short lease row, the IP came out as the hostname, and the
# script looked broken when it was right.
VIRSH = r"""#!/bin/sh
while [ $# -gt 0 ]; do case "$1" in -c) shift 2;; *) break;; esac; done
cmd="$1"; shift
case "$cmd" in
  list) printf '%s\n' $DOMS;;
  net-dhcp-leases)
    echo ' Expiry Time           MAC address         Protocol   IP address            Hostname   Client ID or DUID'
    echo '------------------------------------------------------------------------------------------------------'
    [ -n "$LEASED_MAC" ] && echo " 2026-11-27 21:30:00   $LEASED_MAC   ipv4       192.168.122.45/24     leaseholder   01:$LEASED_MAC"
    exit 0;;
  domstate) eval "echo \${STATE_$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'):-running}";;
  domiflist)
    echo ' Interface   Type      Source     Model    MAC'
    echo '--------------------------------------------------------'
    eval "mac=\${MAC_$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'):-}"
    [ -n "$mac" ] && echo " vnet0       network   default    virtio   $mac"
    exit 0;;
esac
"""


def _has(cmd):
    return subprocess.run(["sh", "-c", "command -v " + cmd],
                          capture_output=True).returncode == 0


@pytest.fixture
def host(tmp_path):
    """A fake hypervisor: a virsh on PATH, a qemu log directory, and whatever
    dmesg we want it to have. Returns a run(**env) -> steps callable."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "virsh").write_text(VIRSH)
    (bin_ / "virsh").chmod(0o755)
    logs = tmp_path / "qemu"
    logs.mkdir()

    def run(doms="", logfiles=None, dmesg=None, **env):
        for name, body in (logfiles or {}).items():
            (logs / (name + ".log")).write_text(body)
        if dmesg is not None:
            (bin_ / "dmesg").write_text("#!/bin/sh\ncat <<'E'\n" + dmesg + "\nE\n")
            (bin_ / "dmesg").chmod(0o755)
        assigns = "".join("%s='%s'\nexport %s\n" % (k, v, k) for k, v in env.items())
        pre = ("PATH='%s':$PATH\nexport PATH\nQEMULOG_DIR='%s'\nexport QEMULOG_DIR\n"
               "DOMS='%s'\nexport DOMS\n" % (bin_, logs, doms)) + assigns
        p = subprocess.run(["sh", "-c", pre + h.WHY_NO_IP_SH],
                           capture_output=True, text=True, input="", timeout=120)
        return h.parse_steps(p.stdout, h.BOOT_STEP_LABELS)

    return run


def _step(steps, key):
    return [s for s in steps if s["step"] == key]


def _one(steps, key):
    got = _step(steps, key)
    assert got, "no %r step in %r" % (key, [s["step"] for s in steps])
    return got[0]


root_only = pytest.mark.skipif(
    os.geteuid() != 0, reason="the script escalates for the VM logs; needs root here")


# --------------------------------------------------------------- the contract

def test_the_script_is_posix_sh(tmp_path):
    """/bin/sh is dash on Debian. A bashism here fails on the host, not in CI."""
    if not _has("dash"):
        pytest.skip("dash not available to check with")
    f = tmp_path / "s.sh"
    f.write_text(h.WHY_NO_IP_SH)
    p = subprocess.run(["dash", "-n", str(f)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_every_step_it_emits_has_a_human_label():
    """An unlabelled step shows the operator a bare key like 'qemulog'."""
    for key in re.findall(r"^\s*S ([a-z_]+) ", h.WHY_NO_IP_SH, re.M):
        assert key in h.BOOT_STEP_LABELS, "step %r has no label" % key


def test_it_writes_nothing_on_the_host():
    """This runs with sudo on someone's hypervisor to answer a question. A
    diagnostic that mutates is not a diagnostic. ('rm ' appears in the script
    only inside the word 'Terraform ', so match command positions, not text.)"""
    banned = ("chmod", "chown", "chgrp", "setfattr", "setenforce", "truncate",
              "tee", "mv", "rm", "virsh start", "destroy", "undefine",
              "net-update", "systemctl", "apparmor_parser", "aa-complain")
    at_cmd = r"(?:^|[;&|(]|\$\(|\bthen\b|\bdo\b|\belse\b)\s*(%s)\b" % "|".join(
        b.replace(" ", r"\s+") for b in banned)
    hits = re.findall(at_cmd, h.WHY_NO_IP_SH, re.M)
    assert not hits, "the diagnostic would change the host: %r" % (hits,)
    # and no redirection into a file
    assert not re.search(r">\s*/(?!dev/null)", h.WHY_NO_IP_SH), "it writes a file"


def test_it_is_reachable_from_the_ui_and_served():
    """An endpoint nothing calls does not exist; a button with no endpoint is worse."""
    views = HERE / "webgui" / "frontend" / "src" / "views"
    ui = "\n".join(f.read_text(encoding="utf-8") for f in views.glob("*.jsx"))
    assert "infra/hypervisor-why-no-ip" in ui, "no view calls it"
    from backend import app as backend_app
    paths = {getattr(r, "path", "") for r in backend_app.app.routes}
    assert "/infra/hypervisor-why-no-ip" in paths, "called from the UI but not served"


def test_the_label_vocabulary_actually_reaches_the_parser():
    """PERM_STEP_LABELS existed, was tested, and was never passed to parse_steps,
    so the storage steps rendered as raw keys. Pin that the labels are USED."""
    import inspect
    from backend import app as backend_app
    src = inspect.getsource(backend_app)
    for fn, table in (("def infra_hypervisor_permissions", "PERM_STEP_LABELS"),
                      ("def infra_hypervisor_fix_permissions", "PERM_STEP_LABELS"),
                      ("def infra_hypervisor_why_no_ip", "BOOT_STEP_LABELS")):
        body = src[src.index(fn):src.index(fn) + 3000]
        assert table in body, "%s does not pass %s to parse_steps" % (fn, table)
    # and the merge means a key shared with PROVISION_SH still resolves
    got = h.parse_steps("SYSIBLE_STEP|sudo|ok|x", h.BOOT_STEP_LABELS)
    assert got[0]["label"] == "Administrative access", got


# ------------------------------------------- the behaviour, actually exercised

@root_only
def test_it_finds_the_cpu_abort_that_looked_like_a_dhcp_timeout(host):
    """THE reported failure. arch-1 is shut off with x86-64-v2 in its qemu log;
    ubuntu-1 is up with a lease. The hint must name host-passthrough, not DHCP."""
    steps = host(doms="arch-1 ubuntu-1",
                 STATE_arch_1="shut off",
                 MAC_arch_1="52:54:00:aa:aa:aa", MAC_ubuntu_1="52:54:00:bb:bb:bb",
                 LEASED_MAC="52:54:00:bb:bb:bb",
                 logfiles={"arch-1": "char device redirected to /dev/pts/3\n"
                                     "Fatal glibc error: CPU does not support x86-64-v2\n",
                           "ubuntu-1": "char device redirected to /dev/pts/4\n"})
    assert "x86-64-v2" in _one(steps, "qemulog")["detail"]
    hint = _one(steps, "hint")
    assert hint["status"] == "failed"
    assert "host-passthrough" in hint["detail"], hint["detail"]
    assert "DHCP" not in hint["detail"] or "looked like a DHCP" in hint["detail"]


@root_only
def test_a_stopped_vm_is_not_crossed_twice_for_one_cause(host):
    """A stopped VM has no lease BECAUSE it is stopped. Reporting that as its own
    failure put two red crosses on screen for one cause and padded the summary."""
    steps = host(doms="arch-1", STATE_arch_1="shut off", MAC_arch_1="52:54:00:aa:aa:aa")
    assert _one(steps, "state")["status"] == "failed"
    lease = _one(steps, "lease")
    assert lease["status"] == "ok", "a stopped VM's missing lease is not a second fault"
    assert "not running" in lease["detail"]


@root_only
def test_a_running_vm_with_no_lease_is_a_real_fault(host):
    """The other half: if it IS up and has no address, that is the guest's doing
    and must still be reported -- the fix above must not silence it."""
    steps = host(doms="ubuntu-1", MAC_ubuntu_1="52:54:00:bb:bb:bb", LEASED_MAC="")
    assert _one(steps, "state")["status"] == "ok"
    lease = _one(steps, "lease")
    assert lease["status"] == "failed", lease
    assert "never asked for an address" in lease["detail"]


@root_only
def test_the_lease_is_reported_as_an_address_not_a_hostname(host):
    """virsh net-dhcp-leases puts the IP in column 5 and the hostname in 6. Read
    the wrong one and the answer to 'what address did it get' is its own name."""
    steps = host(doms="ubuntu-1", MAC_ubuntu_1="52:54:00:bb:bb:bb",
                 LEASED_MAC="52:54:00:bb:bb:bb")
    lease = _one(steps, "lease")
    assert lease["status"] == "ok"
    assert "192.168.122.45" in lease["detail"], lease["detail"]
    assert "leaseholder" not in lease["detail"], "that is the hostname column"


@root_only
def test_a_security_denial_outranks_the_cpu_hint(host):
    """Both can be true at once. A kernel refusing the disk is the more actionable
    of the two and has a button in the same panel, so it must win the hint."""
    steps = host(doms="arch-1", STATE_arch_1="shut off", MAC_arch_1="52:54:00:aa:aa:aa",
                 logfiles={"arch-1": "Fatal glibc error: CPU does not support x86-64-v2\n"},
                 dmesg='audit: type=1400 apparmor="DENIED" operation="open" '
                       'profile="libvirt-9f2c" name="/var/lib/libvirt/images/arch-base.qcow2" '
                       'comm="qemu-system-x86" requested_mask="r" fsuid=64055 ouid=64055')
    assert _one(steps, "denial")["status"] == "failed"
    hint = _one(steps, "hint")
    assert "security module" in hint["detail"], hint["detail"]
    assert "host-passthrough" not in hint["detail"], "the denial is the better answer"


@root_only
def test_it_says_so_when_terraform_rolled_the_domain_back(host):
    """A failed apply often destroys the domain, so there is nothing to inspect.
    Saying 'no VMs' beats listing four healthy-looking checks against nothing."""
    steps = host(doms="", PREFIX="rocky")
    dom = _one(steps, "domains")
    assert dom["status"] == "failed"
    assert "rocky" in dom["detail"], dom["detail"]
    assert "rolled" in dom["detail"]
    assert _one(steps, "hint")["status"] == "failed"


@root_only
def test_the_prefix_keeps_one_project_out_of_anothers_answer(host):
    """A hypervisor runs every project's VMs. Without the prefix the answer for
    one project lists the others' domains."""
    steps = host(doms="arch-1 ubuntu-1", PREFIX="arch",
                 MAC_arch_1="52:54:00:aa:aa:aa")
    assert _one(steps, "domains")["detail"].split() == ["arch-1"]
    assert len(_step(steps, "state")) == 1


@root_only
def test_it_looks_past_dmesg_for_the_denial(host):
    """dmesg was EMPTY on the reported host while auditd had every denial, and
    reading only dmesg sent the whole evening back to file permissions."""
    loop = re.search(r"^for src in (.+); do$", h.WHY_NO_IP_SH, re.M)
    assert loop, "the denial search is no longer a loop over sources"
    for src in ("dmesg", "journalctl", "audit.log"):
        assert src in loop.group(1), "%s is not one of the sources searched" % src
    steps = host(doms="ubuntu-1", MAC_ubuntu_1="52:54:00:bb:bb:bb",
                 LEASED_MAC="52:54:00:bb:bb:bb", dmesg="")
    assert _one(steps, "denial")["status"] == "ok"


@root_only
def test_a_missing_virsh_is_said_not_crashed_through(host, tmp_path):
    """A host that was never provisioned must get a sentence, not a stack of
    empty checks."""
    empty = tmp_path / "empty"
    empty.mkdir()
    p = subprocess.run(["sh", "-c", "PATH='%s'\nexport PATH\n" % empty + h.WHY_NO_IP_SH],
                       capture_output=True, text=True, input="", timeout=60)
    steps = h.parse_steps(p.stdout, h.BOOT_STEP_LABELS)
    assert _one(steps, "domains")["status"] == "failed"
    assert "not installed" in _one(steps, "domains")["detail"]
