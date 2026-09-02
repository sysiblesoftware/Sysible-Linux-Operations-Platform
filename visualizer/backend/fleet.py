"""Sysible Visualizer — the fleet topology model.

The Controller's Network Topology view moved here, so this is where the map's
DATA is built. The Controller exposes four cheap read-only endpoints (hosts,
fleet-health, agents, suppressions) plus one expensive overlay (fleet-posture);
none of them is a topology — the map's shape comes from correlating them:

  * a host's ENVIRONMENT tag, or its SUBNET/gateway, is what groups it;
  * a hypervisor's reported guest names are what nest a VM under the machine it
    actually runs on, across environments;
  * health + posture + suppressions together are what colour a node.

Doing that correlation server-side (rather than in the browser, as the React
view did) keeps it testable and keeps the console dependency-free: the client is
left with layout and drawing.

Every rule here is a port of the Controller view's, deliberately including the
awkward ones — a node's colour honours SUPPRESSIONS, because a map that re-flags
a finding an operator has already silenced disagrees with the dashboard and
teaches people to distrust it.
"""
from __future__ import annotations

import re
import time

# Health/status vocabulary. Kept identical to the Controller view so the two
# never drift into disagreeing about what "critical" means.
STATUSES = ("CRITICAL", "OFFLINE", "WARNING", "SUPPRESSED", "OK", "UNKNOWN")
RANK = {s: i for i, s in enumerate(STATUSES)}

# Posture flags that count as CRITICAL (mirrors the dashboard's critical set).
CRIT_FLAGS = ("ssh_root_login", "firewall_disabled", "eol_os", "risky_accounts")

_IPV4 = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

_HYP_LABEL = {
    "kvm": "KVM hypervisor",
    "qemu": "QEMU hypervisor",
    "proxmox": "Proxmox VE host",
    "xen-dom0": "Xen dom0 (control domain)",
    "virtualbox": "VirtualBox host",
    "vmware": "VMware host",
}


def extract_ip(text: str | None) -> str | None:
    m = _IPV4.search(text or "")
    return m.group(1) if m else None


def subnet_of(ip: str | None) -> str | None:
    return ".".join(ip.split(".")[:3]) + ".0/24" if ip else None


def hypervisor_badge(role: str | None, vms) -> str:
    """e.g. "KVM hypervisor · 4 VMs". Empty for a non-hypervisor."""
    if not role:
        return ""
    try:
        n = int(vms)
    except (TypeError, ValueError):
        n = 0
    count = f"{n} VM{'' if n == 1 else 's'}" if n > 0 else "VM host"
    return f"{_HYP_LABEL.get(role, 'hypervisor')} · {count}"


# --------------------------------------------------------------------------- #
# Suppressions — is a finding currently silenced?
# --------------------------------------------------------------------------- #
def supp_active(s: dict, host: str, env: str, key: str, boot_epoch, now: float) -> bool:
    """Port of the console's suppActive(). A suppression silences a finding only
    for its own key and scope; 'snooze' expires by clock, 'reboot' clears once the
    host has booted past the recorded baseline, the rest are indefinite."""
    if s.get("key") != key:
        return False
    scope, target = s.get("scope"), s.get("target")
    if scope == "host" and target != host:
        return False
    if scope == "env" and target != env:
        return False
    if s.get("type") == "snooze":
        until = s.get("until")
        return (not until) or float(until) > now
    if s.get("type") == "reboot":
        # No boot time known yet (posture not loaded) → treat as still suppressed,
        # matching the console: better to under-flag briefly than to flash a
        # finding the operator has already dealt with.
        if boot_epoch is None or s.get("boot_epoch") is None:
            return True
        return float(boot_epoch) <= float(s["boot_epoch"])
    return True


def _supp_finder(supps, host, env, boot_epoch, now):
    def is_supp(key: str) -> bool:
        return any(supp_active(s, host, env, key, boot_epoch, now) for s in supps or [])
    return is_supp


# --------------------------------------------------------------------------- #
# Health verdict
# --------------------------------------------------------------------------- #
def health_verdict(hh: dict, is_supp) -> str | None:
    """Recompute a host's verdict from the raw fleet-health signals the way the
    dashboard's per-host analysis does — honouring suppressions of its two
    suppressible findings — rather than trusting the server's raw verdict (which
    ignores them). None when the status is genuinely unknown (e.g. an SSH host
    that has never been probed)."""
    if hh.get("online") is False:
        return "OFFLINE"
    if hh.get("online") is None and not hh.get("verdict"):
        return None
    sev = 9
    active = suppressed = 0

    def add(s, key):
        nonlocal sev, active, suppressed
        if key and is_supp(key):
            suppressed += 1
            return
        active += 1
        sev = min(sev, s)

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    if hh.get("ok") is False:
        add(2, None)
    disk = num(hh.get("disk"))
    if disk >= 90:
        add(1, "disk_critical")
    elif disk >= 80:
        add(2, None)
    if num(hh.get("mem")) >= 90:
        add(1, None)
    failed = num(hh.get("failed"))
    if failed > 0:
        add(1 if failed >= 3 else 2, "failed_units")
    if num(hh.get("oom")) > 0:
        add(1, None)
    if active == 0:
        return "SUPPRESSED" if suppressed else "OK"
    return "CRITICAL" if sev == 1 else "WARNING"


