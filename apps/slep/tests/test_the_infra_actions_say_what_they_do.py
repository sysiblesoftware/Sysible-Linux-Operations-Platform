"""The infrastructure action bar has to read as what it does.

Reported: "Oh i guess there is an edit button for build infra but it's not very
intuitive. I didn't know what it did." The capability was there all along -- a bare
"Edit" sitting between Destroy and Enroll just reads as "rename this row". It opens
the Build-infrastructure wizard on the project's VM spec.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
INFRA = (HERE / "webgui" / "frontend" / "src" / "views" / "InfraActions.jsx").read_text(encoding="utf-8")


def _actions():
    """[(label, title), ...] in the order they appear on the bar."""
    block = INFRA[INFRA.index("  return ["):INFRA.index("].map(([label, fn, cls, title, danger])")]
    return re.findall(r"\['([^']+)',[^,]+,\s*'[^']*',\s*'((?:[^'\\]|\\.)*)'", block)


def test_the_edit_button_names_what_it_edits():
    labels = [a[0] for a in _actions()]
    assert "Edit" not in labels, (
        "the bar still has a bare 'Edit', which reads as renaming the row rather "
        "than changing the VM spec")
    assert any("Edit" in l and l != "Edit" for l in labels), "nothing opens the spec editor"


def test_defining_the_vms_comes_before_acting_on_them():
    labels = [a[0] for a in _actions()]
    edit = next(i for i, l in enumerate(labels) if l.startswith("Edit"))
    for verb in ("Plan", "Apply", "Destroy"):
        assert edit < labels.index(verb), (
            "the spec editor sits inside the group of verbs that act on the spec "
            "(%r) -- it belongs in front of them" % (labels,))


def test_the_edit_tooltip_warns_before_the_destructive_part():
    """Regenerating rewrites the .tf under VMs that already exist. The wizard says
    so on screen; the button must not invite the click without it."""
    tip = dict(_actions())[next(l for l in dict(_actions()) if l.startswith("Edit"))]
    assert "Destroy the VMs first" in tip, "the Edit tooltip no longer warns about applied VMs"
    for word in ("counts", "images"):
        assert word in tip, "the Edit tooltip does not say what it changes"


def test_the_bar_order_can_change_without_breaking_enroll():
    """enrollPick reaches for buildActions(...)[0].enroll, which only survives a
    reorder because .map attaches `enroll` to EVERY entry. Reordering the bar is a
    normal thing to do, so pin the thing that makes it safe."""
    assert "danger, enroll }))" in INFRA, (
        "entries no longer all carry `enroll`, so buildActions(...)[0].enroll "
        "depends on which action happens to be first")
    assert "[0].enroll" in INFRA
