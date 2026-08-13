"""The single-page GUI served by app.py. Plain HTML/CSS/JS, no external
assets, so it works offline and needs no build step."""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WNBA DFS Optimizer</title>
<style>
  :root{
    --bg:#0e1116; --panel:#171b22; --panel2:#1e232c; --line:#2a313c;
    --text:#e6e9ee; --muted:#9aa4b2; --accent:#ff5a3c; --accent2:#3ca0ff;
    --good:#39d98a; --chip:#232935;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  header{padding:22px 28px;border-bottom:1px solid var(--line);display:flex;
    align-items:center;gap:14px}
  header h1{font-size:19px;margin:0;letter-spacing:.3px}
  header .ball{width:26px;height:26px;border-radius:50%;
    background:radial-gradient(circle at 35% 30%,#ff8a5c,var(--accent));}
  header .sub{color:var(--muted);font-size:13px;margin-left:auto}
  main{max-width:1120px;margin:0 auto;padding:24px 20px 60px}
  .grid{display:grid;grid-template-columns:320px 1fr;gap:22px;align-items:start}
  @media(max-width:820px){.grid{grid-template-columns:1fr}}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
  .panel h2{font-size:13px;text-transform:uppercase;letter-spacing:.6px;
    color:var(--muted);margin:0 0 14px}
  label{display:block;font-size:13px;color:var(--muted);margin:12px 0 5px}
  input[type=text],input[type=number],input[type=password],textarea,select{width:100%;background:var(--panel2);
    border:1px solid var(--line);color:var(--text);border-radius:9px;padding:9px 11px;font-size:14px;
    font-family:inherit}
  textarea{resize:vertical;min-height:58px;line-height:1.35}
  select{cursor:pointer;-webkit-appearance:none;appearance:none}
  input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent2)}
  .core-star{color:var(--accent2);font-weight:700}
  .offpool{color:var(--accent);font-weight:700}
  .riskdot{color:#e0a030}
  .riskrow td{color:var(--muted)}
  .note{background:#1e2530;border:1px solid #33506e;color:#a8c4e0;border-radius:10px;
    padding:10px 13px;margin-bottom:12px;font-size:13.5px}
  .lockchip{display:inline-block;padding:5px 11px;margin:3px 3px 0 0;border-radius:15px;
    border:1px solid var(--line);cursor:pointer;font-size:13px;background:var(--chip);user-select:none}
  .lockchip.on{background:#3a2a1e;border-color:#e0a030;color:#f0c070}
  .swapcard{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:8px;background:var(--panel)}
  .swapcard.keep{opacity:.55;font-size:13px;padding:7px 12px}
  .swapcard.err{color:#e08080;font-size:13px}
  .swaphdr{font-weight:600;margin-bottom:5px}
  .swaprow{font-size:13px;padding:2px 0}
  .swaprow.out{color:#e88}
  .swaprow.in{color:var(--good)}
  .gain{color:var(--accent);font-weight:700}
  .muted{color:var(--muted);font-weight:400;font-size:12px}
  .typeahead{position:relative}
  .drop-menu{position:absolute;left:0;right:0;top:100%;z-index:30;background:var(--panel2);
    border:1px solid var(--line);border-radius:9px;margin-top:4px;max-height:220px;overflow:auto;display:none}
  .drop-menu.show{display:block}
  .drop-menu div{padding:8px 11px;cursor:pointer;font-size:14px}
  .drop-menu div:hover,.drop-menu div.active{background:var(--chip)}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
  .chip{background:var(--chip);border:1px solid var(--line);border-radius:16px;padding:4px 10px;
    font-size:13px;display:flex;align-items:center;gap:7px}
  .chip .x{cursor:pointer;color:var(--muted);font-weight:700}.chip .x:hover{color:var(--accent)}
  input:disabled{opacity:.5}
  .row{display:flex;gap:12px}.row>*{flex:1}
  .drop{margin-top:4px;border:1.5px dashed var(--line);border-radius:12px;padding:26px 14px;
    text-align:center;cursor:pointer;transition:.15s;background:var(--panel2)}
  .drop:hover,.drop.over{border-color:var(--accent);background:#20262f}
  .drop b{color:var(--text)}.drop small{color:var(--muted);display:block;margin-top:6px}
  .drop.loaded{border-color:var(--good)}
  .slider{width:100%}
  .btn{margin-top:18px;width:100%;background:var(--accent);color:#fff;border:none;
    border-radius:10px;padding:13px;font-size:15px;font-weight:600;cursor:pointer;transition:.15s}
  .btn:hover{filter:brightness(1.08)}.btn:disabled{opacity:.55;cursor:not-allowed}
  .btn.ghost{background:var(--chip);color:var(--text);border:1px solid var(--line)}
  .hint{color:var(--muted);font-size:12px;margin-top:8px}
  .badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600}
  .badge.props{background:rgba(57,217,138,.15);color:var(--good)}
  .badge.csv{background:rgba(255,90,60,.15);color:var(--accent)}
  .badge.season{background:rgba(60,160,255,.15);color:var(--accent2)}
  .coach{margin-bottom:16px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .coach-h{font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin-bottom:10px}
  .coach-note{font-size:13.5px;line-height:1.5;padding:8px 12px;border-radius:8px;margin-bottom:8px;
    border-left:3px solid var(--line);background:var(--panel2)}
  .coach-note.info{border-color:var(--accent2)}
  .coach-note.warn{border-color:#e0a030}
  .coach-note.good{border-color:var(--good)}
  .coach-legend{font-size:12px;color:var(--muted);margin-top:4px;padding-top:8px;border-top:1px solid var(--line)}
  .status{margin-bottom:16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .status .meta{color:var(--muted);font-size:13px}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
  .card .top{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;
    background:var(--panel2);border-bottom:1px solid var(--line)}
  .card .rank{font-weight:700}
  .card .stat{font-size:12px;color:var(--muted)}
  .card .stat b{color:var(--text)}
  .stacks{padding:6px 14px;font-size:12px;color:var(--accent2)}
  .offbadge{cursor:pointer;color:var(--accent);font-weight:600}
  .offbadge:hover{text-decoration:underline}
  .altwrap{border-top:1px dashed var(--line);background:var(--panel2)}
  .althead{padding:8px 14px;font-size:12px;color:var(--muted)}
  .altwrap .use{margin:6px 14px 12px;background:var(--chip);color:var(--text);border:1px solid var(--line);
    border-radius:8px;padding:7px 12px;font-size:13px;cursor:pointer}
  .altwrap .use:hover{border-color:var(--accent);color:#fff}
  table{width:100%;border-collapse:collapse}
  .card td{padding:6px 14px;font-size:13px;border-top:1px solid var(--line)}
  .card td.slot{color:var(--muted);width:46px}
  .card td.sal,.card td.pr{text-align:right;color:var(--muted)}
  .empty{color:var(--muted);text-align:center;padding:60px 0}
  .err{background:rgba(255,90,60,.12);border:1px solid var(--accent);color:#ffb3a3;
    padding:12px 14px;border-radius:10px;margin-bottom:16px}
  details{margin-top:22px}summary{cursor:pointer;color:var(--muted);font-size:13px;
    text-transform:uppercase;letter-spacing:.5px}
  .ptable{width:100%;margin-top:12px;font-size:13px}
  .ptable th{color:var(--muted);text-align:left;font-weight:500;padding:6px 8px;
    border-bottom:1px solid var(--line);cursor:pointer;user-select:none}
  .ptable td{padding:5px 8px;border-bottom:1px solid #20252e}
  .ptable td.num{text-align:right}
  .spin{width:16px;height:16px;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;
    border-radius:50%;display:inline-block;animation:sp .7s linear infinite;vertical-align:-3px;margin-right:8px}
  @keyframes sp{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<header>
  <div class="ball"></div>
  <h1>WNBA DFS Optimizer</h1>
  <span class="sub">DraftKings · GPP · LineStar</span>
</header>
<main>
  <div class="grid">
    <!-- controls -->
    <div class="panel">
      <h2>1 · Your slate</h2>
      <div id="drop" class="drop">
        <b>Drop your LineStar CSV</b>
        <small>projections, floor/ceiling, ownership, starters — all in one file</small>
        <input id="file" type="file" accept=".csv" hidden>
      </div>
      <div id="mindrop" class="drop" style="margin-top:10px;padding:16px">
        <b>+ daily projections CSV</b>
        <small>adds projected minutes — gates out sub-14-min non-rotation plays</small>
        <input id="minfile" type="file" accept=".csv" hidden>
      </div>

      <h2 style="margin-top:22px">2 · Settings</h2>
      <div class="row">
        <div><label>Lineups</label><input id="n" type="number" value="20" min="1" max="150"></div>
        <div><label>Min game stack</label><input id="stack" type="number" value="2" min="1" max="4"></div>
      </div>
      <label>Max exposure — <span id="expv">60</span>%</label>
      <input id="exp" class="slider" type="range" min="10" max="100" value="60">
      <label>Fade chalk (leverage) — <span id="levv">0.05</span></label>
      <input id="lev" class="slider" type="range" min="0" max="100" value="5">

      <h2 style="margin-top:22px">3 · Game-theory cores <span style="text-transform:none;color:var(--muted)">(optional)</span></h2>
      <label>Core plays — type a name to add</label>
      <div class="typeahead">
        <input id="corein" type="text" autocomplete="off" placeholder="drop a CSV first, then type…" disabled>
        <div id="coredrop" class="drop-menu"></div>
      </div>
      <div id="corechips" class="chips"></div>
      <label style="margin-top:12px">Minimum cores per lineup</label>
      <select id="mincores">
        <option value="0">0 — no requirement</option>
        <option value="1" selected>1 — every lineup built around ≥1 core</option>
        <option value="2">2 — every lineup built around ≥2</option>
      </select>
      <div class="hint">Cores are your anchor plays — the sharp's picks that keep landing
        in winning lineups. This guarantees every lineup is built around at least this many;
        which ones vary across your set. No cores set → no requirement. Each core is also
        guaranteed a minimum share of your lineups (data-driven from the slate), so a play you
        believe in can't get buried — the coach grades whether the data agrees, but the call to
        keep or drop a weak core stays yours.</div>
      <label style="margin-top:14px">His full pool — type to add (optional)</label>
      <div class="typeahead">
        <input id="poolin" type="text" autocomplete="off" placeholder="drop a file first, then type…" disabled>
        <div id="pooldrop" class="drop-menu"></div>
      </div>
      <div id="poolchips" class="chips"></div>
      <label style="margin-top:12px">Off-pool players allowed per lineup</label>
      <select id="offpool">
        <option value="0" selected>0 — build only from the pool</option>
        <option value="1">1 — allow one, only if the data earns it</option>
        <option value="2">2 — allow up to two</option>
      </select>
      <div class="hint">The pool is a hard build constraint, not an ownership nudge.
        At 0, every player comes from your pool. At 1–2 the app may spend a slot off-pool,
        but only when that player makes a genuinely better lineup — never a forced
        contrarian dart. Cores (★) count as in-pool. No pool set → no constraint.</div>

      <label style="margin-top:14px">Remove player — out / traded / benched / missed shootaround</label>
      <div class="typeahead">
        <input id="removein" type="text" autocomplete="off" placeholder="drop a file first, then type…" disabled>
        <div id="removedrop" class="drop-menu"></div>
      </div>
      <div id="removechips" class="chips"></div>
      <div class="hint">Zeroes the player <em>and</em> redistributes their minutes/usage
        onto teammates (weighted to same-position replacements), so the rest of the
        roster's projections rise the way they actually would.</div>

      <label style="margin-top:14px">Cap a player's exposure — type to add</label>
      <div class="typeahead">
        <input id="capin" type="text" autocomplete="off" placeholder="drop a file first, then type…" disabled>
        <div id="capdrop" class="drop-menu"></div>
      </div>
      <div id="capchips" class="chips"></div>
      <label style="margin-top:8px">Cap those players at <span id="capv">30</span>%</label>
      <input id="cappct" class="slider" type="range" min="5" max="60" value="30">
      <div class="hint">Limits <em>only</em> the listed players — everyone else stays at your
        max exposure. Use it to rein in one heavy play without capping the whole board on a
        short slate.</div>

      <button id="go" class="btn" disabled>Generate lineups</button>

      <details id="swapwrap" style="margin-top:18px">
        <summary style="cursor:pointer;font-weight:600;color:var(--text)">🔄 Late swap — already entered? adjust for news</summary>
        <div style="margin-top:10px">
          <div class="hint">Drop your DK entries export. Locked players stay pinned; every open slot is
            re-optimized and scored with the same simulator as the main build. Because the LineStar file
            above carries live scores, it also reads how each lineup <em>already stands</em> — a lineup
            running behind (on plays the field didn't have) chases upside, one running ahead protects.
            You get a re-uploadable DK file back.</div>
          <div id="swapdrop" class="drop" style="margin-top:10px;padding:16px">
            <b>+ your DK entries export</b>
            <small>DKEntries*.csv — reads your lineups and who's locked</small>
            <input id="swapfile" type="file" accept=".csv" hidden>
          </div>
          <div id="condrop" class="drop" style="margin-top:10px;padding:16px">
            <b>+ contest standings <span style="font-weight:400;color:var(--muted)">(optional)</span></b>
            <small>DK contest export — your real rank + the score that's winning</small>
            <input id="confile" type="file" accept=".csv" hidden>
          </div>
          <div class="hint" style="margin-top:10px">Re-drop the <b>updated</b> LineStar CSV up top first —
            that's where the new projections and live scores come from. Add the contest standings and the
            tool reads your <em>actual</em> leaderboard position instead of estimating it.</div>
          <button id="swapgo" class="btn" disabled style="margin-top:12px">Recommend late swaps</button>
        </div>
      </details>
    </div>

    <!-- results -->
    <div>
      <div id="err" class="err" style="display:none"></div>
      <div id="note" class="note" style="display:none"></div>
      <div id="status" class="status" style="display:none"></div>
      <div id="coach" class="coach" style="display:none"></div>
      <div id="tools" style="display:none;margin-bottom:14px">
        <button id="dl" class="btn ghost" style="width:auto;margin:0;padding:9px 16px">⬇ Download lineups CSV</button>
        <button id="dkfill" class="btn ghost" style="width:auto;margin:0 0 0 8px;padding:9px 16px">⬆ Fill DK entries file</button>
        <input id="dkfile" type="file" accept=".csv" hidden>
        <div class="hint" style="margin-top:6px">Upload your DK entries export (DKEntries*.csv) and it slots
          these lineups into your entries with real DK IDs — download it and upload straight back to DraftKings.</div>
      </div>
      <div id="swapresults" style="display:none;margin-bottom:14px"></div>
      <div id="cards" class="cards"></div>
      <div id="welcome" class="empty">Drop your LineStar CSV and hit generate.</div>
      <details id="expwrap" style="display:none">
        <summary>Exposure — how spread your lineups are</summary>
        <table class="ptable" id="exptable"></table>
      </details>
      <details id="projwrap" style="display:none">
        <summary>Player projections</summary>
        <table class="ptable" id="ptable"></table>
      </details>
    </div>
  </div>
</main>
<script>
const $ = s => document.querySelector(s);
let csvText = null, lastResult = null, playerNames = [], minText = '';

$('#exp').addEventListener('input', e => $('#expv').textContent = e.target.value);
$('#lev').addEventListener('input', e => $('#levv').textContent = (e.target.value/100).toFixed(2));

// file handling
const drop = $('#drop'), fileIn = $('#file');
drop.addEventListener('click', () => fileIn.click());
fileIn.addEventListener('change', e => loadFile(e.target.files[0]));
['dragover','dragenter'].forEach(ev => drop.addEventListener(ev, e => {e.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => {e.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop', e => loadFile(e.dataTransfer.files[0]));
function loadFile(f){
  if(!f) return;
  const r = new FileReader();
  r.onload = () => { csvText = r.result; drop.classList.add('loaded');
    drop.innerHTML = '<b>✓ '+f.name+'</b><small>ready — change file anytime</small>';
    $('#go').disabled = false;
    playerNames = parseNames(csvText); enablePickers();
  };
  r.readAsText(f);
}
function parseNames(text){
  const lines = text.replace(/\r/g,'').split('\n').filter(l=>l.trim());
  if(!lines.length) return [];
  const cols = csvSplit(lines[0]); const iName = cols.findIndex(c=>c.trim().toLowerCase()==='name');
  const out = [];
  for(let i=1;i<lines.length;i++){ const c = csvSplit(lines[i]); const nm=(c[iName]||'').trim(); if(nm) out.push(nm); }
  return [...new Set(out)];
}
function csvSplit(line){ const out=[]; let cur='',q=false; for(const ch of line){ if(ch==='"')q=!q; else if(ch===','&&!q){out.push(cur);cur='';} else cur+=ch; } out.push(cur); return out; }

// ---- daily projections (minutes) drop ----
const mindrop = $('#mindrop'), minfile = $('#minfile');
mindrop.addEventListener('click', () => minfile.click());
minfile.addEventListener('change', e => loadMin(e.target.files[0]));
['dragover','dragenter'].forEach(ev => mindrop.addEventListener(ev, e => {e.preventDefault();mindrop.classList.add('over')}));
['dragleave','drop'].forEach(ev => mindrop.addEventListener(ev, e => {e.preventDefault();mindrop.classList.remove('over')}));
mindrop.addEventListener('drop', e => loadMin(e.dataTransfer.files[0]));
function loadMin(f){ if(!f) return; const r=new FileReader();
  r.onload=()=>{ minText=r.result; mindrop.classList.add('loaded');
    mindrop.innerHTML='<b>✓ '+f.name+'</b><small>minutes + stat-stuffer floor loaded</small>'; };
  r.readAsText(f); }

// ---- reusable type-ahead picker (used for cores and the full pool) ----
function makePicker(prefix, icon){
  const input=$('#'+prefix+'in'), drop=$('#'+prefix+'drop'), chips=$('#'+prefix+'chips');
  const sel=[]; let active=-1;
  function show(){
    const q=input.value.trim().toLowerCase();
    const chosen=new Set(sel.map(s=>s.toLowerCase()));
    let m=playerNames.filter(n=>!chosen.has(n.toLowerCase()));
    if(q) m=m.filter(n=>n.toLowerCase().includes(q));
    m=m.slice(0,8);
    if(!m.length){ hide(); return; }
    active=-1;
    drop.innerHTML=m.map(n=>'<div>'+n+'</div>').join('');
    drop.querySelectorAll('div').forEach(d=>d.onclick=()=>add(d.textContent));
    drop.classList.add('show');
  }
  function hide(){ drop.classList.remove('show'); active=-1; }
  function paint(items){ items.forEach((it,i)=>it.classList.toggle('active',i===active)); }
  function add(n){ if(!sel.includes(n)) sel.push(n); input.value=''; hide(); render(); input.focus(); }
  function remove(n){ const i=sel.indexOf(n); if(i>=0) sel.splice(i,1); render(); }
  function render(){
    chips.innerHTML=sel.map(n=>'<span class="chip">'+(icon?'<span class="core-star">'+icon+'</span>':'')+n+
      '<span class="x" data-n="'+n.replace(/"/g,'&quot;')+'">✕</span></span>').join('');
    chips.querySelectorAll('.x').forEach(x=>x.onclick=()=>remove(x.dataset.n));
  }
  input.addEventListener('input', show);
  input.addEventListener('focus', show);
  input.addEventListener('keydown', e => {
    const items=drop.querySelectorAll('div'); if(!items.length) return;
    if(e.key==='ArrowDown'){ active=Math.min(active+1,items.length-1); paint(items); e.preventDefault(); }
    else if(e.key==='ArrowUp'){ active=Math.max(active-1,0); paint(items); e.preventDefault(); }
    else if(e.key==='Enter'){ if(active>=0){ add(items[active].textContent); e.preventDefault(); } }
    else if(e.key==='Escape'){ hide(); }
  });
  return { sel, enable(){ input.disabled=false; input.placeholder='type a player…'; } };
}
document.addEventListener('click', e => { if(!e.target.closest('.typeahead')) document.querySelectorAll('.drop-menu').forEach(d=>d.classList.remove('show')); });
const corePicker = makePicker('core','★');
const poolPicker = makePicker('pool','');
const removePicker = makePicker('remove','🚫');
const capPicker = makePicker('cap','🔒');
function enablePickers(){ corePicker.enable(); poolPicker.enable(); removePicker.enable(); capPicker.enable(); }
$('#cappct').addEventListener('input', e => $('#capv').textContent = e.target.value);

// ---- late swap ----
let swapText='', conText='', lastSwapCsv='';
const swapdrop=$('#swapdrop'), swapfile=$('#swapfile');
swapdrop.addEventListener('click',()=>swapfile.click());
['dragover','dragenter'].forEach(ev=>swapdrop.addEventListener(ev,e=>{e.preventDefault();swapdrop.classList.add('over')}));
['dragleave','drop'].forEach(ev=>swapdrop.addEventListener(ev,e=>{e.preventDefault();swapdrop.classList.remove('over')}));
swapdrop.addEventListener('drop',e=>loadSwap(e.dataTransfer.files[0]));
swapfile.addEventListener('change',e=>loadSwap(e.target.files[0]));
function loadSwap(f){ if(!f) return; const r=new FileReader();
  r.onload=()=>{ swapText=r.result; swapdrop.classList.add('loaded');
    swapdrop.innerHTML='<b>✓ '+f.name+'</b><small>ready — hit recommend</small>';
    $('#swapgo').disabled=false; };
  r.readAsText(f); }
const condrop=$('#condrop'), confile=$('#confile');
condrop.addEventListener('click',()=>confile.click());
['dragover','dragenter'].forEach(ev=>condrop.addEventListener(ev,e=>{e.preventDefault();condrop.classList.add('over')}));
['dragleave','drop'].forEach(ev=>condrop.addEventListener(ev,e=>{e.preventDefault();condrop.classList.remove('over')}));
condrop.addEventListener('drop',e=>loadCon(e.dataTransfer.files[0]));
confile.addEventListener('change',e=>loadCon(e.target.files[0]));
function loadCon(f){ if(!f) return; const r=new FileReader();
  r.onload=()=>{ conText=r.result; condrop.classList.add('loaded');
    condrop.innerHTML='<b>✓ '+f.name+'</b><small>real leaderboard position loaded</small>'; };
  r.readAsText(f); }
$('#swapgo').addEventListener('click',runSwap);
async function runSwap(){
  if(!csvText){ showErr('Drop your updated LineStar CSV up top first — that is where the new projections and live scores come from.'); return; }
  if(!swapText) return;
  const btn=$('#swapgo'); btn.disabled=true; btn.innerHTML='<span class="spin"></span>Simulating…';
  $('#err').style.display='none'; $('#note').style.display='none';
  try{
    const res=await fetch('/api/lateswap',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({csv:csvText, dk:swapText, contest:conText,
        options:{maxExposure:(+$('#exp').value)/100}})});
    const data=await res.json();
    if(data.error) showErr(data.error); else renderSwaps(data);
  }catch(e){ showErr(e.message); }
  btn.disabled=false; btn.textContent='Recommend late swaps';
}
function aggrTag(a){
  if(a>=0.62) return '<span style="color:var(--accent)">chasing upside</span>';
  if(a<=0.38) return '<span style="color:var(--good)">protecting</span>';
  return '<span class="muted">neutral</span>';
}
function renderSwaps(d){
  const box=$('#swapresults'); box.style.display='block'; $('#welcome').style.display='none';
  const fieldTxt = d.field
    ? ' · field '+d.field.toLocaleString()+', '+d.target+' projected to win'
    : ' · no contest file — position estimated from projections';
  let html='<div class="coach-h">🔄 Late swap — '+d.changed+' of '+d.entries+
    ' lineups to adjust · '+d.lockedPlayers+' locked'+fieldTxt+'</div>';
  if(d.dkCsv){
    lastSwapCsv=d.dkCsv;
    html+='<div class="note" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'+
      '<b>+'+d.gain.toFixed(1)+' simulated ceiling across '+d.changed+' lineups.</b>'+
      '<button id="swapdl" class="btn ghost" style="width:auto;margin:0;padding:8px 14px">'+
      '⬇ Download updated DK file</button>'+
      '<span class="muted">all '+d.entries+' entries — upload straight back to DK</span></div>';
  }
  html+=d.swaps.map(s=>{
    if(s.error) return '<div class="swapcard err">#'+s.entryId+': '+s.error+'</div>';
    const pace = s.rank
      ? ' · rank <b>#'+s.rank.toLocaleString()+'</b>, banked '+s.banked+' → '+s.projFinal+' projected · '+aggrTag(s.aggression)
      : (s.banked>0
        ? ' · banked <b>'+s.banked+'</b> vs '+s.expected+' expected ('+(s.pace>=0?'+':'')+s.pace+') · '+aggrTag(s.aggression)
        : '');
    if(s.keep) return '<div class="swapcard keep">#'+s.entryId+' · keep as-is '+
      '<span class="muted">('+s.open+' open, proj '+s.proj+')</span>'+pace+'</div>';
    const row=(p,sign,cls)=>'<div class="swaprow '+cls+'">'+sign+' '+p.name+
      ' <span class="muted">$'+p.salary.toLocaleString()+' · '+p.proj+' proj · '+p.own+'%</span></div>';
    return '<div class="swapcard"><div class="swaphdr">#'+s.entryId+
      ' <span class="gain">+'+s.gain+'</span> <span class="muted">→ score '+s.score+
      ', $'+s.salary.toLocaleString()+'</span>'+pace+'</div>'+
      s.out.map(p=>row(p,'−','out')).join('')+s.in.map(p=>row(p,'+','in')).join('')+'</div>';
  }).join('');
  box.innerHTML=html;
  const dl=$('#swapdl');
  if(dl) dl.onclick=()=>{
    const blob=new Blob([lastSwapCsv],{type:'text/csv'});
    const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
    a.download='DKEntries_lateswap.csv'; a.click();
  };
}

$('#go').addEventListener('click', run);
async function run(){
  if(!csvText) return;
  const btn = $('#go'); btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Crunching…';
  $('#err').style.display='none'; $('#note').style.display='none';
  const options = {
    n:+$('#n').value, stack:+$('#stack').value,
    maxExposure:(+$('#exp').value)/100, leverage:(+$('#lev').value)/100,
    cores:corePicker.sel.join('\n'), pool:poolPicker.sel.join('\n'),
    remove:removePicker.sel.join('\n'), maxOffPool:+$('#offpool').value,
    minCores:+$('#mincores').value,
    capPlayers:capPicker.sel.join('\n'), capPct:+$('#cappct').value, minutes:minText,
  };
  try{
    const res = await fetch('/api/optimize', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({csv:csvText||'', options})});
    const data = await res.json();
    if(data.error){ showErr(data.error); }
    else { lastResult = data; render(data); }
  }catch(e){ showErr(e.message); }
  btn.disabled=false; btn.textContent='Generate lineups';
}
function showErr(m){ $('#err').style.display='block'; $('#err').textContent = m; }
function showNote(m){ $('#note').style.display='block'; $('#note').textContent = m; }

function render(d){
  $('#welcome').style.display='none';
  const cls = 'props';
  const outTxt = d.out && d.out.length ? ' · OUT: '+d.out.join(', ') : '';
  const rmTxt = d.removed && d.removed.length ? ' · removed: '+d.removed.join(', ') : '';
  const slateBadge = d.slateType ?
    '<span class="badge '+(d.slateType==='stars-and-scrubs'?'csv':'season')+'">'+d.slateType+'</span>' : '';
  $('#status').style.display='flex';
  $('#status').innerHTML = '<span class="badge '+cls+'">'+d.source+'</span>'+slateBadge+
    '<span class="meta">'+(d.slate.date||'')+' · '+(d.slate.games||[]).join('  ')+outTxt+rmTxt+'</span>';
  $('#tools').style.display = d.lineups.length ? 'block':'none';

  // coach — a read on the build (no edits), plus the flag legend
  const cx = $('#coach');
  if(d.coach && d.coach.length){
    cx.style.display='block';
    const legend = '<div class="coach-legend">★ core · ⚠ gated (sub-14-min non-rotation, excluded unless cored)'+
      ' · 🔥 trending (over-owned, ownership nudged up) · ◇ off-pool</div>';
    cx.innerHTML = '<div class="coach-h">📋 Read on your build — you decide, this is just the data’s take</div>'+
      d.coach.map(c=>'<div class="coach-note '+c.type+'">'+c.text+'</div>').join('')+legend;
  } else cx.style.display='none';

  const poolOn = d.poolActive;
  $('#cards').innerHTML = d.lineups.map((l,i)=>card(l,i,poolOn)).join('');
  wireCards();

  // exposure — spot over-concentration at a glance
  const N=d.lineups.length, ec={};
  d.lineups.forEach(l=>l.players.forEach(p=>{ec[p.name]=(ec[p.name]||0)+1;}));
  const erows=Object.entries(ec).sort((a,b)=>b[1]-a[1]);
  if(erows.length){
    $('#expwrap').style.display='block';
    $('#exptable').innerHTML='<tr><th>Player</th><th class="num">Lineups</th><th class="num">Exposure</th></tr>'+
      erows.map(([n,c])=>{const pctv=Math.round(c/N*100);const hot=pctv>=70?' style="color:var(--accent)"':'';
        return '<tr><td>'+n+'</td><td class="num">'+c+'/'+N+'</td><td class="num"'+hot+'>'+pctv+'%</td></tr>';}).join('');
  }

  // projections table
  if(d.players && d.players.length){
    $('#projwrap').style.display='block';
    renderProj(d.players, 'proj');
  }
}

function lineupRows(players, poolOn){
  return players.map(p => {
    const off = poolOn && p.pool===false;
    const mark = p.core ? '<span class="core-star">★</span> '
      : (p.risk ? '<span class="riskdot" title="gated: sub-14-min non-rotation (cored in by you)">⚠</span> '
      : (off ? '<span class="offpool" title="off your pool">◇</span> ' : ''));
    return '<tr><td class="slot">'+p.slot+'</td><td>'+mark+p.name+'</td><td class="stat">'+p.team+'</td>'+
      '<td class="sal">$'+p.salary.toLocaleString()+'</td><td class="pr">'+p.proj+'</td></tr>';
  }).join('');
}
function card(l,i,poolOn){
  const coreTxt = l.cores ? ' · <span class="core-star">★</span>'+l.cores : '';
  let badge = '';
  if(poolOn && l.offPool){
    badge = l.alt
      ? ' · <span class="offbadge" data-i="'+i+'">◇ '+l.offPool+' off-pool · show pool-only ▾</span>'
      : ' · <span class="offpool">◇ '+l.offPool+' off-pool (no pool-only fit)</span>';
  }
  const head = '<div class="top"><span class="rank">#'+l.rank+coreTxt+badge+'</span>'+
    '<span class="stat"><b>'+l.proj+'</b> proj · <b>'+l.ceiling+'</b> ceil · $'+l.salary.toLocaleString()+' · '+l.totalOwn+'% own</span></div>';
  const stacks = l.stacks.length ? '<div class="stacks">stacks: '+l.stacks.join(', ')+'</div>' : '';
  const main = '<table>'+lineupRows(l.players,poolOn)+'</table>';
  let alt = '';
  if(poolOn && l.offPool && l.alt){
    const a = l.alt;
    alt = '<div class="altwrap" id="alt-'+i+'" style="display:none">'+
      '<div class="althead">Pool-only alternative — <b>'+a.proj+'</b> proj · <b>'+a.ceiling+'</b> ceil · $'+a.salary.toLocaleString()+' · '+a.totalOwn+'% own</div>'+
      '<table>'+lineupRows(a.players,poolOn)+'</table>'+
      '<button class="use" data-i="'+i+'">Use this pool-only lineup</button></div>';
  }
  return '<div class="card">'+head+stacks+main+alt+'</div>';
}
function wireCards(){
  document.querySelectorAll('.offbadge').forEach(b=>b.onclick=()=>{
    const i=b.dataset.i, panel=$('#alt-'+i), show = panel.style.display==='none';
    panel.style.display = show?'block':'none';
    b.innerHTML = '◇ '+lastResult.lineups[i].offPool+' off-pool · '+(show?'hide pool-only ▴':'show pool-only ▾');
  });
  document.querySelectorAll('.use').forEach(btn=>btn.onclick=()=>useAlt(+btn.dataset.i));
}
function useAlt(i){
  const l=lastResult.lineups[i], a=l.alt;
  if(!a) return;
  l.players=a.players; l.upload=a.upload; l.salary=a.salary; l.proj=a.proj;
  l.ceiling=a.ceiling; l.totalOwn=a.totalOwn; l.cores=a.cores; l.stacks=a.stacks;
  l.offPool=0; l.alt=null;
  render(lastResult);
}

let projSort = 'proj', projDir = -1;
function renderProj(players, key){
  const sorted = [...players].sort((a,b)=>{
    const av=a[key], bv=b[key];
    return (typeof av==='number') ? (av-bv)*projDir : String(av).localeCompare(String(bv))*projDir;
  });
  const hasMin = players.some(p=>p.min>0);
  const cols = [['name','Player'],['team','Tm'],['pos','Pos'],['salary','Sal'],['proj','Proj']]
    .concat(hasMin?[['min','Min'],['stuffer','Stuff']]:[['floor','Floor']])
    .concat([['ceil','Ceil'],['own','Own%'],['notes','Notes']]);
  const head = '<tr>'+cols.map(c=>'<th data-k="'+c[0]+'">'+c[1]+'</th>').join('')+'</tr>';
  const body = sorted.map(p=>'<tr'+(p.risk?' class="riskrow"':'')+'>'+cols.map(c=>{
    const num = ['salary','proj','min','stuffer','ceil','own'].includes(c[0]);
    let v = p[c[0]]; if(c[0]==='salary') v='$'+v.toLocaleString();
    if(c[0]==='name'){ if(p.core) v='<span class="core-star">★</span> '+v;
      if(p.risk) v='<span class="riskdot" title="gated: sub-14-min non-rotation (cored in by you)">⚠</span> '+v;
      if(p.trending) v='🔥 '+v; }
    return '<td class="'+(num?'num':'')+'">'+(v===undefined?'':v)+'</td>';
  }).join('')+'</tr>').join('');
  const t = $('#ptable'); t.innerHTML = head+body;
  t.querySelectorAll('th').forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; projDir = (projSort===k)? -projDir : -1; projSort=k; renderProj(players,k);
  });
}

$('#dl').addEventListener('click', ()=>{
  if(!lastResult) return;
  const rows = [['F','F','F','G','G','UTIL'].join(',')]
    .concat(lastResult.lineups.map(l => l.upload.map(s=>'"'+s+'"').join(',')));
  const blob = new Blob([rows.join('\n')], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'wnba_lineups_'+(lastResult.slate.date||'slate')+'.csv';
  a.click();
});

// ---- fill a DK entries export with the generated lineups (real DK IDs) ----
$('#dkfill').addEventListener('click', ()=>$('#dkfile').click());
$('#dkfile').addEventListener('change', e=>{
  const f=e.target.files[0]; if(!f||!lastResult) return;
  const r=new FileReader();
  r.onload=async()=>{
    const btn=$('#dkfill'); btn.disabled=true; btn.textContent='Filling…';
    try{
      const res=await fetch('/api/dkfill',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({dk:r.result, lineups:lastResult.lineups.map(l=>l.players.map(p=>p.name))})});
      const d=await res.json();
      if(d.error){ showErr(d.error); }
      else{
        const blob=new Blob([d.csv],{type:'text/csv'});
        const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
        a.download='DKEntries_filled.csv'; a.click();
        showNote('✓ Filled '+d.filled+' entries.'+(d.warn?' '+d.warn:''));
      }
    }catch(err){ showErr(err.message); }
    btn.disabled=false; btn.textContent='⬆ Fill DK entries file';
    e.target.value='';
  };
  r.readAsText(f);
});
</script>
</body>
</html>
"""
