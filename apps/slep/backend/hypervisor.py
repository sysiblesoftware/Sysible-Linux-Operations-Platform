"""Turn any Linux host into a working KVM hypervisor, over SSH.

SLEP drives hypervisors it does not own. Until now it could *test* a connection,
*list* what was already there and *start* a pool someone else had defined, but it
could not make a plain Linux box into a hypervisor -- that was left to the
operator, by hand, differently on every distro. A freshly installed host had no
storage pool, no network and in most cases no libvirt at all, and the first sign
of it was a Terraform apply failing somewhere in the middle.

Nothing here assumes the remote host is Debian, or Sysible, or anything else: the
family comes off /etc/os-release's ID/ID_LIKE chain, which is why Linux Mint
(ID=linuxmint, ID_LIKE="ubuntu debian") and Sysible Server (ID=sysible,
ID_LIKE=debian) both land on the same package set without being named.

The remote half is one POSIX sh script, not bash -- /bin/sh is dash on Debian and
ash on Alpine. It is idempotent: every step is safe to re-run, and a host that is
already a hypervisor reports "already" rather than failing. Each step prints one
machine-readable line so a partial success can be shown step by step instead of
collapsing into a single wall of output with no indication of how far it got.
"""

# One line per step, parsed by parse_steps(). The delimiter is a pipe because
# virsh/apt detail text contains colons, commas and equals signs freely.
MARK = "SYSIBLE_STEP"

STEP_LABELS = {
    "sudo": "Administrative access",
    "os": "Identify the distribution",
    "virt": "CPU virtualisation",
    "packages": "Install KVM and libvirt",
    "daemon": "Start the libvirt daemon",
    "group": "Grant the account libvirt access",
    "pool": "Default storage pool",
    "network": "Default network",
    "verify": "Verify",
}

# Families we know how to install on. The KEY is what the script resolves an
# ID/ID_LIKE chain down to; every derivative rides in on its parent's name.
FAMILIES = ("debian", "rhel", "suse", "arch", "alpine")


def family_for(id_: str, id_like: str) -> str:
    """Resolve an os-release ID/ID_LIKE chain to a package family, or ''.

    Mirrors the shell in PROVISION_SH so it can be tested without a host. ID is
    tried first, then each word of ID_LIKE in order -- which is how a derivative
    with an unknown ID (linuxmint, pop, zorin, sysible) reaches its parent."""
    for tok in [(id_ or "").strip().lower()] + (id_like or "").lower().split():
        if not tok:
            continue
        if tok in ("debian", "ubuntu"):
            return "debian"
        if tok in ("rhel", "centos", "fedora", "rocky", "almalinux", "ol"):
            return "rhel"
        if tok.startswith("opensuse") or tok in ("suse", "sles", "sled"):
            return "suse"
        if tok in ("arch", "archarm", "manjaro"):
            return "arch"
        if tok == "alpine":
            return "alpine"
    return ""


def parse_steps(stdout: str, labels: dict | None = None):
    """Turn the script's marker lines into [{step,label,status,detail}].

    Anything that is not a marker line is ignored, so apt/dnf chatter on stdout
    cannot be mistaken for a result.

    `labels` is the extra vocabulary for THIS script, merged over the base table
    so the keys every script shares (sudo) keep resolving. Without it a caller
    got PROVISION_SH's table only, so PERM_STEP_LABELS was defined, tested, and
    then never actually reached parse_steps -- the storage steps rendered as
    their raw keys and the operator read "fixed_vols" and "qemu_user" in the UI.
    Unknown keys still fall back to the key itself rather than vanishing."""
    table = dict(STEP_LABELS)
    table.update(labels or {})
    out = []
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line.startswith(MARK + "|"):
            continue
        parts = line.split("|", 3)
        if len(parts) < 3:
            continue
        key = parts[1].strip()
        status = parts[2].strip()
        detail = parts[3].strip() if len(parts) > 3 else ""
        out.append({"step": key, "label": table.get(key, key),
                    "status": status, "detail": detail})
    return out


