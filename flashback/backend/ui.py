"""Sysible Flashback — the server-rendered console (dependency-free).

One HTML page whose left rail lists hosts; picking a host lists its tracked files,
picking a file shows its version timeline, and picking two versions renders a
unified diff. Operators can download any version or restore one (queued for the
host's agent). No build step, no framework — the shell is server-rendered and a
little vanilla fetch() JS drives the panels against /api/*. The palette matches the
SLOP portal / IdP so it reads as one product.
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
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px}
a{color:var(--accent2);text-decoration:none}
.head{display:flex;align-items:center;gap:.6em;padding:.7em 1em;border-bottom:1px solid var(--line);
background:var(--panel);position:sticky;top:0;z-index:5}
.head .brand{font-size:16px}.head .brand b{color:var(--accent)}
.head .who{margin-left:auto;color:var(--muted);font-size:12.5px}
.head a.back{color:var(--muted);font-size:12.5px}
.wrap{display:grid;grid-template-columns:220px 260px 240px 1fr;gap:0;height:calc(100vh - 49px)}
@media(max-width:900px){.wrap{grid-template-columns:1fr;height:auto}.col{max-height:40vh}}
.col{overflow:auto;border-right:1px solid var(--line);padding:.5em}
.col:last-child{border-right:none}
.col h3{margin:.3em .4em .6em;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--faint)}
.item{display:block;width:100%;text-align:left;border:1px solid transparent;background:none;color:var(--text);
padding:.5em .6em;border-radius:8px;cursor:pointer;font-size:13px;font-family:inherit}
.item:hover{background:var(--panel2)}
.item.sel{background:var(--panel2);border-color:var(--line)}
.item .sub{color:var(--muted);font-size:11.5px}
.item .mono{font-family:var(--mono);font-size:12px}
.ver{display:flex;align-items:center;gap:.5em;justify-content:space-between}
.ver .pick{font-size:11px;color:var(--faint)}
.badge{display:inline-block;padding:.05em .5em;border-radius:20px;font-size:11px;border:1px solid var(--line);color:var(--muted)}
.diffwrap{padding:.4em .6em}
.toolbar{display:flex;gap:.5em;align-items:center;flex-wrap:wrap;margin:.2em 0 .7em}
button.btn{border:1px solid var(--line);background:var(--panel2);color:var(--text);padding:.45em .8em;
border-radius:8px;cursor:pointer;font-family:inherit;font-size:12.5px}
button.btn:hover{border-color:var(--accent)}
button.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.btn:disabled{opacity:.5;cursor:not-allowed}
pre.diff{margin:0;padding:.6em .8em;background:var(--field);border:1px solid var(--line);border-radius:10px;
overflow:auto;font-family:var(--mono);font-size:12.5px;line-height:1.5;max-height:calc(100vh - 220px)}
pre.diff .a{color:var(--err)}pre.diff .d{color:var(--ok)}pre.diff .h{color:var(--accent2)}
.empty{color:var(--muted);padding:1em .6em}
.msg{margin:.4em .6em;padding:.5em .7em;border-radius:8px;font-size:12.5px}
.msg.ok{background:rgba(76,175,90,.12);color:var(--ok)}
.msg.err{background:rgba(229,83,75,.12);color:var(--err)}
.foot{color:var(--faint);font-size:11px;padding:.5em 1em;border-top:1px solid var(--line)}
"""

# The interactive layer: plain fetch() against /api/*. No framework. XSS-safe —
# every dynamic string is inserted with textContent, never innerHTML.
_CSS += """
.item.pending .sub{color:var(--faint);font-style:italic}
.note{margin:6px 8px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;
  color:var(--muted);font-size:12px}
"""

