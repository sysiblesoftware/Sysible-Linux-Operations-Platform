"""Flashback and Baseline each get their own agent channel, and the Controller
is told how to reach both.

Reported, on the Baseline console: "Desired-state configuration is not
configured on this controller (SYSIBLE_BASELINE_URL and
SYSIBLE_BASELINE_AGENT_TOKEN unset). Run the SLOP installer, or set them in the
controller's .env." The installer had been run.

Three rebrand casualties, all in install.sh, all in one direction — Flashback's
block was renamed to Baseline's variable names instead of being left alone:

  * Flashback's token was persisted as SYSIBLE_BASELINE_AGENT_TOKEN, so
    SYSIBLE_FLASHBACK_AGENT_TOKEN — the key docker-compose.yml passes to the
    FLASHBACK container — was never written. The container came up with an
    empty token, and agent auth fails closed, so config backup was refused
    fleet-wide.
  * Baseline's block then read SYSIBLE_BASELINE_AGENT_TOKEN, found what
    Flashback had just written there, and used it. Both services shared one
    token — the precise thing the comment above that block says must never
    happen, because "a compromise of either is a compromise of both".
  * The Controller was handed FB_URL and FB_TOKEN under BASELINE's names. Its
    desired-state client was therefore pointed at Flashback's port (8770), and
    its Flashback client — whose own names were never written — fell back to
    those same Baseline names and arrived at the same place. One service
    addressed twice, the other not at all.

These tests run the real shell, not a transcription of it.

Run: pytest tests/test_the_installer_wires_both_agent_channels.py
"""
import os
import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"


def _block(start_marker, end_marker):
    """The part of install.sh that decides the two tokens and urls.

    Sourcing the whole script would install SLOP. This takes the section under
    test and runs it for real against a temporary .env.
    """
    text = INSTALL.read_text(encoding="utf-8")
    i = text.index(start_marker)
    j = text.index(end_marker, i)
    return text[i:j]


HARNESS = r'''
set -eu
ENV_FILE="$TMP/.env"
say(){ :; }
die(){ echo "DIE: $*" >&2; exit 1; }
# A host whose docker bridge is NOT the default. The hard-coded 172.17.0.1
# fallback is only right when it is, which is the whole reason the installer
# asks docker rather than assuming.
_bridge_gateway(){ echo "${FAKE_BRIDGE:-10.88.0.1}"; }
'''


def _run(tmp, env_before="", exported=None):
    """Execute the token/url section with a given starting .env."""
    tmp = pathlib.Path(tmp)
    (tmp / ".env").write_text(env_before, encoding="utf-8")
    helpers = _block("_upsert_kv() {", "# ---- rename pre-rebrand keys")
    # Through the BIND block too: it is part of the same decision, and slicing
    # it off meant the bind assertions ran against a script that never set it.
    fb = _block("_fb_token_from_env() {", "# ---- Baseline controller token")
    bl = _block("_bl_token_from_env() {", "# Where THIS checkout lives.")
    # The slice now carries install.sh's own _bridge_gateway, which shells out
    # to docker and finds nothing here — so it would shadow the stub and every
    # bind assertion would measure the 172.17.0.1 fallback instead.
    import re as _re
    fb = _re.sub(r"_bridge_gateway\(\) \{.*?\n\}\n", "", fb, flags=_re.S)
    bl = _re.sub(r"_bridge_gateway\(\) \{.*?\n\}\n", "", bl, flags=_re.S)
    script = (HARNESS + helpers + "\n" + fb + "\n" + bl + "\n"
              + 'echo "FB_TOKEN=$FB_TOKEN"\necho "BL_TOKEN=$BL_TOKEN"\n'
              + 'echo "FB_URL=$FB_URL"\necho "BL_URL=$BL_URL"\n'
              + 'echo "FB_BIND=$FB_BIND"\necho "BL_BIND=$BL_BIND"\n')
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TMP": str(tmp),
           "FAKE_BRIDGE": "10.88.0.1"}
    env.update(exported or {})
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    out = dict(re.findall(r"^(\w+)=(.*)$", r.stdout, re.M))
    written = dict(re.findall(r"^([A-Z_]+)=(.*)$",
                              (tmp / ".env").read_text(encoding="utf-8"), re.M))
    return out, written