# The remote script. POSIX sh only. Reads the sudo password as the FIRST and ONLY
# line of stdin, so it is never in argv and never visible in ps on the host.
PROVISION_SH = r'''
set -u
S() { printf 'SYSIBLE_STEP|%s|%s|%s\n' "$1" "$2" "$3"; }

# ---------------------------------------------------------------- root access
# Consume stdin exactly once, whether or not sudo turns out to need it. Leaving
# the password unread would hand it to whatever ran next on the same stdin.
SUDOPW=''
if [ ! -t 0 ]; then read -r SUDOPW 2>/dev/null || SUDOPW=''; fi
SUDO=''
if [ "$(id -u)" -eq 0 ]; then
  S sudo already 'running as root'
else
  if printf '%s\n' "$SUDOPW" | sudo -S -p '' -v >/dev/null 2>&1; then
    SUDO='sudo -n'
    S sudo ok 'sudo accepted'
  else
    S sudo failed 'This account cannot use sudo on the host (or the password was wrong). Making a host a hypervisor needs root to install packages.'
    exit 10
  fi
fi
SUDOPW=''

# ------------------------------------------------------------ which distro is it
ID=''; ID_LIKE=''; PRETTY_NAME=''
if [ -r /etc/os-release ]; then . /etc/os-release 2>/dev/null || true; fi
fam=''
for t in ${ID:-} ${ID_LIKE:-}; do
  case "$t" in
    debian|ubuntu)                        fam=debian ;;
    rhel|centos|fedora|rocky|almalinux|ol) fam=rhel ;;
    opensuse*|suse|sles|sled)             fam=suse ;;
    arch|archarm|manjaro)                 fam=arch ;;
    alpine)                               fam=alpine ;;
  esac
  [ -n "$fam" ] && break
done
if [ -z "$fam" ]; then
  S os failed "Unrecognised distribution (ID=${ID:-?} ID_LIKE=${ID_LIKE:-none}). Install qemu-kvm, libvirt and virtinst on it by hand, then connect to it as an existing hypervisor."
  exit 11
fi
S os ok "${PRETTY_NAME:-$ID} -> $fam"

# ------------------------------------------------------- can this CPU run VMs
arch="$(uname -m 2>/dev/null || echo unknown)"
virt=0
grep -qE '(vmx|svm)' /proc/cpuinfo 2>/dev/null && virt=1
[ -e /dev/kvm ] && virt=1
case "$arch" in aarch64|arm64) [ -d /sys/module/kvm ] && virt=1 ;; esac
if [ "$virt" -eq 0 ]; then
  S virt failed "This host's CPU reports no hardware virtualisation (no vmx/svm in /proc/cpuinfo, no /dev/kvm). VMs would fall back to pure emulation, which is too slow to be useful -- so this stops here rather than building something that crawls. If the host is itself a VM, enable nested virtualisation on ITS hypervisor."
  exit 12
fi
S virt ok "$arch with hardware virtualisation"

# ------------------------------------------------------------------- packages
pkg_debian='qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients virtinst dnsmasq-base bridge-utils'
pkg_rhel='qemu-kvm libvirt libvirt-daemon-config-network virt-install'
pkg_suse='qemu-kvm libvirt libvirt-daemon libvirt-daemon-qemu virt-install'
pkg_arch='libvirt virt-install dnsmasq bridge-utils'
pkg_alpine='libvirt-daemon libvirt-client virt-install dnsmasq bridge'
case "$arch" in
  aarch64|arm64) pkg_debian='qemu-system-arm qemu-utils libvirt-daemon-system libvirt-clients virtinst dnsmasq-base bridge-utils' ;;
esac

out=''
rc=0
case "$fam" in
  debian)
    out="$(DEBIAN_FRONTEND=noninteractive $SUDO apt-get update -qq 2>&1)" || rc=$?
    # qemu-system-x86 is the current name; qemu-kvm is the old one and is still
    # all there is on older Ubuntu/Mint. Try the modern set, fall back once.
    if ! out="$(DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq --no-install-recommends $pkg_debian 2>&1)"; then
      out="$(DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq --no-install-recommends qemu-kvm libvirt-daemon-system libvirt-clients virtinst 2>&1)" || rc=$?
    fi ;;
  rhel)
    if command -v dnf >/dev/null 2>&1; then out="$($SUDO dnf install -y -q $pkg_rhel 2>&1)" || rc=$?
    else out="$($SUDO yum install -y -q $pkg_rhel 2>&1)" || rc=$?; fi ;;
  suse)    out="$($SUDO zypper --non-interactive --quiet install $pkg_suse 2>&1)" || rc=$? ;;
  arch)
    out="$($SUDO pacman -Sy --noconfirm --needed $pkg_arch 2>&1)" || rc=$?
    if [ "$rc" -eq 0 ]; then
      $SUDO pacman -Sy --noconfirm --needed qemu-full >/dev/null 2>&1 || \
      $SUDO pacman -Sy --noconfirm --needed qemu-base >/dev/null 2>&1 || true
    fi ;;
  alpine) out="$($SUDO apk add --no-cache $pkg_alpine 2>&1)" || rc=$? ;;
esac
if [ "$rc" -ne 0 ]; then
  S packages failed "$(printf '%s' "$out" | tr '\n' ' ' | tail -c 300)"
  exit 13
fi
command -v virsh >/dev/null 2>&1 || { S packages failed 'The packages installed but virsh is still not on PATH.'; exit 13; }
S packages ok 'qemu-kvm, libvirt and virtinst are installed'

# --------------------------------------------------------------------- daemon
started=''
if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files 2>/dev/null | grep -q '^libvirtd\.service'; then
    $SUDO systemctl enable --now libvirtd >/dev/null 2>&1 && started='libvirtd'
  fi
  if [ -z "$started" ]; then
    # libvirt >= 9 can ship only the modular daemons; the sockets are what matter.
    for u in virtqemud.socket virtnetworkd.socket virtstoraged.socket; do
      $SUDO systemctl enable --now "$u" >/dev/null 2>&1 && started='modular libvirt daemons'
    done
  fi
elif command -v rc-update >/dev/null 2>&1; then
  $SUDO rc-update add libvirtd default >/dev/null 2>&1
  $SUDO rc-service libvirtd start >/dev/null 2>&1 && started='libvirtd (OpenRC)'
elif command -v service >/dev/null 2>&1; then
  $SUDO service libvirtd start >/dev/null 2>&1 && started='libvirtd (sysv)'
fi
if $SUDO virsh -c qemu:///system version >/dev/null 2>&1; then
  S daemon ok "${started:-libvirt} is running"
else
  S daemon failed "libvirt is installed but not answering on qemu:///system. Check its log on the host (journalctl -u libvirtd)."
  exit 14
fi

# ---------------------------------------------------------------------- group
me="$(id -un)"
added=''
if [ "$(id -u)" -eq 0 ]; then
  added='root needs no group'
else
  for g in libvirt libvirtd kvm; do
    if getent group "$g" >/dev/null 2>&1; then
      $SUDO usermod -aG "$g" "$me" >/dev/null 2>&1 && added="$added $g"
    fi
  done
  added="$(printf '%s' "$added" | sed 's/^ //')"
fi
if [ -n "$added" ]; then
  S group ok "$me is in: $added (a new SSH session picks this up)"
else
  S group failed "Could not add $me to a libvirt group. qemu+ssh can log in but every VM operation will be denied."
fi

# ----------------------------------------------------------------------- pool
POOLDIR=/var/lib/libvirt/images
if $SUDO virsh -c qemu:///system pool-info default >/dev/null 2>&1; then
  pstate=already
else
  $SUDO virsh -c qemu:///system pool-define-as default dir --target "$POOLDIR" >/dev/null 2>&1 || true
  pstate=defined
fi
$SUDO virsh -c qemu:///system pool-build default >/dev/null 2>&1 || true
$SUDO virsh -c qemu:///system pool-start default >/dev/null 2>&1 || true
$SUDO virsh -c qemu:///system pool-autostart default >/dev/null 2>&1 || true
if $SUDO virsh -c qemu:///system pool-info default 2>/dev/null | grep -qi 'state: *running'; then
  # A VM's disk is a copy-on-write clone whose BACKING file is the base image, and
  # the qemu process (not libvirtd) opens that whole chain as its own unprivileged
  # user. If the pool directory is not traversable by it, every VM fails to start
  # with "Could not open <base>.qcow2: Permission denied" even though libvirtd,
  # being root, created the files perfectly well.
  $SUDO chmod o+x "$POOLDIR" 2>/dev/null || true
  S pool ok "default -> $POOLDIR ($pstate, autostart on)"
else
  S pool failed "The default storage pool is defined but will not start. On the host: virsh pool-start default"
fi

# -------------------------------------------------------------------- network
if $SUDO virsh -c qemu:///system net-info default >/dev/null 2>&1; then
  nstate=already
else
  for x in /usr/share/libvirt/networks/default.xml /usr/share/libvirt/networks/default-network.xml; do
    [ -r "$x" ] && $SUDO virsh -c qemu:///system net-define "$x" >/dev/null 2>&1 && nstate=defined && break
  done
  nstate="${nstate:-missing}"
fi
$SUDO virsh -c qemu:///system net-start default >/dev/null 2>&1 || true
$SUDO virsh -c qemu:///system net-autostart default >/dev/null 2>&1 || true
if $SUDO virsh -c qemu:///system net-info default 2>/dev/null | grep -qi 'active: *yes'; then
  S network ok "default is active ($nstate, autostart on)"
else
  S network failed "No active 'default' network. Either define one on the host, or point the project at a bridge instead."
fi

# --------------------------------------------------------------------- verify
v="$($SUDO virsh -c qemu:///system version 2>&1 | tr '\n' ' ' | tail -c 200)"
S verify ok "$v"
'''


