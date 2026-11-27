"""A pipeline you built once should be there the next time you open the builder.

The pipeline editor (PipelineModal) is reached from two places. The Pipelines page
passed it an onSaved callback and got the whole experience: a name box, a Save
button, and saved sequences to edit. The IDE's "Pipeline" button -- which is where
an operator actually assembles a build -> configure sequence while looking at the
project -- passed no onSaved, and both controls were written as {onSaved && (...)}.
So from the IDE there was no way to keep a sequence and no way to load one. Every
run meant retyping it: "I shouldn't have to build this pipeline every time."

Worse, reuse lost data. A saved step comes back from the API as
{extra_vars: {...}, start_at_task: "..."} but the editor's own shape is
{vars: "KEY=value\n...", startAt: "..."}. The editor did initialSteps.map(blank),
which spreads the API shape straight over its defaults -- so the vars box opened
EMPTY, and pressing Update wrote extra_vars:{} back over the saved pipeline.
Reuse that silently drops your variables is worse than no reuse at all.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
VIEWS = HERE / "webgui" / "frontend" / "src" / "views"
IDE = (VIEWS / "Ide.jsx").read_text(encoding="utf-8")


def _modal_body() -> str:
    """Just the PipelineModal function, so a match in a sibling modal cannot pass."""
    start = IDE.index("export function PipelineModal(")
    nxt = IDE.find("\nfunction ", start)
    nxt2 = IDE.find("\nexport function ", start + 1)
    end = min(x for x in (nxt, nxt2, len(IDE)) if x > start)
    body = IDE[start:end]
    assert len(body) > 2000, "PipelineModal body not isolated (%d bytes)" % len(body)
    return body


# --------------------------------------------------------------- saving, anywhere

def test_the_save_button_is_not_gated_on_a_callback():
    """The Save/Update button must not be hidden when the caller passes no onSaved --
    that gate is exactly what made the IDE's pipeline builder unsaveable."""
    body = _modal_body()
    # Find the save button's own JSX and confirm no `onSaved &&` guards it.
    i = body.index("💾")
    opens = body.rindex("<button", 0, i)          # index into body, not into a slice
    guard_window = body[max(0, opens - 200):opens]
    assert "onSaved &&" not in guard_window, (
        "the Save button is still gated on onSaved -- the IDE cannot save a pipeline:\n"
        + guard_window)


def test_the_name_box_is_not_gated_on_a_callback():
    body = _modal_body()
    i = body.index("Save this sequence as")
    field = body.rindex("<Field", 0, i)
    guard_window = body[max(0, field - 200):field]
    assert "onSaved &&" not in guard_window, (
        "the pipeline name box is still gated on onSaved -- nothing can be named, so "
        "nothing can be saved, from the IDE")


def test_a_caller_without_onsaved_still_gets_told_it_saved():
    """Closing the dialog silently would look like the save did nothing."""
    body = _modal_body()
    assert "setSavedNote(`Saved as" in body, (
        "the save handler sets no confirmation note -- saving from the IDE just closes "
        "the dialog, which looks like nothing happened")
    assert re.search(r"if\s*\(\s*onSaved\s*\)", body), (
        "onSaved is called unconditionally -- it is optional now, so this throws from the IDE")


def test_the_ide_opens_a_builder_that_can_save():
    """The IDE call site must reach the saving editor, not a crippled copy."""
    i = IDE.index("pipeOpen && <PipelineModal")
    call = IDE[i:IDE.index("/>", i) + 2]
    assert "project={project}" in call, call


# ------------------------------------------------------- loading a saved sequence

def test_the_builder_offers_the_project_s_saved_pipelines():
    body = _modal_body()
    assert "Start from a saved pipeline" in body, (
        "the builder does not offer saved pipelines, so one must be rebuilt by hand")
    assert "api('pipelines')" in body, "nothing fetches the saved pipelines"
    assert re.search(r"filter\(\(pl\) => pl\.project_id === project\.id\)", body), (
        "the picker is not filtered to this project -- it would list other projects' pipelines")


