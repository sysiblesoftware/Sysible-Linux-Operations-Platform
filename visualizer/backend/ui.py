"""Sysible Visualizer — the server-rendered console (dependency-free).

One page, one tab per app. Selecting an app loads that app's normalised activity
into a table; where the app exposes a log (SLEP run logs, the Controller's own
service log) a "Log" pane can be opened alongside. No build step, no framework:
the shell is server-rendered and a little vanilla fetch() JS drives the panels.
Palette matches the SLOP portal / IdP / Flashback so it reads as one product.
"""
from __future__ import annotations

from html import escape

_CSS = """
:root{--bg:#0d1117;--panel:#131923;--panel2:#1a212d;--line:#26303f;
--text:#e6edf5;--muted:#93a1b5;--faint:#6f7d92;--accent:#43a047;--accent2:#5580ee;
--ok:#4caf5a;--err:#e5534b;--amber:#e0a83b;--field:#0d1320;
--font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
:root[data-theme="light"]{--bg:#eef1f6;--panel:#fff;--panel2:#f3f5f9;--line:#dbe1ea;
--text:#1b2431;--muted:#5b6675;--faint:#8794a4;--accent:#2f8a37;--accent2:#2f6fe0;
--field:#fff}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px}
.head{display:flex;align-items:center;gap:.6em;padding:.7em 1em;border-bottom:1px solid var(--line);
background:var(--panel);position:sticky;top:0;z-index:5}
.head .brand{font-size:16px}.head .brand b{color:var(--accent)}
.head .who{margin-left:auto;color:var(--muted);font-size:12.5px}
.tabs{display:flex;gap:.3em;padding:.6em 1em 0;border-bottom:1px solid var(--line);background:var(--panel);
flex-wrap:wrap}
.tab{border:1px solid transparent;border-bottom:none;background:none;color:var(--muted);
padding:.5em .9em;border-radius:8px 8px 0 0;cursor:pointer;font-family:inherit;font-size:13px}
.tab:hover{color:var(--text)}
.tab.sel{background:var(--bg);border-color:var(--line);color:var(--text);font-weight:600}
.tab .cnt{color:var(--faint);font-size:11.5px;margin-left:.4em}
.bar{display:flex;gap:.6em;align-items:center;flex-wrap:wrap;padding:.7em 1em .2em}
.bar input[type=search]{flex:1;min-width:160px;padding:.45em .7em;border-radius:8px;
border:1px solid var(--line);background:var(--field);color:var(--text);font-family:inherit;font-size:13px}
.bar select,.bar button{border:1px solid var(--line);background:var(--panel2);color:var(--text);
padding:.45em .7em;border-radius:8px;font-family:inherit;font-size:12.5px;cursor:pointer}
.bar button:hover{border-color:var(--accent)}
.wrap{padding:.6em 1em 2em}
/* table-layout:fixed is load-bearing. A single Controller row can carry a
   multi-kilobyte fleet-posture script in Detail; with auto layout that one cell
   sets the column width and squeezes When/Who/Action into unreadable slivers. */
table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}
th{text-align:left;color:var(--faint);font-size:11px;letter-spacing:.06em;text-transform:uppercase;
padding:.5em .6em;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg)}
td{padding:.5em .6em;border-bottom:1px solid var(--line);vertical-align:top}
tr:hover td{background:var(--panel2)}
th.ts,td.ts{width:11.5em;white-space:nowrap;color:var(--muted);font-size:12.5px}
th.actor,td.actor{width:9em;overflow-wrap:anywhere}
th.act,td.act{width:11em;font-weight:600;overflow-wrap:anywhere}
th.tgt,td.tgt{width:11em;overflow-wrap:anywhere}
td.detail{font-family:var(--mono);font-size:12px;color:var(--muted);overflow-wrap:anywhere}
/* Long detail is CLAMPED to a few lines, not truncated: nothing is lost, the row
   just stays scannable until you open it. */
td.detail .clip{white-space:pre-wrap;display:-webkit-box;-webkit-line-clamp:3;
-webkit-box-orient:vertical;overflow:hidden}
td.detail.open .clip{display:block;max-height:24em;overflow:auto;
background:var(--field);border:1px solid var(--line);border-radius:8px;padding:.5em .6em}
td.detail .more{margin-top:.35em;border:1px solid var(--line);background:var(--panel2);
color:var(--muted);font-family:inherit;font-size:11px;padding:.1em .55em;border-radius:20px;cursor:pointer}
td.detail .more:hover{color:var(--text);border-color:var(--accent)}
.pill{display:inline-block;padding:.05em .5em;border-radius:20px;font-size:11px;border:1px solid var(--line);color:var(--muted)}
.msg{margin:.5em 1em;padding:.55em .8em;border-radius:8px;font-size:12.5px}
.msg.err{background:rgba(229,83,75,.12);color:var(--err)}
.msg.note{background:rgba(224,168,59,.12);color:var(--amber)}
.empty{color:var(--muted);padding:1.4em .6em}
pre.log{margin:.6em 0 0;padding:.6em .8em;background:var(--field);border:1px solid var(--line);
border-radius:10px;overflow:auto;font-family:var(--mono);font-size:12.5px;line-height:1.5;max-height:55vh}
"""

