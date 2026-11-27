"""A hypervisor with no hardware virtualisation must refuse the build, not crawl.

A host without KVM accepts every libvirt call perfectly happily — it just runs each
VM under TCG software emulation, roughly an order of magnitude slower. The
connection test passed (connection ✓, network ✓, pool ✓), the apply succeeded, and
what came out the other end was a set of machines nobody could use, with nothing in
the run saying why.

Decided: refuse. No TCG fallback. And say it in the connection test too, when it is
still cheap to fix, rather than letting the first apply be where it is discovered.

The question asked is `virsh capabilities`, not /proc/cpuinfo: a host with the kvm
modules loaded but no CPU support still offers qemu only, and so does one whose
/dev/kvm the connecting account cannot open. That is the state a VM really runs in.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
RUNNER = HERE / "backend" / "runners" / "terraform_runner.py"

KVM_CAPS = """<capabilities><guest><os_type>hvm</os_type>
  <arch name='x86_64'><domain type='qemu'/><domain type='kvm'/></arch></guest></capabilities>"""
TCG_CAPS = """<capabilities><guest><os_type>hvm</os_type>
  <arch name='x86_64'><domain type='qemu'/></arch></guest></capabilities>"""


@pytest.fixture
def fake_virsh(tmp_path, monkeypatch):
    """Put a stub `virsh` on PATH. Its output, and its exit code, are what the probe
    has to read — so the stub is the hypervisor for the purposes of this test."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "virsh"

    def install(body, rc=0):
        script.write_text("#!/bin/sh\ncat <<'XML'\n%s\nXML\nexit %d\n" % (body, rc))
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    return install


def _probe():
    from backend.runners.terraform_runner import libvirt_acceleration
    return libvirt_acceleration


def test_a_host_with_kvm_is_accepted(fake_virsh):
    fake_virsh(KVM_CAPS)
    accel, why = _probe()("qemu+ssh://u@h/system")
    assert accel == "kvm", why


def test_a_host_with_only_software_emulation_is_identified(fake_virsh):
    fake_virsh(TCG_CAPS)
    accel, why = _probe()("qemu+ssh://u@h/system")
    assert accel == "tcg", why
    assert "software emulation" in why


def test_a_probe_that_fails_is_unknown_not_bad(fake_virsh):
    """Not knowing is not the same as knowing it is missing. A broken probe must
    never refuse somebody's build."""
    fake_virsh("error: failed to connect", rc=1)
    accel, _ = _probe()("qemu+ssh://u@h/system")
    assert accel == "unknown"


def test_no_virsh_is_unknown_not_bad(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))      # nothing on PATH at all
    accel, why = _probe()("qemu+ssh://u@h/system")
    assert accel == "unknown"
    assert "virsh" in why


def test_an_empty_uri_is_unknown():
    assert _probe()("")[0] == "unknown"


def test_both_quoting_styles_are_read(fake_virsh):
    """libvirt emits single quotes; do not be defeated by a host that emits double."""
    fake_virsh(KVM_CAPS.replace("'", '"'))
    assert _probe()("qemu:///system")[0] == "kvm"


# --------------------------------------------------- what the build path does

def _apply_preflight():
    """The KVM block inside launch()'s apply preflight."""
    src = RUNNER.read_text(encoding="utf-8")
    i = src.index('if "libvirt_domain" in tf_text:')
    return src[i:i + 2200]


def test_the_build_refuses_rather_than_falling_back():
    block = _apply_preflight()
    assert 'if _accel == "tcg":' in block
    assert 'db.set_run_status(run_id, "failed"' in block, "a TCG host does not fail the run"
    assert "return" in block
    assert "Nothing was created" in block


def test_the_refusal_is_before_anything_is_built():
    """Refusing after the base-image download has already run is not refusing."""
    src = RUNNER.read_text(encoding="utf-8")
    assert src.index('if _accel == "tcg":') < src.index('cmd = [tool, "apply"'), (
        "the KVM check happens after the apply command is built")


def test_the_refusal_says_how_to_fix_it():
    block = _apply_preflight()
    for hint in ("nested virtualisation", "VT-x/AMD-V", "kvm group", "virsh"):
        assert hint in block, "the refusal does not mention %r" % hint


def test_an_unknown_result_does_not_block_the_build():
    block = _apply_preflight()
    assert 'if _accel == "unknown":' in block
    tail = block[block.index('if _accel == "unknown":'):]
    assert "set_run_status" not in tail, "an unconfirmed probe stops the build"
    assert "Carrying on" in tail


def test_only_libvirt_builds_are_checked():
    """An AWS or DigitalOcean project has no hypervisor to ask."""
    assert 'if "libvirt_domain" in tf_text:' in RUNNER.read_text(encoding="utf-8")


# ------------------------------------------- and the connection test says so

def _connection_test_body():
    """The whole function, bounded by the next route — not a fixed byte window,
    which silently cut the function short and failed on its own arithmetic."""
    app = (HERE / "backend" / "app.py").read_text(encoding="utf-8")
    i = app.index("def infra_test_hypervisor")
    return app[i:app.index("@app.post", i)]


def test_the_connection_test_reports_it_before_a_build():
    body = _connection_test_body()
    assert "libvirt_acceleration" in body, (
        "the connection test can pass on a host that will only ever emulate")
    assert "Hardware virtualisation" in body
    assert re.search(r'accel == "tcg"[\s\S]{0,400}ok = False', body), (
        "a host with no KVM still passes the connection test")


def test_the_two_places_ask_the_same_question():
    """One probe, not a copy in the runner and another in the API."""
    app = (HERE / "backend" / "app.py").read_text(encoding="utf-8")
    assert "from .runners.terraform_runner import libvirt_acceleration" in app
    assert RUNNER.read_text(encoding="utf-8").count("def libvirt_acceleration") == 1
    # The API must not shell out to virsh itself for this — that is how the two
    # answers drift apart.
    after = _connection_test_body().split("Hardware virtualisation")[0]
    assert "capabilities" not in after.split("libvirt_acceleration")[-1], (
        "the connection test is probing capabilities on its own as well")
