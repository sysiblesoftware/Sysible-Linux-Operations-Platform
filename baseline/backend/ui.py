"""Sysible Baseline — the server-rendered console (dependency-free).

Same shape as the other SLOP modules: one page, no build step, no framework.
The console's job is to answer three questions an operator actually has —
what is declared, what drifted, and what fires when it does — in that order.
"""

from __future__ import annotations

from html import escape

FAVICON_SVG = """\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  Sysible Baseline — canonical mark. Family tile (dark rounded square + thin
  brand-green ring, see controller.svg); the glyph says what Baseline IS: a
  declared line that the fleet is held to. A level baseline rule with three
  hosts sitting exactly on it, one pulled off and snapped back.
-->
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" role="img" aria-label="Sysible Baseline">
  <defs>
    <linearGradient id="bl-tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1c2b25"/><stop offset="1" stop-color="#0d1512"/>
    </linearGradient>
  </defs>
  <rect x="4" y="4" width="120" height="120" rx="26" fill="url(#bl-tile)"/>
  <rect x="4.75" y="4.75" width="118.5" height="118.5" rx="25.25" fill="none"
        stroke="#1d9f6c" stroke-opacity=".55" stroke-width="1.5"/>
  <line x1="26" y1="78" x2="102" y2="78" stroke="#3fd39a" stroke-width="5" stroke-linecap="round"/>
  <rect x="34" y="58" width="16" height="20" rx="3" fill="#3fd39a"/>
  <rect x="56" y="58" width="16" height="20" rx="3" fill="#3fd39a"/>
  <rect x="78" y="44" width="16" height="34" rx="3" fill="#6aa8ff"/>
  <path d="M86 40 l0 -12 M80 34 l6 -6 6 6" fill="none" stroke="#6aa8ff"
        stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

_CSS = """
:root{--bg:#0d1512;--fg:#e8f0ec;--mut:#9fb3aa;--ok:#3fd39a;--warn:#e0a83b;
      --err:#e5534b;--line:#1f2d28;--card:#121c18;--accent:#1d9f6c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.head{display:flex;align-items:center;justify-content:space-between;gap:16px;
      padding:14px 20px;border-bottom:1px solid var(--line)}
.brand{font-size:16px}.brand b{color:var(--ok)}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
h2{font-size:14px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);
   margin:28px 0 10px;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.sp{display:flex;justify-content:space-between;align-items:center;gap:12px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.mut{color:var(--mut)}.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--err)}
.pill{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:12px;color:var(--mut);font-weight:600;padding:6px 8px;
   border-bottom:1px solid var(--line)}
td{padding:6px 8px;border-bottom:1px solid var(--line);font-size:13.5px}
.empty{color:var(--mut);padding:10px 2px;font-size:13.5px}
a{color:var(--ok)}
"""


def _head(title: str) -> str:
    return ("<!doctype html><html lang=en data-theme=dark><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title>"
            "<link rel=icon type=image/svg+xml href=favicon.svg>"
            f"<style>{_CSS}</style></head><body>")


def console_page(who) -> str:
    return (
        _head("Sysible Baseline")
        + "<header class=head><div class=brand>Sysible <b>Baseline</b> — desired-state "
          "configuration</div>"
          f"<div class=mut>{escape(who.user)} · {escape(who.role)}</div></header>"
          "<div class=wrap>"
          "<div class=card id=drift><div class=mut>Loading fleet state…</div></div>"
          "<h2>Declared states</h2><div id=states class=card><div class=empty>…</div></div>"
          "<h2>After a state is applied</h2><div id=rules class=card><div class=empty>…</div></div>"
          "<h2>Recent runs</h2><div id=runs class=card><div class=empty>…</div></div>"
          "</div>"
          "<script>" + _JS + "</script></body></html>"
    )


def refusal_page(code: str, why: str) -> str:
    """Say WHICH of the three ways this can fail happened. 'Not signed in.' on a
    page reached through a gateway that is meant to sign you in is a dead end."""
    return (
        _head("Sysible Baseline — not signed in")
        + "<header class=head><div class=brand>Sysible <b>Baseline</b> — desired-state "
          "configuration</div></header><div class=wrap><div class=card>"
          f"<p>{escape(why)}</p>"
          "<p class=mut>Baseline has no login of its own — it takes your identity from "
          "the SLOP gateway. Open it at <code>/baseline/</code> after signing in at "
          "<a href='/login'>/login</a>.</p>"
          f"<p class=mut>Reason code: <span class=mono>{escape(code)}</span></p>"
          "</div></div></body></html>"
    )


# The console is read-mostly: everything it shows comes from the four API routes.
# Served under /baseline/ with the prefix stripped, so every call is relative.
_JS = r"""
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g,
  (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function get(p){ const r = await fetch('api/'+p, {headers:{accept:'application/json'}});
  if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || r.statusText);
  return r.json(); }