def test_loading_a_saved_pipeline_does_not_renudge_its_first_step():
    """A saved sequence is its author's. The Terraform-first nudge must not rewrite it."""
    body = _modal_body()
    load = body[body.index("const loadSaved = ("):]
    load = load[:load.index("\n  const ", 10)] if "\n  const " in load[10:] else load[:1200]
    assert "freshPipeline.current = false" in load, (
        "loadSaved does not clear freshPipeline -- the Terraform nudge can overwrite a "
        "loaded sequence's first step")


def test_the_picker_shows_which_saved_pipeline_is_loaded():
    body = _modal_body()
    assert re.search(r"value=\{editId \? String\(editId\) : ''\}", body), (
        "the picker does not reflect the loaded pipeline")


def test_update_overwrites_the_pipeline_that_was_loaded():
    """Not the saveId prop -- a pipeline loaded from the picker must be the one updated."""
    body = _modal_body()
    assert re.search(r"editId\s*\n?\s*\?\s*await api\(`pipelines/\$\{editId\}`, \{ method: 'PUT'", body), (
        "Update does not PUT to the loaded pipeline's id")
    assert "`pipelines/${saveId}`" not in body, (
        "still PUTs to the saveId prop, so a pipeline loaded from the picker cannot be updated")


# ------------------------------------------------- reuse must not lose your data

def test_a_saved_step_is_normalised_not_spread():
    body = _modal_body()
    assert "const fromSaved = (s) =>" in body, "no normaliser for a saved step"
    assert re.search(r"initialSteps\.map\(fromSaved\)", body), (
        "initialSteps still goes through blank(), which drops extra_vars and start_at_task")
    assert not re.search(r"initialSteps\.map\(\(s\) => blank\(s\)\)", body)


def test_extra_vars_come_back_into_the_vars_box():
    body = _modal_body()
    fs = body[body.index("const fromSaved = (s) =>"):]
    fs = fs[:fs.index("\n  const ", 10)]
    assert "extra_vars" in fs, "fromSaved ignores extra_vars -- every per-step variable is lost on reuse"
    assert re.search(r"\$\{k\}=\$\{ev\[k\]\}", fs), (
        "extra_vars is not rendered back as KEY=value lines")
    assert "join('\\n')" in fs, "the KEY=value lines are not newline-joined"


def test_start_at_task_comes_back_too():
    body = _modal_body()
    fs = body[body.index("const fromSaved = (s) =>"):]
    fs = fs[:fs.index("\n  const ", 10)]
    assert "start_at_task" in fs, "--start-at-task is lost when a saved pipeline is reopened"


def test_a_saved_secret_is_not_pretended_to_be_there():
    """The server strips become_password on save; the box must come back empty, not undefined."""
    body = _modal_body()
    fs = body[body.index("const fromSaved = (s) =>"):]
    fs = fs[:fs.index("\n  const ", 10)]
    assert re.search(r"becomePw: o\.becomePw \|\| ''", fs)


def test_the_selects_get_string_ids():
    """Numeric ids from the API bound to <select> values that are strings."""
    body = _modal_body()
    fs = body[body.index("const fromSaved = (s) =>"):]
    fs = fs[:fs.index("\n  const ", 10)]
    for key in ("inventory_id", "credential_id", "controller_id"):
        assert re.search(r"%s: id\(o\.%s\)" % (key, key), fs), "%s is not stringified" % key


# ------------------------------------- no third call site that cannot save again

def test_there_is_no_unreachable_pipeline_dialog():
    """InfraActions destructured setPipe but never called it, so its PipelineModal was
    dead -- and dead, it was a no-onSaved call site waiting to be revived."""
    infra = (VIEWS / "InfraActions.jsx").read_text(encoding="utf-8")
    assert "setPipe" not in infra, "the dead pipeline modal is back in InfraActions.jsx"
    assert "PipelineModal" not in infra


def test_every_live_call_site_is_accounted_for():
    """If a new call site appears, it must be checked for the save gate on purpose."""
    sites = []
    for f in sorted(VIEWS.glob("*.jsx")):
        txt = f.read_text(encoding="utf-8")
        for m in re.finditer(r"<PipelineModal\b", txt):
            sites.append(f.name)
    assert sorted(sites) == ["Ide.jsx", "Pipelines.jsx"], (
        "unexpected PipelineModal call sites %r -- check each can save and load" % (sites,))
