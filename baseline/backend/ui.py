"""Sysible Baseline — the server-rendered console (dependency-free).

Same house as every other SLOP surface: the CSS ground below is lifted verbatim
from Flashback's — the same variables, the same topographic body, the same
header — so the two sit in one family rather than looking like two products
that happen to be installed together. Baseline's own layout is built on top of
it, not beside it.

The console is a WORKING one, not a dashboard. Everything the product does is
reachable here: declare a state, edit it into a new version, assign it to hosts
or an environment, write the rules that fire afterwards, and preview or apply.
A console that can only display is a status page, and a configuration manager
whose only interface is an HTTP API is not finished.
"""

from __future__ import annotations

from html import escape

from . import build_info

_CSS = """
:root{--bg:#0d1117;--panel:#131923;--panel2:#1a212d;--line:#26303f;
--text:#e6edf5;--muted:#93a1b5;--faint:#6f7d92;--accent:#43a047;--accent2:#5580ee;
--ok:#4caf5a;--err:#e5534b;--amber:#e0a83b;--field:#0d1320;
--font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;--topo:url(/brand/topo-dark.svg);}

:root[data-theme="light"]{--bg:#eef1f6;--panel:#fff;--panel2:#f3f5f9;--line:#dbe1ea;
--text:#1b2431;--muted:#5b6675;--faint:#8794a4;--accent:#2f8a37;--accent2:#2f6fe0;
--field:#fff;--topo:url(/brand/topo-light.svg);}

*{box-sizing:border-box}

html,body{margin:0;height:100%}

/* A column, so the rail and the view take WHAT IS LEFT. It used to claim
   calc(100vh - 51px) — the header's height, written down — so anything else
   above it (the message bar, and now the apply panel) pushed the bottom of
   the page off the screen. */
body{background:var(--topo) center / cover no-repeat fixed,
radial-gradient(70% 55% at 15% -10%,rgba(67,160,71,.12),transparent 60%) fixed,
radial-gradient(70% 60% at 100% 110%,rgba(85,128,238,.09),transparent 55%) fixed,
var(--bg);color:var(--text);font-family:var(--font);font-size:14px;
display:flex;flex-direction:column;overflow:hidden}

a{color:var(--accent2);text-decoration:none}

.head{display:flex;align-items:center;gap:.6em;padding:.7em 1em;border-bottom:1px solid var(--line);
background:var(--panel);position:sticky;top:0;z-index:5}

.head .brand{font-size:16px}.head .brand b{color:var(--accent)}

.head .who{margin-left:auto;color:var(--muted);font-size:12.5px}

.head a.back{color:var(--muted);font-size:12.5px}

.msg{margin:.4em .6em;padding:.5em .7em;border-radius:8px;font-size:12.5px}

.msg.ok{background:rgba(76,175,90,.12);color:var(--ok)}

.msg.err{background:rgba(229,83,75,.12);color:var(--err)}

/* ---- Baseline's own layout, on the shared ground above ------------------ */
/* A LEFT RAIL AND ONE VIEW. This is the shape SLEP and the Sysible Controller
   both use — SLEP's App.jsx says so in a comment on its own rail: "Left-rail
   navigation — same shape as the Sysible Controller console". Baseline was the
   only console in the family not built this way.

   WHAT IT REPLACED, and why. Three resizable columns: browse, "what the
   selection is made of", detail. The middle one was the mistake. It listed the
   selected host's states and its recent runs — and the detail pane immediately
   to its right listed the same two things again, in more detail. It was not a
   drill-down either: picking something in it did not narrow the pane, it
   REPLACED the pane's subject. So one fact appeared twice, the detail had a
   third of the width it needed, and the bottom two thirds of the screen were
   empty. Reported as "this whole layout doesn't make much sense and isn't as
   intuitive as SLEP or the other apps", with a screenshot that showed all
   three problems at once.

   Five sections, one at a time, each the full width. The things stranded in
   the middle column — the event rules, the run history — are sections in their
   own right now, which is what they always were. */
.shell{display:flex;flex:1 1 auto;min-height:0}
.rail{width:216px;flex:none;background:var(--panel);border-right:1px solid var(--line);
display:flex;flex-direction:column;padding:.75em .5em .6em;gap:.1em;overflow:auto}
.rail-item{display:flex;align-items:center;gap:.6em;width:100%;text-align:left;
background:transparent;border:0;border-left:2px solid transparent;border-radius:0 8px 8px 0;
color:var(--muted);font-family:inherit;font-size:13px;padding:.5em .7em;cursor:pointer}
.rail-item:hover{background:var(--panel2);color:var(--text);border-color:transparent;
border-left-color:transparent}
.rail-item.on{background:var(--panel2);color:var(--text);font-weight:600;
border-left-color:var(--accent)}
/* The count carries the WORST thing in that section, not just how many there
   are: "16" beside Hosts and "16" in red when three of them are failing are
   different messages, and the second is the one worth walking past. */
.rail-item .cnt{margin-left:auto;font-size:11px;color:var(--faint);font-family:var(--mono)}
.rail-item .cnt.err{color:var(--err)}
.rail-item .cnt.amber{color:var(--amber)}
.rail-foot{margin-top:auto;padding-top:.7em;border-top:1px solid var(--line);
color:var(--faint);font-size:11.5px;padding-left:.7em}
.view{flex:1 1 auto;min-width:0;overflow:auto;padding:1.1em 1.3em 3em}
/* The header of whichever section is open: its name, what it is counting, and
   the controls that belong to it. */
.vh{display:flex;align-items:baseline;gap:.7em;margin:0 0 .9em;flex-wrap:wrap}
.vh h1{margin:0;font-size:17px;font-weight:600;letter-spacing:0}
.vh .n{color:var(--faint);font-size:12.5px;font-family:var(--mono)}
.vh .sp{margin-left:auto}
.vh .srch{width:15rem;margin:0}
/* A section's rows, full width. The 290px strip these used to live in is why
   a hostname, its address, its environment, what it must satisfy and how it
   went could not all be on the same line. */
.grid{border:1px solid var(--line);border-radius:10px;background:var(--panel);
overflow:hidden}
.grid .hd,.grid .r{display:grid;gap:0 1em;align-items:center;padding:.5em .85em;
border-bottom:1px solid var(--line);min-width:0}
.grid .r:last-child{border-bottom:0}
.grid .hd{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;
color:var(--faint);font-weight:600;background:var(--panel2)}
.grid .r{cursor:pointer}
.grid .r:hover{background:var(--panel2)}
.grid .r.sel{background:var(--panel2);box-shadow:inset 2px 0 0 var(--accent)}
.grid .c{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
font-size:12.5px}
.grid .c.m{font-family:var(--mono)}
.grid .c.f{color:var(--faint);font-size:11.5px}
/* Section headings INSIDE a grid — the environment a block of hosts is in. */
.grid .gh{display:flex;align-items:baseline;gap:.5em;padding:.55em .85em;
background:var(--panel2);border-bottom:1px solid var(--line);font-size:10.5px;
letter-spacing:.07em;text-transform:uppercase;color:var(--faint)}
.grid .gh .s{letter-spacing:0;text-transform:none;font-size:11px;color:var(--faint)}
.grid .gh button{margin-left:auto;flex:none}
/* The overview's roll-up: one chip per verdict, each a way into the list. */
/* The next step. Sits under the fleet chips and above the problem list,
   because a half-configured fleet has no problems to list — nothing is
   drifting when nothing is being checked, and "all clear" is then the most
   misleading thing this screen could say. */
.next{border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:10px;background:var(--panel);padding:.7em .9em;margin:0 0 1.3em}
.next .nx{color:var(--accent);font-size:11px;letter-spacing:.07em;
  text-transform:uppercase;font-weight:600}
.next .nw{font-size:14px;font-weight:600;margin:.15em 0 .25em}
.next .ny{color:var(--muted);font-size:12.5px;line-height:1.55;max-width:60em}
.next button{margin-top:.6em}
.chips{display:flex;flex-wrap:wrap;gap:.5em;margin:0 0 1.3em}
.chip{display:flex;align-items:baseline;gap:.45em;border:1px solid var(--line);
border-radius:10px;background:var(--panel);padding:.55em .8em;cursor:pointer;
font-family:inherit;font-size:12.5px;color:var(--muted)}
.chip:hover{border-color:var(--accent)}
.chip b{font-size:17px;font-family:var(--mono);font-weight:600;color:var(--text)}
.chip.err b{color:var(--err)}.chip.amber b{color:var(--amber)}
.chip.ok b{color:var(--ok)}.chip.faint b{color:var(--faint)}
/* Back out of a detail into the section that opened it. */
.crumb{background:transparent;border:0;color:var(--muted);font-family:inherit;
font-size:12.5px;padding:0 0 .6em;cursor:pointer}
.crumb:hover{color:var(--text);border-color:transparent}
.srch{width:100%;margin-top:.5em;padding:.35em .6em;border-radius:7px;
border:1px solid var(--line);background:var(--field);color:var(--text);
font-family:inherit;font-size:12.5px}
.srch:focus{outline:none;border-color:var(--accent2)}
.grp{padding:.8em .9em .25em;color:var(--faint);font-size:10.5px;
letter-spacing:.07em;text-transform:uppercase;display:flex;align-items:baseline;
gap:.5em;min-width:0}
/* The summary is the part that may wrap; the button is not. Letting it flex
   broke "Assign all" over two lines and squeezed the counts it sits beside. */
.grp .s{letter-spacing:0;text-transform:none;font-size:11px;min-width:0}
.grp button{margin-left:auto;flex:none;white-space:nowrap}
/* One row in a column: selectable, one line, the status as a dot. */
.it{display:flex;align-items:center;gap:.5em;padding:.34em .9em;cursor:pointer;
border-left:2px solid transparent;min-width:0}
.it:hover{background:var(--panel2)}
.it.sel{background:var(--panel2);border-left-color:var(--accent)}
.it .nm{font-family:var(--mono);font-size:12.5px;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}
.it .ad{color:var(--faint);font-size:11px;font-family:var(--mono);white-space:nowrap}
.it .dot{width:7px;height:7px;border-radius:50%;margin-left:auto;flex:none}
.dot.ok{background:var(--ok)}.dot.amber{background:var(--amber)}
.dot.err{background:var(--err)}.dot.faint{background:var(--faint);opacity:.5}
.pill{font-size:10.5px;padding:.1em .5em;border-radius:20px;
border:1px solid var(--line);color:var(--faint);white-space:nowrap}
.pill.inh{border-color:color-mix(in srgb,var(--accent2) 45%,var(--line));color:var(--accent2)}
/* The detail of one thing, inside the section that opened it. */
.det h2{margin:0 0 .15em;font-size:17px;font-family:var(--mono);font-weight:600;
text-transform:none;letter-spacing:0;color:var(--text);display:block}
.det .sub{color:var(--faint);font-size:12px;font-family:var(--mono)}
.bar{display:flex;gap:.45em;margin:.9em 0 1.3em;flex-wrap:wrap}
/* Wider than the 900px it was: that number was chosen for a detail COLUMN
   with two other columns beside it. The cap stays, because a line of prose
   1500px long is its own kind of unreadable. */
.sec{margin-bottom:1.4em;max-width:1180px}
.sec>h4{margin:0 0 .5em;font-size:11px;letter-spacing:.07em;text-transform:uppercase;
color:var(--muted);font-weight:600}
.box{border:1px solid var(--line);border-radius:9px;background:var(--panel);overflow:hidden}
.ln{display:flex;align-items:center;gap:.6em;padding:.45em .75em;
border-bottom:1px solid var(--line);min-width:0}
.ln:last-child{border-bottom:0}
.ln.pick{cursor:pointer}.ln.pick:hover{background:var(--panel2)}
.ln .k{font-family:var(--mono);font-size:12.5px}
.ln .t{color:var(--faint);font-size:11.5px;margin-left:auto;white-space:nowrap}
pre.sls{margin:0;padding:.7em .85em;background:var(--field);font-family:var(--mono);
font-size:12px;line-height:1.55;white-space:pre-wrap;word-break:break-word;color:var(--text);
max-height:48vh;overflow:auto}
.note{color:var(--faint);font-size:11.5px;line-height:1.55;margin:.5em 0 0}
.mono{font-family:var(--mono);font-size:12.5px}
.muted{color:var(--muted)}.faint{color:var(--faint)}
.ok{color:var(--ok)}.err{color:var(--err)}.amber{color:var(--amber)}
.empty{color:var(--faint);font-size:12.5px;padding:.7em .9em;line-height:1.55}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
      padding:14px 16px;margin-bottom:12px}
/* For the pages that are not the console — a refusal, and anything else that
   is one piece of prose. They used to borrow the console's grid, which gave
   them two empty columns and put the text in the third. */
.plain{flex:1 1 auto;min-height:0;overflow:auto;padding:1.4em 1.3em;
max-width:70ch}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;
   color:var(--faint);font-weight:600;padding:7px 8px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
tr:last-child td{border-bottom:0}
/* The day-one panel. Lives in the Overview, where it is the whole answer. */
.start{padding:1.4em 0 0;max-width:62ch}
.start b{font-size:15px}
.start p{margin:.5em 0 .8em;color:var(--muted);font-size:13px;line-height:1.65}
.start em{font-style:normal;color:var(--text)}
.start ol{margin:0 0 1em;padding-left:1.3em;color:var(--muted);font-size:13px;line-height:1.8}
.start ol b{font-size:13px;color:var(--text)}
/* Narrow: the rail becomes a strip of icons-with-labels across the top, and
   the view takes the rest. A 216px rail on a phone is most of the phone. */
@media(max-width:820px){
  .shell{flex-direction:column}
  .rail{width:auto;flex-direction:row;overflow-x:auto;padding:.4em;
    border-right:0;border-bottom:1px solid var(--line)}
  .rail-item{width:auto;white-space:nowrap;border-left:0;border-radius:8px;
    border-bottom:2px solid transparent}
  .rail-item.on{border-left-color:transparent;border-bottom-color:var(--accent)}
  .rail-foot{display:none}
  .view{padding:.9em}
}
  .hfilter input{width:9rem}
}
button,.btn{font-family:inherit;font-size:12.5px;padding:.42em .85em;border-radius:8px;
  border:1px solid var(--line);background:var(--panel2);color:var(--text);cursor:pointer}
button:hover{border-color:var(--accent)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover{filter:brightness(1.08)}
button.danger{color:var(--err);border-color:var(--err)}
button.sm{font-size:12px;padding:.3em .6em}
button[disabled]{opacity:.5;cursor:default}
input,select,textarea{font-family:inherit;font-size:13px;padding:.45em .6em;border-radius:8px;
  border:1px solid var(--line);background:var(--field);color:var(--text);width:100%}
textarea{font-family:var(--mono);font-size:12.5px;line-height:1.5;min-height:230px;resize:vertical}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent)}
/* `hidden` is only display:none from the user agent stylesheet, so ANY explicit
   display in here beats it — label.f sets display:block, and the "type a target
   instead" field was therefore on screen permanently, beside the picker it is
   the fallback for. Two controls under one label, both called Target.
   Fourth element in this codebase to be hidden-but-shown by the same rule, so
   the guard is global and the next one cannot be. */
[hidden]{display:none !important}
label.f{display:block;margin-bottom:10px}
label.f span{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.row{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}
.row > label.f{flex:1 1 150px;margin-bottom:0}
.pill{border:1px solid var(--line);border-radius:999px;padding:2px 9px;font-size:11.5px;
      color:var(--muted);white-space:nowrap}
.pill.on{border-color:var(--accent);color:var(--accent)}
.pill.bad{border-color:var(--err);color:var(--err)}
.pill.warn{border-color:var(--amber);color:var(--amber)}
/* A state can be eight items long. Without a bounded height the dialog grows
   past the viewport and takes its own Save button with it — the list gets
   longer exactly as the reason to save it gets stronger. */
dialog{border:1px solid var(--line);border-radius:14px;background:var(--panel);
       color:var(--text);padding:0;width:min(880px,96vw);max-height:92vh}
dialog form{display:flex;flex-direction:column;max-height:92vh}
dialog .db{overflow:auto;flex:1 1 auto;min-height:0}
dialog .dh,dialog .df{flex:0 0 auto}
dialog::backdrop{background:rgba(0,0,0,.55)}
dialog .dh{padding:14px 16px;border-bottom:1px solid var(--line);font-weight:600}
dialog .db{padding:16px}
dialog .df{padding:12px 16px;border-top:1px solid var(--line);display:flex;gap:10px;
           justify-content:flex-end}
/* ---- the state builder ------------------------------------------------
   A state is a list of things that must be true, each one a small card. The
   YAML it generates is a MODE, not the way in. */
dialog .dh{display:flex;align-items:center;gap:12px}
.modes{margin-left:auto;display:flex;gap:4px;border:1px solid var(--line);
       border-radius:9px;padding:2px}
button.mode{font-size:12px;padding:.3em .7em;border:0;background:transparent;
            color:var(--muted);border-radius:7px;font-weight:500}
button.mode:hover{color:var(--text)}
button.mode.on{background:var(--panel2);color:var(--text);font-weight:600}
#s-items:empty::after{content:'Nothing declared yet. Add the first thing below.';
  display:block;color:var(--faint);font-size:12.5px;padding:14px 2px}
.item{border:1px solid var(--line);border-radius:11px;background:var(--panel2);
      padding:10px 12px;margin-bottom:8px}
.item-h{display:flex;align-items:center;gap:8px;margin-bottom:2px}
.item-h b{font-size:13px}
.item-h .faint{font-size:11.5px}
.item-h .sp{margin-left:auto;display:flex;gap:4px}
.item-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
           gap:8px 10px;margin-top:8px}
.item-grid .wide{grid-column:1/-1}
.item-grid label{display:flex;flex-direction:column;gap:3px;font-size:12px;
                 color:var(--muted);min-width:0}
.item-grid textarea{min-height:92px}
.item-grid .hint{color:var(--faint);font-size:11px}
.item-grid label.check{flex-direction:row;align-items:center;gap:7px}
.item-grid label.check input{width:auto}
/* Starting points, in the NEW-state dialog only. A card per template, so the
   first thing an operator does is edit something real instead of conjuring a
   state from an empty list. */
.startlab{color:var(--faint);font-size:12px;margin:0 0 8px}
.tpls{display:grid;grid-template-columns:repeat(auto-fill,minmax(14em,1fr));gap:8px;
  margin:0 0 14px}
button.tpl{display:block;text-align:left;border:1px solid var(--line);border-radius:10px;
  background:var(--panel2);color:var(--text);padding:9px 11px;cursor:pointer;font:inherit}
button.tpl:hover{border-color:var(--accent);background:var(--panel)}
button.tpl .tl{font-weight:600;font-size:13px}
button.tpl .tb{color:var(--muted);font-size:11.5px;line-height:1.45;margin-top:2px}
button.tpl .tc{color:var(--faint);font-size:11px;margin-top:4px;font-family:var(--mono)}
.additem{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:4px;
         padding-top:12px;border-top:1px dashed var(--line)}
.additem select{min-width:220px}
.additem .faint{font-size:11.5px;flex:1 1 200px}
/* Why the YAML is read-only until you say otherwise. */
.yamlnote{font-size:12px;color:var(--muted);margin-bottom:8px;line-height:1.5}
.yamlnote.warn{color:var(--amber)}
.faint.warn{color:var(--amber)}
#s-yaml textarea[readonly]{opacity:.85}
/* The YAML view is for READING a whole state — eight items of generated SLS is
   fifty lines, and the shared 230px textarea showed eight of them. It gets the
   height the dialog has going spare, which in this mode is nearly all of it:
   there is only a name field and a note above. Still drag-resizable. */
#s-sls{min-height:min(60vh,640px);font-size:13px;line-height:1.55}
.msg{padding:.6em 1em;font-size:13px;border-bottom:1px solid var(--line)}
.msg.err{background:rgba(229,83,75,.12);color:var(--err)}
.msg.ok{background:rgba(76,175,90,.12);color:var(--ok)}

/* ---- the apply panel ----------------------------------------------------
   An apply is the one thing in this console that reaches out and changes
   sixteen machines, and it was the one thing with nothing to watch: a toast
   that said "requested" and hid itself after four seconds, then a page that
   looked exactly as it had. You could not tell whether it had started, which
   host it was on, which ones had come back, or what any of them said.

   It is full width and above the columns because it is about the fleet, not
   about whichever host or state happens to be selected — and because the
   columns are independently scrollable, so anything inside one of them can be
   scrolled away from. It stays until it is dismissed or the next apply
   replaces it; this is a record, not a notification. */
.applypanel{border-bottom:1px solid var(--line);background:var(--panel);
padding:.55em 1em .65em;font-size:12.5px;flex:0 0 auto}
.applypanel .ah{display:flex;align-items:baseline;gap:.7em;min-width:0}
.applypanel .ah b{font-size:13px}
.applypanel .ah .sp{margin-left:auto}
.applypanel .ah .x{border:0;background:transparent;color:var(--muted);
cursor:pointer;font-size:15px;line-height:1;padding:0 .2em}
.applypanel .ah .x:hover{color:var(--text)}
.applybar{height:4px;border-radius:3px;background:var(--line);overflow:hidden;
margin:.45em 0 .5em}
.applybar i{display:block;height:100%;background:var(--accent);
transition:width .25s ease}
.applybar.bad i{background:var(--err)}
/* The per-host lines, across the width rather than down it: sixteen hosts one
   per row is a panel taller than the console. */
.applyhosts{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));
gap:.1em .9em;max-height:8.5em;overflow:auto}
/* GRID, not flex. The reason used to sit in this row as a third flex item, and
   a long one — "Desired-state configuration is not configured on this
   controller (SYSIBLE_BASELINE_URL and …)" — took the whole line and squeezed
   the hostname to two characters and an ellipsis. Sixteen hosts failing for one
   reason rendered as sixteen walls of identical text with no readable names in
   them. The reasons are grouped below the list now; this row is a name and an
   outcome, which is all it ever needed to be. */
.applyhosts .h{display:grid;grid-template-columns:minmax(0,1fr) auto;
gap:0 .5em;align-items:baseline;padding:.1em 0}
.applyhosts .h .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.applyhosts .h .r{font-size:11.5px;white-space:nowrap}
.applyhosts .h.waiting{color:var(--faint)}
.applyhosts .h.running .r{color:var(--accent2)}
.applyhosts .h.ok .r{color:var(--ok)}
.applyhosts .h.err .r{color:var(--err)}
/* One entry per distinct reason, with the hosts it happened to. Sixteen hosts
   that failed the same way is ONE thing to read and one thing to fix. */
.applywhy{margin-top:.5em;border-top:1px solid var(--line);padding-top:.45em;
display:grid;gap:.35em;max-height:9em;overflow:auto}
.applywhy .w{font-size:12px;line-height:1.45}
.applywhy .w .msg{color:var(--err)}
.applywhy .w .on{display:block;color:var(--faint);font-size:11.5px;
overflow:hidden;text-overflow:ellipsis}

/* The roll-up under a state's name: how many hosts are in each condition. */
.tally{margin:.1em 0 .7em;font-size:12.5px;color:var(--muted);
display:flex;flex-wrap:wrap;gap:.15em .5em}
.tally span{white-space:nowrap}

""" + build_info.CSS