# --------------------------------------------------------------------------- perms
#
# The failure this exists for, verbatim from a host:
#
#   error creating libvirt domain: internal error: process exited while connecting
#   to monitor: qemu-system-x86_64: -blockdev {"driver":"file","filename":
#   "/var/lib/libvirt/images/arch-base.qcow2","node-name":"libvirt-3-storage",
#   "auto-read-only":true,...}: Could not open '...arch-base.qcow2': Permission denied
#
# Note WHO fails: qemu-system-x86_64, not libvirtd. libvirtd is root and creates
# the files perfectly; the VM process runs unprivileged (libvirt-qemu on Debian,
# qemu on RHEL) and must open the ENTIRE backing chain of its disk. Each VM disk
# here is a copy-on-write clone whose backing file is the shared base image, so
# the base image is opened by every VM, read-only, as that unprivileged user.
#
# Three separate things break that, and they look identical from the outside:
#
#   1. A volume libvirt creates in a dir pool lands 0600 root:root. libvirt
#      relabels a domain's own disk on start, but a backing file it did not put in
#      the domain XML itself can be missed -- so the top disk is readable and the
#      base underneath it is not.
#   2. remember_owner=1 (the default) makes libvirt RESTORE a file's original
#      owner when a domain stops. With one base image shared by several VMs,
#      stopping one can hand the base back to root:root while the others still
#      need it -- which is why this can work once and then stop working.
#   3. The pool directory itself is not traversable by the qemu user (mode 0700),
#      so the open fails before the file's own mode matters at all.
#
# DIAGNOSE_SH reports the actual state of all three instead of guessing between
# them; REPAIR_SH fixes whichever are wrong, idempotently.