_JS = r"""
const $=s=>document.querySelector(s);
const CAN_WRITE = document.body.dataset.canWrite === '1';
// The console is served at the app root, but behind the SLOP gateway that root is
// /flashback/ (the gateway strips the prefix before the app sees it). So every API
// URL is built relative to the page's own directory: standalone -> /api/..., behind
// the gateway -> /flashback/api/..., with no build-time base to configure.
const BASE = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
const U = p => BASE + String(p).replace(/^\//,'');
let state={host:null,path:null,a:null,b:null};
function fmtTs(t){if(!t)return '—';const d=new Date(t*1000);return d.toLocaleString();}
function el(tag,cls,txt){const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e;}
async function jget(u){const r=await fetch(u,{cache:'no-store'});if(!r.ok)throw new Error(await r.text());return r.json();}
function setMsg(t,kind){const m=$('#msg');m.textContent=t||'';m.className='msg '+(kind||'');m.hidden=!t;}

async function loadHosts(){
  const col=$('#hosts');col.innerHTML='';col.appendChild(el('h3',null,'Hosts'));
  let d;try{d=await jget(U('/api/hosts'));}catch(e){col.appendChild(el('div','empty','Failed to load.'));return;}
  const hosts=(d&&d.hosts)||[];
  // Why the fleet might be missing, when it is. Without this the empty state
  // could not distinguish "not wired up" from "nothing captured yet".
  if(d&&d.note){const n=el('div','note',d.note);col.appendChild(n);}
  if(!hosts.length){col.appendChild(el('div','empty',
    'No hosts. Enrolled agent hosts appear here as soon as the Controller lists them.'));return;}
  hosts.forEach(h=>{
    const b=el('button','item');
    b.appendChild(el('div','', h.label||h.host_id));
    // A host with no history yet says so plainly, rather than "0 files · never",
    // which reads like a fault. It is still clickable — the empty file list then
    // explains that capture has not run.
    b.appendChild(el('div','sub', h.backed_up
      ? (h.files+' files · '+h.versions+' versions · '+fmtTs(h.last_ts))
      : 'enrolled — no backup captured yet'));
    if(!h.backed_up) b.classList.add('pending');
    b.onclick=()=>{state.host=h.host_id;state.path=null;state.a=null;state.b=null;
      [...col.children].forEach(c=>c.classList&&c.classList.remove('sel'));b.classList.add('sel');
      loadFiles();$('#diff').innerHTML='';};
    col.appendChild(b);
  });
}
async function loadFiles(){
  const col=$('#files');col.innerHTML='';col.appendChild(el('h3',null,'Files'));
  if(!state.host)return;
  let files;try{files=await jget(U('/api/hosts/'+encodeURIComponent(state.host)+'/files'));}catch(e){return;}
  if(!files.length){col.appendChild(el('div','empty','No files for this host.'));return;}
  files.forEach(f=>{
    const b=el('button','item');
    const p=el('div','mono');p.textContent=f.path;b.appendChild(p);
    b.appendChild(el('div','sub', f.versions+' versions · last '+fmtTs(f.last_ts)));
    b.onclick=()=>{state.path=f.path;state.a=null;state.b=null;
      [...col.children].forEach(c=>c.classList&&c.classList.remove('sel'));b.classList.add('sel');
      loadVersions();$('#diff').innerHTML='';};
    col.appendChild(b);
  });
}
async function loadVersions(){
  const col=$('#versions');col.innerHTML='';col.appendChild(el('h3',null,'Versions'));
  if(!state.host||!state.path)return;
  let vers;try{vers=await jget(U('/api/hosts/'+encodeURIComponent(state.host)+'/versions?path='+encodeURIComponent(state.path)));}catch(e){return;}
  if(!vers.length){col.appendChild(el('div','empty','No versions.'));return;}
  vers.forEach((v,i)=>{
    const b=el('button','item');const row=el('div','ver');
    row.appendChild(el('span','', fmtTs(v.captured_at)));
    row.appendChild(el('span','pick', v.size+' B · '+v.sha256.slice(0,10)));
    b.appendChild(row);
    b.onclick=()=>selectVersion(v,b,col);
    if(i===0)b.appendChild(el('div','sub','current (newest)'));
    col.appendChild(b);
  });
}
function selectVersion(v,b,col){
  // First click sets B (compare-to / newest), second sets A (older). Then diff.
  if(!state.b){state.b=v.sha256;}
  else if(!state.a){state.a=v.sha256;}
  else {state.b=v.sha256;state.a=null;}
  [...col.children].forEach(c=>c.classList&&c.classList.remove('sel'));
  b.classList.add('sel');
  renderDetail(v);
}
async function renderDetail(v){
  const d=$('#diff');d.innerHTML='';
  const bar=el('div','toolbar');
  const dl=el('button','btn','Download this version');
  dl.onclick=()=>{window.location=U('/api/hosts/'+encodeURIComponent(state.host)+'/download?path='+encodeURIComponent(state.path)+'&sha='+encodeURIComponent(v.sha256));};
  bar.appendChild(dl);
  if(CAN_WRITE){
    const rb=el('button','btn primary','Restore this version');
    rb.onclick=()=>doRestore(v.sha256);
    bar.appendChild(rb);
  }
  const info=el('span','badge', state.a&&state.b?'diff: '+state.a.slice(0,8)+' → '+state.b.slice(0,8):'pick a second version to diff');
  bar.appendChild(info);
  d.appendChild(bar);
  if(state.a&&state.b){
    let diff;try{diff=await jget(U('/api/hosts/'+encodeURIComponent(state.host)+'/diff?path='+encodeURIComponent(state.path)+'&a='+encodeURIComponent(state.a)+'&b='+encodeURIComponent(state.b)));}catch(e){d.appendChild(el('div','empty','Diff failed.'));return;}
    const pre=el('pre','diff');
    (diff.diff||'').split('\n').forEach(line=>{
      const span=el('span',null,line+'\n');
      if(line.startsWith('+'))span.className='d';else if(line.startsWith('-'))span.className='a';else if(line.startsWith('@@'))span.className='h';
      pre.appendChild(span);
    });
    if(!(diff.diff||'').trim())pre.textContent='(identical)';
    d.appendChild(pre);
  }
}
async function doRestore(sha){
  if(!confirm('Queue a restore of this version to '+state.host+'? The host agent will write it back on its next check-in (backing up the current file first).'))return;
  setMsg('','');
  try{
    const r=await fetch(U('/api/hosts/'+encodeURIComponent(state.host)+'/restore'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:state.path,sha:sha})});
    if(!r.ok){setMsg('Restore failed: '+(await r.text()),'err');return;}
    const j=await r.json();setMsg('Restore #'+j.id+' queued for '+state.host+'.', 'ok');
  }catch(e){setMsg('Restore failed.','err');}
}
loadHosts();
"""