FAVICON_SVG = """\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  Sysible Baseline — canonical mark. Family tile (dark rounded square + thin
  brand-green ring, see controller.svg).

  The glyph is a CHECKLIST: the things a host must be true about, each one met.
  That is what Baseline is — a declaration, and a fleet held to it — and it is
  what the state builder now looks like, so the mark and the screen agree.

  It replaces a drawing of hosts standing on a declared line. That was a nice
  picture of the idea and the wrong picture for an icon: at the 40px the portal
  actually renders it at, "small upright rounded bars with a green stroke
  across them" is also the Visualizer, and the two tiles were indistinguishable
  sitting next to each other. Checks and rules share no silhouette with
  anything else in the family.

  Geometry is deliberately centred: content spans 28–100 across and 32–96 down,
  so the 128 box has equal margins and the glyph does not drift toward the ring.
-->
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" role="img" aria-label="Sysible Baseline">
  <defs>
    <linearGradient id="bl-tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#161d29"/><stop offset="1" stop-color="#0a0d14"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="124" height="124" rx="28" ry="28" fill="url(#bl-tile)"/>
  <rect x="3.5" y="3.5" width="121" height="121" rx="26.5" ry="26.5" fill="none" stroke="#6ddb73" stroke-width="2"/>
  <!-- Met (brand green): three ticks, one per declared item. -->
  <g fill="none" stroke="#6ddb73" stroke-width="7.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M28 40 L36 48 L50 32"/>
    <path d="M28 64 L36 72 L50 56"/>
    <path d="M28 88 L36 96 L50 80"/>
  </g>
  <!-- The declared items themselves (brand blue). The last is short so the
       block reads as a list rather than a grid. -->
  <g fill="#7aa2ff">
    <rect x="60" y="36" width="40" height="8" rx="4"/>
    <rect x="60" y="60" width="40" height="8" rx="4"/>
    <rect x="60" y="84" width="26" height="8" rx="4"/>
  </g>
</svg>
"""


def _shell(title: str, body: str, who: str = "", tail: str = "") -> str:
    return (
        "<!doctype html><html lang=en data-theme=dark><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<link rel=icon type='image/svg+xml' href='favicon.svg'>"
        f"<title>{escape(title)}</title>"
        # Theme, chosen the same way and from the same key as every other SLOP
        # app, so switching between them does not flash a different colour.
        "<script>try{var t=localStorage.getItem('slop-theme');"
        "if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: light)')"
        ".matches?'light':'dark';"
        "document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>"
        f"<style>{_CSS}</style></head><body>"
        "<header class=head><div class=brand>Sysible <b>Baseline</b> — desired-state "
        "configuration</div>"
        f"<span class=who>{who}</span>{build_info.chip()}</header>"
        + body + tail
        + "</body></html>"
    )