PERM_STEP_LABELS = {
    "qemu_user": "Which user the VMs run as",
    "pooldir": "Pool directory",
    "volumes": "Volume ownership",
    "remember_owner": "remember_owner setting",
    "saved_owner": "Owner libvirt saved on the files",
    "mac": "AppArmor / SELinux",
    "denials": "Recent kernel denials",
    "fixed_dir": "Pool directory traversable",
    "fixed_vols": "Volumes readable by the VM user",
    "fixed_remember": "remember_owner",
    "reloaded": "libvirt reloaded",
    "apparmor": "AppArmor access to the pool",
}

# Shared by DIAGNOSE_SH and REPAIR_SH, in ONE place on purpose: the repair used to
# group-own volumes to `id -gn $QU` while the diagnosis read the group out of
# qemu.conf, so the repair could hand a file to a group the VM process is not in
# and still report success.
_PREAMBLE_SH = r"""
set -u
S() { printf 'SYSIBLE_STEP|%s|%s|%s\n' "$1" "$2" "$3"; }
SUDOPW=''
if [ ! -t 0 ]; then read -r SUDOPW 2>/dev/null || SUDOPW=''; fi
SUDO=''
if [ "$(id -u)" -ne 0 ]; then
  if printf '%s\n' "$SUDOPW" | sudo -S -p '' -v >/dev/null 2>&1; then SUDO='sudo -n'
  else S sudo failed 'Needs sudo on the host.'; exit 10; fi
fi
SUDOPW=''
POOLDIR="${POOLDIR:-/var/lib/libvirt/images}"
"""

# How the VM process is actually credentialed, which is the whole crux:
#
#   libvirt starts qemu as the user= and group= from /etc/libvirt/qemu.conf -- on
#   Debian that is libvirt-qemu:kvm -- and it does NOT add the user's supplementary
#   groups. So making a file group-libvirt-qemu and g+r does nothing for a process
#   whose only group is kvm. The group that matters is qemu.conf's, not the user's.
_QEMU_ID_SH = r"""
QU="$($SUDO sed -n 's/^[[:space:]]*user[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' /etc/libvirt/qemu.conf 2>/dev/null | tail -1)"
QG="$($SUDO sed -n 's/^[[:space:]]*group[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' /etc/libvirt/qemu.conf 2>/dev/null | tail -1)"
QUSRC='qemu.conf'
if [ -z "$QU" ]; then
  QUSRC='package default'
  for c in libvirt-qemu qemu; do id "$c" >/dev/null 2>&1 && QU="$c" && break; done
  QU="${QU:-root}"
fi
if [ -z "$QG" ]; then
  for c in kvm libvirt-qemu qemu; do getent group "$c" >/dev/null 2>&1 && QG="$c" && break; done
  QG="${QG:-kvm}"
fi

# Probe exactly as the VM process is credentialed: its user AND only its qemu.conf
# group. `su` alone would hand the account its own supplementary groups, which the
# real process does not have -- so a file could probe readable and still be denied.
as_qemu() {
  if [ "$(id -u)" -eq 0 ] && command -v setpriv >/dev/null 2>&1; then
    setpriv --reuid "$QU" --regid "$QG" --clear-groups sh -c "$1" 2>/dev/null
  elif [ "$(id -u)" -eq 0 ]; then
    su -s /bin/sh "$QU" -c "$1" 2>/dev/null
  else
    sudo -n -u "$QU" -g "$QG" sh -c "$1" 2>/dev/null
  fi
}
"""