# --------------------------------------------------------------------------- #
# Browser-tab icon.
#
# The tab for this console came up BLANK next to the portal's and Connect's,
# because nothing here ever declared an icon: with no <link rel=icon> a browser
# falls back to /favicon.ico at the ORIGIN ROOT, which behind the SLOP gateway is
# the portal's path, not ours. The mark is the canonical one from
# portal/marks/flashback.svg — but the portal is not in this image's Docker build
# context (the Dockerfile copies only backend/), so it is embedded here rather
# than shared by file. If the mark changes there, re-copy it here; the family
# rules live in portal/marks/README.md.
FAVICON_SVG = """\
<?xml version="1.0" encoding="UTF-8"?>
<!--
  Sysible Flashback — canonical mark. Family tile (dark rounded square + thin
  brand-green ring, see controller.svg); the glyph says what Flashback IS: a
  time machine for config. A brand-green clock face whose hand points BACK,
  wrapped by a blue counter-clockwise arrow — rewind, not just history.
-->
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" role="img" aria-label="Sysible Flashback">
  <defs>
    <linearGradient id="fb-tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#161d29"/><stop offset="1" stop-color="#0a0d14"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="124" height="124" rx="28" ry="28" fill="url(#fb-tile)"/>
  <rect x="3.5" y="3.5" width="121" height="121" rx="26.5" ry="26.5" fill="none" stroke="#6ddb73" stroke-width="2"/>
  <!-- Counter-clockwise rewind arc (blue), open at the top-left. -->
  <path d="M40 47 A 30 30 0 1 1 34 64" fill="none" stroke="#7aa2ff" stroke-width="6.5"
        stroke-linecap="round"/>
  <path d="M24 50 L34 64 L48 55" fill="none" stroke="#7aa2ff" stroke-width="6.5"
        stroke-linecap="round" stroke-linejoin="round"/>
  <!-- Clock hands (green): pointing back. -->
  <g stroke="#6ddb73" stroke-width="6" stroke-linecap="round">
    <line x1="64" y1="64" x2="64" y2="48"/>
    <line x1="64" y1="64" x2="79" y2="70"/>
  </g>
  <circle cx="64" cy="64" r="4.5" fill="#6ddb73"/>
</svg>"""


def page(user: str, role: str, can_write: bool) -> str:
    who = f"{escape(user)} · {escape(role)}" if user else ""
    return (
        "<!doctype html><html lang=en data-theme=dark><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<link rel=icon type='image/svg+xml' href='favicon.svg'>"
        "<title>Sysible Flashback</title>"
        "<script>try{var t=localStorage.getItem('slop-theme');"
        "if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';"
        "document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>"
        f"<style>{_CSS}</style></head>"
        f"<body data-can-write='{'1' if can_write else '0'}'>"
        "<header class=head><div class=brand>Sysible <b>Flashback</b> — config time machine</div>"
        f"<span class=who>{who}</span></header>"
        "<div class=msg id=msg hidden></div>"
        "<div class=wrap>"
        "<div class=col id=hosts></div>"
        "<div class=col id=files></div>"
        "<div class=col id=versions></div>"
        "<div class=col id=diff></div>"
        "</div>"
        f"<script>{_JS}</script>"
        "</body></html>"
    )


def denied_page(reason: str, code: str, status: int = 401) -> str:
    """The page a BROWSER gets instead of raw `{"detail":"Not signed in."}`.

    Landing on a bare JSON error is a dead end: from the portal tile it just looks
    like "Flashback does nothing". This says what happened, which of the three
    wiring faults caused it, and where to go next. `reason` is generated by
    identity.deny_reason() and never contains a secret.
    """
    head = "Not signed in" if status == 401 else "Not permitted"
    return (
        "<!doctype html><html lang=en data-theme=dark><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<link rel=icon type='image/svg+xml' href='favicon.svg'>"
        "<title>Sysible Flashback — " + escape(head) + "</title>"
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
        "<header class=head><div class=brand>Sysible <b>Flashback</b> — config time machine</div></header>"
        "<div class=gate>"
        f"<h1>{escape(head)}</h1>"
        f"<div class=why>{escape(reason)}</div>"
        "<p>Flashback has no login of its own — it takes your identity from the "
        "Sysible Linux Operations Platform gateway. Open it from the portal tile at "
        "<code>/flashback/</code> after signing in at <a href='/login'>/login</a>.</p>"
        f"<p><code>{escape(code)}</code></p>"
        "</div></body></html>"
    )