# ---- a fresh install --------------------------------------------------------
def test_each_service_gets_its_own_token(tmp_path):
    got, env = _run(tmp_path)
    assert len(got["FB_TOKEN"]) == 64 and len(got["BL_TOKEN"]) == 64, got
    assert got["FB_TOKEN"] != got["BL_TOKEN"], (
        "Flashback and Baseline were issued the SAME agent token. They are "
        "different services with different powers — one reads a host's stored "
        "configuration, the other declares what the host must become.")


def test_each_token_is_written_under_the_name_compose_reads(tmp_path):
    """docker-compose.yml passes SYSIBLE_FLASHBACK_AGENT_TOKEN to the flashback
    container and SYSIBLE_BASELINE_AGENT_TOKEN to the baseline one. A token
    under the wrong name is an empty token at runtime, and agent auth fails
    closed."""
    got, env = _run(tmp_path)
    assert env.get("SYSIBLE_FLASHBACK_AGENT_TOKEN") == got["FB_TOKEN"], env
    assert env.get("SYSIBLE_BASELINE_AGENT_TOKEN") == got["BL_TOKEN"], env


def test_the_two_services_are_on_their_own_ports(tmp_path):
    got, _ = _run(tmp_path)
    assert got["FB_URL"].endswith(":8770"), got["FB_URL"]
    assert got["BL_URL"].endswith(":8771"), got["BL_URL"]
    assert got["FB_URL"] != got["BL_URL"]


# ---- upgrading an install that has the old, merged key ----------------------
def test_an_existing_flashback_token_is_kept(tmp_path):
    """The comment on that block is emphatic about why: the token is what every
    host's config backup authenticates with, so minting a fresh one on upgrade
    takes the whole fleet's backup offline while both halves look configured."""
    old = "f" * 64
    got, env = _run(tmp_path, env_before=f"SYSIBLE_FLASHBACK_AGENT_TOKEN={old}\n")
    assert got["FB_TOKEN"] == old, "an upgrade minted a new Flashback token"
    assert env["SYSIBLE_FLASHBACK_AGENT_TOKEN"] == old


def test_a_token_stranded_under_baselines_name_is_migrated_not_lost(tmp_path):
    """What an install from before this fix actually looks like: Flashback's
    token sitting under SYSIBLE_BASELINE_AGENT_TOKEN and nothing under its
    own."""
    old = "a" * 64
    got, env = _run(tmp_path, env_before=f"SYSIBLE_BASELINE_AGENT_TOKEN={old}\n")
    assert got["FB_TOKEN"] == old, "Flashback's existing token was discarded"
    assert env["SYSIBLE_FLASHBACK_AGENT_TOKEN"] == old, \
        "it was not moved to the name compose reads"
    assert got["BL_TOKEN"] != old, (
        "Baseline took over the token Flashback has been using — which both "
        "breaks Flashback and gives two services one credential")
    assert env["SYSIBLE_BASELINE_AGENT_TOKEN"] == got["BL_TOKEN"]


def test_a_correctly_configured_install_is_left_alone(tmp_path):
    fb, bl = "f" * 64, "b" * 64
    got, env = _run(tmp_path, env_before=(
        f"SYSIBLE_FLASHBACK_AGENT_TOKEN={fb}\nSYSIBLE_BASELINE_AGENT_TOKEN={bl}\n"))
    assert (got["FB_TOKEN"], got["BL_TOKEN"]) == (fb, bl), got


# ---- what the Controller is told -------------------------------------------
def test_the_controller_is_given_both_services_each_with_its_own(tmp_path):
    """Read from the source: this is the branch that writes the app's .env, and
    there is no way to reach it without installing."""
    text = INSTALL.read_text(encoding="utf-8")
    i = text.index('if [ "$p" = "controller" ]; then')
    # To the NEXT branch, not the next "fi": the controller branch contains
    # several, and slicing at the first one returned an empty block that made
    # every assertion below read None.
    block = text[i:text.index('if [ "$p" = "connect" ]', i)]
    pairs = dict(re.findall(r'_upsert_kv "\$_aenv" (\S+) "\$(\w+)"', block))
    assert pairs.get("SYSIBLE_BASELINE_URL") == "BL_URL", (
        f"the Controller's desired-state client is pointed at {pairs.get('SYSIBLE_BASELINE_URL')}"
        " — Baseline is the one on 8771")
    assert pairs.get("SYSIBLE_BASELINE_AGENT_TOKEN") == "BL_TOKEN", pairs
    assert pairs.get("SYSIBLE_FLASHBACK_URL") == "FB_URL", (
        "the Controller is never told where Flashback is, so its client falls "
        "back to the Baseline names and reaches the wrong service")
    assert pairs.get("SYSIBLE_FLASHBACK_AGENT_TOKEN") == "FB_TOKEN", pairs


