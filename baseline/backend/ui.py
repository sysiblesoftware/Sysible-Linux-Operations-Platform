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
dialog{border:1px solid var(--line);border-radius:14px;background:var(--panel);
       color:var(--text);padding:0;width:min(760px,94vw)}
dialog::backdrop{background:rgba(0,0,0,.55)}
dialog .dh{padding:14px 16px;border-bottom:1px solid var(--line);font-weight:600}
dialog .db{padding:16px}
dialog .df{padding:12px 16px;border-top:1px solid var(--line);display:flex;gap:10px;
           justify-content:flex-end}
.msg{padding:.6em 1em;font-size:13px;border-bottom:1px solid var(--line)}
.msg.err{background:rgba(229,83,75,.12);color:var(--err)}
.msg.ok{background:rgba(76,175,90,.12);color:var(--ok)}

"""

FAVICON_SVG = """\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  Sysible Baseline — canonical mark. Family tile (dark rounded square + thin
  brand-green ring, see controller.svg); the glyph says what Baseline IS: a
  declared line the fleet is held to. A brand-green baseline rule with two
  hosts sitting exactly on it, and a blue one pulled above being snapped back
  down to it — drift, corrected, which is the whole product in one picture.
-->
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" role="img" aria-label="Sysible Baseline">
  <defs>
    <linearGradient id="bl-tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#161d29"/><stop offset="1" stop-color="#0a0d14"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="124" height="124" rx="28" ry="28" fill="url(#bl-tile)"/>
  <rect x="3.5" y="3.5" width="121" height="121" rx="26.5" ry="26.5" fill="none" stroke="#6ddb73" stroke-width="2"/>
  <!-- The declared line. -->
  <line x1="28" y1="84" x2="100" y2="84" stroke="#6ddb73" stroke-width="6" stroke-linecap="round"/>
  <!-- Two hosts already on it (green). -->
  <rect x="34" y="64" width="15" height="20" rx="3" fill="#6ddb73"/>
  <rect x="56" y="64" width="15" height="20" rx="3" fill="#6ddb73"/>
  <!-- One drifted above (blue), with an arrow pulling it back to the line. -->
  <rect x="78" y="40" width="15" height="20" rx="3" fill="#7aa2ff"/>
  <path d="M85.5 66 L85.5 78" fill="none" stroke="#7aa2ff" stroke-width="5" stroke-linecap="round"/>
  <path d="M79 72 L85.5 79 L92 72" fill="none" stroke="#7aa2ff" stroke-width="5"
        stroke-linecap="round" stroke-linejoin="round"/>
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
        + body + tail + "</body></html>"
    )


def console_page(who) -> str:
    w = f"{escape(who.user)} · {escape(who.role)}"
    can_write = who.role in ("operator", "superuser")
    body = (
        "<div class=msg id=msg hidden></div>"
        "<div class=wrap>"
        "  <div class=grid id=drift></div>"

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
        "<dialog id=dlg-state><form method=dialog>"
        "<div class=dh id=dlg-state-h>New state</div><div class=db>"
        "<label class=f><span>Name</span><input id=s-name placeholder='web-servers'></label>"
        "<label class=f><span>Salt state (SLS)</span>"
        "<textarea id=s-sls spellcheck=false placeholder='nginx_installed:\n"
        "  pkg.installed:\n    - name: nginx'></textarea></label>"
        "<div class='faint' style='font-size:12px'>Saving creates a new version. "
        "Nothing is overwritten — the previous declaration stays readable.</div>"
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
        "<label class=f><span>Target</span>"
        "<input id=a-target placeholder='web-01  or  env:production'></label>"
        "<label class=f><span>State</span><select id=a-state></select></label>"
        "</div>"
        "<div class='faint' style='font-size:12px'>A host id applies to that host. "
        "<span class=mono>env:&lt;name&gt;</span> applies to every host in that "
        "environment, in addition to anything assigned to the host itself.</div>"
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
        </td></tr>`).join('') + '</table>';
}

function assignTable(rows){
  if(!rows.length) return `<div class=empty>Nothing assigned. Declaring a state does
    not apply it — assign it to a host id, or to <span class=mono>env:&lt;name&gt;</span>
    for a whole environment.</div>`;
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
    const [s, r, runs, a] = await Promise.all([
      api('states'), api('rules'), api('runs?limit=50'), api('assignments')]);
    STATE = {states: s.states, rules: r.rules, runs: runs.runs,
             drift: runs.drift, assign: a.assignments, meta: r};
    $('drift').innerHTML = drift(runs.drift);
    $('states').innerHTML = statesTable(s.states);
    $('assign').innerHTML = assignTable(a.assignments);
    $('rules').innerHTML = rulesTable(r);
    $('runs').innerHTML = runsTable(runs.runs);
  }catch(e){ say(e.message, 'err'); }
}

// ---- actions ---------------------------------------------------------------

async function openState(name){
  $('dlg-state-h').textContent = name ? `Edit ${name}` : 'New state';
  $('s-name').value = name || '';
  $('s-name').disabled = !!name;        // a rename would orphan its assignments
  $('s-sls').value = '';
  if(name){
    try{ $('s-sls').value = (await api('states/' + encodeURIComponent(name))).state.sls; }
    catch(e){ say(e.message, 'err'); return; }
  }
  $('s-sls').readOnly = !CAN_WRITE;
  $('s-save').hidden = !CAN_WRITE;
  $('dlg-state').showModal();
}

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

function openAssign(stateName){
  $('a-target').value = '';
  $('a-state').innerHTML = STATE.states.map((s) =>
    `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join('');
  if(stateName) $('a-state').value = stateName;
  $('dlg-assign').showModal();
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
  try{
    const r = await api('states/' + encodeURIComponent(name),
                        {method: 'POST', body: JSON.stringify({sls: $('s-sls').value})});
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
    await api('assign', {method: 'POST', body: JSON.stringify({
      target: $('a-target').value.trim(), state: $('a-state').value})});
    say('Assigned.', 'ok'); load();
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
