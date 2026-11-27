"""Any output SLEP puts on screen has to be copyable.

Asked for directly: "And I should be able to copy logs from ANYWHERE in the
platforms. Especially in SLEP." Only one surface had a Copy button — the hypervisor
key command — and everything else expected the operator to drag-select. That does
not work on a pane that is still streaming: the run log auto-scrolls, so the
selection is lost every time a line lands, and the part already scrolled past is
not selectable at all.

One CopyButton in ui.jsx, on every output surface. It takes `text` as a value or a
function, so a long streaming log is only turned into a string when the button is
pressed rather than on every render.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "webgui" / "frontend" / "src"
VIEWS = SRC / "views"
UI = (SRC / "ui.jsx").read_text(encoding="utf-8")

# Anything that renders captured output a person might want to keep.
PANE = re.compile(r'<pre\b|className="job-log"|className="log |className="log run-scroll"')


def test_there_is_one_copy_control_not_a_copy_per_view():
    assert "export function CopyButton" in UI
    for f in VIEWS.glob("*.jsx"):
        txt = f.read_text(encoding="utf-8")
        # The IDE's editor Cut/Copy is the Monaco clipboard acting on the editor's
        # SELECTION, not an output pane. Those lines carry selectionText(ed); judge
        # by the line the call is on, not by what happens to precede it.
        others = [ln for ln in txt.splitlines()
                  if "navigator.clipboard.writeText" in ln and "selectionText(" not in ln]
        assert not others, (
            "%s writes to the clipboard directly instead of using CopyButton — that is "
            "how one pane ends up with feedback and another silently doing nothing: %s"
            % (f.name, others))


def test_every_view_with_an_output_pane_has_a_copy_button():
    missing = []
    for f in sorted(VIEWS.glob("*.jsx")):
        txt = f.read_text(encoding="utf-8")
        if not PANE.search(txt):
            continue
        if "CopyButton" not in txt:
            missing.append(f.name)
    assert not missing, "output panes with no way to copy them: %s" % missing


def _runs_log_pane():
    """Just the Log pane's JSX. Runs.jsx has a second Copy in the Visualize pane, and
    a file-level assertion was satisfied by that one while this pane had none."""
    txt = (VIEWS / "Runs.jsx").read_text(encoding="utf-8")
    i = txt.index('<div className="pane-title">Log</div>')
    return txt[max(0, i - 400):i + 900]


def test_the_main_run_log_itself_has_a_copy():
    pane = _runs_log_pane()
    assert "CopyButton" in pane, "the Log pane has no Copy button"
    assert 'label="Copy log"' in pane


def test_the_main_run_logs_copy_is_lazy():
    """A streaming log re-renders constantly; joining it on every render is waste."""
    pane = _runs_log_pane()
    assert re.search(r"<CopyButton\s+text=\{\(\) =>", pane), (
        "the Log pane's copy text is materialised on every render, not on the click")


@pytest.mark.parametrize("view,needle", [
    ("Runs.jsx", 'label="Copy all stages"'),          # a whole pipeline at once
    ("RunViz.jsx", "CopyButton"),                     # the per-stage excerpt
    ("Inventories.jsx", "copyRow"),                   # test result + both progress panes
    ("Ide.jsx", 'label="Copy output"'),               # the IDE's command output
    ("Infrastructure.jsx", "cloud-init log"),         # per-host cloud-init
    ("Infrastructure.jsx", 'label="Copy result"'),    # hypervisor provisioning steps
])
def test_each_known_pane_is_covered(view, needle):
    assert needle in (VIEWS / view).read_text(encoding="utf-8"), (
        "%s lost its copy control (%s)" % (view, needle))


def test_the_shared_job_log_actually_renders_its_copy_row():
    """It was defined and never rendered, so all three panes using JobLog shipped
    with no button at all while the file still mentioned CopyButton."""
    txt = (VIEWS / "Inventories.jsx").read_text(encoding="utf-8")
    assert "const copyRow = (" in txt
    assert "{copyRow}" in txt, (
        "JobLog builds a copy row and never puts it in the output — the three panes "
        "that use it have no Copy button")


def test_the_control_supports_a_lazy_text():
    assert "typeof text === 'function'" in UI


def test_the_clipboard_can_fail_and_the_button_says_so():
    """navigator.clipboard needs a secure context. A button that silently does
    nothing on a plain-http deployment is worse than no button."""
    assert "execCommand('copy')" in UI, "no fallback when navigator.clipboard is unavailable"
    assert "Blocked" in UI, "a failed copy is not reported to the operator"
    assert "setTimeout" in UI, "the confirmation never reverts"


# ------------------------------------------------- actually clicked, in a browser

BROWSER_JS = Path(__file__).parent / "fixtures" / "copybutton_browser.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.skipif(not BROWSER_JS.exists(), reason="browser fixture not present")
def test_the_button_really_puts_the_text_on_the_clipboard(tmp_path):
    """Rendering the real component, clicking it, and reading the clipboard back —
    a Copy button that renders but does not copy is the whole failure mode here."""
    p = subprocess.run(["node", str(BROWSER_JS), str(SRC / "ui.jsx"), str(tmp_path)],
                       capture_output=True, text=True, timeout=300)
    if p.returncode == 2:
        pytest.skip("browser deps unavailable: %s" % (p.stderr or "")[-200:])
    assert p.returncode == 0, "clipboard behaviour failed:\n%s\n%s" % (p.stdout, p.stderr[-500:])