_DIAGNOSE_BODY = r"""
S qemu_user ok "$QU:$QG (from $QUSRC) -- this is how the VM process is credentialed, and it must read every disk AND its backing files"

# The pool directory: an open fails here first if it is not traversable.
if [ -d "$POOLDIR" ]; then
  dinfo="$(ls -ld "$POOLDIR" 2>/dev/null | awk '{print $1" "$3":"$4}')"
  if as_qemu "test -x '$POOLDIR'"; then
    S pooldir ok "$POOLDIR is $dinfo and $QU can traverse it"
  else
    S pooldir failed "$POOLDIR is $dinfo -- $QU:$QG CANNOT traverse it, so every disk under it fails to open whatever its own mode says. Check the parent directories too."
  fi
else
  S pooldir failed "$POOLDIR does not exist."
fi

# Every volume, and whether the VM process can really read it. This is the answer.
bad=''; n=0
for f in "$POOLDIR"/*.qcow2 "$POOLDIR"/*.img "$POOLDIR"/*.raw; do
  [ -e "$f" ] || continue
  n=$((n+1))
  if ! as_qemu "test -r '$f'"; then
    bad="$bad $(basename "$f")[$($SUDO ls -l "$f" 2>/dev/null | awk '{print $1" "$3":"$4}')]"
  fi
done
bad="$(printf '%s' "$bad" | sed 's/^ //')"
if [ "$n" -eq 0 ]; then S volumes ok 'no volumes in the pool yet'
elif [ -z "$bad" ]; then S volumes ok "$n volume(s), all readable by $QU:$QG -- so if a VM still cannot open its disk, the cause is ABOVE the filesystem (AppArmor/SELinux), not these permissions"
else S volumes failed "$QU:$QG cannot read: $bad -- a VM whose disk has one of these as its BACKING file dies with Could not open ...: Permission denied."; fi

# The saved owner. A file with these set has its ownership RESTORED on every
# domain start, which is why a hand-made chown does not stick.
xa=''
for f in "$POOLDIR"/*.qcow2; do
  [ -e "$f" ] || continue
  if $SUDO getfattr -m 'trusted.libvirt.security' -d "$f" 2>/dev/null | grep -q 'trusted.libvirt'; then
    xa="$xa $(basename "$f")"
  fi
done
xa="$(printf '%s' "$xa" | sed 's/^ //')"
if [ -z "$xa" ]; then S saved_owner ok 'no saved-owner attributes on the volumes'
else S saved_owner failed "libvirt has a saved owner stored on:$xa -- it restores that owner every time a domain starts, so chown/chmod by hand is undone on the next boot of the VM."; fi

# remember_owner: why this can work once and then stop working.
RO="$($SUDO sed -n 's/^[[:space:]]*remember_owner[[:space:]]*=[[:space:]]*\([0-9]\).*/\1/p' /etc/libvirt/qemu.conf 2>/dev/null | tail -1)"
if [ "${RO:-1}" = "0" ]; then
  S remember_owner ok 'remember_owner = 0 -- libvirt will not hand a shared base image back to root when one VM stops'
else
  S remember_owner failed "remember_owner is ${RO:-unset (defaults to 1)} -- when ONE VM stops, libvirt restores its disks original owner. A base image shared by several VMs can be handed back to root:root while the others still need it, so VMs that worked stop working."
fi

# AppArmor and SELinux produce the identical errno, so say which is enforcing.
mac='none'
if command -v aa-enabled >/dev/null 2>&1 && $SUDO aa-enabled >/dev/null 2>&1; then mac='AppArmor enforcing'
elif [ -d /sys/kernel/security/apparmor ]; then mac='AppArmor present'
fi
if command -v getenforce >/dev/null 2>&1; then mac="$mac; SELinux $($SUDO getenforce 2>/dev/null)"; fi
S mac ok "$mac"

# Look in all three places. On a real host that was denying exactly this, dmesg
# was EMPTY of denials while AppArmor was loaded and enforcing: the kernel ring
# buffer had rotated and auditd had the records. Reporting "no denial" from dmesg
# alone sent the investigation back to file permissions that were already correct.
pool_base="$(basename "$POOLDIR")"
den=""
for src in "dmesg" "journalctl -k --since -1h --no-pager" "cat /var/log/audit/audit.log"; do
  hit="$($SUDO sh -c "$src" 2>/dev/null | grep -iE 'apparmor=.DENIED|avc: *denied' | grep -i "$pool_base" | tail -2 | tr '\n' ' ')"
  [ -n "$hit" ] && den="$den $hit"
done
den="$(printf '%s' "$den" | tail -c 400)"
if [ -n "$den" ]; then
  S denials failed "A security module is denying this, NOT the file permissions: $den"
else
  S denials ok 'no AppArmor/SELinux denial found in dmesg, the kernel journal or the audit log'
fi
"""