_JS = r"""
const $=s=>document.querySelector(s);
// Served at the app root, which behind the SLOP gateway is /visualizer/ (the
// gateway strips the prefix before we see it). Build every API URL relative to the
// page's own directory so the console works standalone AND behind the gateway.
const BASE = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
const U = p => BASE + String(p).replace(/^\//,'');
let APPS=[], cur=null, rows=[], limit=100, lastFailed=false;
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e;}
function fmt(t){if(!t)return '—';const d=new Date(t*1000);return d.toLocaleString();}
async function jget(u){const r=await fetch(u,{cache:'no-store'});if(!r.ok)throw new Error(await r.text());return r.json();}

async function boot(){
  try{APPS=(await jget(U('/api/apps'))).apps;}catch(e){$('#tabs').appendChild(el('div','empty','Not signed in.'));return;}
  APPS.forEach((a,i)=>{
    const b=el('button','tab',a.label);b.dataset.key=a.key;
    b.onclick=()=>select(a.key);
    $('#tabs').appendChild(b);
    if(i===0)b.classList.add('sel');
  });
  select(APPS[0].key);
}
function select(key){
  cur=key;
  [...document.querySelectorAll('.tab')].forEach(t=>t.classList.toggle('sel',t.dataset.key===key));
  $('#log').innerHTML=''; load();
}
async function load(){
  const body=$('#body'); body.innerHTML=''; $('#msgs').innerHTML='';
  body.appendChild(el('div','empty','Loading…'));
  let d;
  try{ d=await jget(U('/api/activity?app='+encodeURIComponent(cur)+'&limit='+limit)); }
  catch(e){ body.innerHTML=''; body.appendChild(el('div','msg err','Failed to load: '+e.message)); return; }
  (d.errors||[]).forEach(m=>$('#msgs').appendChild(el('div','msg err', d.label+' — '+m)));
  (d.notes ||[]).forEach(m=>$('#msgs').appendChild(el('div','msg note', d.label+' — '+m)));
  rows=d.events||[];
  lastFailed=(d.errors||[]).length>0;
  const tab=document.querySelector('.tab[data-key="'+cur+'"]');
  if(tab && !tab.querySelector('.cnt')){const s=el('span','cnt','');tab.appendChild(s);}
  if(tab)tab.querySelector('.cnt').textContent=rows.length?('· '+rows.length):'';
  render();
}
// Detail is whatever the owning app recorded, and some of it is enormous — a
// Controller fleet-posture run stores the entire several-kilobyte shell script it
// ran. Dumping that raw makes the table unreadable, and truncating it throws away
// the thing an operator opened the console to read. So: clamp to three lines, with
// a toggle that opens the full text in place (scrollable, never re-fetched).
const CLAMP_AT = 240;
function detailCell(text){
  const td=el('td','detail');
  if(!text) return td;
  td.appendChild(el('div','clip',text));
  if(text.length>CLAMP_AT){
    const b=el('button','more','more');
    b.setAttribute('aria-expanded','false');
    b.onclick=()=>{const open=td.classList.toggle('open');
      b.textContent=open?'less':'more'; b.setAttribute('aria-expanded',String(open));};
    td.appendChild(b);
  }
  return td;
}
function render(){
  const q=($('#q').value||'').toLowerCase();
  const body=$('#body'); body.innerHTML='';
  const list=rows.filter(r=>!q || (r.actor+' '+r.action+' '+r.target+' '+r.detail).toLowerCase().includes(q));
  if(!list.length){
    // Distinguish "this app has recorded nothing" from "we could not read it" —
    // the same blank table otherwise reads as a quiet, healthy fleet.
    const why = rows.length ? 'No rows match that filter.'
              : lastFailed  ? 'Could not read this app’s activity — see the message above.'
                            : 'No activity recorded yet.';
    body.appendChild(el('div','empty', why)); return;
  }
  const t=el('table'); const thead=el('thead'); const tr=el('tr');
  [['When','ts'],['Who','actor'],['Action','act'],['Target','tgt'],['Detail','detail']]
    .forEach(([h,c])=>tr.appendChild(el('th',c,h)));
  thead.appendChild(tr); t.appendChild(thead);
  const tb=el('tbody');
  list.forEach(r=>{
    const row=el('tr');
    row.appendChild(el('td','ts',fmt(r.ts)));
    row.appendChild(el('td','actor',r.actor||'—'));
    row.appendChild(el('td','act',r.action||'—'));
    row.appendChild(el('td','tgt',r.target||''));
    row.appendChild(detailCell(r.detail||''));
    tb.appendChild(row);
  });
  t.appendChild(tb); body.appendChild(t);
}
async function showLog(){
  const pane=$('#log'); pane.innerHTML='';
  let ref='';
  if(cur==='slep'){
    ref=prompt('SLEP run id to fetch the log for:');
    if(!ref)return;
  }
  pane.appendChild(el('div','empty','Loading log…'));
  try{
    const r=await fetch(U('/api/log?app='+encodeURIComponent(cur)+'&ref='+encodeURIComponent(ref)),{cache:'no-store'});
    const text=await r.text();
    pane.innerHTML='';
    if(!r.ok){pane.appendChild(el('div','msg err','No log: '+text));return;}
    const pre=el('pre','log'); pre.textContent=text||'(empty)'; pane.appendChild(pre);
  }catch(e){pane.innerHTML=''; pane.appendChild(el('div','msg err','No log: '+e.message));}
}
document.addEventListener('DOMContentLoaded',()=>{
  $('#q').addEventListener('input',render);
  $('#refresh').addEventListener('click',load);
  $('#logbtn').addEventListener('click',showLog);
  $('#limit').addEventListener('change',e=>{limit=parseInt(e.target.value,10)||100;load();});
  boot();
});
"""


