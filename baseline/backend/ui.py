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

body{background:var(--topo) center / cover no-repeat fixed,
radial-gradient(70% 55% at 15% -10%,rgba(67,160,71,.12),transparent 60%) fixed,
radial-gradient(70% 60% at 100% 110%,rgba(85,128,238,.09),transparent 55%) fixed,
var(--bg);color:var(--text);font-family:var(--font);font-size:14px}

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
/* THREE COLUMNS, not a stack of tables.
   Reported: "I don't like the hosts being on top. They should be off to the
   side like all the other tools" and "This workflow is confusing and linear".
   Both true. The console was five full-width sections — hosts, states,
   assignments, rules, runs — one under the other, and doing anything meant
   scrolling between them and joining them up by eye. Flashback is not built
   that way: hosts | files | versions | diff, fixed columns, full height, no
   page scroll. This is the same shape for this domain.
     1. browse   — the fleet, or the declared states
     2. middle   — what the selection is made of
     3. detail   — the thing itself
   Rules and runs stop being sections of their own and appear where they are
   about something: a host's runs beside the host, a state's rules beside the
   state. */
.wrap{display:grid;
  grid-template-columns:var(--c1,290px) 6px var(--c2,300px) 6px minmax(0,1fr);
  gap:0;height:calc(100vh - 51px)}
.col{overflow:auto;border-right:1px solid var(--line);padding-bottom:2em;min-width:0}
.col:last-child{border-right:0}
/* Draggable, like Flashback's: a fleet of long hostnames and a fleet of short
   ones want different widths, and that is not a decision this file can make. */
.gut{background:var(--line);opacity:.45;cursor:col-resize}
.gut:hover,.gut.drag{opacity:1;background:var(--accent2)}
.colh{position:sticky;top:0;z-index:2;background:var(--bg);padding:.7em .9em .55em;
border-bottom:1px solid var(--line)}
.colh h3{margin:0;font-size:11.5px;letter-spacing:.07em;text-transform:uppercase;
color:var(--muted);font-weight:600;display:flex;align-items:center;gap:.5em;min-height:1.7em}
.colh h3 .n{color:var(--faint);font-weight:500;letter-spacing:0;
text-transform:none;font-size:11.5px;min-width:0;overflow:hidden;text-overflow:ellipsis}
.colh h3 .sp{margin-left:auto}
/* The pivot. Start from a host and reach its states, or start from a state and
   reach its hosts — the relationship runs both ways and the console used to
   offer only one direction. */
.seg{display:flex;margin-top:.55em;border:1px solid var(--line);
border-radius:7px;overflow:hidden}
.seg button{flex:1;border:0;background:transparent;color:var(--muted);
font-family:inherit;font-size:12px;padding:.36em 0;cursor:pointer}
.seg button.on{background:var(--panel2);color:var(--text);font-weight:600}
.seg button:hover:not(.on){color:var(--text)}
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
/* Column three. */
.det{padding:1em 1.2em}
.det h2{margin:0 0 .15em;font-size:17px;font-family:var(--mono);font-weight:600;
text-transform:none;letter-spacing:0;color:var(--text);display:block}
.det .sub{color:var(--faint);font-size:12px;font-family:var(--mono)}
.bar{display:flex;gap:.45em;margin:.9em 0 1.3em;flex-wrap:wrap}
.sec{margin-bottom:1.4em;max-width:900px}
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
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;
   color:var(--faint);font-weight:600;padding:7px 8px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
tr:last-child td{border-bottom:0}
/* The day-one panel. Lives in the detail column now, where there is room for
   it and where it is not competing with a tally. */