def status_of(node: dict) -> str:
    if node.get("online") is False:
        return "OFFLINE"
    if node.get("online") is None and not node.get("verdict"):
        return "UNKNOWN"
    return (node.get("verdict") or "OK").upper()


def worst(nodes) -> str:
    r = min((RANK.get(status_of(n), 5) for n in nodes), default=5)
    return STATUSES[r] if r < len(STATUSES) else "OK"


# --------------------------------------------------------------------------- #
# The merge
# --------------------------------------------------------------------------- #
def build(hosts, health, agents, supps, posture, now: float | None = None) -> list[dict]:
    """One rich record per host, from the Controller's four/five readings.

    Any input may be empty — a failed or slow upstream degrades the map rather
    than blanking it, which is why posture (the expensive sweep) is optional: the
    map draws without it and gains critical rings + gateway labels when it lands.
    """
    now = time.time() if now is None else now
    by_id = {h.get("id"): h for h in health or []}
    ag_by_name, ag_by_id = {}, {}
    for a in agents or []:
        if a.get("hostname"):
            ag_by_name[a["hostname"]] = a
        if a.get("host_id"):
            ag_by_id[a["host_id"]] = a
    post_by_id = {p.get("id"): p for p in posture or []}

    out = []
    for h in hosts or []:
        hh = by_id.get(h.get("id")) or {}
        ag = ag_by_name.get(h.get("label")) or ag_by_id.get(h.get("id")) or {}
        pr = post_by_id.get(h.get("id")) or {}
        flags = pr.get("flags") or {}
        p_body = pr.get("posture") or {}
        ip = ag.get("ip") or extract_ip(h.get("address"))
        gateway = ((p_body.get("net") or {}).get("gateway")) or None
        boot = (p_body.get("os") or {}).get("boot_epoch")
        env = h.get("environment") or "Unassigned"
        is_supp = _supp_finder(supps, h.get("label"), env, boot, now)

        verdict = health_verdict(hh, is_supp)
        disk = hh.get("disk")
        try:
            disk_crit = float(disk) >= 90
        except (TypeError, ValueError):
            disk_crit = False
        has_crit = (disk_crit and not is_supp("disk_critical")) or any(
            flags.get(k) is True and not is_supp(k) for k in CRIT_FLAGS
        )

        # Hypervisor role: the heartbeat reading first, the host record second —
        # the posture sweep covers hosts whose agent predates hypervisor reporting.
        hyp = hh.get("hyp") or h.get("hypervisor")
        vms = hh.get("vms") if hh.get("vms") is not None else h.get("vms")
        vm_names = hh.get("vm_names") or h.get("vm_names") or []

        out.append({
            "id": h.get("id"),
            "label": h.get("label") or "",
            "env": env,
            "kind": h.get("type_text") or "",
            "address": h.get("address") or "",
            # The reliable is_controller flag is computed on the AGENT record; the
            # merged-host source may not carry it, which would draw the controller's
            # own host as an ordinary node (a duplicate "controller"). Trust either.
            "isController": bool(ag.get("is_controller") or h.get("is_controller")),
            "online": hh.get("online") if hh else h.get("online"),
            "verdict": verdict,
            "disk": disk,
            "mem": hh.get("mem"),
            "agentVersion": ag.get("agent_version"),
            "ip": ip,
            "gateway": gateway,
            "subnet": subnet_of(ip),
            "revoked": bool(ag.get("revoked")),
            "quarantined": bool(ag.get("integrity_quarantined")),
            "hasCrit": bool(has_crit),
            "hypervisor": hyp,
            "vms": vms,
            "vmNames": list(vm_names),
            "hypBadge": hypervisor_badge(hyp, vms),
        })
    return out


def parents(nodes) -> dict:
    """VM label -> hypervisor label, computed GLOBALLY across every environment
    and network group: a host whose label appears in some hypervisor's reported
    guest names is a guest of that hypervisor, whatever either one is tagged. That
    is what lets a VM hang off the machine it actually runs on even when the two
    sit in different groups."""
    by_label = {n["label"]: n for n in nodes}
    out = {}
    for h in nodes:
        if h.get("hypervisor"):
            for nm in h.get("vmNames") or []:
                if nm != h["label"] and nm in by_label:
                    out[nm] = h["label"]
    return out


def counts(nodes) -> dict:
    on = off = crit = 0
    for n in nodes:
        if n.get("isController"):
            continue
        if n.get("online") is False:
            off += 1
        elif n.get("online"):
            on += 1
        if n.get("hasCrit") and n.get("online") is not False:
            crit += 1
    return {"online": on, "offline": off, "critical": crit}