def console_page(who) -> str:
    w = f"{escape(who.user)} · {escape(who.role)}"
    can_write = who.role in ("operator", "superuser")
    body = (
        "<div class=msg id=msg hidden></div>"
        # Written entirely by renderApply(); hidden until something is applied.
        "<div class=applypanel id=applypanel hidden></div>"
        "<div class=shell>"

        # THE RAIL. Five sections, named for the five questions an operator
        # arrives with: is the fleet all right, what is this host supposed to
        # be, what have I declared, what has been applied, and what happens
        # afterwards. Each is a section rather than a column, so each gets the
        # whole width — which is the thing the three-column version could not
        # give any of them.
        "  <aside class=rail>"
        "    <button class='rail-item on' data-nav=overview>Overview</button>"
        "    <button class=rail-item data-nav=hosts>Hosts"
        "      <span class=cnt id=n-hosts></span></button>"
        "    <button class=rail-item data-nav=states>States"
        "      <span class=cnt id=n-states></span></button>"
        "    <button class=rail-item data-nav=runs>Runs"
        "      <span class=cnt id=n-runs></span></button>"
        "    <button class=rail-item data-nav=rules>Rules"
        "      <span class=cnt id=n-rules></span></button>"
        f"    <div class=rail-foot>{w}</div>"
        "  </aside>"

        # …and the one view. Everything that used to be three columns and five
        # full-width tables below them renders in here.
        "  <main class=view id=view></main>"
        "</div>"

        # ---- state editor -------------------------------------------------
        # A state used to be one textarea of raw Salt SLS. That is the right
        # thing to SHIP and the wrong thing to ASK FOR: "declare what this host
        # must be" should not start with remembering whether the key is
        # pkg.installed or pkg.install. It is a list of things now, and YAML is
        # a mode you can switch to rather than the only way in.
        "<dialog id=dlg-state><form method=dialog>"
        "<div class=dh><span id=dlg-state-h>New state</span>"
        "<span class=modes id=s-modes>"
        "<button type=button class='mode on' data-mode=build>Build it</button>"
        "<button type=button class=mode data-mode=yaml>YAML</button>"
        "</span></div><div class=db>"
        "<label class=f><span>Name</span><input id=s-name placeholder='web-servers'></label>"

        # Build mode: the list, and one picker to add to it.
        "<div id=s-build>"
        # Somewhere to start. A NEW state used to open on an empty list under a
        # picker, which is a blank page — the console had just explained what a
        # state is and then asked the operator to invent one. These fill the
        # list with something real to edit. Hidden when editing an existing
        # state, where replacing its items wholesale is not a starting point,
        # it is data loss.
        "  <div id=s-start hidden>"
        "    <div class=startlab>Start from one of these, or build your own:</div>"
        "    <div class=tpls id=s-tpls></div>"
        "  </div>"
        "  <div id=s-items></div>"
        "  <div class=additem>"
        "    <select id=s-kind aria-label='What to add'></select>"
        "    <button type=button class=sm id=s-add>+ Add</button>"
        "    <span class=faint id=s-kindhelp></span>"
        "  </div>"
        "</div>"

        # YAML mode: a preview of what the list makes, or the editor itself
        # once this state is authored as YAML.
        "<div id=s-yaml hidden>"
        "  <div class=yamlnote id=s-yamlnote></div>"
        "  <textarea id=s-sls spellcheck=false placeholder='nginx_installed:\n"
        "  pkg.installed:\n    - name: nginx'></textarea>"
        "  <button type=button class=sm id=s-takeover hidden>Edit this YAML by hand</button>"
        "</div>"

        "<div class='faint' style='font-size:12px;margin-top:10px'>Saving creates a new "
        "version. Nothing is overwritten — the previous declaration stays readable.</div>"
        "</div><div class=df>"
        "<button value=cancel>Cancel</button>"
        "<button class=primary id=s-save value=save>Save version</button>"
        "</div></form></dialog>"

        # ---- rule editor --------------------------------------------------
        "<dialog id=dlg-rule><form method=dialog>"
        "<div class=dh id=dlg-rule-h>New rule</div><div class=db>"
        "<label class=f><span>Name</span><input id=r-name placeholder='restart nginx'></label>"
        "<div class=row style='margin-bottom:10px'>"
        "<label class=f><span>When a state matching</span>"
        "<input id=r-match placeholder='*' value='*'></label>"
        "<label class=f><span>…does this</span><select id=r-trigger></select></label>"
        "<label class=f><span>then</span><select id=r-action></select></label>"
        "</div>"
        "<label class=f id=r-argwrap><span>Command to run on the host</span>"
        "<input id=r-arg placeholder='systemctl reload nginx'></label>"
        "<div class='faint' style='font-size:12px' id=r-hint></div>"
        "</div><div class=df>"
        "<button value=cancel>Cancel</button>"
        "<button class=primary id=r-save value=save>Save rule</button>"
        "</div></form></dialog>"

        # ---- assign -------------------------------------------------------
        "<dialog id=dlg-assign><form method=dialog>"
        "<div class=dh>Assign a state</div><div class=db>"
        "<div class=row style='margin-bottom:10px'>"
        # The fleet, from the Controller. Typing an id from memory is how a
        # state gets assigned to a host that does not exist — and an assignment
        # to a target nobody has heard of looks exactly like one whose host has
        # not checked in yet.
        "<label class=f><span>Target</span><select id=a-pick></select></label>"
        "<label class=f><span>State</span><select id=a-state></select></label>"
        "</div>"
        # Kept, and revealed by the 'type it' option: when the Controller is
        # unreachable the list is empty, and losing the ability to assign
        # because the host PICKER is down would be worse than typing.
        "<label class=f id=a-otherwrap hidden><span>Target</span>"
        "<input id=a-target placeholder='web-01  or  env:production'></label>"
        "<div class='faint' style='font-size:12px' id=a-note>A host applies to that "
        "host. An environment applies to every host in it, in addition to anything "
        "assigned to the host itself.</div>"
        "</div><div class=df>"
        "<button value=cancel>Cancel</button>"
        "<button class=primary id=a-save value=save>Assign</button>"
        "</div></form></dialog>"
    )
    return _shell("Sysible Baseline", body, w,
                  f"<script>const CAN_WRITE={'true' if can_write else 'false'};{_JS}</script>")


def refusal_page(code: str, why: str) -> str:
    """Say WHICH of the ways this can fail happened. "Not signed in." on a page
    reached through a gateway whose whole job is signing you in is a dead end."""
    # `.plain`, not `.wrap`: that class was the three-column grid and it is
    # gone. A page with no console on it wants a readable column, not a layout.
    body = ("<div class=plain><div class=card>"
            f"<p>{escape(why)}</p>"
            "<p class=muted>Baseline has no login of its own — it takes your identity "
            "from the SLOP gateway. Open it at <span class=mono>/baseline/</span> after "
            "signing in at <a href='/login'>/login</a>.</p>"
            f"<p class=faint style='font-size:12px'>Reason code: "
            f"<span class=mono>{escape(code)}</span></p></div></div>")
    return _shell("Sysible Baseline — not signed in", body)