function drift(d){
  const n = d.drifted.length, f = d.failed.length;
  return `<div class=sp><div><b style="font-size:22px">${d.clean}</b>
      <span class=mut>host(s) as declared</span></div>
    <div class=row>
      <span class="pill ${n?'warn':'mut'}">${n} drifted</span>
      <span class="pill ${f?'err':'mut'}">${f} failing</span>
    </div></div>` +
    (n ? `<div class="mut mono" style="margin-top:8px">drifted: ${d.drifted.map(esc).join(', ')}</div>` : '') +
    (f ? `<div class="err mono" style="margin-top:4px">failing: ${d.failed.map(esc).join(', ')}</div>` : '');
}

function statesTable(rows){
  if(!rows.length) return '<div class=empty>No state declared yet. A host with no '
    + 'assigned state is not managed by Baseline — it is simply not looked at.</div>';
  return '<table><tr><th>State</th><th>Version</th></tr>' + rows.map((s) =>
    `<tr><td class=mono>${esc(s.name)}</td><td class=mut>v${s.version}</td></tr>`).join('') + '</table>';
}

function rulesTable(d){
  if(!d.rules.length) return '<div class=empty>No rules. A state will be applied and '
    + 'nothing will follow it — no restart, no capture, no alert.</div>';
  return '<table><tr><th>Rule</th><th>Does</th><th></th></tr>' + d.rules.map((r) =>
    `<tr><td class=mono>${esc(r.name)}</td><td>${esc(r.describes)}</td>
     <td class=mut>${r.enabled ? '' : 'disabled'}</td></tr>`).join('') + '</table>';
}

function runsTable(rows){
  if(!rows.length) return '<div class=empty>Nothing applied yet.</div>';
  return '<table><tr><th>Host</th><th>State</th><th>Result</th><th>Fired</th></tr>' +
    rows.slice(0,25).map((r) => {
      const cls = r.error || r.failed ? 'err' : r.changed ? 'warn' : 'ok';
      const what = r.error ? esc(r.error)
        : r.failed ? `${r.failed} failed`
        : r.changed ? `${r.changed} changed` : 'already as declared';
      return `<tr><td class=mono>${esc(r.host_id)}</td>
        <td class=mono>${esc(r.state_name)} <span class=mut>v${r.state_version}</span>
        ${r.preview ? '<span class="pill mut">preview</span>' : ''}</td>
        <td class=${cls}>${what}</td>
        <td class=mut>${(r.actions||[]).map((a)=>esc(a.rule)).join(', ') || '—'}</td></tr>`;
    }).join('') + '</table>';
}

async function load(){
  try{
    const [s, r, runs] = await Promise.all([get('states'), get('rules'), get('runs?limit=50')]);
    $('drift').innerHTML = drift(runs.drift);
    $('states').innerHTML = statesTable(s.states);
    $('rules').innerHTML = rulesTable(r);
    $('runs').innerHTML = runsTable(runs.runs);
  }catch(e){ $('drift').innerHTML = '<div class=err>'+esc(e.message)+'</div>'; }
}
load();
// Poll only while the tab is visible: one request per app every 20s in every
// tab left open, forever, is work the gateway and this service did for nobody.
let t = setInterval(load, 20000);
document.addEventListener('visibilitychange', () => {
  clearInterval(t);
  if(!document.hidden){ load(); t = setInterval(load, 20000); }
});
"""