.start{padding:1.4em 0 0;max-width:62ch}
.start b{font-size:15px}
.start p{margin:.5em 0 .8em;color:var(--muted);font-size:13px;line-height:1.65}
.start em{font-style:normal;color:var(--text)}
.start ol{margin:0 0 1em;padding-left:1.3em;color:var(--muted);font-size:13px;line-height:1.8}
.start ol b{font-size:13px;color:var(--text)}
/* Narrow: the cascade collapses to one column at a time, browse first. */
@media(max-width:1000px){
  .wrap{grid-template-columns:1fr;height:auto}
  .col{border-right:0;border-bottom:1px solid var(--line);max-height:none}
  .gut{display:none}
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
        "<div class=wrap>"

        # 1. BROWSE. The fleet, off to the side — or the declared states, which
        #    is the same column seen from the other end. The relationship runs
        #    both ways: a host must satisfy states, a state is on hosts. The
        #    console used to offer only the first direction, down a page.
        "  <div class=col id=col1>"
        "    <div class=colh>"
        "      <h3>Browse <span class=n id=browsecount></span>"
        "        <span class=sp></span>"
        + ("        <button class='sm primary' id=newstate>+ New state</button>"
           if can_write else "")
        + "      </h3>"
        "      <div class=seg>"
        "        <button id=by-host class=on>By host</button>"
        "        <button id=by-state>By state</button>"
        "      </div>"
        "      <input class=srch id=browsefilter type=search "
        "             placeholder='Filter\u2026' aria-label='Filter'>"
        "    </div>"
        "    <div id=browselist><div class=empty>Loading\u2026</div></div>"
        "  </div>"
        "  <div class=gut id=gut1 role=separator aria-orientation=vertical></div>"

        # 2. What the selection is MADE OF.
        "  <div class=col id=col2>"
        "    <div class=colh><h3 id=col2head></h3></div>"
        "    <div id=col2list></div>"
        "  </div>"
        "  <div class=gut id=gut2 role=separator aria-orientation=vertical></div>"

        # 3. The thing itself, and everything about it that used to be a
        #    separate full-width table further down the page.
        "  <div class='col det' id=col3></div>"
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
    body = ("<div class=wrap><div class=card>"
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
// MODE is which end of the relationship you are browsing from. H1 is the thing
// picked in column one, H2 the thing picked in column two. A host and a state
// each remember their own selection, so switching ends and switching back does
// not lose your place.
let MODE = 'host';
let SEL = {host: null, state: null, run: null};

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
  if(!declared.length) return {cls: 'faint', label: 'not managed', rank: 4,
                               note: 'no state is assigned to it or to its environment, '
                                     + 'so Baseline does not look at it'};
  const d = STATE.drift || {drifted: [], failed: []};
  if((d.failed || []).includes(host.id)) return {cls: 'err', label: 'failing', rank: 0,
                                                 note: 'the last run could not finish'};
  if((d.drifted || []).includes(host.id)) return {cls: 'amber', label: 'drifted', rank: 1,
                                                  note: 'the last run had to change it'};
  const ran = (STATE.runs || []).some((r) => r.host_id === host.id && !r.preview);
  if(!ran) return {cls: 'faint', label: 'never applied', rank: 2,
                   note: 'declared, but nothing has been applied to it yet'};
  return {cls: 'ok', label: 'as declared', rank: 3, note: 'the last run changed nothing'};
}
// How one state is doing ON ONE HOST. Column two lists the states a host must
// satisfy, so a dot there has to mean "this state, on this host" — showing the
// state's worst host anywhere puts a red dot beside a state that is perfectly
// fine here, which is worse than no dot.
function hostStateStatus(host, name){
  const runs = (STATE.runs || [])
    .filter((r) => r.host_id === host.id && r.state_name === name && !r.preview);
  if(!runs.length) return {cls: 'faint', label: 'never applied',
                           note: 'this state has not been applied to this host'};
  const r = runs[0];
  if(r.failed) return {cls: 'err', label: 'failing', note: r.error || 'the last run could not finish'};
  if(r.changed) return {cls: 'amber', label: 'drifted',
                        note: `the last run had to change ${r.changed}`};
  return {cls: 'ok', label: 'as declared', note: 'the last run changed nothing'};
}

// A state is as bad as the worst host it is on: the number worth knowing about
// "web-stack" is not how many hosts have it but whether any of them is broken.
const STATE_RANK = {failing: 0, drifted: 1, 'never applied': 2, 'as declared': 3};
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

function row(opts){
  const sel = opts.sel ? ' sel' : '';
  return `<div class="it${sel}" ${opts.data}>`
    + `<span class=nm>${esc(opts.name)}</span>`
    + (opts.pill ? `<span class="pill${opts.pillCls ? ' ' + opts.pillCls : ''}">${esc(opts.pill)}</span>` : '')
    + (opts.extra ? `<span class=ad>${esc(opts.extra)}</span>` : '')
    + (opts.dot ? `<i class="dot ${opts.dot}" title="${esc(opts.title || '')}"></i>` : '')
    + '</div>';
}

// ---- column one: browse ----------------------------------------------------
function renderBrowse(){
  const q = (($('browsefilter') || {}).value || '').trim().toLowerCase();
  $('by-host').classList.toggle('on', MODE === 'host');
  $('by-state').classList.toggle('on', MODE === 'state');
  const hosts = STATE.hosts || [], states = STATE.states || [];
  $('browsecount').textContent =
    `${hosts.length} host${hosts.length === 1 ? '' : 's'} · ` +
    `${states.length} state${states.length === 1 ? '' : 's'}`;
  $('browsefilter').placeholder = MODE === 'host' ? 'Filter hosts…' : 'Filter states…';
  const out = MODE === 'host' ? browseHosts(q) : browseStates(q);
  $('browselist').innerHTML = out ||
    `<div class=empty>Nothing matches “${esc(q)}”.</div>`;
}

function browseHosts(q){
  const hosts = STATE.hosts || [];
  if(!hosts.length){
    return `<div class=empty>${esc(STATE.fleetNote ||
      'The Controller has no hosts enrolled yet.')} States can still be declared, and
      assigned by typing a target.</div>`;
  }
  const byEnv = {};
  for(const h of hosts){ (byEnv[h.environment || ''] = byEnv[h.environment || ''] || []).push(h); }
  let out = '';
  for(const env of Object.keys(byEnv).sort()){
    const all = byEnv[env].map((h) => ({h, d: statesFor(h)}))
      .map((x) => ({...x, st: hostStatus(x.h, x.d.all)}));
    const hit = (x) => !q || (x.h.label || x.h.id).toLowerCase().includes(q)
      || (x.h.address || '').toLowerCase().includes(q)
      || x.st.label.includes(q)
      || x.d.all.some((n) => n.toLowerCase().includes(q));
    const vis = all.filter(hit);
    if(!vis.length) continue;
    // Attention first, then by name.
    vis.sort((a, b) => a.st.rank - b.st.rank
      || (a.h.label || a.h.id).localeCompare(b.h.label || b.h.id));
    const counts = {};
    for(const x of all){ counts[x.st.label] = (counts[x.st.label] || 0) + 1; }
    const managed = all.filter((x) => x.d.all.length).length;
    const sum = managed
      ? ['failing', 'drifted', 'never applied', 'as declared', 'not managed']
          .filter((k) => counts[k]).map((k) => `${counts[k]} ${k}`).join(' · ')
      : `${all.length} host${all.length === 1 ? '' : 's'}, none managed`;
    out += `<div class=grp>${esc(env || 'No environment')}<span class=s>· ${esc(sum)}</span>`
      + (env && CAN_WRITE ? `<button class='sm${managed ? '' : ' primary'}'
          data-assignenv="${esc(env)}">Assign all</button>` : '')
      + '</div>';
    out += vis.map(({h, st}) => row({
      name: h.label || h.id,
      extra: h.address ? (h.address + (h.kind === 'ssh' ? ' · SSH' : '')) : '',
      dot: st.cls, title: st.note,
      sel: MODE === 'host' && SEL.host === h.id,
      data: `data-host="${esc(h.id)}"`})).join('');
  }
  return out;
}

function browseStates(q){
  const states = STATE.states || [];
  if(!states.length){
    return `<div class=empty>Nothing declared yet.
      ${CAN_WRITE ? 'Use <b>+ New state</b> in the detail pane.' : ''}</div>`;
  }
  const rows = states.map((s) => ({s, st: stateStatus(s.name), on: hostsWith(s.name).length}))
    .filter((x) => !q || x.s.name.toLowerCase().includes(q) || x.st.label.includes(q));
  if(!rows.length) return '';
  rows.sort((a, b) => a.st.rank - b.st.rank || a.s.name.localeCompare(b.s.name));
  return `<div class=grp>Declared states<span class=s>· ${rows.length} shown</span></div>`
    + rows.map(({s, st, on}) => row({
        name: s.name, pill: `v${s.version}`,
        extra: on ? `on ${on}` : '',
        dot: st.cls, title: st.note,
        sel: MODE === 'state' && SEL.state === s.name,
        data: `data-state="${esc(s.name)}"`})).join('');
}

// ---- column two: what the selection is made of -----------------------------
function renderMiddle(){
  const head = $('col2head'), list = $('col2list');
  if(MODE === 'host'){
    const h = hostById(SEL.host);
    if(!h){
      head.innerHTML = 'Must satisfy';
      list.innerHTML = '<div class=empty>Pick a host.</div>';
      return;
    }
    const d = statesFor(h);
    head.innerHTML = `Must satisfy <span class=n>${esc(h.label || h.id)}</span>`
      + (CAN_WRITE ? `<span class=sp></span><button class='sm'
          data-assignhost="${esc(h.id)}">Assign…</button>` : '');
    const dotFor = (n) => hostStateStatus(h, n);
    let out = d.all.length
      ? d.direct.map((n) => { const st = dotFor(n); return row({name: n, dot: st.cls,
            title: st.note, sel: SEL.state === n,
            data: `data-pickstate="${esc(n)}"`}); }).join('')
        + d.viaEnv.map((n) => { const st = dotFor(n); return row({name: n,
            pill: `via ${h.environment}`, pillCls: 'inh', dot: st.cls, title: st.note,
            sel: SEL.state === n, data: `data-pickstate="${esc(n)}"`}); }).join('')
      : `<div class=empty>Nothing assigned. Baseline does not look at this host
         until something is.</div>`;
    // Rules and runs are ABOUT something. They were two more full-width tables
    // at the bottom of the page, which is where you went to find out whether
    // the thing you were looking at had any.
    const rules = (STATE.rules || []);
    if(rules.length){
      out += '<div class=grp>After a state is applied<span class=s>· event rules</span></div>'
        + rules.map((r) => row({name: r.name, extra: r.on || r.event || '',
            data: `data-rule="${esc(r.name)}"`})).join('');
    }
    const runs = runsFor(h.id).slice(0, 12);
    out += '<div class=grp>Recent runs<span class=s>· this host</span></div>'
      + (runs.length ? runs.map((r) => row({
          name: runTime(r), extra: runWord(r), dot: runDot(r),
          sel: SEL.run === r.id, data: `data-run="${esc(r.id)}"`})).join('')
        : '<div class=empty>Nothing applied to it yet.</div>');
    list.innerHTML = out;
    return;
  }
  // By state: the hosts it lands on.
  const name = SEL.state;
  if(!name){
    head.innerHTML = 'On these hosts';
    list.innerHTML = '<div class=empty>Pick a state.</div>';
    return;
  }
  const on = hostsWith(name);
  head.innerHTML = `On these hosts <span class=n>${esc(name)}</span>`
    + (CAN_WRITE ? `<span class=sp></span><button class='sm'
        data-assign="${esc(name)}">Assign…</button>` : '');
  list.innerHTML = on.length
    ? on.map((h) => {
        const st = hostStatus(h, statesFor(h).all);
        const viaEnv = statesFor(h).viaEnv.includes(name);
        return row({name: h.label || h.id, extra: h.address || '',
          pill: viaEnv ? `via ${h.environment}` : '', pillCls: 'inh',
          dot: st.cls, title: st.note, sel: SEL.host === h.id,
          data: `data-pickhost="${esc(h.id)}"`});
      }).join('')
    : `<div class=empty>Not assigned to anything yet — declaring a state does
       not apply it.</div>`;
}

function runTime(r){
  const t = new Date((r.started || 0) * 1000);
  return isNaN(t) ? '?' : t.toLocaleString(undefined,
    {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}
function runWord(r){
  if(r.preview) return 'preview';
  if(r.failed) return `${r.failed} failed`;
  if(r.changed) return `${r.changed} changed`;
  return 'nothing changed';
}
function runDot(r){
  if(r.preview) return 'faint';
  if(r.failed) return 'err';
  if(r.changed) return 'amber';
  return 'ok';
}
// ---- column three: the thing itself ----------------------------------------
function renderDetail(){
  const c = $('col3');
  if(startingOut()){ c.innerHTML = startPanel(); return; }
  // A state is selected: show the state, and how it went on the selected host.
  if(SEL.state && (STATE.states || []).some((s) => s.name === SEL.state)){
    c.innerHTML = stateDetail(SEL.state, MODE === 'host' ? hostById(SEL.host)
                                                         : hostById(SEL.host));
    return;
  }
  if(SEL.run){
    const r = (STATE.runs || []).find((x) => x.id === SEL.run);
    if(r){ c.innerHTML = runDetail(r); return; }
  }
  const h = hostById(SEL.host);
  if(h){ c.innerHTML = hostDetail(h); return; }
  c.innerHTML = `<div class=empty>Pick a host or a state on the left.</div>`;
}

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
    </div>
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
              <span class=faint>assigned to this host</span>
              <span class="t ${st.cls}">${esc(st.label)}</span></div>`; }).join('')
          + d.viaEnv.map((n) => { const st = hostStateStatus(h, n);
            return `<div class="ln pick" data-pickstate="${esc(n)}">
              <span class=k>${esc(n)}</span>
              <span class=faint>via ${esc(h.environment)}</span>
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
  let out = `<h2>${esc(name)}</h2>
    <div class=sub>v${esc(s.version)} · on ${on.length} host${on.length === 1 ? '' : 's'}
      · <span class="${st.cls}">${esc(st.label)}</span></div>
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
            const hs = hostStatus(h, statesFor(h).all);
            return `<div class="ln pick" data-pickhost="${esc(h.id)}">
              <span class=k>${esc(h.label || h.id)}</span>
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

function renderAll(){
  renderBrowse(); renderMiddle(); renderDetail();
  if(SEL.state) fillSls(SEL.state);
}

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
  renderItems();
  $('s-save').hidden = !CAN_WRITE;
  $('s-add').hidden = !CAN_WRITE;
  $('s-kind').disabled = !CAN_WRITE;
  if(SOURCE === 'yaml'){ await showYaml(); } else { setMode('build'); }
  $('dlg-state').showModal();
}

// ---- builder events --------------------------------------------------------
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
function targetsOf(stateName){
  return (STATE.assign || []).filter((a) => a.state === stateName)
    .map((a) => a.target);
}

// APPLY IT TO ALL OF THEM. The console only ever offered "Apply to <one host>",
// so a state assigned to sixteen hosts meant visiting sixteen hosts — which is
// not a workflow, it is the absence of one. Declare, assign, apply: the third
// step has to be able to mean the same set as the second.
//
// Worth being exact about what apply DOES, because it is not "apply this one
// state". Baseline is a convergence tool: the Controller asks for a host's
// WHOLE desired state and runs all of it (see _run_baseline_cycle). So this
// brings every host the state reaches up to everything it must satisfy, and
// the confirmation says so rather than implying a narrower promise.
async function applyEverywhere(stateName, preview){
  const targets = targetsOf(stateName);
  if(!targets.length) return say('That state is not assigned to anything yet.', 'err');
  const hosts = hostsWith(stateName).length;
  if(!preview && !confirm(
      `Apply on ${hosts} host${hosts === 1 ? '' : 's'}?\n\n`
      + `This brings ${hosts === 1 ? 'it' : 'them'} up to everything they must `
      + `satisfy, which may include states other than "${stateName}".`)) return;
  say(`${preview ? 'Previewing' : 'Applying'} on ${hosts} host${hosts === 1 ? '' : 's'}…`);
  let ok = 0;
  const failed = [];
  for(const t of targets){
    try{
      await api('hosts/' + encodeURIComponent(t) + '/apply',
                {method: 'POST', body: JSON.stringify({preview: !!preview})});
      ok++;
    }catch(e){ failed.push(`${t}: ${e.message}`); }
  }
  // One failed target out of three is not a failure and not a success, and
  // reporting either would be a lie about the other two.
  if(failed.length && ok) say(
    `${ok} of ${targets.length} started; ${failed.join('; ')}`, 'err');
  else if(failed.length) say(failed.join('; '), 'err');
  else say(`${preview ? 'Preview' : 'Apply'} requested for `
           + `${hosts} host${hosts === 1 ? '' : 's'}.`, 'ok');
  setTimeout(load, 1500);
}

async function applyTo(target, preview){
  // Baseline does not talk to hosts — the Controller does. This asks the
  // Controller to run this host's desired state over the agent channel it
  // already has, with OUR identity forwarded, so the Controller applies its own
  // RBAC to the real human rather than trusting this service.
  say(preview ? `Previewing ${target}…` : `Applying to ${target}…`);
  try{
    const r = await api('hosts/' + encodeURIComponent(target) + '/apply',
                        {method: 'POST', body: JSON.stringify({preview: !!preview})});
    say(r.detail || (preview ? 'Preview requested.' : 'Apply requested.'), 'ok');
    setTimeout(load, 1500);
  }catch(e){ say(e.message, 'err'); }
}

// ---- wiring ----------------------------------------------------------------

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('button'); if(!t) return;
  try{
    // Both buttons open the same editor: the one in the Declared states
    // header, and the one in the day-one panel that is the whole point of
    // that panel.
    if(t.id === 'newstate' || t.id === 'startstate') return openState('');
    if(t.id === 'newrule')  return openRule('');
    if(t.dataset.edit)      return openState(t.dataset.edit);
    if(t.dataset.delstate)  return deleteState(t.dataset.delstate);
    // Assigning FROM a host row: the target is already known, so the dialog
    // opens with it chosen rather than making you find it in the list again.
    if(t.dataset.assignhost) return openAssign('', t.dataset.assignhost);
    if(t.dataset.assignenv)  return openAssign('', 'env:' + t.dataset.assignenv);
    if(t.dataset.rule)      return openRule(t.dataset.rule);
    if(t.dataset.assign)    return openAssign(t.dataset.assign);
    if(t.dataset.previewall) return applyEverywhere(t.dataset.previewall, true);
    if(t.dataset.applyall)   return applyEverywhere(t.dataset.applyall, false);
    if(t.dataset.preview)   return applyTo(t.dataset.preview, true);
    if(t.dataset.apply)     return applyTo(t.dataset.apply, false);
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
    MODE = 'state'; SEL.state = r.name; SEL.run = null;
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
    MODE = 'state'; SEL.state = $('a-state').value; SEL.run = null;
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
    // reporting, a state somebody deleted — must not leave the columns beside
    // it describing something that is gone.
    if(SEL.host && !hostById(SEL.host)) SEL.host = null;
    if(SEL.state && !(STATE.states || []).some((x) => x.name === SEL.state)) SEL.state = null;
    if(SEL.run && !(STATE.runs || []).some((x) => x.id === SEL.run)) SEL.run = null;
    // Open on something rather than on three empty columns.
    if(!SEL.host && MODE === 'host' && (STATE.hosts || []).length){
      const ranked = STATE.hosts.map((h) => ({h, st: hostStatus(h, statesFor(h).all)}))
        .sort((x, y) => x.st.rank - y.st.rank
          || (x.h.label || x.h.id).localeCompare(y.h.label || y.h.id));
      SEL.host = ranked[0].h.id;
    }
    if(!SEL.state && MODE === 'state' && (STATE.states || []).length){
      SEL.state = STATE.states[0].name;
    }
    renderAll();
  }catch(e){ say(e.message, 'err'); }
}

// ---- picking ----------------------------------------------------------------
function pick(what, value){
  if(what === 'host'){ SEL.host = value; SEL.run = null;
    // Keep a state selected only while the new host actually has it; otherwise
    // the detail pane would describe a state this host does not have to satisfy.
    const h = hostById(value);
    if(SEL.state && (!h || !statesFor(h).all.includes(SEL.state))) SEL.state = null;
  }
  if(what === 'state'){ SEL.state = value; SEL.run = null; }
  if(what === 'run'){ SEL.run = value; SEL.state = null; }
  renderAll();
}

document.addEventListener('click', (ev) => {
  const it = ev.target.closest('.it, .ln.pick');
  if(!it || ev.target.closest('button')) return;
  const d = it.dataset;
  if(d.host) return pick('host', d.host);
  if(d.state) return pick('state', d.state);
  if(d.pickstate) return pick('state', d.pickstate);
  if(d.run) return pick('run', d.run);
  if(d.pickhost){
    // Following a host from a state keeps you on that state: the question
    // "how is web-stack doing on ubuntu-web-1" is one thing, not two.
    SEL.host = d.pickhost; SEL.run = null; renderAll();
  }
});

// Switching ends. Each end keeps its own selection, so going back and forth
// does not lose your place.
$('by-host').addEventListener('click', () => {
  MODE = 'host'; $('browsefilter').value = ''; renderAll();
});
$('by-state').addEventListener('click', () => {
  MODE = 'state'; $('browsefilter').value = '';
  if(!SEL.state && (STATE.states || []).length) SEL.state = STATE.states[0].name;
  renderAll();
});

// Filtering redraws from STATE, not from the server: it is a view of data the
// page already has, and a request per keystroke would be a request per
// keystroke to the Controller.
$('browsefilter').addEventListener('input', renderBrowse);

// Draggable column edges, as Flashback has: a fleet of long hostnames and one
// of short names do not want the same widths, and this file cannot know which.
for(const [gut, varName, min, max] of [['gut1', '--c1', 190, 560],
                                       ['gut2', '--c2', 190, 620]]){
  const el = $(gut); if(!el) continue;
  el.addEventListener('mousedown', (down) => {
    down.preventDefault();
    el.classList.add('drag');
    const startX = down.clientX;
    const start = parseInt(getComputedStyle(document.documentElement)
      .getPropertyValue(varName) || (varName === '--c1' ? '290' : '300'), 10);
    const move = (m) => {
      const w = Math.min(max, Math.max(min, start + (m.clientX - startX)));
      document.documentElement.style.setProperty(varName, w + 'px');
    };
    const up = () => {
      el.classList.remove('drag');
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
}

load();
// Poll only while the tab is visible: one request per app every 20s in every
// tab left open, forever, is work the gateway and this service did for nobody.
let timer = setInterval(load, 20000);
document.addEventListener('visibilitychange', () => {
  clearInterval(timer);
  if(!document.hidden){ load(); timer = setInterval(load, 20000); }
});
"""
