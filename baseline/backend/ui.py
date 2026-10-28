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
.wrap{max-width:1180px;margin:0 auto;padding:18px 16px 70px}
.bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 14px}
h2{font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
   margin:26px 0 10px;font-weight:600;display:flex;align-items:center;gap:10px}
h2 .n{color:var(--faint);font-weight:500;letter-spacing:0;text-transform:none;font-size:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
      padding:14px 16px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat b{display:block;font-size:26px;line-height:1.1}
.stat span{color:var(--muted);font-size:12.5px}
.mono{font-family:var(--mono);font-size:12.5px}
.muted{color:var(--muted)}.faint{color:var(--faint)}
.ok{color:var(--ok)}.err{color:var(--err)}.amber{color:var(--amber)}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;
   color:var(--faint);font-weight:600;padding:7px 8px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
tr:last-child td{border-bottom:0}
.empty{color:var(--muted);font-size:13px;padding:6px 2px}
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

"""

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
        f"<span class=who>{who}</span></header>"
        + body + tail
        + build_info.footer("Sysible Baseline")
        + "</body></html>"
    )


def console_page(who) -> str:
    w = f"{escape(who.user)} · {escape(who.role)}"
    can_write = who.role in ("operator", "superuser")
    body = (
        "<div class=msg id=msg hidden></div>"
        "<div class=wrap>"
        "  <div class=grid id=drift></div>"

        # THE FLEET, FIRST. The console listed declarations and assignments and
        # never the hosts — so the question an operator actually arrives with,
        # "what is this host supposed to be, and is it?", could only be answered
        # by reading two tables and joining them in your head. The fleet itself
        # existed on this screen only inside the Assign dialog.
        "  <h2>Hosts <span class=n id=nhosts></span>"
        "    <span style='margin-left:auto'></span>"
        "  </h2>"
        "  <div class=card id=hosts><div class=empty>Loading&hellip;</div></div>"

        "  <h2>Declared states <span class=n id=nstates></span>"
        "    <span style='margin-left:auto'></span>"
        + ("    <button class='sm primary' id=newstate>+ New state</button>" if can_write else "")
        + "  </h2>"
        "  <div class=card id=states><div class=empty>Loading…</div></div>"

        "  <h2>Assignments <span class=n>which hosts must satisfy which state</span></h2>"
        "  <div class=card id=assign><div class=empty>Loading…</div></div>"

        "  <h2>After a state is applied <span class=n>event rules</span>"
        "    <span style='margin-left:auto'></span>"
        + ("    <button class='sm' id=newrule>+ New rule</button>" if can_write else "")
        + "  </h2>"
        "  <div class=card id=rules><div class=empty>Loading…</div></div>"

        "  <h2>Recent runs</h2>"
        "  <div class=card id=runs><div class=empty>Loading…</div></div>"
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

function drift(d){
  const n = d.drifted.length, f = d.failed.length;
  return `
    <div class=stat><b>${d.clean}</b><span>as declared</span></div>
    <div class=stat><b class="${n ? 'amber' : ''}">${n}</b><span>drifted</span></div>
    <div class=stat><b class="${f ? 'err' : ''}">${f}</b><span>failing</span></div>`
    + (n || f ? `<div class=stat style="grid-column:1/-1">
        ${n ? `<div class="mono amber">drifted: ${d.drifted.map(esc).join(', ')}</div>` : ''}
        ${f ? `<div class="mono err">failing: ${d.failed.map(esc).join(', ')}</div>` : ''}
       </div>` : '');
}

// What each host must satisfy, and whether it does.
//
// A host's states come from TWO places — assigned to it directly, and assigned
// to its environment — and before this screen showed them together an operator
// had to know that rule and apply it themselves against two other tables.
function statesFor(host){
  const direct = STATE.assign.filter((a) => a.target === host.id).map((a) => a.state);
  const viaEnv = host.environment
    ? STATE.assign.filter((a) => a.target === 'env:' + host.environment).map((a) => a.state)
    : [];
  return {direct, viaEnv, all: direct.concat(viaEnv)};
}

function hostStatus(host, declared){
  // "Nothing declared for it" is NOT compliance, and it is the state most of a
  // fleet is in on day one. Saying "as declared" there would report an entirely
  // unconfigured fleet as green.
  if(!declared.length) return {cls: 'faint', label: 'Not managed',
                               note: 'no state assigned to it or its environment'};
  const d = STATE.drift || {drifted: [], failed: []};
  if((d.failed || []).includes(host.id)) return {cls: 'err', label: 'Failing',
                                                 note: 'the last run could not finish'};
  if((d.drifted || []).includes(host.id)) return {cls: 'amber', label: 'Drifted',
                                                  note: 'the last run had to change it'};
  const ran = (STATE.runs || []).some((r) => r.host_id === host.id && !r.preview);
  if(!ran) return {cls: 'faint', label: 'Never applied',
                   note: 'declared, but nothing has been applied to it yet'};
  return {cls: 'ok', label: 'As declared', note: 'the last run changed nothing'};
}

function hostsTable(){
  const hosts = STATE.hosts || [];
  $('nhosts').textContent = hosts.length
    ? `${hosts.length} from the Controller` : '';
  if(!hosts.length){
    return `<div class=empty>${esc(STATE.fleetNote ||
      'The Controller has no hosts enrolled yet.')}<br>
      States can still be declared and assigned by typing a target.</div>`;
  }
  const byEnv = {};
  for(const h of hosts){ (byEnv[h.environment || ''] = byEnv[h.environment || ''] || []).push(h); }
  let out = '';
  for(const env of Object.keys(byEnv).sort()){
    const label = env || 'No environment';
    // The environment is itself a target — assigning here is how one state
    // covers a whole environment, which is otherwise a piece of syntax you
    // have to know (env:<name>) rather than a thing you can click.
    out += `<table><tr><th colspan=4 style="padding-top:14px">${esc(label)}
      ${env && CAN_WRITE ? `<button class='sm' style='margin-left:10px'
        data-assignenv="${esc(env)}">Assign to all of ${esc(label)}</button>` : ''}
      </th></tr>
      <tr><th>Host</th><th>Must satisfy</th><th>State</th><th></th></tr>`;
    out += byEnv[env].map((h) => {
      const d = statesFor(h);
      const st = hostStatus(h, d.all);
      const must = d.all.length
        ? d.direct.map((n) => `<span class=mono>${esc(n)}</span>`).join(', ') +
          (d.viaEnv.length
            ? (d.direct.length ? ', ' : '') + d.viaEnv.map((n) =>
                `<span class=mono>${esc(n)}</span> <span class=faint>(via ${esc(h.environment)})</span>`).join(', ')
            : '')
        : '<span class=faint>nothing</span>';
      return `<tr>
        <td><span class=mono>${esc(h.label || h.id)}</span>
            ${h.address ? `<div class=faint style="font-size:11.5px">${esc(h.address)}${
              h.kind === 'ssh' ? ' · SSH' : ''}</div>` : ''}</td>
        <td>${must}</td>
        <td><span class="${st.cls}">${esc(st.label)}</span>
            <div class=faint style="font-size:11.5px">${esc(st.note)}</div></td>
        <td style="text-align:right;white-space:nowrap">
          ${CAN_WRITE ? `<button class=sm data-assignhost="${esc(h.id)}">Assign…</button>` : ''}
          ${d.all.length ? `<button class=sm data-preview="${esc(h.id)}">Preview</button>` : ''}
          ${d.all.length && CAN_WRITE ? `<button class='sm primary'
            data-apply="${esc(h.id)}">Apply now</button>` : ''}
        </td></tr>`;
    }).join('');
    out += '</table>';
  }
  return out;
}

function statesTable(rows){
  $('nstates').textContent = rows.length ? `${rows.length} declared` : '';
  if(!rows.length) return `<div class=empty>Nothing declared yet. A host with no
    assigned state is not managed by Baseline — it is simply not looked at.
    ${CAN_WRITE ? 'Use <b>+ New state</b> to declare one.' : ''}</div>`;
  return '<table><tr><th>State</th><th>Version</th><th></th></tr>' + rows.map((s) => `
    <tr><td class=mono>${esc(s.name)}</td>
        <td class=faint>v${s.version}</td>
        <td style="text-align:right">
          <button class=sm data-edit="${esc(s.name)}">${CAN_WRITE ? 'Edit' : 'View'}</button>
          ${CAN_WRITE ? `<button class=sm data-assign="${esc(s.name)}">Assign</button>` : ''}
          ${CAN_WRITE ? `<button class='sm danger' data-delstate="${esc(s.name)}">Delete</button>` : ''}
        </td></tr>`).join('') + '</table>';
}

function assignTable(rows){
  // This told people to type a host id, which stopped being how it works when
  // the picker arrived: the instruction on the screen outlived the thing it
  // was describing.
  if(!rows.length) return `<div class=empty>Nothing assigned yet. Declaring a state
    does not apply it — use <b>Assign\u2026</b> on a host above, or
    <b>Assign to all of &lt;environment&gt;</b> to cover a whole environment.</div>`;
  return '<table><tr><th>Target</th><th>State</th><th></th></tr>' + rows.map((a) => `
    <tr><td class=mono>${esc(a.target)}</td><td class=mono>${esc(a.state)}</td>
        <td style="text-align:right">
          <button class=sm data-preview="${esc(a.target)}">Preview</button>
          ${CAN_WRITE ? `<button class='sm primary' data-apply="${esc(a.target)}">Apply now</button>` : ''}
          ${CAN_WRITE ? `<button class='sm danger' data-unassign="${esc(a.target)}|${esc(a.state)}">Remove</button>` : ''}
        </td></tr>`).join('') + '</table>';
}

function rulesTable(d){
  if(!d.rules.length) return `<div class=empty>No rules. A state will be applied and
    nothing will follow it — no restart, no capture, no alert.
    ${CAN_WRITE ? 'Use <b>+ New rule</b> to add one.' : ''}</div>`;
  return '<table><tr><th>Rule</th><th>Does</th><th></th></tr>' + d.rules.map((r) => `
    <tr><td class=mono>${esc(r.name)}${r.enabled ? '' : ' <span class=pill>off</span>'}</td>
        <td>${esc(r.describes)}</td>
        <td style="text-align:right">${CAN_WRITE ? `
          <button class=sm data-rule="${esc(r.name)}">Edit</button>
          <button class='sm danger' data-delrule="${esc(r.name)}">Delete</button>` : ''}
        </td></tr>`).join('') + '</table>';
}

function runsTable(rows){
  if(!rows.length) return '<div class=empty>Nothing applied yet.</div>';
  return '<table><tr><th>Host</th><th>State</th><th>Result</th><th>Fired</th><th>When</th></tr>'
    + rows.slice(0, 25).map((r) => {
      const cls = (r.error || r.failed) ? 'err' : r.changed ? 'amber' : 'ok';
      const what = r.error ? esc(r.error)
        : r.failed ? `${r.failed} failed`
        : r.changed ? `${r.changed} changed` : 'already as declared';
      const when = r.started ? new Date(r.started * 1000).toLocaleString() : '';
      return `<tr><td class=mono>${esc(r.host_id)}</td>
        <td class=mono>${esc(r.state_name)} <span class=faint>v${r.state_version}</span>
          ${r.preview ? '<span class=pill>preview</span>' : ''}</td>
        <td class=${cls}>${what}</td>
        <td class=faint>${(r.actions || []).map((a) => esc(a.rule)).join(', ') || '—'}</td>
        <td class=faint>${esc(when)}</td></tr>`;
    }).join('') + '</table>';
}

// ---- loading ---------------------------------------------------------------

async function load(){
  try{
    // The fleet is fetched with everything else now, not only when the Assign
    // dialog opens. It is allowed to fail on its own: an unreachable Controller
    // must cost the host list, not the whole console.
    const [s, r, runs, a, t] = await Promise.all([
      api('states'), api('rules'), api('runs?limit=50'), api('assignments'),
      api('targets').catch((e) => ({hosts: [], environments: [], note: e.message}))]);
    STATE = {states: s.states, rules: r.rules, runs: runs.runs,
             drift: runs.drift, assign: a.assignments, meta: r,
             hosts: t.hosts || [], envs: t.environments || [], fleetNote: t.note};
    $('drift').innerHTML = drift(runs.drift);
    $('hosts').innerHTML = hostsTable();
    $('states').innerHTML = statesTable(s.states);
    $('assign').innerHTML = assignTable(a.assignments);
    $('rules').innerHTML = rulesTable(r);
    $('runs').innerHTML = runsTable(runs.runs);
  }catch(e){ say(e.message, 'err'); }
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
    if(t.id === 'newstate') return openState('');
    if(t.id === 'newrule')  return openRule('');
    if(t.dataset.edit)      return openState(t.dataset.edit);
    if(t.dataset.delstate)  return deleteState(t.dataset.delstate);
    // Assigning FROM a host row: the target is already known, so the dialog
    // opens with it chosen rather than making you find it in the list again.
    if(t.dataset.assignhost) return openAssign('', t.dataset.assignhost);
    if(t.dataset.assignenv)  return openAssign('', 'env:' + t.dataset.assignenv);
    if(t.dataset.rule)      return openRule(t.dataset.rule);
    if(t.dataset.assign)    return openAssign(t.dataset.assign);
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
    say(`Assigned ${$('a-state').value} to ${target}.`, 'ok'); load();
  }catch(e){ say(e.message, 'err'); }
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