# The console is a working interface, not a dashboard: every route the service
# exposes is driven from here. Served under /baseline/ with the prefix stripped,
# so every call is relative.
_JS = r"""
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function say(text, kind){
  const m = $('msg');
  if(!text){ m.hidden = true; return; }
  m.className = 'msg ' + (kind || '');
  m.textContent = text; m.hidden = false;
  if(kind === 'ok') setTimeout(() => { m.hidden = true; }, 4000);
}

async function api(path, opts){
  const r = await fetch('api/' + path, Object.assign({
    headers: {accept: 'application/json', 'content-type': 'application/json'}
  }, opts || {}));
  const body = await r.json().catch(() => ({}));
  if(!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

let STATE = {states: [], rules: [], runs: [], drift: null, assign: []};

// ---- rendering -------------------------------------------------------------

// Day one: nothing declared and nothing assigned. The panel at the top of the
// page explains the whole flow, so the sections below do not each explain their
// own part of it again.
// Day one: nothing declared and nothing assigned. The console then has no
// cascade to show, so the detail column explains the flow instead.
function startingOut(){
  return !(STATE.states || []).length && !(STATE.assign || []).length;
}

// ---- what the selection is -------------------------------------------------
// NAV and SEL are declared with the rail below, which is what sets them.

function statesFor(host){
  const direct = STATE.assign.filter((a) => a.target === host.id).map((a) => a.state);
  const viaEnv = host.environment
    ? STATE.assign.filter((a) => a.target === 'env:' + host.environment).map((a) => a.state)
    : [];
  return {direct, viaEnv, all: direct.concat(viaEnv)};
}
function hostById(id){ return (STATE.hosts || []).find((h) => h.id === id) || null; }
function hostsWith(stateName){
  return (STATE.hosts || []).filter((h) => statesFor(h).all.includes(stateName));
}
function runsFor(hostId){
  return (STATE.runs || []).filter((r) => r.host_id === hostId);
}

// `rank` orders a list. A fleet is read for what needs attention, and sorting
// it alphabetically buries the one failing host wherever its name happens to
// fall.
function hostStatus(host, declared){
  // "Nothing declared for it" is NOT compliance, and it is the state most of a
  // fleet is in on day one. Saying "as declared" there would report an entirely
  // unconfigured fleet as green.
  if(!declared.length) return {cls: 'faint', label: 'not managed',
                               rank: STATE_RANK['not managed'],
                               note: 'no state is assigned to it or to its environment, '
                                     + 'so Baseline does not look at it'};
  // THE WORST OF ITS OWN ROWS, exactly as a state's status is the worst of its
  // hosts. This used to read STATE.drift — a list computed on the SERVER by
  // store.drift_summary — while every row underneath it was derived here from
  // the runs. Two computations of one fact, with different rules: the server
  // counted a run that ERRORED as failed, the browser did not, and a host whose
  // apply never ran showed "failing" at the top and "as declared" on every line
  // below it. Reported with a screenshot of exactly that.
  //
  // Derived from the same function the rows use, a roll-up cannot disagree with
  // what it is summarising. It can only be wrong in the same direction, which
  // is a bug you can find.
  const worst = declared.map((n) => hostStateStatus(host, n))
    .sort((a, b) => STATE_RANK[a.label] - STATE_RANK[b.label])[0];
  return {cls: worst.cls, label: worst.label, rank: STATE_RANK[worst.label],
          note: worst.note};
}
// How one state is doing ON ONE HOST. Column two lists the states a host must
// satisfy, so a dot there has to mean "this state, on this host" — showing the
// state's worst host anywhere puts a red dot beside a state that is perfectly
// fine here, which is worse than no dot.
// How THIS state is doing on THIS host, in one phrase — plus when that was
// last true, because "failing" with no date does not say whether it broke five
// minutes ago or last Tuesday.
function hostStateStatus(host, name){
  const runs = (STATE.runs || [])
    .filter((r) => r.host_id === host.id && r.state_name === name && !r.preview);
  if(!runs.length) return {cls: 'faint', label: 'never applied', when: null,
                           note: 'this state has not been applied to this host'};
  const r = runs[0];
  const when = r.started || r.finished || null;
  const cur = ((STATE.states || []).find((x) => x.name === name) || {}).version;
  // ORDER MATTERS, and I had it the wrong way round first — two existing tests
  // caught it. `failed` means salt-call ran and that many states did not come
  // out right; `error` is a message that may accompany that failure ("motd blew
  // up") OR stand alone. So a failure is a failure, and `error` is its detail.
  if(r.failed) return {cls: 'err', label: 'failing', when, ran: r.state_version,
                       note: r.error || 'the last run could not finish'};
  // `error` with NOTHING failed is the other thing entirely: the run could not
  // be carried out at all — Controller unreachable, agent refusing, the
  // desired-state request failing before salt-call ever started — so it comes
  // back with nothing failed and nothing changed. Reading only those two
  // numbers, the console called it "as declared": a desired-state tool
  // reporting a host as compliant when it could not even reach the host. That
  // is the worst single thing this product can say, and it said it sixteen
  // times in one screenshot.
  //
  // Its own verdict rather than "failing", because they are different problems
  // with different fixes: one is something wrong ON the host, the other is
  // something wrong between here and it, and the host's real condition is not
  // bad — it is unknown.
  if(r.error) return {cls: 'err', label: 'could not run', when, ran: r.state_version,
                      note: r.error};
  // A host that satisfied v1 of a state that is now v3 is NOT "as declared".
  // It is running configuration two edits old, and this console was reporting
  // it green — which is the most misleading thing a desired-state tool can say,
  // and the reason "I can't tell what has been applied" is a fair complaint:
  // the only version on screen was the state's current one.
  if(cur != null && r.state_version != null && r.state_version < cur){
    return {cls: 'amber', label: 'out of date', when, ran: r.state_version,
            note: `last applied v${r.state_version}; this state is now v${cur}`};
  }
  if(r.changed) return {cls: 'amber', label: 'drifted', when, ran: r.state_version,
                        note: `the last run had to change ${r.changed}`};
  return {cls: 'ok', label: 'as declared', when, ran: r.state_version,
          note: 'the last run changed nothing'};
}

// "3 minutes ago" beats a timestamp for the question actually being asked,
// which is how stale this is rather than what the clock said.
function ago(ts){
  if(!ts) return '';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if(s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if(m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if(h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// A state is as bad as the worst host it is on: the number worth knowing about
// "web-stack" is not how many hosts have it but whether any of them is broken.
// Worst first. "could not run" sits beside "failing" because both are red and
// both need somebody; it is second because a failure is a KNOWN problem on the
// host, where a run that never started leaves the host's condition unknown.
// 'not managed' is last: it is not a verdict about a host, it is the absence of
// one, and it only ever reaches here through hostStatus.
const STATE_RANK = {failing: 0, 'could not run': 1, 'out of date': 2, drifted: 3,
                    'never applied': 4, 'as declared': 5, 'not managed': 6};
function stateStatus(name){
  const on = hostsWith(name);
  if(!on.length) return {cls: 'faint', label: 'not assigned', rank: 3,
                         note: 'declared, but no host has to satisfy it'};
  // How THIS STATE is doing on each host, not how each host is doing overall.
  // Rolling up the host's status put a red dot on a state that is fine
  // everywhere, because some OTHER state on one of its hosts had failed.
  const worst = on.map((h) => hostStateStatus(h, name))
    .sort((a, b) => STATE_RANK[a.label] - STATE_RANK[b.label])[0];
  return {cls: worst.cls, label: worst.label, rank: STATE_RANK[worst.label],
          note: `${on.length} host${on.length === 1 ? '' : 's'} — worst is ${worst.label}`};
}

// ---- assignments to an environment -----------------------------------------
// The assignments table is the ONLY place in SLOP that stores an environment
// NAME; everywhere else the environment is read live from the Controller, so a
// host moved between environments is picked up on the next poll and at apply
// time without anything here having to be told.
//
// What is NOT picked up is an assignment left pointing at an environment the
// Controller no longer reports — renamed, or emptied of its last host. The
// assignment stays, covers nothing, and looks exactly like one that covers
// everything, right up until somebody notices those hosts were never
// configured. So it is named on the screen instead.
function envAssignments(stateName){
  return (STATE.assign || [])
    .filter((a) => a.state === stateName && String(a.target).startsWith('env:'))
    .map((a) => {
      const env = String(a.target).slice(4);
      return {env, target: a.target,
              live: (STATE.envs || []).includes(env),
              hosts: (STATE.hosts || []).filter((h) => h.environment === env).length};
    });
}
function orphanedEnvs(){
  // Across every state, for the one-line warning at the top of the console.
  const seen = {};
  for(const a of (STATE.assign || [])){
    const t = String(a.target);
    if(!t.startsWith('env:')) continue;
    const env = t.slice(4);
    if(!(STATE.envs || []).includes(env)) seen[env] = (seen[env] || 0) + 1;
  }
  return seen;
}


// ---- the rail ---------------------------------------------------------------
// Which section is open, and what is open inside it. MODE used to mean "am I
// browsing hosts or states", which was a two-way switch bolted onto a column;
// hosts and states are sections of their own now and NAV says which one you
// are in.
let NAV = 'overview';
let SEL = {host: null, state: null, run: null};

// The worst verdict anywhere in a section, so the rail carries urgency and not
// just arithmetic. A fleet of sixteen reads "16"; the same fleet with three
// hosts failing reads "16" in red, and you can see that from the Overview.
function worstOf(labels){
  let worst = null;
  for(const l of labels){
    if(worst === null || STATE_RANK[l] < STATE_RANK[worst]) worst = l;
  }
  return worst;
}
const CLS_OF = {failing: 'err', 'could not run': 'err', 'out of date': 'amber',
                drifted: 'amber', 'never applied': 'faint', 'as declared': 'ok',
                'not managed': 'faint', 'not assigned': 'faint'};

function renderRail(){
  document.querySelectorAll('.rail-item').forEach((b) => {
    b.classList.toggle('on', b.dataset.nav === NAV);
  });
  const hosts = STATE.hosts || [], states = STATE.states || [];
  const hv = hosts.map((h) => hostStatus(h, statesFor(h).all).label);
  const sv = states.map((x) => stateStatus(x.name).label);
  const runs = (STATE.runs || []).filter((r) => !r.preview);
  const bad = runs.filter((r) => r.failed || r.error).length;
  const put = (id, n, cls) => {
    const e = $(id); if(!e) return;
    e.textContent = n === 0 ? '' : String(n);
    e.className = 'cnt' + (cls ? ' ' + cls : '');
  };
  put('n-hosts', hosts.length, CLS_OF[worstOf(hv)]);
  put('n-states', states.length, CLS_OF[worstOf(sv)]);
  put('n-runs', runs.length, bad ? 'err' : '');
  put('n-rules', (STATE.rules || []).length, '');
}

// ---- the view ---------------------------------------------------------------
// One section at a time, full width. A detail opens INSIDE its section with a
// way back, rather than in a column of its own that duplicated the column
// beside it.
function renderView(){
  const v = $('view');
  if(!v) return;
  // The whole view is rebuilt from STATE, including the filter box — so a poll
  // landing mid-word would take the cursor out of it and the next keystroke
  // would go nowhere. Twenty seconds is long enough to be typing in and short
  // enough to happen while you are.
  const f = document.activeElement;
  const hadFocus = f && f.id === 'filter';
  const caret = hadFocus ? f.selectionStart : null;
  _renderView(v);
  if(hadFocus){
    const again = $('filter');
    if(again){
      again.focus();
      const at = caret == null ? again.value.length : caret;
      try{ again.setSelectionRange(at, at); }catch(e){}
    }
  }
}

function _renderView(v){
  if(NAV === 'hosts' && SEL.host) { v.innerHTML = back('hosts', 'Hosts')
      + `<div class=det id=detail>${hostDetail(hostById(SEL.host)) }</div>`; return; }
  if(NAV === 'states' && SEL.state){ v.innerHTML = back('states', 'States')
      + `<div class=det id=detail>${stateDetail(SEL.state, hostById(SEL.host))}</div>`;
    fillSls(SEL.state); return; }
  if(NAV === 'runs' && SEL.run){
    const r = (STATE.runs || []).find((x) => x.id === SEL.run);
    if(r){ v.innerHTML = back('runs', 'Runs') + `<div class=det id=detail>${runDetail(r)}</div>`;
      return; }
  }
  v.innerHTML = {overview: viewOverview, hosts: viewHosts, states: viewStates,
                 runs: viewRuns, rules: viewRules}[NAV]();
}

function back(nav, label){
  return `<button class=crumb data-nav="${esc(nav)}">\u2190 ${esc(label)}</button>`;
}

function vh(title, count, right){
  return `<div class=vh><h1>${esc(title)}</h1>
    ${count ? `<span class=n>${esc(count)}</span>` : ''}
    ${right ? `<span class=sp></span>${right}` : ''}</div>`;
}

const FILTER = {hosts: '', states: '', runs: ''};
function srch(nav, placeholder){
  return `<input class=srch id=filter type=search data-filter="${esc(nav)}"
    placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"
    value="${esc(FILTER[nav] || '')}">`;
}

// ---- overview ----------------------------------------------------------------
// The question this answers is "is my fleet all right", which nothing on the
// three-column screen answered: you had to pick a host to learn anything, and
// then you had learned it about one host.
// What to do next, once there IS something managed. startPanel() covers the
// empty console and then stops: the moment one state exists the Overview goes
// back to listing problems, and a half-configured fleet — a state declared and
// assigned to nothing, sixteen hosts with nothing on them, a host that has
// never actually been applied — reads as "no problems" because nothing is
// drifting. Nothing is drifting because nothing is being checked.
//
// ONE step, the earliest unfinished one. A list of five suggestions is a
// backlog, and a backlog on the first screen is the thing a new operator
// scrolls past.
function nextStep(){
  const hosts = STATE.hosts || [];
  const states = STATE.states || [];
  const assigned = new Set((STATE.assign || []).map((a) => a.state));

  // 1. Declared and assigned to nothing. The most common half-finished state,
  //    and the one the console is quietest about: it has no verdict at all.
  const orphans = states.filter((s) => !assigned.has(s.name));
  if(orphans.length && states.length){
    const n = orphans.length;
    return {
      what: `${n} state${n === 1 ? ' is' : 's are'} declared but assigned to nothing`,
      why: `${n === 1 ? esc(orphans[0].name) + ' is' : 'They are'} not being checked on
        any host. Declaring a state does not apply it.`,
      go: CAN_WRITE ? {label: 'Assign ' + (n === 1 ? esc(orphans[0].name) : 'a state'),
                       nav: 'states', state: n === 1 ? orphans[0].name : null} : null,
    };
  }

  // 2. Hosts with nothing on them.
  const bare = hosts.filter((h) => !statesFor(h).all.length);
  if(bare.length && states.length){
    const n = bare.length;
    return {
      what: `${n} host${n === 1 ? ' has' : 's have'} no state assigned`,
      why: `Baseline is not checking ${n === 1 ? 'it' : 'them'} at all, so
        ${n === 1 ? 'it' : 'they'} will never report drift. Assigning a whole
        environment at once is usually quicker than one host at a time.`,
      go: {label: 'Open hosts', nav: 'hosts'},
    };
  }

  // 3. Assigned, but never actually run. "Must satisfy nginx" is a wish until
  //    something applies it.
  const never = hosts.filter((h) => statesFor(h).all.length && !runsFor(h.id).length);
  if(never.length){
    const n = never.length;
    return {
      what: `${n} host${n === 1 ? ' has' : 's have'} a state that has never been applied`,
      why: `What ${n === 1 ? 'it' : 'they'} must satisfy is declared, but nothing has
        run yet — so Baseline has not checked whether it is true.`,
      go: {label: 'Open hosts', nav: 'hosts'},
    };
  }

  // 4. Salt missing. Last because it is a blocker rather than a step, and the
  //    host rows already say it loudly where it applies.
  const nosalt = hosts.filter(noSalt);
  if(nosalt.length){
    const n = nosalt.length;
    return {
      what: `${n} host${n === 1 ? '' : 's'} cannot be checked \u2014 Salt is not installed`,
      why: `Baseline evaluates a state with <span class=mono>salt-call --local</span> on
        the host. Nothing else about ${n === 1 ? 'it is' : 'them is'} wrong, and the
        console can install it.`,
      go: {label: 'Open hosts', nav: 'hosts'},
    };
  }
  return null;
}

function stepPanel(step){
  return `<div class=next>
    <div class=nx>Next</div>
    <div class=nw>${step.what}</div>
    <div class=ny>${step.why}</div>
    ${step.go ? `<button class='sm primary' data-step="${esc(step.go.nav)}"
       ${step.go.state ? `data-stepstate="${esc(step.go.state)}"` : ''}
       >${step.go.label}</button>` : ''}
  </div>`;
}

function viewOverview(){
  if(startingOut()) return startPanel();
  const hosts = STATE.hosts || [];
  const rows = hosts.map((h) => ({h, d: statesFor(h)}))
    .map((x) => ({...x, st: hostStatus(x.h, x.d.all)}));
  const counts = {};
  rows.forEach((x) => { counts[x.st.label] = (counts[x.st.label] || 0) + 1; });
  const chips = Object.keys(STATE_RANK).filter((l) => counts[l]).map((l) =>
    `<button class="chip ${CLS_OF[l] || ''}" data-verdict="${esc(l)}">
       <b>${counts[l]}</b> ${esc(l)}</button>`).join('');
  // Attention first. A fleet is read top-down and the top is the only part
  // anyone is guaranteed to read.
  // Everything that is not "as declared", "never applied" or unmanaged — i.e.
   // every host Baseline has something to say about. Drifted belongs here: it
   // means Baseline had to CHANGE the host to keep it declared, which is worth
   // a look even though it ended well.
  const need = rows.filter((x) => x.st.rank <= STATE_RANK.drifted)
    .sort((a, b) => a.st.rank - b.st.rank)
    .slice(0, 12);
  let out = vh('Overview', `${hosts.length} host${hosts.length === 1 ? '' : 's'} · `
    + `${(STATE.states || []).length} state${(STATE.states || []).length === 1 ? '' : 's'}`)
    + `<div class=chips>${chips}</div>`;
  const step = nextStep();
  if(step) out += stepPanel(step);
  if(need.length){
    // A SECTION heading, not a second view heading. It was `.vh h1` — the
    // element that names the open section — so the Overview read as two
    // sections on one screen, which is the exact thing this layout exists to
    // stop doing.
    out += `<div class=sec><h4>Needs attention</h4></div>`
      + `<div class=grid>` + need.map(({h, st}) =>
        `<div class=r data-host="${esc(h.id)}" style="grid-template-columns:minmax(0,14em) minmax(0,9em) minmax(0,1fr) auto">
           <span class="c m">${esc(h.label || h.id)}</span>
           <span class="c f m">${esc(h.address || '')}</span>
           <span class="c f">${esc(st.note)}</span>
           <span class="c ${st.cls}">${esc(st.label)}</span></div>`).join('')
      + '</div>';
  } else {
    // "Every managed host is as declared" is VACUOUSLY true when nothing is
    // managed, and reads as all-clear directly under a panel saying nothing is
    // being checked — the console arguing with itself on its first screen. An
    // absence of verdicts is not a good verdict.
    const managed = rows.filter((x) => x.d.all.length).length;
    out += `<div class=grid><div class=empty>${managed
      ? 'Every managed host is as declared.'
      : 'No host has a state assigned yet, so there is nothing to check and no '
        + 'verdict to give.'}</div></div>`;
  }
  return out;
}

// ---- hosts -------------------------------------------------------------------
function viewHosts(){
  const hosts = STATE.hosts || [];
  const head = vh('Hosts', `${hosts.length} host${hosts.length === 1 ? '' : 's'}`,
                  srch('hosts', 'Filter hosts\u2026'));
  if(!hosts.length){
    return head + `<div class=grid><div class=empty>${esc(STATE.fleetNote ||
      'The Controller has no hosts enrolled yet.')} States can still be declared,
      and assigned by typing a target.</div></div>`;
  }
  const q = (FILTER.hosts || '').trim().toLowerCase();
  const byEnv = {};
  for(const h of hosts){ (byEnv[h.environment || ''] = byEnv[h.environment || ''] || []).push(h); }
  const COLS = 'grid-template-columns:minmax(0,13em) minmax(0,10em) minmax(0,1fr) minmax(0,9em) auto';
  let out = head + '<div class=grid>'
    + `<div class=hd style="${COLS}"><span>Host</span><span>Address</span>
       <span>Must satisfy</span><span>Last run</span><span>Verdict</span></div>`;
  let any = false;
  for(const env of Object.keys(byEnv).sort()){
    const all = byEnv[env].map((h) => ({h, d: statesFor(h)}))
      .map((x) => ({...x, st: hostStatus(x.h, x.d.all)}));
    const hit = (x) => !q || (x.h.label || x.h.id).toLowerCase().includes(q)
      || (x.h.address || '').toLowerCase().includes(q)
      || x.st.label.includes(q)
      || x.d.all.some((n) => n.toLowerCase().includes(q));
    const vis = all.filter(hit);
    if(!vis.length) continue;
    any = true;
    vis.sort((a, b) => a.st.rank - b.st.rank
      || (a.h.label || a.h.id).localeCompare(b.h.label || b.h.id));
    const counts = {};
    for(const x of all){ counts[x.st.label] = (counts[x.st.label] || 0) + 1; }
    const managed = all.filter((x) => x.d.all.length).length;
    const sum = managed
      ? Object.keys(STATE_RANK).filter((k) => counts[k])
          .map((k) => `${counts[k]} ${k}`).join(' \u00b7 ')
      : `${all.length} host${all.length === 1 ? '' : 's'}, none managed`;
    out += `<div class=gh>${esc(env || 'No environment')}<span class=s>\u00b7 ${esc(sum)}</span>`
      + (env && CAN_WRITE ? `<button class='sm${managed ? '' : ' primary'}'
          data-assignenv="${esc(env)}">Assign all</button>` : '')
      + '</div>';
    out += vis.map(({h, d, st}) => {
      const last = runsFor(h.id)[0];
      // A host with nothing assigned gets DASHES, not the words "not managed"
      // in two more columns. Sixteen unconfigured hosts used to come to 840px
      // of screen whose entire content was "nothing is set up yet", said
      // thirty-two times; the group header above says it once, for all of
      // them, and the row says nothing because there is nothing to say. The
      // title still explains it on hover.
      const bare = !d.all.length;
      return `<div class="r${SEL.host === h.id ? ' sel' : ''}" data-host="${esc(h.id)}"
        style="${COLS}" title="${esc(st.note)}">
        <span class="c m">${esc(h.label || h.id)}</span>
        <span class="c f m">${esc(h.address || '')}${h.kind === 'ssh' ? ' \u00b7 SSH' : ''}</span>
        <span class="c f">${bare ? '\u2014' : esc(d.all.join(', '))}</span>
        <span class="c f">${last ? esc(ago(last.started)) : '\u2014'}</span>
        <span class="c ${bare ? 'f' : st.cls}">${bare ? '\u2014' : esc(st.label)}</span></div>`;
    }).join('');
  }
  out += '</div>';
  return any ? out : head + `<div class=grid><div class=empty>Nothing matches
    \u201c${esc(q)}\u201d.</div></div>`;
}

// ---- states ------------------------------------------------------------------
function viewStates(){
  const states = STATE.states || [];
  const head = vh('States', `${states.length} declared`,
    (CAN_WRITE ? `<button class='sm primary' id=newstate>+ New state</button>` : '')
    + srch('states', 'Filter states\u2026'));
  if(!states.length){
    return head + `<div class=grid><div class=empty>Nothing declared yet. A state is
      the packages, files and services a host must have.${CAN_WRITE
        ? ' <b>+ New state</b> opens a builder with half a dozen starting points \u2014 a web server, SSH hardening, the common tools \u2014 so you can edit something real rather than start from an empty list.'
        : ' Ask an operator to declare the first one.'}</div></div>`;
  }
  const q = (FILTER.states || '').trim().toLowerCase();
  const rows = states.map((x) => ({s: x, st: stateStatus(x.name), on: hostsWith(x.name)}))
    .filter((r) => !q || r.s.name.toLowerCase().includes(q) || r.st.label.includes(q));
  if(!rows.length){
    return head + `<div class=grid><div class=empty>Nothing matches
      \u201c${esc(q)}\u201d.</div></div>`;
  }
  rows.sort((a, b) => a.st.rank - b.st.rank || a.s.name.localeCompare(b.s.name));
  // WHAT IT DECLARES is a column now. The list carried a name, a version and a
  // verdict — three facts about a declaration and none about what it declares —
  // so "does web-stack install nginx?" could only be answered by opening every
  // state in turn. The summary is read back out of the SLS server-side, which
  // means a hand-written state is summarised too.
  const COLS = 'grid-template-columns:minmax(0,14em) minmax(0,5.5em) minmax(0,1fr) '
             + 'minmax(0,7em) minmax(0,9em)';
  return head + '<div class=grid>'
    + `<div class=hd style="${COLS}"><span>State</span><span>Version</span>
       <span>Declares</span><span>On</span><span>Worst</span></div>`
    + rows.map(({s, st, on}) => {
        const what = s.unreadable
          ? '<span class="err">cannot be read \u2014 open it to see why</span>'
          : (s.contains || []).length
            ? esc((s.contains || []).join(' \u00b7 '))
            : '<span class="f">nothing \u2014 an empty declaration</span>';
        return `<div class="r${SEL.state === s.name ? ' sel' : ''}"
        data-state="${esc(s.name)}" style="${COLS}" title="${esc(st.note)}">
        <span class="c m">${esc(s.name)}</span>
        <span class="c f m">v${esc(s.version)}</span>
        <span class="c f">${what}</span>
        <span class="c f">${on.length
          ? esc(`${on.length} host${on.length === 1 ? '' : 's'}`)
          : 'no host'}</span>
        <span class="c ${st.cls}">${esc(st.label)}</span></div>`;
      }).join('')
    + '</div>';
}

// ---- runs --------------------------------------------------------------------
// Its own section at last. The run history used to be the bottom third of the
// middle column, about whichever host happened to be selected, so "what has
// been applied lately" was a question the console could not be asked.
function viewRuns(){
  const all = (STATE.runs || []);
  const q = (FILTER.runs || '').trim().toLowerCase();
  const rows = all.filter((r) => !q
    || String(r.host_id).toLowerCase().includes(q)
    || String(r.state_name || '').toLowerCase().includes(q)
    || runWord(r).includes(q));
  const head = vh('Runs', `${all.length} recorded`, srch('runs', 'Filter runs\u2026'));
  if(!rows.length){
    return head + `<div class=grid><div class=empty>${all.length
      ? `Nothing matches \u201c${esc(q)}\u201d.`
      : 'Nothing has been applied yet.'}</div></div>`;
  }
  const COLS = 'grid-template-columns:minmax(0,13em) minmax(0,12em) minmax(0,1fr) auto';
  return head + '<div class=grid>'
    + `<div class=hd style="${COLS}"><span>When</span><span>Host</span>
       <span>State</span><span>Outcome</span></div>`
    + rows.slice(0, 200).map((r) => {
        const h = hostById(r.host_id);
        return `<div class="r${SEL.run === r.id ? ' sel' : ''}" data-run="${esc(r.id)}"
          style="${COLS}">
          <span class="c f m">${esc(runTime(r))}</span>
          <span class="c m">${esc(h ? (h.label || h.id) : r.host_id)}</span>
          <span class="c f">${esc(r.state_name || '')}</span>
          <span class="c ${runDot(r)}">${esc(runWord(r))}</span></div>`;
      }).join('')
    + '</div>';
}

// ---- rules -------------------------------------------------------------------
function viewRules(){
  const rules = STATE.rules || [];
  const head = vh('Rules', `${rules.length} rule${rules.length === 1 ? '' : 's'}`,
    CAN_WRITE ? `<button class='sm primary' id=newrule>+ New rule</button>` : '');
  if(!rules.length){
    return head + `<div class=grid><div class=empty>No rules. A rule runs
      something after a state is applied \u2014 capture the host's config,
      reapply a state, or run a command.</div></div>`;
  }
  const COLS = 'grid-template-columns:minmax(0,14em) minmax(0,12em) minmax(0,1fr) auto';
  return head + '<div class=grid>'
    + `<div class=hd style="${COLS}"><span>Rule</span><span>When</span>
       <span>Does</span><span></span></div>`
    + rules.map((r) => `<div class=r data-rule="${esc(r.name)}" style="${COLS}">
        <span class="c m">${esc(r.name)}</span>
        <span class="c f">${esc(r.on || r.event || '')}</span>
        <span class="c f">${esc(r.action || '')}${r.argument ? ' \u00b7 ' + esc(r.argument) : ''}</span>
        <span class="c f">${CAN_WRITE ? `<button class=sm data-delrule="${esc(r.name)}"
          >Delete</button>` : ''}</span></div>`).join('')
    + '</div>';
}

function runTime(r){
  const t = new Date((r.started || 0) * 1000);
  return isNaN(t) ? '?' : t.toLocaleString(undefined,
    {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}
function runWord(r){
  if(r.preview) return 'preview';
  if(r.failed) return `${r.failed} failed`;
  // AFTER `failed`, which an error may merely be the message for. On its own it
  // means the run never happened — and that was being described as "nothing
  // changed", which is true and the exact opposite of useful.
  if(r.error) return 'could not run';
  if(r.changed) return `${r.changed} changed`;
  return 'nothing changed';
}
function runDot(r){
  if(r.preview) return 'faint';
  if(r.failed || r.error) return 'err';
  if(r.changed) return 'amber';
  return 'ok';
}
// ---- column three: the thing itself ----------------------------------------
function startPanel(){
  const hosts = (STATE.hosts || []).length;
  return `<div class=start>
    <b>Nothing is managed yet</b>
    <p>Baseline applies <em>declared states</em> to hosts and tells you when a
    host has drifted from one.${hosts ? ` ${hosts} host${hosts === 1 ? ' is' : 's are'}
    enrolled and ${hosts === 1 ? 'it has' : 'none has'} a state assigned, so
    ${hosts === 1 ? 'it is' : 'none of them is'} being checked.` : ''}</p>
    <ol>
      <li><b>Declare a state</b> — the packages, files and services a host must have.</li>
      <li><b>Assign it</b> to a host, or to a whole environment at once.</li>
      <li><b>Apply it</b>, and from then on Baseline reports what has drifted.</li>
    </ol>
    ${CAN_WRITE ? `<button class='primary' id=startstate>Declare the first state</button>`
                : `<span class=faint>Ask an operator to declare the first state.</span>`}
  </div>`;
}

// A host whose last run could not start because Salt is not on it. The
// Controller's reply is the sentence Baseline's own parser wrote, so this
// looks for the fact rather than for a phrase that might be reworded.
function noSalt(h){
  const last = runsFor(h.id)[0];
  return !!(last && !last.preview && last.error && /not installed/i.test(last.error)
            && /salt/i.test(last.error));
}

function hostDetail(h){
  const d = statesFor(h), st = hostStatus(h, d.all);
  const last = runsFor(h.id)[0];
  const bits = [h.address, h.kind === 'ssh' ? 'SSH' : 'agent',
                h.environment ? 'in ' + h.environment : ''].filter(Boolean);
  let out = `<h2>${esc(h.label || h.id)}</h2>
    <div class=sub>${esc(bits.join(' · '))}</div>
    <div class=bar>
      ${CAN_WRITE ? `<button class='primary' data-assignhost="${esc(h.id)}">Assign a state…</button>` : ''}
      ${d.all.length ? `<button data-preview="${esc(h.id)}">Preview</button>` : ''}
      ${d.all.length && CAN_WRITE ? `<button data-apply="${esc(h.id)}">Apply now</button>` : ''}
      ${noSalt(h) && CAN_WRITE ? `<button class=primary data-installsalt="${esc(h.id)}"
        >Install Salt on this host</button>` : ''}
    </div>
    ${noSalt(h) ? `<div class=sec><div class=box><div class=ln>
      <span class="k err">no Salt</span>
      <span class=faint>Baseline evaluates a state with <span class=mono>salt-call
      --local</span> on the host, so nothing can be applied here until it is
      installed. Nothing else about this host is wrong.</span></div></div></div>` : ''}
    <div class=sec><h4>State</h4><div class=box>
      <div class=ln><span class="k ${st.cls}">${esc(st.label)}</span>
        <span class=faint>${esc(st.note)}</span>
        ${last ? `<span class=t>${esc(runTime(last))}</span>` : ''}</div>
    </div></div>`;
  out += `<div class=sec><h4>Must satisfy</h4><div class=box>`
    + (d.all.length
        ? d.direct.map((n) => { const st = hostStateStatus(h, n);
            return `<div class="ln pick" data-pickstate="${esc(n)}">
              <span class=k>${esc(n)}</span>
              <span class="faint when">assigned to this host${st.when ? ' · ' + esc(ago(st.when)) : ''}</span>
              <span class="t ${st.cls}">${esc(st.label)}</span></div>`; }).join('')
          + d.viaEnv.map((n) => { const st = hostStateStatus(h, n);
            return `<div class="ln pick" data-pickstate="${esc(n)}">
              <span class=k>${esc(n)}</span>
              <span class="faint when">via ${esc(h.environment)}${st.when ? ' · ' + esc(ago(st.when)) : ''}</span>
              <span class="t ${st.cls}">${esc(st.label)}</span></div>`; }).join('')
        : `<div class=empty>Nothing. A host with no assigned state is not looked at
           by Baseline — it is not reported as compliant, it is simply not
           checked.</div>`)
    + '</div></div>';
  const runs = runsFor(h.id).slice(0, 8);
  if(runs.length){
    out += `<div class=sec><h4>Recent runs</h4><div class=box>` + runs.map((r) =>
      `<div class="ln pick" data-run="${esc(r.id)}">
        <span class="k ${runDot(r)}">${esc(runWord(r))}</span>
        <span class=faint>${esc(r.state_name || '')}</span>
        <span class=t>${esc(runTime(r))}</span></div>`).join('') + '</div></div>';
  }
  return out;
}

function stateDetail(name, host){
  const s = (STATE.states || []).find((x) => x.name === name) || {name, version: '?'};
  const on = hostsWith(name);
  // The selected host is only RELEVANT here if it actually has to satisfy this
  // state. Carrying it over regardless offered "Just deb-web-1" on a state
  // deb-web-1 was never assigned — an apply that would run the host's own
  // desired state and nothing of what the page was showing.
  if(host && !on.some((h) => h.id === host.id)) host = null;
  const st = stateStatus(name);
  const hostRuns = host ? runsFor(host.id).filter((r) => r.state_name === name) : [];
  const last = hostRuns[0];
  // The roll-up. "on 17 hosts · failing" says there is a problem and nothing
  // about its size: one failing host out of seventeen and seventeen out of
  // seventeen read identically. The tally is the answer to "what has been
  // applied", in one line, before any scrolling.
  const tally = {};
  on.forEach((h) => { const l = hostStateStatus(h, name).label;
                      tally[l] = (tally[l] || 0) + 1; });
  // CLS_OF, not a second copy of it. There were two of these maps for a
  // while and "could not run" had to be added to both; the one that was
  // missed renders a red verdict in no colour at all.
  const counts = Object.keys(STATE_RANK).filter((l) => tally[l])
    .map((l) => `<span class="${CLS_OF[l] || ''}">${tally[l]} ${esc(l)}</span>`)
    .join('<span class=faint>·</span>');
  let out = `<h2>${esc(name)}</h2>
    <div class=sub>v${esc(s.version)} · on ${on.length} host${on.length === 1 ? '' : 's'}
      · <span class="${st.cls}">${esc(st.label)}</span></div>
    ${counts ? `<div class=tally>${counts}</div>` : ''}
    <div class=bar>
      ${on.length && CAN_WRITE ? `<button class=primary data-applyall="${esc(name)}"
        >Apply to all ${on.length} host${on.length === 1 ? '' : 's'}</button>` : ''}
      ${on.length ? `<button data-previewall="${esc(name)}">Preview on all</button>` : ''}
      ${host && CAN_WRITE ? `<button data-apply="${esc(host.id)}"
        >Just ${esc(host.label || host.id)}</button>` : ''}
      ${CAN_WRITE ? `<button data-assign="${esc(name)}">Assign…</button>` : ''}
      <button data-edit="${esc(name)}">${CAN_WRITE ? 'Edit' : 'View'}</button>
      ${CAN_WRITE ? `<button class=danger data-delstate="${esc(name)}">Delete</button>` : ''}
    </div>
    ${on.length ? '' : `<p class=note>Declaring a state does not apply it. Assign
      it to a host, or to a whole environment, and it becomes appliable.</p>`}`;
  if(host){
    const hst = hostStateStatus(host, name);
    out += `<div class=sec><h4>On ${esc(host.label || host.id)}</h4><div class=box>
      <div class=ln><span class="k ${hst.cls}">${esc(hst.label)}</span>
        <span class=faint>${esc(hst.note)}</span>
        ${last ? `<span class=t>${esc(runTime(last))}</span>` : ''}</div>
      ${last && last.error && last.error !== hst.note ? `<div class=ln>
        <span class="k err">error</span>
        <span class=faint>${esc(last.error)}</span></div>` : ''}
    </div></div>`;
  }
  out += `<div class=sec><h4>What it declares</h4>
    <div class=box><pre class=sls id=sls-${esc(cssId(name))}>Loading…</pre></div></div>`;
  const envs = envAssignments(name);
  if(envs.length){
    out += `<div class=sec><h4>Assigned to environments</h4><div class=box>`
      + envs.map((e) => `<div class=ln>
          <span class=k>${esc(e.env)}</span>
          ${e.live
            ? `<span class=faint>${e.hosts} host${e.hosts === 1 ? '' : 's'} in it</span>
               <span class=t>covered</span>`
            : `<span class="faint err">the Controller does not report this
                 environment any more</span>
               <span class="t err">covers nothing</span>`}
        </div>`).join('')
      + '</div>'
      + (envs.some((e) => !e.live)
          ? `<p class="note err">An environment that was renamed, or whose last host
             moved out of it, leaves its assignment pointing at nothing. The state
             is still declared and still assigned \u2014 it simply reaches no host.
             Re-assign it to the environment the hosts are in now.</p>`
          : `<p class=note>Hosts are matched by the environment the Controller
             reports for them right now, so moving a host between environments
             changes what it must satisfy without anything being re-assigned.</p>`)
      + '</div>';
  }
  out += `<div class=sec><h4>${host ? 'Also on' : 'On these hosts'}</h4><div class=box>`
    + (on.length
        ? on.filter((h) => !host || h.id !== host.id).map((h) => {
            // THIS state on that host — not hostStatus(), which rolls up every
            // state the host has. That roll-up put "failing" beside a host on a
            // state that was fine there, because some OTHER state had failed on
            // it; the comment on stateStatus() warns about exactly this and
            // this list was still doing it.
            const hs = hostStateStatus(h, name);
            return `<div class="ln pick" data-pickhost="${esc(h.id)}">
              <span class=k>${esc(h.label || h.id)}</span>
              ${hs.when ? `<span class="faint when">${esc(ago(hs.when))}</span>` : ''}
              <span class="t ${hs.cls}">${esc(hs.label)}</span></div>`;
          }).join('') || '<div class=empty>No other host.</div>'
        : `<div class=empty>Not assigned to anything. Declaring a state does not
           apply it.</div>`)
    + `</div><p class=note>Every host this state is assigned to, so a change to it
       is never made without seeing who it lands on.</p></div>`;
  return out;
}

function runDetail(r){
  const h = hostById(r.host_id);
  return `<h2>${esc(r.state_name || 'run')}</h2>
    <div class=sub>${esc(runTime(r))} · ${esc(r.host_id)}${r.preview ? ' · preview' : ''}</div>
    <div class=bar>
      ${h && CAN_WRITE ? `<button data-pickhost="${esc(h.id)}">Back to ${esc(h.label || h.id)}</button>` : ''}
    </div>
    <div class=sec><h4>Outcome</h4><div class=box>
      <div class=ln><span class="k ${runDot(r)}">${esc(runWord(r))}</span>
        ${r.total != null ? `<span class=faint>${esc(r.total)} declared</span>` : ''}
        <span class=t>${esc(runTime(r))}</span></div>
      ${r.error ? `<div class=ln><span class="k err">error</span>
        <span class=faint>${esc(r.error)}</span></div>` : ''}
    </div></div>
    ${r.output ? `<div class=sec><h4>Output</h4><div class=box>
      <pre class=sls>${esc(r.output)}</pre></div></div>` : ''}`;
}

// The SLS is fetched on demand: it is the one piece of a state that is big, and
// loading every state's body to draw a list of names would be a request per
// state per poll.
const cssId = (s) => String(s).replace(/[^A-Za-z0-9_-]/g, '_');
const SLS_CACHE = {};
async function fillSls(name){
  const el = $('sls-' + cssId(name));
  if(!el) return;
  if(SLS_CACHE[name] != null){ el.textContent = SLS_CACHE[name]; return; }
  try{
    // {state: <record>, versions: [...]} — the SLS is on the record, not at
    // the top level.
    const d = await api('states/' + encodeURIComponent(name));
    SLS_CACHE[name] = ((d.state || {}).sls || '').trim() || '(empty)';
  }catch(e){ SLS_CACHE[name] = 'Could not read it: ' + e.message; }
  const now = $('sls-' + cssId(name));
  if(now) now.textContent = SLS_CACHE[name];
}

function renderAll(){ renderRail(); renderView(); }

// ---- actions ---------------------------------------------------------------

// ---- the state builder -----------------------------------------------------
// A state is a list of things that must be true. KINDS comes from the server so
// the fields drawn here and the fields the generator reads are one list; the
// SLS is generated there too, because it is the thing that runs as root on
// every host the state is assigned to.
let KINDS = null;          // kind -> spec, once fetched
let ITEMS = [];            // what is being built right now
let SOURCE = 'builder';    // 'builder' | 'yaml' — which editor owns this state

async function kinds(){
  if(KINDS) return KINDS;
  const list = (await api('kinds')).kinds || [];
  KINDS = {};
  for(const k of list) KINDS[k.kind] = k;
  const sel = $('s-kind');
  sel.innerHTML = list.map((k) =>
    `<option value="${esc(k.kind)}">${esc(k.label)}</option>`).join('');
  kindHelp();
  return KINDS;
}
function kindHelp(){
  const k = KINDS && KINDS[$('s-kind').value];
  $('s-kindhelp').textContent = k ? (k.help || '') : '';
}

function fieldHtml(kind, idx, field, value){
  const id = `it-${idx}-${field.key}`;
  const wide = (field.type === 'body' || field.type === 'lines') ? ' wide' : '';
  const hint = field.help ? `<span class=hint>${esc(field.help)}</span>` : '';
  if(field.type === 'bool'){
    return `<label class="check${wide}"><input type=checkbox id="${id}"` +
           `${value ? ' checked' : ''}><span>${esc(field.label)}</span>${hint}</label>`;
  }
  const label = `<span>${esc(field.label)}${field.required ? ' *' : ''}</span>`;
  if(field.type === 'body' || field.type === 'lines'){
    const v = Array.isArray(value) ? value.join('\n') : (value || '');
    const ph = field.placeholder ? ` placeholder="${esc(field.placeholder)}"` : '';
    return `<label class="${wide.trim()}">${label}<textarea id="${id}" ` +
           `spellcheck=false${ph}>${esc(v)}</textarea>${hint}</label>`;
  }
  const ph = field.placeholder ? ` placeholder="${esc(field.placeholder)}"` : '';
  return `<label>${label}<input id="${id}" value="${esc(value || '')}"${ph}>${hint}</label>`;
}

function renderItems(){
  const box = $('s-items');
  box.innerHTML = ITEMS.map((item, i) => {
    const spec = KINDS[item.kind];
    if(!spec) return '';
    const fields = spec.fields.map((f) => fieldHtml(item.kind, i, f, item[f.key])).join('');
    const moves = CAN_WRITE ? (
      `<span class=sp>` +
      `<button type=button class=sm data-move="${i}:-1" title="Move up"` +
      `${i === 0 ? ' disabled' : ''}>\u2191</button>` +
      `<button type=button class=sm data-move="${i}:1" title="Move down"` +
      `${i === ITEMS.length - 1 ? ' disabled' : ''}>\u2193</button>` +
      `<button type=button class="sm danger" data-drop="${i}">Remove</button></span>`) : '';
    return `<div class=item data-i="${i}"><div class=item-h><b>${esc(spec.label)}</b>` +
           `<span class=faint>${esc(spec.help || '')}</span>${moves}</div>` +
           `<div class=item-grid>${fields}</div></div>`;
  }).join('');
  if(!CAN_WRITE){
    box.querySelectorAll('input,textarea').forEach((e) => {
      if(e.type === 'checkbox') e.disabled = true; else e.readOnly = true; });
  }
}

// Read the form back into ITEMS. Called before anything that needs the current
// values — switching to YAML, saving, reordering — so a half-typed field is
// never lost by a re-render.
function collectItems(){
  ITEMS = ITEMS.map((item, i) => {
    const spec = KINDS[item.kind];
    const out = {kind: item.kind};
    for(const f of spec.fields){
      const el = $(`it-${i}-${f.key}`);
      if(!el){ out[f.key] = item[f.key]; continue; }
      out[f.key] = (f.type === 'bool') ? el.checked : el.value;
    }
    return out;
  });
  return ITEMS;
}

function setMode(mode){
  SOURCE = (mode === 'yaml' && SOURCE === 'yaml') ? 'yaml' : SOURCE;
  $('s-build').hidden = (mode !== 'build');
  $('s-yaml').hidden = (mode !== 'yaml');
  $('s-modes').querySelectorAll('.mode').forEach((b) =>
    b.classList.toggle('on', b.dataset.mode === mode));
}

// The YAML view. For a builder state this is a PREVIEW, generated by the
// server — the same call the save uses — so what is read here is the string the
// fleet runs, not a second implementation of it that drifts.
async function showYaml(){
  if(SOURCE === 'yaml'){
    $('s-yamlnote').textContent =
      'This state is written as YAML. It is saved exactly as typed.';
    $('s-yamlnote').classList.remove('warn');
    $('s-sls').readOnly = !CAN_WRITE;
    $('s-takeover').hidden = true;
    setMode('yaml');
    return;
  }
  collectItems();
  setMode('yaml');
  // A state with nothing in it yet has no list to protect, so writing your own
  // Salt state is just: New state -> YAML -> type. Making that go through
  // "take this over, the builder cannot be used again" was a warning about
  // losing something that does not exist, in front of the one path an operator
  // with an existing .sls file wants.
  if(!ITEMS.length){
    SOURCE = 'yaml';
    $('s-sls').readOnly = !CAN_WRITE;
    $('s-takeover').hidden = true;
    $('s-yamlnote').textContent =
      'Write the state as Salt SLS. Paste an existing one if you have it — ' +
      'this is saved exactly as typed.';
    $('s-yamlnote').classList.remove('warn');
    if(CAN_WRITE) $('s-sls').focus();
    return;
  }
  $('s-sls').readOnly = true;
  $('s-yamlnote').textContent = 'Generating…';
  $('s-yamlnote').classList.remove('warn');
  try{
    const name = $('s-name').value.trim() || 'preview';
    const r = await api('states/' + encodeURIComponent(name) + '/preview',
                        {method: 'POST', body: JSON.stringify({items: ITEMS})});
    $('s-sls').value = r.sls;
    $('s-yamlnote').textContent =
      'This is what the list builds — read-only, because the list is what gets saved.';
    $('s-takeover').hidden = !CAN_WRITE;
  }catch(e){
    $('s-sls').value = '';
    $('s-yamlnote').textContent = e.message;
    $('s-yamlnote').classList.add('warn');
    $('s-takeover').hidden = true;
  }
}

// Starting points for a new state. Fetched once; the list is small, fixed, and
// generated server-side through the real emitters so a card cannot show a shape
// the builder would not produce.
let TPLS = null;
async function loadTemplates(){
  if(TPLS) return TPLS;
  try{ TPLS = (await api('templates')).templates || []; }
  catch(e){ TPLS = []; }          // no starting points is a smaller problem
  return TPLS;                     // than a dialog that will not open
}

async function renderTemplates(show){
  const box = $('s-start');
  box.hidden = !show;
  if(!show) return;
  const list = await loadTemplates();
  if(!list.length){ box.hidden = true; return; }
  $('s-tpls').innerHTML = list.map((t) => `<button type=button class=tpl
      data-tpl="${esc(t.id)}">
      <div class=tl>${esc(t.label)}</div>
      <div class=tb>${esc(t.blurb || '')}</div>
      <div class=tc>${esc((t.contains || []).join(' \u00b7 '))}</div></button>`).join('');
}

async function openState(name){
  try{ await kinds(); }
  catch(e){ say(e.message, 'err'); return; }
  $('dlg-state-h').textContent = name ? `Edit ${name}` : 'New state';
  $('s-name').value = name || '';
  $('s-name').disabled = !!name;        // a rename would orphan its assignments
  $('s-sls').value = '';
  ITEMS = [];
  SOURCE = 'builder';
  if(name){
    let st;
    try{ st = (await api('states/' + encodeURIComponent(name))).state; }
    catch(e){ say(e.message, 'err'); return; }
    // A state remembers how it was written. One typed as YAML opens as YAML:
    // parsing it back into items would be a guess, and guessing at the file
    // that configures someone's fleet is not a feature.
    SOURCE = st.source === 'builder' ? 'builder' : 'yaml';
    ITEMS = (st.items || []).slice();
    $('s-sls').value = st.sls || '';
  }
  // Only on a NEW state, and only for someone who can save one. Replacing an
  // existing state's items with a template is not a starting point, it is
  // throwing away what is there.
  await renderTemplates(!name && CAN_WRITE);
  renderItems();
  $('s-save').hidden = !CAN_WRITE;
  $('s-add').hidden = !CAN_WRITE;
  $('s-kind').disabled = !CAN_WRITE;
  if(SOURCE === 'yaml'){ await showYaml(); } else { setMode('build'); }
  $('dlg-state').showModal();
}

// ---- builder events --------------------------------------------------------
$('s-tpls').addEventListener('click', (ev) => {
  const b = ev.target.closest('[data-tpl]');
  if(!b) return;
  ev.preventDefault();
  const t = (TPLS || []).find((x) => x.id === b.dataset.tpl);
  if(!t) return;
  ITEMS = JSON.parse(JSON.stringify(t.items || []));
  SOURCE = 'builder';
  // The name is a SUGGESTION, and only into an empty box: someone who already
  // typed "web-01-nginx" and then wanted the web template as a starting point
  // should not have their name replaced by "web-server".
  if(!$('s-name').value.trim()) $('s-name').value = t.name || '';
  setMode('build');
  renderItems();
  // The strip has done its job. Leaving it up invites a second click that
  // silently discards everything the first one just filled in.
  $('s-start').hidden = true;
  const first = $('s-items').querySelector('.item input, .item textarea');
  if(first) first.focus();
});
$('s-kind').addEventListener('change', kindHelp);
$('s-add').addEventListener('click', () => {
  const kind = $('s-kind').value;
  const spec = KINDS && KINDS[kind];
  if(!spec) return;
  collectItems();
  ITEMS.push(Object.assign({kind}, spec.defaults || {}));
  renderItems();
  const first = $('s-items').querySelector(`.item[data-i="${ITEMS.length - 1}"] input,` +
                                           `.item[data-i="${ITEMS.length - 1}"] textarea`);
  if(first) first.focus();
});
$('s-items').addEventListener('click', (ev) => {
  const drop = ev.target.closest('[data-drop]');
  const move = ev.target.closest('[data-move]');
  if(!drop && !move) return;
  ev.preventDefault();
  collectItems();
  if(drop){ ITEMS.splice(Number(drop.dataset.drop), 1); }
  else{
    const [i, d] = move.dataset.move.split(':').map(Number);
    const j = i + d;
    if(j < 0 || j >= ITEMS.length) return;
    [ITEMS[i], ITEMS[j]] = [ITEMS[j], ITEMS[i]];
  }
  renderItems();
});
$('s-modes').addEventListener('click', (ev) => {
  const b = ev.target.closest('.mode');
  if(!b) return;
  ev.preventDefault();
  if(b.dataset.mode === 'yaml'){ showYaml(); }
  else if(SOURCE === 'yaml'){
    // An untouched YAML box on a new state is not a decision yet — switching
    // back is free. Once something is typed it IS the state, and the builder
    // has nothing to show.
    if(!$('s-sls').value.trim()){ SOURCE = 'builder'; setMode('build'); return; }
    say('This state is written as YAML, so there is no list to build from. ' +
        'Create a new state to use the builder.', 'err');
  }
  else { setMode('build'); }
});
// One-way on purpose, and said so. Parsing SLS back into items would be a
// guess; what it would silently throw away is the part an operator cannot see.
$('s-takeover').addEventListener('click', () => {
  if(!confirm('Take over this YAML by hand?\n\nThe list stops being the source: ' +
              'from the next version on, this state is whatever is typed here, and ' +
              'the builder cannot be used on it again.')) return;
  SOURCE = 'yaml';
  $('s-sls').readOnly = false;
  $('s-takeover').hidden = true;
  $('s-yamlnote').textContent =
    'Hand-edited. Saving stores this YAML as typed; the builder is no longer used ' +
    'for this state.';
  $('s-yamlnote').classList.add('warn');
  $('s-sls').focus();
});

async function openRule(name){
  const meta = STATE.meta || {triggers: ['changed'], actions: ['notify']};
  $('r-trigger').innerHTML = (meta.triggers || []).map((t) =>
    `<option value="${esc(t)}">${esc({changed:'changes', failed:'fails',
      ok:'is already correct', any:'runs at all'}[t] || t)}</option>`).join('');
  $('r-action').innerHTML = (meta.actions || []).map((a) =>
    `<option value="${esc(a)}">${esc({notify:'record it',
      command:'run a command on the host', capture:'capture the host in Flashback',
      reapply:'apply the state again'}[a] || a)}</option>`).join('');
  const r = STATE.rules.find((x) => x.name === name);
  $('dlg-rule-h').textContent = name ? `Edit ${name}` : 'New rule';
  $('r-name').value = r ? r.name : '';
  $('r-name').disabled = !!r;
  $('r-match').value = r ? r.match : '*';
  $('r-trigger').value = r ? r.trigger : 'changed';
  $('r-action').value = r ? r.action : 'notify';
  $('r-arg').value = r ? r.argument : '';
  ruleActionChanged();
  $('dlg-rule').showModal();
}

function ruleActionChanged(){
  const a = $('r-action').value;
  $('r-argwrap').hidden = a !== 'command';
  $('r-hint').textContent = a === 'command'
    ? 'This runs as root on the host, through the same attributed, logged path as any other operator command.'
    : a === 'capture'
    ? 'Baseline has just put the host into a declared state — this is the moment to record what it actually looks like.'
    : a === 'reapply'
    ? 'Use sparingly: a state that needs applying twice usually has an ordering problem.'
    : 'Recorded against the run, and shown in Recent runs.';
}

// The fleet, as the Controller knows it. Fetched once per dialog open rather
// than cached for the session: a host enrolled a minute ago should be in the
// list, and this is one small request against a click.
// The sentinel for "let me type it instead". It must be something a real
// target can never be: controller.py's _TARGET_RE requires the first character
// to be [A-Za-z0-9], so a leading '!' cannot collide.
//
// NOT a NUL. The first version used a \\u0000 prefix, which looks unmistakable
// and is not: the HTML parser replaces U+0000 with U+FFFD, so the attribute
// read back as a replacement character, the comparison never matched, and the
// SENTINEL ITSELF was saved as the assignment's target. Invisible in the
// source, obvious the moment it was clicked.
const TYPE_IT = '!type-a-target';

async function openAssign(stateName, target){
  $('a-target').value = '';
  $('a-state').innerHTML = STATE.states.map((s) =>
    `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join('');
  if(stateName) $('a-state').value = stateName;

  const pick = $('a-pick');
  pick.innerHTML = '<option value="">Loading the fleet…</option>';
  $('a-otherwrap').hidden = true;
  $('dlg-assign').showModal();

  let d = {hosts: [], environments: [], note: null};
  try{ d = await api('targets'); }
  catch(e){ d.note = e.message; }

  const opts = [];
  if((d.environments || []).length){
    opts.push('<optgroup label="Environments">' + d.environments.map((e) =>
      `<option value="env:${esc(e)}">Everything in ${esc(e)}</option>`).join('') +
      '</optgroup>');
  }
  if((d.hosts || []).length){
    // Grouped by environment, the way an operator holds the fleet in their
    // head — and the address alongside, because a bare id does not say which
    // box it is.
    const byEnv = {};
    for(const h of d.hosts){ (byEnv[h.environment || ''] = byEnv[h.environment || ''] || []).push(h); }
    for(const env of Object.keys(byEnv).sort()){
      const label = env || 'No environment';
      opts.push(`<optgroup label="${esc(label)}">` + byEnv[env].map((h) => {
        const extra = [h.address, h.kind === 'ssh' ? 'SSH' : ''].filter(Boolean).join(' · ');
        return `<option value="${esc(h.id)}">${esc(h.label)}` +
               `${extra ? ' — ' + esc(extra) : ''}</option>`;
      }).join('') + '</optgroup>');
    }
  }
  // ALWAYS offered, not only when the list is empty: the Controller's list is
  // the hosts it knows, and assigning to something it has not enrolled yet is
  // a legitimate thing to want.
  opts.push(`<option value="${TYPE_IT}">Type a target instead…</option>`);
  pick.innerHTML = opts.join('');

  const empty = !(d.hosts || []).length && !(d.environments || []).length;
  if(target && [...pick.options].some((o) => o.value === target)){
    pick.value = target;
  } else if(target){
    // A target the Controller does not list — still assignable, by typing.
    pick.value = TYPE_IT;
    $('a-target').value = target;
  } else if(empty){
    pick.value = TYPE_IT;
  }
  syncAssignPick();
  $('a-note').textContent = d.note
    ? d.note
    : 'A host applies to that host. An environment applies to every host in it, ' +
      'in addition to anything assigned to the host itself.';
  $('a-note').classList.toggle('warn', !!d.note);
}

function syncAssignPick(){
  const typing = $('a-pick').value === TYPE_IT;
  $('a-otherwrap').hidden = !typing;
  if(typing) $('a-target').focus();
}
$('a-pick').addEventListener('change', syncAssignPick);

// Remove a state, every version of it, and every assignment to it.
//
// The assignments go too, and the confirmation NAMES them before anything
// happens: those hosts stop being configured by this state, and finding that
// out afterwards is the kind of thing nobody traces back. Run history is kept
// — it records what actually happened on hosts, and deleting it because the
// declaration went away is how an audit trail becomes useless.
async function deleteState(name){
  const assigned = STATE.assign.filter((a) => a.state === name).map((a) => a.target);
  const warn = assigned.length
    ? `Delete the state "${name}"?\n\n` +
      `${assigned.length} assignment(s) go with it, so these stop being ` +
      `configured by it:\n  ${assigned.join('\n  ')}\n\n` +
      'Every version of the declaration is removed. Past runs are kept.'
    : `Delete the state "${name}"?\n\nEvery version of it is removed. ` +
      'It is not assigned to anything. Past runs are kept.';
  if(!confirm(warn)) return;
  try{
    const r = await api('states/' + encodeURIComponent(name), {method: 'DELETE'});
    const n = (r.unassigned || []).length;
    say(`Deleted ${r.name} (${r.versions} version${r.versions === 1 ? '' : 's'})` +
        (n ? `, and unassigned it from ${n} target${n === 1 ? '' : 's'}.` : '.'), 'ok');
    load();
  }catch(e){ say(e.message, 'err'); }
}

// Every target this state is assigned to. Usually one — an environment, which
// the Controller fans out across every host in it — but a state can also be
// assigned to hosts directly, and then there is one per host.
// ---- applying ---------------------------------------------------------------
// APPLY IT TO ALL OF THEM. The console only ever offered "Apply to <one host>",
// so a state assigned to sixteen hosts meant visiting sixteen hosts — which is
// not a workflow, it is the absence of one. Declare, assign, apply: the third
// step has to be able to mean the same set as the second.
//
// ONE REQUEST PER HOST, and this is the whole bug. It used to loop the
// ASSIGNMENT TARGETS — so a state assigned to `env:production` sent ONE request
// for `env:production`, and the Controller's _run_baseline_cycle expands that
// into every host in the environment and runs them all SYNCHRONOUSLY inside
// that one request. Sixteen hosts' salt-calls, at up to 60s each by the
// Controller's own task budget, inside a call this service gives up on after
// 20 seconds. The console reported "the Controller is unreachable" (or nothing
// at all, twenty seconds later, in a bar at the very top of a page whose
// content is in three scrolling columns) while the applies carried on running
// unobserved. "Apply to all 16 hosts doesn't do anything" is exactly what that
// looks like from the outside.
//
// Per host, each request is one host's work and fits in a timeout; each one
// reports separately; a host assigned the state BOTH directly and through its
// environment is applied once, not twice; and "16 hosts" is now a count of what
// was actually asked for rather than of what the button hoped.
//
// Worth being exact about what apply DOES, because it is not "apply this one
// state". Baseline is a convergence tool: the Controller asks for a host's
// WHOLE desired state and runs all of it. So this brings every host the state
// reaches up to everything it must satisfy, and the confirmation says so rather
// than implying a narrower promise.
async function applyEverywhere(stateName, preview){
  const hosts = hostsWith(stateName);
  if(!hosts.length) return say('That state is not assigned to anything yet.', 'err');
  const n = hosts.length;
  if(!preview && !confirm(
      `Apply on ${n} host${n === 1 ? '' : 's'}?\n\n`
      + `This brings ${n === 1 ? 'it' : 'them'} up to everything they must `
      + `satisfy, which may include states other than "${stateName}".`)) return;
  return applyMany(hosts, preview, `${preview ? 'Preview' : 'Apply'} of ${stateName}`);
}

async function applyTo(target, preview){
  const h = hostById(target);
  return applyMany([h || {id: target, label: target}], preview,
                   `${preview ? 'Preview' : 'Apply'} on ${(h && h.label) || target}`);
}

// The run in progress, or the last one. Kept until it is dismissed or replaced:
// an apply is minutes of work on other people's machines, and a toast that
// hides itself after four seconds is not a report of it.
let APPLY = null;
// How many hosts at once. Each request holds the Controller for as long as one
// host's salt-call takes, so this is a choice between finishing and not
// flattening it — four is roughly the Controller's own comfortable parallelism
// for agent tasks.
const APPLY_LANES = 4;

async function applyMany(hosts, preview, title){
  // Baseline does not talk to hosts — the Controller does. Each of these asks
  // the Controller to run that host's desired state over the agent channel it
  // already has, with OUR identity forwarded, so the Controller applies its own
  // RBAC to the real human rather than trusting this service.
  APPLY = {
    title, preview, started: Date.now(), running: true,
    rows: hosts.map((h) => ({id: h.id, label: h.label || h.id,
                             state: 'waiting', note: ''})),
  };
  renderApply();
  let next = 0;
  async function lane(){
    while(next < APPLY.rows.length){
      const row = APPLY.rows[next++];
      row.state = 'running'; renderApply();
      try{
        const r = await api('hosts/' + encodeURIComponent(row.id) + '/apply',
                            {method: 'POST', body: JSON.stringify({preview: !!preview})});
        row.state = 'ok';
        row.note = r.detail || '';
      }catch(e){
        row.state = 'err';
        row.note = e.message || 'failed';
      }
      renderApply();
      // Each host that comes back is a run the fleet can now show, so refresh
      // as they land rather than once at the end — on sixteen hosts the end is
      // minutes away. Coalesced: one reload is five API calls, and sixteen
      // hosts finishing in four lanes would otherwise fire sixteen of them.
      scheduleLoad();
    }
  }
  await Promise.all(Array.from({length: Math.min(APPLY_LANES, APPLY.rows.length)},
                               lane));
  APPLY.running = false;
  renderApply();
  load();
}

let _loadSoon = null;
function scheduleLoad(){
  if(_loadSoon) return;
  _loadSoon = setTimeout(() => { _loadSoon = null; load(); }, 2000);
}

function applyCounts(){
  const r = (APPLY && APPLY.rows) || [];
  return {total: r.length,
          done: r.filter((x) => x.state === 'ok' || x.state === 'err').length,
          failed: r.filter((x) => x.state === 'err').length,
          running: r.filter((x) => x.state === 'running').length};
}

let _applyTick = null;
function renderApply(){
  const el = $('applypanel');
  if(_applyTick){ clearInterval(_applyTick); _applyTick = null; }
  if(!APPLY){ el.hidden = true; el.innerHTML = ''; return; }
  // While it runs the only thing that changes between host results is the
  // clock, and a panel whose timer is frozen at 0s reads as a panel that has
  // stopped working.
  if(APPLY.running) _applyTick = setInterval(renderApply, 1000);
  const c = applyCounts();
  const pct = c.total ? Math.round((c.done / c.total) * 100) : 0;
  const secs = Math.max(0, Math.round((Date.now() - APPLY.started) / 1000));
  let head;
  if(APPLY.running){
    head = `${c.done} of ${c.total} · ${secs}s`;
  }else if(c.failed && c.failed < c.total){
    head = `${c.total - c.failed} of ${c.total} done, ${c.failed} failed · ${secs}s`;
  }else if(c.failed){
    head = `${c.failed} failed · ${secs}s`;
  }else{
    head = `${c.total} done · ${secs}s`;
  }
  el.hidden = false;
  el.innerHTML = `
    <div class=ah>
      <b>${esc(APPLY.title)}</b>
      <span class=faint>${esc(head)}</span>
      <span class=sp></span>
      <button class=x title='Dismiss' data-applyclose='1'>&times;</button>
    </div>
    <div class="applybar${c.failed && !APPLY.running ? ' bad' : ''}">
      <i style="width:${pct}%"></i></div>
    <div class=applyhosts>${APPLY.rows.map(applyRow).join('')}</div>
    ${applyWhy()}`;
}

function applyRow(r){
  const word = {waiting: 'waiting', running: 'running…', ok: 'done', err: 'failed'}[r.state];
  return `<div class="h ${r.state}">
    <span class=nm title="${esc(r.label)}">${esc(r.label)}</span>
    <span class=r>${esc(word)}</span>
  </div>`;
}

// The reasons, grouped. A fleet-wide cause — the Controller not being wired to
// this service, a shared secret that does not match — fails every host with the
// SAME sentence, and repeating it once per host buries the one thing there is
// to do under sixteen copies of it.
function applyWhy(){
  const by = new Map();
  (APPLY.rows || []).forEach((r) => {
    // A failure always has something to say. A success only when the Controller
    // said something beyond the obvious.
    const msg = (r.state === 'err' || (r.state === 'ok' && r.note
                 && !/^Applied|^Previewed/.test(r.note))) ? (r.note || '') : '';
    if(!msg) return;
    if(!by.has(msg)) by.set(msg, []);
    by.get(msg).push(r.label);
  });
  if(!by.size) return '';
  return '<div class=applywhy>' + [...by.entries()].map(([msg, hosts]) => {
    const n = hosts.length;
    const on = n === APPLY.rows.length ? `all ${n} host${n === 1 ? '' : 's'}`
             : n > 6 ? `${hosts.slice(0, 6).join(', ')} and ${n - 6} more`
             : hosts.join(', ');
    return `<div class=w><span class=msg>${esc(msg)}</span>
      <span class=on>${esc(on)}</span></div>`;
  }).join('') + '</div>';
}

// Installing Salt is minutes, not seconds, on a host that has to fetch package
// metadata first — so the button says what it is doing and stays disabled,
// rather than looking like it did nothing for four minutes.
async function installSalt(hostId){
  const btn = document.querySelector(`[data-installsalt="${CSS.escape(hostId)}"]`);
  if(btn){ btn.disabled = true; btn.textContent = 'Installing Salt\u2026'; }
  say(`Installing Salt on ${hostId} \u2014 this can take a few minutes.`, 'ok');
  try{
    const r = await api('hosts/' + encodeURIComponent(hostId) + '/install-salt',
                        {method: 'POST'});
    say(r.detail || `Salt installed on ${hostId}.`, 'ok');
    load();
  }catch(e){
    say(e.message, 'err');
    if(btn){ btn.disabled = false; btn.textContent = 'Install Salt on this host'; }
  }
}

// ---- wiring ----------------------------------------------------------------

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('button'); if(!t) return;
  try{
    // Both buttons open the same editor: the one in the Declared states
    // header, and the one in the day-one panel that is the whole point of
    // that panel.
    if(t.id === 'newstate' || t.id === 'startstate') return openState('');
    // The Next panel's button. It navigates — and when one state is the whole
    // problem, it opens the assign dialog for that state rather than making
    // the operator find it again in a list it just named.
    if(t.dataset.step){
      if(t.dataset.stepstate && CAN_WRITE) openAssign(t.dataset.stepstate);
      return go(t.dataset.step);
    }
    if(t.id === 'newrule')  return openRule('');
    if(t.dataset.edit)      return openState(t.dataset.edit);
    if(t.dataset.delstate)  return deleteState(t.dataset.delstate);
    // Assigning FROM a host row: the target is already known, so the dialog
    // opens with it chosen rather than making you find it in the list again.
    if(t.dataset.assignhost) return openAssign('', t.dataset.assignhost);
    if(t.dataset.assignenv)  return openAssign('', 'env:' + t.dataset.assignenv);
    if(t.dataset.rule)      return openRule(t.dataset.rule);
    if(t.dataset.assign)    return openAssign(t.dataset.assign);
    if(t.dataset.applyclose){ APPLY = null; return renderApply(); }
    if(t.dataset.previewall) return applyEverywhere(t.dataset.previewall, true);
    if(t.dataset.applyall)   return applyEverywhere(t.dataset.applyall, false);
    if(t.dataset.preview)   return applyTo(t.dataset.preview, true);
    if(t.dataset.apply)     return applyTo(t.dataset.apply, false);
    if(t.dataset.installsalt) return installSalt(t.dataset.installsalt);
    if(t.dataset.delrule){
      if(!confirm(`Delete the rule "${t.dataset.delrule}"? Anything it was doing after a state is applied stops happening.`)) return;
      await api('rules/' + encodeURIComponent(t.dataset.delrule), {method: 'DELETE'});
      say('Rule deleted.', 'ok'); return load();
    }
    if(t.dataset.unassign){
      const [target, state] = t.dataset.unassign.split('|');
      if(!confirm(`Stop applying "${state}" to ${target}? The host keeps its current configuration — Baseline simply stops holding it there.`)) return;
      await api('unassign', {method: 'POST', body: JSON.stringify({target, state})});
      say('Unassigned.', 'ok'); return load();
    }
  }catch(e){ say(e.message, 'err'); }
});

$('r-action').addEventListener('change', ruleActionChanged);

$('dlg-state').addEventListener('close', async (ev) => {
  if($('dlg-state').returnValue !== 'save' || !CAN_WRITE) return;
  const name = $('s-name').value.trim();
  if(!name) return say('A state needs a name.', 'err');
  // Whichever editor owns this state is what gets sent. Sending both and
  // letting the server choose would mean a stale preview could overwrite the
  // list it was generated from.
  const payload = (SOURCE === 'yaml')
    ? {sls: $('s-sls').value}
    : {items: collectItems()};
  try{
    const r = await api('states/' + encodeURIComponent(name),
                        {method: 'POST', body: JSON.stringify(payload)});
    // Land on what was just written. Saving a state and being returned to the
    // host you happened to be looking at is the console losing your place at
    // the one moment it knows exactly where you are.
    NAV = 'states'; SEL.state = r.name; SEL.run = null;
    delete SLS_CACHE[r.name];      // it is a new version; the old body is stale
    say(`Saved ${r.name} v${r.version}.`, 'ok'); load();
  }catch(e){ say(e.message, 'err'); }
});

$('dlg-rule').addEventListener('close', async () => {
  if($('dlg-rule').returnValue !== 'save' || !CAN_WRITE) return;
  try{
    await api('rules', {method: 'POST', body: JSON.stringify({
      name: $('r-name').value.trim(), match: $('r-match').value.trim() || '*',
      trigger: $('r-trigger').value, action: $('r-action').value,
      argument: $('r-arg').value.trim(), enabled: true})});
    say('Rule saved.', 'ok'); load();
  }catch(e){ say(e.message, 'err'); }
});

$('dlg-assign').addEventListener('close', async () => {
  if($('dlg-assign').returnValue !== 'save' || !CAN_WRITE) return;
  try{
    const picked = $('a-pick').value;
    const target = (picked === TYPE_IT || !picked)
      ? $('a-target').value.trim() : picked;
    if(!target) return say('Pick a host or environment, or type a target.', 'err');
    await api('assign', {method: 'POST', body: JSON.stringify({
      target: target, state: $('a-state').value})});
    say(`Assigned ${$('a-state').value} to ${target}.`, 'ok');
    // Show the state that was just assigned, so "did that land where I meant"
    // is answered by the screen rather than by looking for it.
    NAV = 'states'; SEL.state = $('a-state').value; SEL.run = null;
    load();
  }catch(e){ say(e.message, 'err'); }
});

// ---- loading ---------------------------------------------------------------
async function load(){
  try{
    // The fleet is allowed to fail on its own: an unreachable Controller must
    // cost the host list, not the whole console.
    const [s, r, runs, a, t] = await Promise.all([
      api('states'), api('rules'), api('runs?limit=200'), api('assignments'),
      api('targets').catch((e) => ({hosts: [], environments: [], note: e.message}))]);
    STATE = {states: s.states, rules: r.rules, runs: runs.runs,
             drift: runs.drift, assign: a.assignments, meta: r,
             hosts: t.hosts || [], envs: t.environments || [], fleetNote: t.note};
    // A selection that no longer exists — a host the Controller stopped
    // reporting, a state somebody deleted — must not leave a detail pane
    // describing something that is gone.
    if(SEL.host && !hostById(SEL.host)) SEL.host = null;
    if(SEL.state && !(STATE.states || []).some((x) => x.name === SEL.state)) SEL.state = null;
    if(SEL.run && !(STATE.runs || []).some((x) => x.id === SEL.run)) SEL.run = null;
    renderAll();
  }catch(e){ say(e.message, 'err'); }
}

// ---- picking ----------------------------------------------------------------
// `go(nav)` is the only way the section changes, and it is what the rail, the
// crumbs and the overview's chips all call. One entry point, so a section can
// never be open with another section's selection showing.
function go(nav, sel){
  NAV = nav;
  if(sel !== undefined){ Object.assign(SEL, sel); }
  renderAll();
  const v = $('view'); if(v) v.scrollTop = 0;
}

function pick(what, value){
  if(what === 'host'){
    SEL.host = value; SEL.run = null;
    // Keep a state selected only while the new host actually has it; otherwise
    // the detail would describe a state this host does not have to satisfy.
    const h = hostById(value);
    if(SEL.state && (!h || !statesFor(h).all.includes(SEL.state))) SEL.state = null;
    return go('hosts');
  }
  if(what === 'state'){ SEL.state = value; SEL.run = null; return go('states'); }
  if(what === 'run'){ SEL.run = value; SEL.state = null; return go('runs'); }
}

document.addEventListener('click', (ev) => {
  // The rail, the crumbs and the overview chips are buttons, so they are read
  // BEFORE the row handler bails out on a button click.
  const nav = ev.target.closest('[data-nav]');
  if(nav){
    // Leaving a section closes whatever was open inside it. Coming back to
    // "Hosts" and landing in one host's detail because that is where you were
    // last time is the console deciding where you are going.
    const to = nav.dataset.nav;
    if(to === 'hosts') SEL.host = null;
    if(to === 'states') SEL.state = null;
    if(to === 'runs') SEL.run = null;
    return go(to);
  }
  const chip = ev.target.closest('[data-verdict]');
  if(chip){ FILTER.hosts = chip.dataset.verdict; SEL.host = null; return go('hosts'); }

  const it = ev.target.closest('.r, .ln.pick');
  if(!it || ev.target.closest('button')) return;
  const d = it.dataset;
  if(d.host) return pick('host', d.host);
  if(d.state) return pick('state', d.state);
  if(d.pickstate) return pick('state', d.pickstate);
  if(d.run) return pick('run', d.run);
  // A rule row opens the rule. openRule() has always taken a name and filled
  // the dialog from it; nothing ever called it with one, because the rules were
  // rows in a column whose click handler had no case for them. They were
  // editable in principle and unreachable in practice.
  if(d.rule && CAN_WRITE) return openRule(d.rule);
  if(d.pickhost){
    // Following a host from a state keeps you on that state: the question
    // "how is web-stack doing on ubuntu-web-1" is one thing, not two.
    SEL.host = d.pickhost; SEL.run = null; return go('states');
  }
});

// Filtering redraws from STATE, not from the server: it is a view of data the
// page already has, and a request per keystroke would be a request per
// keystroke to the Controller. The value is kept in FILTER because the view is
// rebuilt from scratch on every render and the input would otherwise empty
// itself under the cursor on the twenty-second poll.
document.addEventListener('input', (ev) => {
  const f = ev.target.closest('[data-filter]');
  if(!f) return;
  FILTER[f.dataset.filter] = f.value;
  renderView();          // which puts the cursor back where it was
});

load();
// Poll only while the tab is visible: one request per app every 20s in every
// tab left open, forever, is work the gateway and this service did for nobody.
let timer = setInterval(load, 20000);
document.addEventListener('visibilitychange', () => {
  clearInterval(timer);
  if(!document.hidden){ load(); timer = setInterval(load, 20000); }
});
"""