_REPAIR_BODY = r"""
SET_REMEMBER="${SET_REMEMBER:-0}"
FIX_APPARMOR="${FIX_APPARMOR:-0}"

# 1. Traversable. o+x only -- NOT o+r: a path open needs only search, and the
#    directory listing stays private, which is what libvirt's own 0711 intends.
if $SUDO chmod o+x "$POOLDIR" 2>/dev/null; then
  S fixed_dir ok "$POOLDIR is now $($SUDO ls -ld "$POOLDIR" | awk '{print $1}')"
else
  S fixed_dir failed "Could not chmod $POOLDIR"
fi

# 2. Every volume readable by the VM process, come what may. Group ownership is
#    NOT the lever: libvirt re-chowns a disk when its domain starts, so any group
#    set here is overwritten moments later.
n=0; failed=''
for f in "$POOLDIR"/*.qcow2 "$POOLDIR"/*.img "$POOLDIR"/*.raw; do
  [ -e "$f" ] || continue
  n=$((n+1))
  # a+r, not chgrp+g+r: libvirt re-chowns a disk when the domain starts, so any
  # group set here is overwritten and a 0640 file ends up readable by nobody that
  # matters. These are read-only backing images; 0644 is what libvirt leaves them
  # as when this works, and it survives the relabel.
  $SUDO chmod a+r "$f" 2>/dev/null || failed="$failed $(basename "$f")"
  # Stale security xattrs are the reason a hand-made chown does not stick: with
  # remember_owner on, libvirt saves each file's original owner here and RESTORES
  # it on every domain start, quietly undoing the fix. Clearing them is harmless
  # when they are absent.
  for a in dac ref_dac selinux ref_selinux; do
    $SUDO setfattr -x "trusted.libvirt.security.$a" "$f" 2>/dev/null || true
  done
done
failed="$(printf '%s' "$failed" | sed 's/^ //')"
# Report whether it WORKED, not merely whether chmod returned 0. Probing as the VM
# process is the only claim worth making -- and it is the claim that was wrong.
still=''
for f in "$POOLDIR"/*.qcow2 "$POOLDIR"/*.img "$POOLDIR"/*.raw; do
  [ -e "$f" ] || continue
  as_qemu "test -r '$f'" || still="$still $(basename "$f")"
done
still="$(printf '%s' "$still" | sed 's/^ //')"
if [ "$n" -eq 0 ]; then S fixed_vols ok 'no volumes to fix yet'
elif [ -n "$still" ]; then S fixed_vols failed "$QU:$QG STILL cannot read:$still -- the files were changed but the open is still denied, so something else is in the way: an ancestor directory that is not traversable, AppArmor/SELinux, or an ACL. Run the permission check for details."
elif [ -n "$failed" ]; then S fixed_vols failed "could not change: $failed"
else S fixed_vols ok "$n volume(s) are world-readable with libvirt's saved owner cleared -- verified by opening them as $QU:$QG"; fi

# 3. Stop libvirt handing a shared base image back to root when one VM stops.
if [ "$SET_REMEMBER" = "1" ]; then
  if [ -f /etc/libvirt/qemu.conf ]; then
    $SUDO cp -n /etc/libvirt/qemu.conf /etc/libvirt/qemu.conf.sysible-bak 2>/dev/null || true
    if $SUDO grep -qE '^[[:space:]]*remember_owner' /etc/libvirt/qemu.conf 2>/dev/null; then
      $SUDO sed -i 's/^[[:space:]]*remember_owner.*/remember_owner = 0/' /etc/libvirt/qemu.conf
    else
      printf '\n# Set by Sysible SLEP: a base image shared by several VMs must not be\n# handed back to its original owner when one of them stops.\nremember_owner = 0\n' | $SUDO tee -a /etc/libvirt/qemu.conf >/dev/null
    fi
    now="$($SUDO sed -n 's/^[[:space:]]*remember_owner[[:space:]]*=[[:space:]]*\([0-9]\).*/\1/p' /etc/libvirt/qemu.conf | tail -1)"
    if [ "$now" = "0" ]; then
      S fixed_remember ok 'remember_owner = 0 (the previous file is kept as /etc/libvirt/qemu.conf.sysible-bak)'
      if command -v systemctl >/dev/null 2>&1; then
        $SUDO systemctl restart libvirtd >/dev/null 2>&1 || $SUDO systemctl restart virtqemud >/dev/null 2>&1 || true
      fi
      if $SUDO virsh -c qemu:///system version >/dev/null 2>&1; then
        S reloaded ok 'libvirt restarted and answering (running VMs are unaffected)'
      else
        S reloaded failed 'libvirt did not come back after the restart -- check journalctl -u libvirtd on the host.'
      fi
    else
      S fixed_remember failed 'could not set remember_owner = 0 in /etc/libvirt/qemu.conf'
    fi
  else
    S fixed_remember failed '/etc/libvirt/qemu.conf is not present on this host'
  fi
fi

# 4. AppArmor. On a real host this was the whole problem, and every file
#    permission was already correct:
#
#      apparmor="DENIED" operation="open" profile="libvirt-<uuid>"
#      name="/var/lib/libvirt/images/arch-base.qcow2" comm="qemu-system-x86"
#      requested_mask="r" denied_mask="r" fsuid=64055 ouid=64055
#
#    fsuid == ouid == libvirt-qemu: qemu owned the file it was refused. libvirt
#    generates a profile per domain with virt-aa-helper, listing that VM's own
#    disks -- and a COW clone's shared BACKING file does not always make it in.
#    The kernel then refuses the open no matter what the mode says, which is why
#    this survives every chmod and is not undone by remember_owner.
#
#    The per-domain profiles all include abstractions/libvirt-qemu, and they are
#    regenerated at domain start, so a rule there reaches every VM on the next
#    start. Some Debian versions include a local/ override and some do not, so
#    use it when it exists and append to the abstraction when it does not.
if [ "$FIX_APPARMOR" = "1" ]; then
  # Overridable so a host that keeps the abstraction elsewhere can be pointed at
  # it -- and so this step can be exercised against a real file in a test instead
  # of only being read for the right words.
  ABS="${APPARMOR_ABS:-/etc/apparmor.d/abstractions/libvirt-qemu}"
  ABS_LOCAL="${APPARMOR_ABS_LOCAL:-/etc/apparmor.d/local/abstractions/libvirt-qemu}"
  if [ ! -f "$ABS" ]; then
    S apparmor ok 'no AppArmor libvirt abstraction on this host -- nothing to do'
  elif $SUDO grep -qF "$POOLDIR/** rwk," "$ABS" 2>/dev/null || \
       $SUDO grep -qF "$POOLDIR/** rwk," "$ABS_LOCAL" 2>/dev/null; then
    S apparmor already "$POOLDIR is already allowed in the libvirt-qemu profile"
  else
    $SUDO cp -n "$ABS" "$ABS.sysible-bak" 2>/dev/null || true
    # rwk, not rk. The first attempt granted read only, reasoning about the shared
    # BASE images -- which are indeed read-only. But the same directory holds every
    # VM's OWN disk, which qemu opens for WRITE, so the denial simply moved one
    # level up the backing chain:
    #
    #   -blockdev {"node-name":"libvirt-2-format","read-only":false,"driver":"qcow2",
    #   "backing":"libvirt-3-format"}: Could not open '.../ubuntu-1.qcow2': Permission denied
    #
    # libvirt-3 (the base) now opened and libvirt-2 (the VM's disk) did not. This is
    # the storage pool qemu runs out of; read-only was never the right scope for it.
    rule="  $POOLDIR/** rwk,"
    if $SUDO grep -q 'local/abstractions/libvirt-qemu' "$ABS" 2>/dev/null; then
      $SUDO mkdir -p "$(dirname "$ABS_LOCAL")"
      printf '%s\n' "$rule" | $SUDO tee -a "$ABS_LOCAL" >/dev/null
      where="$ABS_LOCAL"
    else
      printf '%s\n' "$rule" | $SUDO tee -a "$ABS" >/dev/null
      where="$ABS (previous kept as $ABS.sysible-bak)"
    fi
    $SUDO apparmor_parser -r "$ABS" >/dev/null 2>&1 || $SUDO systemctl reload apparmor >/dev/null 2>&1 || true
    if $SUDO grep -qF "$POOLDIR/** rwk," "$where" 2>/dev/null || $SUDO grep -qF "$POOLDIR/** rwk," "$ABS" 2>/dev/null; then
      S apparmor ok "qemu may now read $POOLDIR -- added to $where. Each VM picks this up when it next starts."
    else
      S apparmor failed "Could not add the rule to the libvirt-qemu profile."
    fi
  fi
fi
"""

