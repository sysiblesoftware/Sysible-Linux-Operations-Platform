"""Tests for the fleet topology model — the correlation the map is built on.

The Controller exposes hosts, health, agents, suppressions and posture; none of
them is a topology. These pin the rules that turn them into one, including the
ones that are easy to get subtly wrong and hard to notice on screen:

  * a node's colour must honour SUPPRESSIONS, or the map re-flags findings the
    dashboard has already been told to silence and the two disagree;
  * a hypervisor's guests nest under it ACROSS environments, or a VM shows up as
    a peer of the machine it runs on;
  * a missing/slow upstream degrades the map instead of blanking it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import fleet  # noqa: E402

NOW = 1_700_000_000.0


def H(hid, label, env="Prod", **kw):
    return dict(id=hid, label=label, environment=env, type_text="Agent", **kw)


# ---- health verdict --------------------------------------------------------
def test_verdict_grades_the_usual_signals():
    no_supp = lambda k: False                                    # noqa: E731
    assert fleet.health_verdict({"online": False}, no_supp) == "OFFLINE"
    assert fleet.health_verdict({"online": True, "disk": 40}, no_supp) == "OK"
    assert fleet.health_verdict({"online": True, "disk": 84}, no_supp) == "WARNING"
    assert fleet.health_verdict({"online": True, "disk": 93}, no_supp) == "CRITICAL"
    assert fleet.health_verdict({"online": True, "failed": 4}, no_supp) == "CRITICAL"
    assert fleet.health_verdict({"online": True, "failed": 1}, no_supp) == "WARNING"
    # Never probed and no verdict → genuinely unknown, not a green "OK".
    assert fleet.health_verdict({}, no_supp) is None


def test_a_suppressed_finding_stops_colouring_the_node():
    # disk 93 alone would be CRITICAL; with disk_critical suppressed the host has
    # NO active finding left, so it reads SUPPRESSED rather than critical red.
    hh = {"online": True, "disk": 93}
    assert fleet.health_verdict(hh, lambda k: k == "disk_critical") == "SUPPRESSED"
    # …but an unsuppressed second signal still grades it.
    hh2 = {"online": True, "disk": 93, "mem": 95}
    assert fleet.health_verdict(hh2, lambda k: k == "disk_critical") == "CRITICAL"


# ---- suppression matching --------------------------------------------------
def test_supp_scope_and_expiry():
    s = {"key": "eol_os", "scope": "host", "target": "web-1", "type": "acknowledged"}
    assert fleet.supp_active(s, "web-1", "Prod", "eol_os", None, NOW)
    assert not fleet.supp_active(s, "web-2", "Prod", "eol_os", None, NOW)
    assert not fleet.supp_active(s, "web-1", "Prod", "firewall_disabled", None, NOW)

    env = {"key": "eol_os", "scope": "env", "target": "Prod", "type": "acknowledged"}
    assert fleet.supp_active(env, "anything", "Prod", "eol_os", None, NOW)
    assert not fleet.supp_active(env, "anything", "Dev", "eol_os", None, NOW)

    snooze = {"key": "eol_os", "scope": "host", "target": "web-1",
              "type": "snooze", "until": NOW + 60}
    assert fleet.supp_active(snooze, "web-1", "Prod", "eol_os", None, NOW)
    assert not fleet.supp_active(snooze, "web-1", "Prod", "eol_os", None, NOW + 120)


def test_reboot_suppression_clears_once_the_host_reboots():
    s = {"key": "failed_units", "scope": "host", "target": "web-1",
         "type": "reboot", "boot_epoch": 1000}
    assert fleet.supp_active(s, "web-1", "Prod", "failed_units", 1000, NOW)   # same boot
    assert not fleet.supp_active(s, "web-1", "Prod", "failed_units", 2000, NOW)  # rebooted
    # Boot time unknown (posture hasn't loaded) → stay suppressed rather than
    # flashing a finding the operator has already dealt with.
    assert fleet.supp_active(s, "web-1", "Prod", "failed_units", None, NOW)


# ---- the merge -------------------------------------------------------------
def test_build_merges_every_source():
    nodes = fleet.build(
        [H("h1", "web-1", address="10.0.0.5")],
        [{"id": "h1", "online": True, "verdict": "OK", "disk": 30, "mem": 40}],
        [{"host_id": "h1", "hostname": "web-1", "ip": "10.0.0.5", "agent_version": "2.0"}],
        [],
        [{"id": "h1", "flags": {}, "posture": {"net": {"gateway": "10.0.0.1"}}}],
    )
    n = nodes[0]
    assert n["ip"] == "10.0.0.5" and n["subnet"] == "10.0.0.0/24"
    assert n["gateway"] == "10.0.0.1" and n["agentVersion"] == "2.0"
    assert n["verdict"] == "OK" and n["hasCrit"] is False


def test_a_critical_posture_flag_rings_the_node_unless_suppressed():
    hosts = [H("h1", "web-1")]
    posture = [{"id": "h1", "flags": {"ssh_root_login": True}, "posture": {}}]
    health = [{"id": "h1", "online": True, "verdict": "OK", "disk": 10}]
    assert fleet.build(hosts, health, [], [], posture)[0]["hasCrit"] is True
    supp = [{"key": "ssh_root_login", "scope": "host", "target": "web-1",
             "type": "acknowledged"}]
    assert fleet.build(hosts, health, [], supp, posture)[0]["hasCrit"] is False


def test_controller_is_recognised_from_either_source():
    # The merged-host record may not carry is_controller; the AGENT record does.
    nodes = fleet.build([H("h1", "ctl")], [], [{"host_id": "h1", "hostname": "ctl",
                                                "is_controller": True}], [], [])
    assert nodes[0]["isController"] is True


def test_guests_nest_under_their_hypervisor_across_environments():
    # The hypervisor is tagged "Labs" and its guests "Dev" — they must still hang
    # off the machine they actually run on, not float in a separate Dev cluster.
    hosts = [H("h1", "kvm-1", env="Labs"), H("h2", "vm-a", env="Dev"), H("h3", "vm-b", env="Dev")]
    health = [{"id": "h1", "online": True, "verdict": "OK",
               "hyp": "kvm", "vms": 2, "vm_names": ["vm-a", "vm-b"]}]
    nodes = fleet.build(hosts, health, [], [], [])
    assert fleet.parents(nodes) == {"vm-a": "kvm-1", "vm-b": "kvm-1"}
    assert nodes[0]["hypBadge"] == "KVM hypervisor · 2 VMs"


def test_a_hypervisor_does_not_adopt_a_guest_that_is_not_enrolled():
    hosts = [H("h1", "kvm-1")]
    health = [{"id": "h1", "online": True, "verdict": "OK", "hyp": "kvm",
               "vms": 1, "vm_names": ["ghost-vm"]}]
    assert fleet.parents(fleet.build(hosts, health, [], [], [])) == {}


def test_counts_exclude_the_controller_and_offline_criticals():
    hosts = [H("h0", "ctl"), H("h1", "a"), H("h2", "b"), H("h3", "c")]
    health = [
        {"id": "h0", "online": True, "verdict": "OK"},
        {"id": "h1", "online": True, "verdict": "CRITICAL", "disk": 95},
        {"id": "h2", "online": False},
        {"id": "h3", "online": True, "verdict": "OK", "disk": 10},
    ]
    agents = [{"host_id": "h0", "hostname": "ctl", "is_controller": True}]
    c = fleet.counts(fleet.build(hosts, health, agents, [], []))
    assert c == {"online": 2, "offline": 1, "critical": 1}


def test_missing_upstreams_degrade_rather_than_blank_the_map():
    # Only the host inventory answered: the map still has every node and its
    # shape, just without health colour or posture rings.
    nodes = fleet.build([H("h1", "web-1"), H("h2", "web-2")], [], [], [], [])
    assert [n["label"] for n in nodes] == ["web-1", "web-2"]
    assert all(n["verdict"] is None and n["hasCrit"] is False for n in nodes)
    assert fleet.status_of(nodes[0]) == "UNKNOWN"


def test_worst_picks_the_most_severe_status_in_a_cluster():
    assert fleet.worst([{"online": True, "verdict": "OK"},
                        {"online": True, "verdict": "WARNING"}]) == "WARNING"
    assert fleet.worst([{"online": True, "verdict": "WARNING"},
                        {"online": False}]) == "OFFLINE"
    assert fleet.worst([{"online": True, "verdict": "CRITICAL"},
                        {"online": False}]) == "CRITICAL"
    # Nothing to grade is UNKNOWN, not a reassuring green — matching the
    # Controller view (a group only exists because it has hosts, so this is
    # the degenerate case, and it must not read as "all clear").
    assert fleet.worst([]) == "UNKNOWN"


def test_ip_and_subnet_extraction():
    assert fleet.extract_ip("root@10.2.3.4:22") == "10.2.3.4"
    assert fleet.extract_ip("no address here") is None
    assert fleet.subnet_of("10.2.3.4") == "10.2.3.0/24"
    assert fleet.subnet_of(None) is None