def test_the_controller_reads_exactly_those_four_names():
    """The other end of the contract. If the Controller's clients are renamed,
    the installer writing the old names is silence, not an error."""
    ctl = REPO.parent / "Sysible-Controller"
    if not ctl.is_dir():
        pytest.skip("the Controller checkout is not alongside this one")
    bl = (ctl / "backend" / "baseline.py").read_text(encoding="utf-8")
    fb = (ctl / "backend" / "flashback.py").read_text(encoding="utf-8")
    assert 'SYSIBLE_BASELINE_{suffix}' in bl, \
        "the Controller's Baseline client no longer reads SYSIBLE_BASELINE_*"
    assert 'SYSIBLE_FLASHBACK_{suffix}' in fb, \
        "the Controller's Flashback client no longer reads SYSIBLE_FLASHBACK_*"


# ---- the address each container binds its agent port to --------------------
def test_each_service_binds_under_the_name_compose_reads(tmp_path):
    """docker-compose.yml passes SYSIBLE_FLASHBACK_AGENT_BIND to the flashback
    container and SYSIBLE_BASELINE_AGENT_BIND to the baseline one, each with a
    hard-coded 172.17.0.1 default. A bind address written under the wrong name
    means that default — correct only on a host whose docker bridge happens to
    be the usual one, and silently wrong everywhere else: the port binds to an
    address that is not there, and the Controller cannot reach the service."""
    got, env = _run(tmp_path)
    assert got["FB_BIND"] == "10.88.0.1", got
    assert got["BL_BIND"] == "10.88.0.1", got
    assert env.get("SYSIBLE_FLASHBACK_AGENT_BIND") == got["FB_BIND"], (
        "Flashback's bind address is not under the name compose reads, so that "
        "container falls back to the file's hard-coded 172.17.0.1")
    assert env.get("SYSIBLE_BASELINE_AGENT_BIND") == got["BL_BIND"], env


def test_an_operators_explicit_bind_is_respected(tmp_path):
    got, env = _run(tmp_path, env_before=(
        "SYSIBLE_FLASHBACK_AGENT_BIND=192.168.5.1\n"
        "SYSIBLE_BASELINE_AGENT_BIND=192.168.5.2\n"),
        exported={"SYSIBLE_FLASHBACK_AGENT_BIND": "192.168.5.1",
                  "SYSIBLE_BASELINE_AGENT_BIND": "192.168.5.2"})
    assert (got["FB_BIND"], got["BL_BIND"]) == ("192.168.5.1", "192.168.5.2"), got


# ---- the class, not just the instances -------------------------------------
def test_no_variable_is_written_under_the_other_products_name():
    """Three bugs in this file had one shape: Flashback's block reading and
    writing a SYSIBLE_BASELINE_* key, because the rebrand renamed the variables
    in a block that was Flashback's all along. Each left the key compose
    actually passes to the flashback container unwritten.

    So: inside the Flashback section, nothing may be persisted under a BASELINE
    name, and vice versa.
    """
    text = INSTALL.read_text(encoding="utf-8")
    fb = text[text.index("_fb_token_from_env() {"):
              text.index("# ---- Baseline controller token")]
    bl = text[text.index("_bl_token_from_env() {"):
              text.index("# Where THIS checkout lives.")]
    import re
    fb_writes = re.findall(r'_upsert(?:_env|_kv)[^\n]*?\b(SYSIBLE_[A-Z_]+)', fb)
    bl_writes = re.findall(r'_upsert(?:_env|_kv)[^\n]*?\b(SYSIBLE_[A-Z_]+)', bl)
    assert fb_writes, "the Flashback block persists nothing at all now"
    assert bl_writes, "the Baseline block persists nothing at all now"
    bad_fb = [v for v in fb_writes if "BASELINE" in v]
    bad_bl = [v for v in bl_writes if "FLASHBACK" in v]
    assert not bad_fb, f"the Flashback block writes Baseline's keys: {bad_fb}"
    assert not bad_bl, f"the Baseline block writes Flashback's keys: {bad_bl}"