DIAGNOSE_SH = _PREAMBLE_SH + _QEMU_ID_SH + _DIAGNOSE_BODY
REPAIR_SH = _PREAMBLE_SH + _QEMU_ID_SH + _REPAIR_BODY


# --------------------------------------------------------------------- why no IP
#
# When a VM does not come up, the provider says:
#
#   couldn't retrieve IP address of domain ... 1) is the domain running properly?
#   2) has the network interface an IP address? 3) Networking issues on your
#   libvirt setup? 4) is DHCP enabled on this Domain's network? 5) if you use
#   bridge network, the domain should have the pkg qemu-agent installed
#
# Five guesses, all about networking, and tonight the answer was none of them: the
# guest aborted in glibc before the NIC existed, because its Terraform predated the
# host-passthrough CPU. Ubuntu and Rocky booted on the same host at the same moment,
# which was the only clue there was.
#
# SLEP could say nothing, because the generated domain's serial console is a pty and
# a pty is not retained. What IS retained, for every started domain, is
# /var/log/libvirt/qemu/<name>.log -- qemu's own stderr. That is where tonight's
# "Could not open ...qcow2: Permission denied" appeared, verbatim, while the UI was
# still talking about DHCP.
#
# So: state, lease, that log, and any security denial naming the domain. Read-only.
BOOT_STEP_LABELS = {
    "domains": "VMs found",
    "state": "VM state",
    "lease": "DHCP lease",
    "qemulog": "What qemu said",
    "denial": "Security denial",
    "hint": "Most likely cause",
}