def page(user: str, role: str) -> str:
    who = f"{escape(user)} · {escape(role)}" if user else ""
    return (
        "<!doctype html><html lang=en data-theme=dark><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Sysible Visualizer</title>"
        "<script>try{var t=localStorage.getItem('slop-theme');"
        "if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';"
        "document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>"
        f"<style>{_CSS}</style></head><body>"
        "<header class=head><div class=brand>Sysible <b>Visualizer</b> — activity &amp; logs</div>"
        f"<span class=who>{who}</span></header>"
        "<div class=tabs id=tabs></div>"
        "<div class=bar>"
        "<input type=search id=q placeholder='Filter this app&rsquo;s activity…'>"
        "<select id=limit><option value=100>100 rows</option>"
        "<option value=250>250 rows</option><option value=500>500 rows</option></select>"
        "<button id=refresh>Refresh</button>"
        "<button id=logbtn>Log&hellip;</button>"
        "</div>"
        "<div id=msgs></div>"
        "<div class=wrap><div id=body></div><div id=log></div></div>"
        f"<script>{_JS}</script>"
        "</body></html>"
    )


def denied_page(reason: str, code: str, status: int = 401) -> str:
    """What a BROWSER gets instead of raw `{"detail":"Not signed in."}` — the same
    self-describing refusal Flashback serves. `reason` comes from
    identity.deny_reason() and never contains a secret."""
    head = "Not signed in" if status == 401 else "Not permitted"
    return (
        "<!doctype html><html lang=en data-theme=dark><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Sysible Visualizer &mdash; " + escape(head) + "</title>"
        "<script>try{var t=localStorage.getItem('slop-theme');"
        "if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';"
        "document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>"
        f"<style>{_CSS}"
        ".gate{max-width:44em;margin:12vh auto;padding:0 1.2em}"
        ".gate h1{font-size:20px;margin:0 0 .5em}"
        ".gate p{color:var(--muted);line-height:1.6;margin:.6em 0}"
        ".gate .why{background:var(--panel);border:1px solid var(--line);border-radius:10px;"
        "padding:.9em 1.1em;color:var(--text)}"
        ".gate code{font-family:var(--mono);font-size:12.5px;color:var(--accent2)}"
        ".gate a{color:var(--accent)}"
        "</style></head><body>"
        "<header class=head><div class=brand>Sysible <b>Visualizer</b> &mdash; activity &amp; logs</div></header>"
        "<div class=gate>"
        f"<h1>{escape(head)}</h1>"
        f"<div class=why>{escape(reason)}</div>"
        "<p>Visualizer has no login of its own — it takes your identity from the "
        "Sysible Operations Platform gateway. Open it from the portal tile at "
        "<code>/visualizer/</code> after signing in at <a href='/login'>/login</a>.</p>"
        f"<p><code>{escape(code)}</code></p>"
        "</div></body></html>"
    )
