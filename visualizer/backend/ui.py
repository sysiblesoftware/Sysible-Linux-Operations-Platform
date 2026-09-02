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
/* Our own .bar/.tabs are display:flex, which OUTRANKS the user agent's
   [hidden]{display:none} — so hiding a toolbar by setting .hidden silently
   did nothing. Make the attribute win. */
[hidden]{display:none!important}
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

/* ---- Topology ---------------------------------------------------------- */
/* The Topology tab is pushed to the right of the per-app tabs: it is a view of
   the FLEET, not of one app's activity, and sitting in the same row unqualified
   made it read as a fifth app. */
.tab.topo{margin-left:auto}
.bar .seg{display:flex;gap:2px}
.bar .seg button.on{border-color:var(--accent);color:var(--text)}
.bar label.chk{display:flex;align-items:center;gap:.35em;color:var(--muted);font-size:12.5px;cursor:pointer}
.topo-card{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--panel)}
.topo-card svg{display:block;width:100%;max-height:74vh;cursor:grab;touch-action:none}
.topo-card svg.grabbing{cursor:grabbing}
.topo-legend{display:flex;gap:1.1em;flex-wrap:wrap;align-items:center;padding:.55em .9em;
border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
.topo-legend .sw{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:middle;margin-right:.35em}
.topo-legend .ring{display:inline-block;width:10px;height:10px;border-radius:50%;
border:2px solid #e06c6c;vertical-align:middle;margin-right:.35em}
.topo-legend .hint{margin-left:auto;color:var(--faint)}
"""

_JS = r"""
const $=s=>document.querySelector(s);
// Served at the app root, which behind the SLOP gateway is /visualizer/ (the
// gateway strips the prefix before we see it). Build every API URL relative to the
// page's own directory so the console works standalone AND behind the gateway.
const BASE = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
const U = p => BASE + String(p).replace(/^\//,'');
let APPS=[], cur=null, rows=[], limit=100, lastFailed=false;
const TOPO='__topology__';
// The Controller console is a sibling of this one behind the SLOP gateway
// (/controller/ next to /visualizer/), so a node click can open that host's
// posture page. Derived from our own path so it also works if the prefix moves.
const CTRL = BASE.endsWith('/visualizer/')
  ? BASE.slice(0, -('visualizer/'.length)) + 'controller/' : '/controller/';
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
  const t=el('button','tab topo','Fleet Topology');t.dataset.key=TOPO;
  t.onclick=()=>select(TOPO); $('#tabs').appendChild(t);
  select(APPS[0].key);
}
function select(key){
  cur=key;
  [...document.querySelectorAll('.tab')].forEach(t=>t.classList.toggle('sel',t.dataset.key===key));
  const isTopo = key===TOPO;
  $('#bar-activity').hidden=isTopo; $('#bar-topo').hidden=!isTopo;
  $('#body').hidden=isTopo; $('#log').hidden=isTopo; $('#topo').hidden=!isTopo;
  $('#log').innerHTML=''; $('#msgs').innerHTML='';
  topoAuto(isTopo && tauto);
  if(isTopo) topoLoad(false); else load();
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

// =========================================================================== //
// Fleet Topology — the Controller's Network Topology view, rebuilt here.
// =========================================================================== //
// A controller-centric map: the controller is the hub, managed hosts cluster
// around it grouped by ENVIRONMENT or by NETWORK segment, and a hypervisor's
// guests hang off the machine they actually run on. The server does the
// correlation (see fleet.py); this does layout, drawing and interaction.
// Two-level layout so it scales: controller -> group hub -> hosts.
const TCOLOR={OK:'#4ec07a',WARNING:'#e0a83a',CRITICAL:'#e06c6c',
              OFFLINE:'#7a7a7a',SUPPRESSED:'#6c7fa8',UNKNOWN:'#5a6270'};
const TACCENT='#3d7dd8';
const TRANK={CRITICAL:0,OFFLINE:1,WARNING:2,SUPPRESSED:3,OK:4,UNKNOWN:5};
const TW=1000, TH=660, TCX=TW/2, TCY=TH/2;

let tnodes=[], tparents={}, tcounts={}, tlens='env', tcollapsed={}, tpositions={};
let tview={s:1,tx:0,ty:0}, tauto=true, thover=null, tuserAdjusted=false, tlastFit='';
let tinflight=false, tpostureInflight=false, ttimer=null, tloaded=false;
let tdrag=null, tnodeDrag=null, tframe=null;

const tesc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ttrunc=(s,n)=>{n=n||15;s=s||'';return s.length>n?s.slice(0,n-1)+'…':s;};
function tstatus(n){
  if(n.online===false)return 'OFFLINE';
  if(n.online==null && !n.verdict)return 'UNKNOWN';
  return String(n.verdict||'OK').toUpperCase();
}
const tcolor=n=>TCOLOR[tstatus(n)]||TCOLOR.OK;
function tworst(list){let r=5;for(const h of list)r=Math.min(r,TRANK[tstatus(h)]??5);
  return Object.keys(TRANK).find(k=>TRANK[k]===r)||'OK';}

async function topoLoad(force){
  if(tinflight)return; tinflight=true;
  const st=$('#topo-status'); if(st)st.textContent='loading…';
  try{
    // Fast pass: hosts + health + agents + suppressions. The map paints from
    // this; it must not wait on the fleet posture sweep, which on a cold cache
    // can take many seconds and was what made the original view feel hung.
    const d=await jget(U('/api/topology'));
    topoApply(d); tloaded=true;
  }catch(e){
    $('#topo').innerHTML=''; $('#topo').appendChild(el('div','msg err','Could not load the fleet: '+e.message));
    tinflight=false; return;
  }
  tinflight=false;
  if(!tpostureInflight){
    // Posture is an OVERLAY only — the red critical rings and the network lens's
    // gateway labels. Fire it separately and let it fill in.
    tpostureInflight=true;
    jget(U('/api/topology?posture=1')).then(d=>{topoApply(d);},()=>{})
      .then(()=>{tpostureInflight=false;});
  }
}
function topoApply(d){
  tnodes=d.nodes||[]; tparents=d.parents||{}; tcounts=d.counts||{};
  $('#msgs').innerHTML='';
  (d.errors||[]).forEach(m=>$('#msgs').appendChild(el('div','msg err','Fleet — '+m)));
  (d.notes ||[]).forEach(m=>$('#msgs').appendChild(el('div','msg note','Fleet — '+m)));
  topoRender();
}
function topoAuto(on){
  if(ttimer){clearInterval(ttimer);ttimer=null;}
  if(on)ttimer=setInterval(()=>topoLoad(false),10000);
}

// ---- model ---------------------------------------------------------------
function topoGroups(){
  // Guests are excluded: they don't form their own group, they hang under their
  // hypervisor — otherwise an environment made entirely of one host's VMs draws
  // a second hub duplicating what is already nested under that host.
  const others=tnodes.filter(m=>!m.isController && !(m.label in tparents));
  const g={};
  others.forEach(m=>{
    let key,label;
    if(tlens==='network'){
      key=m.subnet||'unknown';
      label=m.subnet?(m.subnet+(m.gateway?(' · gw '+m.gateway):'')):'no IP / unknown';
    }else{ key=m.env; label=m.env; }
    (g[key]||(g[key]={key,label,hosts:[]})).hosts.push(m);
  });
  const list=Object.values(g);
  list.forEach(grp=>{grp.hosts.sort((a,b)=>a.label.localeCompare(b.label));grp.worst=tworst(grp.hosts);});
  list.sort((a,b)=>b.hosts.length-a.hosts.length||a.label.localeCompare(b.label));
  return list;
}
function topoLayout(){
  const groups=topoGroups(), G=groups.length||1, Rhub=200;
  const hubs=[], nodes=[], placedTop=new Map();
  groups.forEach((grp,i)=>{
    const th=-Math.PI/2+(2*Math.PI)*(i+0.5)/G;
    const rad={x:Math.cos(th),y:Math.sin(th)}, tan={x:-Math.sin(th),y:Math.cos(th)};
    const hx=TCX+Rhub*rad.x, hy=TCY+Rhub*rad.y, isC=!!tcollapsed[grp.key];
    hubs.push(Object.assign({},grp,{x:hx,y:hy,collapsed:isC}));
    if(isC)return;
    // Column spacing is set by the LABEL width, not the dot: at the Controller's
    // 42px the names of adjacent hosts ran into each other ("edge-ssh-build-1").
    // Scaling doesn't help — zoom magnifies text and spacing alike — so the grid
    // itself has to be wide enough for a truncated 15-char label.
    const cols=Math.max(1,Math.min(6,Math.ceil(Math.sqrt(grp.hosts.length)))), sp=96;
    grp.hosts.forEach((h,k)=>{
      const r=Math.floor(k/cols), c=k%cols;
      const colOff=(c-(cols-1)/2)*sp, dist=Rhub+60+r*46;
      nodes.push(Object.assign({},h,{x:TCX+rad.x*dist+tan.x*colOff,y:TCY+rad.y*dist+tan.y*colOff,hub:grp.key}));
      placedTop.set(h.label,{rad,tan,dist,colOff,hubKey:grp.key});
    });
  });
  // Guests fan out BEYOND their hypervisor, using that host's own direction, so
  // a VM reads as a subtree of the machine it runs on whatever group it is in.
  const kids=new Map();
  tnodes.forEach(m=>{
    if(m.isController||!(m.label in tparents))return;
    const hyp=tparents[m.label];
    if(!kids.has(hyp))kids.set(hyp,[]);
    kids.get(hyp).push(m);
  });
  kids.forEach((vms,hypLabel)=>{
    const pl=placedTop.get(hypLabel); if(!pl)return;   // hypervisor hidden/collapsed
    vms.sort((a,b)=>a.label.localeCompare(b.label));
    const vcols=Math.max(1,Math.min(5,Math.ceil(Math.sqrt(vms.length)))), vsp=92;
    vms.forEach((vm,k)=>{
      const r=Math.floor(k/vcols), c=k%vcols;
      const colOff=pl.colOff+(c-(vcols-1)/2)*vsp, dist=pl.dist+74+r*46;
      nodes.push(Object.assign({},vm,{x:TCX+pl.rad.x*dist+pl.tan.x*colOff,
        y:TCY+pl.rad.y*dist+pl.tan.y*colOff,hub:pl.hubKey,vmParentLabel:hypLabel}));
    });
  });
  return {hubs,nodes};
}
function topoLaid(){
  const L=topoLayout(), ctrl=tpositions.__ctrl__||{x:TCX,y:TCY};
  const hubs=L.hubs.map(h=>{const o=tpositions['__hub__'+h.key];return o?Object.assign({},h,{x:o.x,y:o.y}):h;});
  const hubBy={}; hubs.forEach(h=>{hubBy[h.key]=h;});
  const nodes=L.nodes.map(n=>{const o=tpositions[n.id];return o?Object.assign({},n,{x:o.x,y:o.y}):n;});
  // Edges are rebuilt from the FINAL positions so a dragged node keeps its
  // connectors attached.
  const edges=[];
  hubs.forEach(h=>edges.push({x1:ctrl.x,y1:ctrl.y,x2:h.x,y2:h.y,kind:'hub',worst:h.worst}));
  const byLabel={}; nodes.forEach(n=>{byLabel[n.label]=n;});
  nodes.forEach(n=>{
    const parent=n.vmParentLabel?byLabel[n.vmParentLabel]:hubBy[n.hub];
    if(parent)edges.push({x1:parent.x,y1:parent.y,x2:n.x,y2:n.y,kind:'host',host:n});
  });
  return {hubs,nodes,edges,ctrl,center:tnodes.find(m=>m.isController)||null};
}
function topoFit(laid){
  const pts=[[laid.ctrl.x,laid.ctrl.y]];
  laid.hubs.forEach(h=>pts.push([h.x,h.y]));
  laid.nodes.forEach(n=>pts.push([n.x,n.y]));
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  pts.forEach(([x,y])=>{if(x<minX)minX=x;if(x>maxX)maxX=x;if(y<minY)minY=y;if(y>maxY)maxY=y;});
  if(!isFinite(minX))return;
  minX-=80; maxX+=80; minY-=46; maxY+=66;      // labels hang below the nodes
  const cw=Math.max(1,maxX-minX), ch=Math.max(1,maxY-minY);
  const sc=Math.max(0.3,Math.min(3,Math.min(TW/cw,TH/ch)));
  tview={s:sc,tx:TW/2-sc*((minX+maxX)/2),ty:TH/2-sc*((minY+maxY)/2)};
}

// ---- draw ----------------------------------------------------------------
function topoRender(){
  const host=$('#topo'); if(!host)return;
  const laid=topoLaid();
  // Re-fit only when the STRUCTURE changes — not on every 10s value refresh,
  // and never after the user has panned or zoomed themselves.
  const sig=tlens+'|'+laid.hubs.map(h=>h.key+(h.collapsed?'c':'')).join(',')+'|'+laid.nodes.map(n=>n.id).join(',');
  if(laid.hubs.length && !tuserAdjusted && sig!==tlastFit){tlastFit=sig; topoFit(laid);}

  const c=tcounts||{};
  const st=$('#topo-status');
  if(st)st.innerHTML=(c.online||0)+' online'
    +(c.offline?' · <span style="color:'+TCOLOR.CRITICAL+'">'+c.offline+' offline</span>':'')
    +(c.critical?' · <span style="color:'+TCOLOR.CRITICAL+'">'+c.critical+' critical</span>':'');

  if(!tnodes.length){
    host.innerHTML='';
    host.appendChild(el('div','empty',tloaded?'No hosts enrolled yet.':'Loading the fleet…'));
    return;
  }
  const out=[];
  out.push('<g transform="translate('+tview.tx+' '+tview.ty+') scale('+tview.s+')">');
  laid.edges.forEach(e=>{
    const isHub=e.kind==='hub', h=e.host;
    const col=isHub?(TCOLOR[e.worst]||TCOLOR.UNKNOWN)
      :h.revoked?TCOLOR.CRITICAL:h.quarantined?TCOLOR.WARNING:tcolor(h);
    const dash=isHub?'':(h.revoked||h.quarantined)?'3 3':(h.kind==='SSH'?'5 4':'');
    const op=isHub?0.5:(h.online===false?0.22:0.5);
    out.push('<line x1="'+e.x1+'" y1="'+e.y1+'" x2="'+e.x2+'" y2="'+e.y2+'" stroke="'+col+
      '" stroke-opacity="'+op+'" stroke-width="'+(isHub?2:1.4)+'"'+(dash?' stroke-dasharray="'+dash+'"':'')+'/>');
  });
  laid.hubs.forEach(h=>{
    const col=TCOLOR[h.worst]||TCOLOR.UNKNOWN;
    out.push('<g class="tnode" data-key="__hub__'+tesc(h.key)+'" data-act="group" data-group="'+tesc(h.key)+
      '" data-x="'+h.x+'" data-y="'+h.y+'" transform="translate('+h.x+' '+h.y+')">');
    out.push('<circle r="'+(h.collapsed?18:8)+'" fill="'+(h.collapsed?col:'#1a2130')+'" stroke="'+col+'" stroke-width="2"/>');
    if(h.collapsed)out.push('<text text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="700" fill="#0d1117">'+h.hosts.length+'</text>');
    out.push('<text y="'+(h.collapsed?32:22)+'" text-anchor="middle" font-size="12.5" font-weight="600" fill="#8b93a7">'+
      tesc(ttrunc(h.label,22))+' <tspan font-weight="400">('+h.hosts.length+(h.collapsed?' ▸':' ▾')+')</tspan></text>');
    out.push('</g>');
  });
  laid.nodes.forEach(n=>{
    const hov=thover===n.id;
    const ring=n.revoked?TCOLOR.CRITICAL:n.quarantined?TCOLOR.WARNING
      :((n.hasCrit&&n.online!==false)?TCOLOR.CRITICAL:null);
    out.push('<g class="tnode" data-key="'+tesc(n.id)+'" data-act="host" data-id="'+tesc(n.id)+
      '" data-label="'+tesc(n.label)+'" data-x="'+n.x+'" data-y="'+n.y+'" transform="translate('+n.x+' '+n.y+')">');
    if(ring)out.push('<circle r="'+(hov?15:13.5)+'" fill="none" stroke="'+ring+'" stroke-width="2"'+
      ((n.revoked||n.quarantined)?' stroke-dasharray="3 3"':'')+'/>');
    out.push('<circle r="'+(hov?11:9)+'" fill="'+tcolor(n)+'" stroke="#0d1117" stroke-width="2"/>');
    if(n.kind==='Agent + SSH')out.push('<circle r="'+(hov?4.5:3.5)+'" fill="#0d1117"/>');
    if(n.hypervisor)out.push('<g transform="translate(0 -16)" stroke="#eab308" fill="none" stroke-width="1.5" stroke-linecap="round">'+
      '<rect x="-6" y="-5" width="12" height="4.5" rx="1"/><rect x="-6" y="0.5" width="12" height="4.5" rx="1"/>'+
      '<line x1="-3.5" y1="-2.75" x2="-3.4" y2="-2.75"/><line x1="-3.5" y1="2.75" x2="-3.4" y2="2.75"/></g>');
    out.push('<text y="24" text-anchor="middle" font-size="11.5" fill="#e6edf5"'+(hov?' font-weight="700"':'')+'>'+
      tesc(ttrunc(n.label))+'</text></g>');
  });
  const ct=laid.center;
  out.push('<g class="tnode" data-key="__ctrl__" data-act="host"'+
    (ct?(' data-id="'+tesc(ct.id)+'" data-label="'+tesc(ct.label)+'"'):'')+
    ' data-x="'+laid.ctrl.x+'" data-y="'+laid.ctrl.y+'" transform="translate('+laid.ctrl.x+' '+laid.ctrl.y+')">');
  out.push('<circle r="26" fill="'+TACCENT+'" stroke="#0d1117" stroke-width="3"/>');
  out.push('<path d="M-8 -3 h16 M-8 3 h16 M-5 -6 v12 M5 -6 v12" stroke="#fff" stroke-width="1.5" fill="none" opacity="0.9"/>');
  out.push('<text y="43" text-anchor="middle" font-size="13" font-weight="700" fill="#e6edf5">'+
    tesc(ct?ttrunc(ct.label,22):'Sysible Controller')+'</text>');
  out.push('<text y="58" text-anchor="middle" font-size="11" fill="#8b93a7">controller</text></g>');
  const hov=thover?(laid.nodes.find(n=>n.id===thover)||null):null;
  if(hov)out.push(topoTip(hov));
  out.push('</g>');

  host.innerHTML='<div class="topo-card"><svg id="topo-svg" viewBox="0 0 '+TW+' '+TH+'">'+out.join('')+'</svg>'+
    '<div class="topo-legend">'+
    ['OK|healthy','WARNING|warning','CRITICAL|critical','OFFLINE|offline'].map(x=>{
      const [k,l]=x.split('|');
      return '<span><span class="sw" style="background:'+TCOLOR[k]+'"></span>'+l+'</span>';}).join('')+
    '<span><span class="ring"></span>critical finding / revoked</span>'+
    '<span class="hint">solid = agent · dashed = SSH · drag a node to move it · drag the background to pan · '+
    'scroll to zoom · click a cluster to collapse · click a host to open it in the Controller</span>'+
    '</div></div>';
  topoBind();
}
function topoTip(n){
  const lines=[n.isController?'This host is the controller':(n.kind||'host'), n.env,
    n.ip?(n.ip+(n.gateway?('  · gw '+n.gateway):'')):(n.address||''),
    'status: '+tstatus(n).toLowerCase(),
    (n.disk!=null||n.mem!=null)?('disk '+(n.disk??'—')+'%  ·  mem '+(n.mem??'—')+'%'):'',
    n.hypervisor?('\u{1F5A5} '+(n.hypBadge||'')):'',
    n.hasCrit?'⚠ active critical finding':'',
    n.revoked?'⦸ agent revoked':(n.quarantined?'⚠ integrity quarantined':''),
    n.agentVersion?('agent '+n.agentVersion):''].filter(Boolean);
  const w=220, h=22+lines.length*16;
  const x=Math.max(4,Math.min(TW-w-4, n.x>TW/2 ? n.x-w-18 : n.x+18));
  const y=Math.max(4,Math.min(TH-h-4, n.y-h/2));
  return '<g transform="translate('+x+' '+y+')" pointer-events="none">'+
    '<rect width="'+w+'" height="'+h+'" rx="8" fill="#141a24" stroke="#2a3242" opacity="0.98"/>'+
    '<text x="12" y="19" font-size="13" font-weight="700" fill="#e6edf5">'+tesc(n.label)+'</text>'+
    lines.map((l,i)=>'<text x="12" y="'+(38+i*16)+'" font-size="11.5" fill="#aeb6c6">'+tesc(l)+'</text>').join('')+'</g>';
}

// ---- interaction ---------------------------------------------------------
function topoRedraw(){ if(tframe)return; tframe=requestAnimationFrame(()=>{tframe=null;topoRender();}); }
function topoBind(){
  const svg=$('#topo-svg'); if(!svg)return;
  const perPx=()=>TW/svg.getBoundingClientRect().width;
  svg.addEventListener('wheel',e=>{e.preventDefault();topoZoom(e.deltaY<0?1.12:0.89);},{passive:false});
  svg.addEventListener('mousedown',e=>{
    const g=e.target.closest('.tnode');
    if(g){ // a node press: drag it, or (if it doesn't move) treat as a click
      e.stopPropagation();
      tnodeDrag={key:g.dataset.key,x0:e.clientX,y0:e.clientY,
        ox:parseFloat(g.dataset.x),oy:parseFloat(g.dataset.y),
        act:g.dataset.act,group:g.dataset.group,id:g.dataset.id,label:g.dataset.label,moved:false};
      return;
    }
    tdrag={x:e.clientX,y:e.clientY,tx:tview.tx,ty:tview.ty}; svg.classList.add('grabbing');
  });
  svg.addEventListener('mousemove',e=>{
    if(tnodeDrag){
      const k=perPx()/tview.s;
      if(!tnodeDrag.moved && Math.hypot(e.clientX-tnodeDrag.x0,e.clientY-tnodeDrag.y0)>3)tnodeDrag.moved=true;
      if(tnodeDrag.moved){
        tpositions[tnodeDrag.key]={x:tnodeDrag.ox+(e.clientX-tnodeDrag.x0)*k,
                                   y:tnodeDrag.oy+(e.clientY-tnodeDrag.y0)*k};
        topoRedraw();
      }
      return;
    }
    if(tdrag){
      tuserAdjusted=true; const k=perPx();
      tview={s:tview.s,tx:tdrag.tx+(e.clientX-tdrag.x)*k,ty:tdrag.ty+(e.clientY-tdrag.y)*k};
      topoRedraw(); return;
    }
    const g=e.target.closest('.tnode');
    const key=(g&&g.dataset.act==='host'&&g.dataset.key!=='__ctrl__')?g.dataset.key:null;
    if(key!==thover){thover=key;topoRedraw();}
  });
  const end=()=>{
    if(tnodeDrag){
      const nd=tnodeDrag; tnodeDrag=null;
      if(!nd.moved){                      // a press that didn't move is a click
        if(nd.act==='group'){tcollapsed[nd.group]=!tcollapsed[nd.group];tlastFit='';topoRender();}
        else if(nd.id){window.open(CTRL+'?view=host&id='+encodeURIComponent(nd.id)+
          '&label='+encodeURIComponent(nd.label||''),'_blank','noopener');}
      }
      return;
    }
    tdrag=null; svg.classList.remove('grabbing');
  };
  svg.addEventListener('mouseup',end);
  svg.addEventListener('mouseleave',()=>{tnodeDrag=null;tdrag=null;svg.classList.remove('grabbing');
    if(thover){thover=null;topoRedraw();}});
}
function topoZoom(f){
  tuserAdjusted=true;
  const s=Math.max(0.4,Math.min(3,tview.s*f));
  tview={s,tx:tview.tx+(tview.s-s)*TCX,ty:tview.ty+(tview.s-s)*TCY};
  topoRedraw();
}
function topoReset(){ tuserAdjusted=false; tlastFit=''; tpositions={}; topoRender(); }
function topoSetLens(l){
  tlens=l; tlastFit='';
  [...document.querySelectorAll('#topo-lens button')].forEach(b=>b.classList.toggle('on',b.dataset.lens===l));
  topoRender();
}

document.addEventListener('DOMContentLoaded',()=>{
  $('#q').addEventListener('input',render);
  $('#refresh').addEventListener('click',load);
  $('#logbtn').addEventListener('click',showLog);
  $('#limit').addEventListener('change',e=>{limit=parseInt(e.target.value,10)||100;load();});
  [...document.querySelectorAll('#topo-lens button')].forEach(b=>
    b.addEventListener('click',()=>topoSetLens(b.dataset.lens)));
  $('#topo-collapse').addEventListener('click',()=>{
    topoGroups().forEach(g=>{tcollapsed[g.key]=true;}); tlastFit=''; topoRender();});
  $('#topo-expand').addEventListener('click',()=>{tcollapsed={};tlastFit='';topoRender();});
  $('#topo-in').addEventListener('click',()=>topoZoom(1.2));
  $('#topo-out').addEventListener('click',()=>topoZoom(0.83));
  $('#topo-fit').addEventListener('click',topoReset);
  $('#topo-auto').addEventListener('change',e=>{tauto=e.target.checked;topoAuto(tauto&&cur===TOPO);});
  $('#topo-refresh').addEventListener('click',()=>topoLoad(true));
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
        "<header class=head><div class=brand>Sysible <b>Visualizer</b> — activity, logs &amp; fleet topology</div>"
        f"<span class=who>{who}</span></header>"
        "<div class=tabs id=tabs></div>"
        "<div class=bar id=bar-activity>"
        "<input type=search id=q placeholder='Filter this app&rsquo;s activity…'>"
        "<select id=limit><option value=100>100 rows</option>"
        "<option value=250>250 rows</option><option value=500>500 rows</option></select>"
        "<button id=refresh>Refresh</button>"
        "<button id=logbtn>Log&hellip;</button>"
        "</div>"
        # Topology toolbar. The same controls the Controller view had: the two
        # lenses, cluster collapse, zoom/fit, and the 10s auto-refresh.
        "<div class=bar id=bar-topo hidden>"
        "<span class=seg id=topo-lens>"
        "<button data-lens=env class=on>Environment</button>"
        "<button data-lens=network>Network</button>"
        "</span>"
        "<span id=topo-status style='font-size:12.5px;color:var(--muted)'></span>"
        "<span style='margin-left:auto'></span>"
        "<button id=topo-collapse>Collapse all</button>"
        "<button id=topo-expand>Expand all</button>"
        "<span class=seg>"
        "<button id=topo-in title='Zoom in'>&plus;</button>"
        "<button id=topo-out title='Zoom out'>&minus;</button>"
        "<button id=topo-fit title='Fit everything to view and reset node positions'>&#10530;</button>"
        "</span>"
        "<label class=chk><input type=checkbox id=topo-auto checked> Auto</label>"
        "<button id=topo-refresh>Refresh</button>"
        "</div>"
        "<div id=msgs></div>"
        "<div class=wrap><div id=body></div><div id=log></div>"
        "<div id=topo hidden></div></div>"
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
        "<header class=head><div class=brand>Sysible <b>Visualizer</b> &mdash; activity, logs &amp; fleet topology</div></header>"
        "<div class=gate>"
        f"<h1>{escape(head)}</h1>"
        f"<div class=why>{escape(reason)}</div>"
        "<p>Visualizer has no login of its own — it takes your identity from the "
        "Sysible Operations Platform gateway. Open it from the portal tile at "
        "<code>/visualizer/</code> after signing in at <a href='/login'>/login</a>.</p>"
        f"<p><code>{escape(code)}</code></p>"
        "</div></body></html>"
    )