WHY_NO_IP_SH = r'''
set -u
S() { printf 'SYSIBLE_STEP|%s|%s|%s\n' "$1" "$2" "$3"; }
SUDOPW=''
if [ ! -t 0 ]; then read -r SUDOPW 2>/dev/null || SUDOPW=''; fi
SUDO=''
if [ "$(id -u)" -ne 0 ]; then
  if printf '%s\n' "$SUDOPW" | sudo -S -p '' -v >/dev/null 2>&1; then SUDO='sudo -n'
  else S domains failed 'Needs sudo on the hypervisor to read the VM logs.'; exit 10; fi
fi
SUDOPW=''
PREFIX="${PREFIX:-}"
NET="${NET:-default}"
CPUABORT=0
V="$SUDO virsh -c qemu:///system"

if ! command -v virsh >/dev/null 2>&1; then
  S domains failed 'virsh is not installed on this host.'; exit 11
fi

doms="$($V list --all --name 2>/dev/null | grep -v '^$' | { [ -n "$PREFIX" ] && grep "^$PREFIX" || cat; })"
if [ -z "$doms" ]; then
  S domains failed "No VMs${PREFIX:+ named $PREFIX*} are defined on this hypervisor. Terraform may have rolled the domain back after the failure, in which case the log below is the only thing left of it."
else
  S domains ok "$(printf '%s' "$doms" | tr '\n' ' ')"
fi

leases="$($V net-dhcp-leases "$NET" 2>/dev/null || true)"

for d in $doms; do
  st="$($V domstate "$d" 2>/dev/null || echo unknown)"
  if [ "$st" = "running" ]; then S state ok "$d is $st"
  else S state failed "$d is $st -- it is not running, so it was never going to get an address."; fi

  mac="$($V domiflist "$d" 2>/dev/null | awk 'NR>2 && $5 ~ /:/ {print $5; exit}')"
  if [ -n "$mac" ] && printf '%s' "$leases" | grep -qi "$mac"; then
    S lease ok "$d has a lease: $(printf '%s' "$leases" | grep -i "$mac" | awk '{print $5}')"
  elif [ -n "$mac" ] && [ "$st" != "running" ]; then
    # Not a separate problem: it follows from the state above. Said, not crossed.
    S lease ok "$d ($mac) has no lease, which follows from it not running."
  elif [ -n "$mac" ]; then
    S lease failed "$d ($mac) is running but has no lease on network '$NET' -- it booted and never asked for an address. Look at the guest, not the hypervisor."
  fi

  # qemu's own stderr. The one thing that is always kept, and the one place
  # tonight's real error appeared while the UI talked about DHCP.
  log="${QEMULOG_DIR:-/var/log/libvirt/qemu}/${d}.log"
  if $SUDO test -r "$log"; then
    tail="$($SUDO grep -iE 'error|denied|fail|panic|abort|not support' "$log" 2>/dev/null | tail -4 | tr '\n' ' ')"
    if [ -n "$tail" ]; then
      S qemulog failed "$d: $(printf '%s' "$tail" | tail -c 400)"
      printf '%s' "$tail" | grep -qiE 'x86-64-v[234]|CPU does not support' && CPUABORT=1
    else S qemulog ok "$d: nothing in its qemu log looks like an error."; fi
  else
    S qemulog failed "$d has no $log -- it may never have been started."
  fi
done

# A denial naming any of these domains. AppArmor logs to dmesg, the kernel journal
# or auditd depending on the host -- tonight dmesg was empty while auditd had every
# one of them, and reading only dmesg sent the search back to file permissions.
den=''
for src in "dmesg" "journalctl -k --since -1h --no-pager" "cat /var/log/audit/audit.log"; do
  hit="$($SUDO sh -c "$src" 2>/dev/null | grep -iE 'apparmor=.DENIED|avc: *denied' | tail -3 | tr '\n' ' ')"
  [ -n "$hit" ] && den="$den $hit"
done
if [ -n "$den" ]; then
  S denial failed "A security module is refusing these VMs, NOT the file permissions: $(printf '%s' "$den" | tail -c 400)"
else
  S denial ok 'no AppArmor/SELinux denial in dmesg, the kernel journal or the audit log'
fi

# Say what it looks like, rather than leaving five possibilities on screen.
if printf '%s' "$den" | grep -q .; then
  S hint failed 'A security module is denying access to the disks. This is NOT a networking problem and not a file-permission problem: fix the AppArmor/SELinux profile for the storage pool.'
elif [ "$CPUABORT" = "1" ]; then
  S hint failed 'The guest aborted because the emulated CPU is too old for it -- Rocky/Alma 9+, Arch and openSUSE need x86-64-v2 and glibc kills them before networking starts, which is why this looked like a DHCP timeout. Give the VMs the host CPU: cpu { mode = "host-passthrough" }. Regenerate this project'"'"'s Terraform and apply again; Debian and Ubuntu are unaffected, which is why only some VMs failed.'
elif [ -z "$doms" ]; then
  S hint failed 'No VM survived the apply, so look at what qemu said above rather than at the network.'
else
  S hint ok 'Nothing here points at the disks or a security module. If a VM is running with no lease, look at the guest: it booted and did not ask for an address.'
fi
'''
