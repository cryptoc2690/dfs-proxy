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
  input[type=text],input[type=number],input[type=password],textarea{width:100%;background:var(--panel2);
    border:1px solid var(--line);color:var(--text);border-radius:9px;padding:9px 11px;font-size:14px;
    font-family:inherit}
  textarea{resize:vertical;min-height:58px;line-height:1.35}
  input:focus,textarea:focus{outline:none;border-color:var(--accent2)}
  .core-star{color:var(--accent2);font-weight:700}
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
  .keystat{font-size:13px}
  .keystat.ok{color:var(--good)}.keystat.bad{color:var(--accent)}.keystat.wait{color:var(--muted)}
  .badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600}
  .badge.props{background:rgba(57,217,138,.15);color:var(--good)}
  .badge.csv{background:rgba(255,90,60,.15);color:var(--accent)}
  .badge.season{background:rgba(60,160,255,.15);color:var(--accent2)}
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
  <span class="sub">DraftKings · GPP · props-first</span>
</header>
<main>
  <div class="grid">
    <!-- controls -->
    <div class="panel">
      <h2>1 · Your slate</h2>
      <div id="drop" class="drop">
        <b>Drop DKSalaries.csv</b>
        <small>or click to choose a file</small>
        <input id="file" type="file" accept=".csv" hidden>
      </div>
      <div id="dffdrop" class="drop" style="margin-top:10px;padding:16px">
        <b>+ DFF cheatsheet</b>
        <small>optional — best projections (no key needed)</small>
        <input id="dfffile" type="file" accept=".csv" hidden>
      </div>

      <h2 style="margin-top:22px">2 · Settings</h2>
      <div class="row">
        <div><label>Lineups</label><input id="n" type="number" value="20" min="1" max="150"></div>
        <div><label>Min game stack</label><input id="stack" type="number" value="2" min="1" max="4"></div>
      </div>
      <label>Max exposure — <span id="expv">60</span>%</label>
      <input id="exp" class="slider" type="range" min="10" max="100" value="60">
      <label>Fade chalk (leverage) — <span id="levv">0.15</span></label>
      <input id="lev" class="slider" type="range" min="0" max="100" value="15">

      <h2 style="margin-top:22px">3 · balldontlie key <span style="text-transform:none;color:var(--muted)">(optional)</span></h2>
      <input id="key" type="password" placeholder="GOAT API key — stays on your machine">
      <div style="display:flex;gap:10px;align-items:center;margin-top:8px">
        <button id="testkey" class="btn ghost" style="width:auto;margin:0;padding:8px 14px">Test key</button>
        <span id="keystat" class="keystat"></span>
      </div>
      <div class="hint">Enables live player-prop projections. Without it, the app
        projects from the CSV's own averages. Saved in this browser only.</div>

      <h2 style="margin-top:22px">4 · Game-theory cores <span style="text-transform:none;color:var(--muted)">(optional)</span></h2>
      <label>Core plays — type a name to add</label>
      <div class="typeahead">
        <input id="corein" type="text" autocomplete="off" placeholder="drop a CSV first, then type…" disabled>
        <div id="coredrop" class="drop-menu"></div>
      </div>
      <div id="corechips" class="chips"></div>
      <label style="margin-top:14px">His full pool — optional, paste the name column</label>
      <textarea id="poollist" rows="3" placeholder="optional — paste his player names (one per line), or skip if you're short on time"></textarea>
      <div class="hint">Cores (★) are treated as the field's chalk; the app decides how
        many to use from value + leverage and diversifies them across your lineups.
        The pool is only an ownership signal — off-pool sharp plays still get used.</div>

      <button id="go" class="btn" disabled>Generate lineups</button>
    </div>

    <!-- results -->
    <div>
      <div id="err" class="err" style="display:none"></div>
      <div id="status" class="status" style="display:none"></div>
      <div id="tools" style="display:none;margin-bottom:14px">
        <button id="dl" class="btn ghost" style="width:auto;margin:0;padding:9px 16px">⬇ Download DraftKings CSV</button>
      </div>
      <div id="cards" class="cards"></div>
      <div id="welcome" class="empty">Drop your DraftKings CSV and hit generate.</div>
      <details id="projwrap" style="display:none">
        <summary>Player projections</summary>
        <table class="ptable" id="ptable"></table>
      </details>
    </div>
  </div>
</main>
<script>
const $ = s => document.querySelector(s);
let csvText = null, lastResult = null, playerNames = [], selectedCores = [], dffText = '';

// persist key locally
$('#key').value = localStorage.getItem('bdlKey') || '';
$('#key').addEventListener('input', e => localStorage.setItem('bdlKey', e.target.value));

// test the API key -> green light or the real reason
$('#testkey').addEventListener('click', async () => {
  const stat = $('#keystat'); const key = $('#key').value.trim();
  if(!key){ stat.className='keystat bad'; stat.textContent='Enter a key first.'; return; }
  stat.className='keystat wait'; stat.innerHTML='<span class="spin"></span>Checking…';
  try{
    const r = await fetch('/api/check', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({apiKey:key})});
    const d = await r.json();
    stat.className = 'keystat ' + (d.ok?'ok':'bad');
    stat.textContent = (d.ok?'✓ ':'✗ ') + d.message;
  }catch(e){ stat.className='keystat bad'; stat.textContent='✗ '+e.message; }
});

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
    playerNames = parseNames(csvText);
    const ci = $('#corein'); ci.disabled = false; ci.placeholder = 'type a player…';
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

// ---- DFF cheatsheet drop ----
const dffdrop = $('#dffdrop'), dfffile = $('#dfffile');
dffdrop.addEventListener('click', () => dfffile.click());
dfffile.addEventListener('change', e => loadDff(e.target.files[0]));
['dragover','dragenter'].forEach(ev => dffdrop.addEventListener(ev, e => {e.preventDefault();dffdrop.classList.add('over')}));
['dragleave','drop'].forEach(ev => dffdrop.addEventListener(ev, e => {e.preventDefault();dffdrop.classList.remove('over')}));
dffdrop.addEventListener('drop', e => loadDff(e.dataTransfer.files[0]));
function loadDff(f){ if(!f) return; const r=new FileReader();
  r.onload=()=>{ dffText=r.result; dffdrop.classList.add('loaded');
    dffdrop.innerHTML='<b>✓ '+f.name+'</b><small>DFF projections loaded</small>'; };
  r.readAsText(f); }

// ---- core type-ahead ----
const corein = $('#corein'), coredrop = $('#coredrop'); let activeIdx = -1;
corein.addEventListener('input', showMatches);
corein.addEventListener('focus', showMatches);
corein.addEventListener('keydown', e => {
  const items = coredrop.querySelectorAll('div'); if(!items.length) return;
  if(e.key==='ArrowDown'){ activeIdx=Math.min(activeIdx+1,items.length-1); paintActive(items); e.preventDefault(); }
  else if(e.key==='ArrowUp'){ activeIdx=Math.max(activeIdx-1,0); paintActive(items); e.preventDefault(); }
  else if(e.key==='Enter'){ if(activeIdx>=0){ addCore(items[activeIdx].textContent); e.preventDefault(); } }
  else if(e.key==='Escape'){ hideDrop(); }
});
document.addEventListener('click', e => { if(!e.target.closest('.typeahead')) hideDrop(); });
function showMatches(){
  const q = corein.value.trim().toLowerCase();
  const chosen = new Set(selectedCores.map(s=>s.toLowerCase()));
  let matches = playerNames.filter(n => !chosen.has(n.toLowerCase()));
  if(q) matches = matches.filter(n => n.toLowerCase().includes(q));
  matches = matches.slice(0,8);
  if(!matches.length){ hideDrop(); return; }
  activeIdx = -1;
  coredrop.innerHTML = matches.map(n=>'<div>'+n+'</div>').join('');
  coredrop.querySelectorAll('div').forEach(d=>d.onclick=()=>addCore(d.textContent));
  coredrop.classList.add('show');
}
function paintActive(items){ items.forEach((it,i)=>it.classList.toggle('active',i===activeIdx)); }
function hideDrop(){ coredrop.classList.remove('show'); activeIdx=-1; }
function addCore(name){ if(!selectedCores.includes(name)) selectedCores.push(name); corein.value=''; hideDrop(); renderChips(); corein.focus(); }
function removeCore(name){ selectedCores = selectedCores.filter(n=>n!==name); renderChips(); }
function renderChips(){
  $('#corechips').innerHTML = selectedCores.map(n=>
    '<span class="chip"><span class="core-star">★</span>'+n+'<span class="x" data-n="'+n.replace(/"/g,'&quot;')+'">✕</span></span>').join('');
  $('#corechips').querySelectorAll('.x').forEach(x=>x.onclick=()=>removeCore(x.dataset.n));
}

$('#go').addEventListener('click', run);
async function run(){
  if(!csvText) return;
  const btn = $('#go'); btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Crunching…';
  $('#err').style.display='none';
  const options = {
    n:+$('#n').value, stack:+$('#stack').value,
    maxExposure:(+$('#exp').value)/100, leverage:(+$('#lev').value)/100,
    apiKey:$('#key').value.trim(),
    cores:selectedCores.join('\n'), pool:$('#poollist').value, dff:dffText,
  };
  try{
    const res = await fetch('/api/optimize', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({csv:csvText, options})});
    const data = await res.json();
    if(data.error){ showErr(data.error); }
    else { lastResult = data; render(data); }
  }catch(e){ showErr(e.message); }
  btn.disabled=false; btn.textContent='Generate lineups';
}
function showErr(m){ $('#err').style.display='block'; $('#err').textContent = m; }

function render(d){
  $('#welcome').style.display='none';
  const cls = d.source==='props-first'?'props':d.source==='csv-only'?'csv':'season';
  const outTxt = d.out && d.out.length ? ' · OUT: '+d.out.join(', ') : '';
  $('#status').style.display='flex';
  $('#status').innerHTML = '<span class="badge '+cls+'">'+d.source+'</span>'+
    '<span class="meta">'+(d.slate.date||'')+' · '+(d.slate.games||[]).join('  ')+outTxt+'</span>';
  $('#tools').style.display = d.lineups.length ? 'block':'none';

  $('#cards').innerHTML = d.lineups.map(l => {
    const rows = l.players.map(p =>
      '<tr><td class="slot">'+p.slot+'</td><td>'+(p.core?'<span class="core-star">★</span> ':'')+p.name+'</td><td class="stat">'+p.team+'</td>'+
      '<td class="sal">$'+p.salary.toLocaleString()+'</td><td class="pr">'+p.proj+'</td></tr>').join('');
    const coreTxt = l.cores ? ' · <span class="core-star">★</span>'+l.cores : '';
    return '<div class="card"><div class="top"><span class="rank">#'+l.rank+coreTxt+'</span>'+
      '<span class="stat"><b>'+l.proj+'</b> proj · <b>'+l.ceiling+'</b> ceil · $'+l.salary.toLocaleString()+' · '+l.totalOwn+'% own</span></div>'+
      (l.stacks.length?'<div class="stacks">stacks: '+l.stacks.join(', ')+'</div>':'')+
      '<table>'+rows+'</table></div>';
  }).join('');

  // projections table
  if(d.players && d.players.length){
    $('#projwrap').style.display='block';
    renderProj(d.players, 'proj');
  }
}

let projSort = 'proj', projDir = -1;
function renderProj(players, key){
  const sorted = [...players].sort((a,b)=>{
    const av=a[key], bv=b[key];
    return (typeof av==='number') ? (av-bv)*projDir : String(av).localeCompare(String(bv))*projDir;
  });
  const cols = [['name','Player'],['team','Tm'],['pos','Pos'],['salary','Sal'],
    ['proj','Proj'],['floor','Floor'],['ceil','Ceil'],['own','Own%'],['notes','Notes']];
  const head = '<tr>'+cols.map(c=>'<th data-k="'+c[0]+'">'+c[1]+'</th>').join('')+'</tr>';
  const body = sorted.map(p=>'<tr>'+cols.map(c=>{
    const num = ['salary','proj','floor','ceil','own'].includes(c[0]);
    let v = p[c[0]]; if(c[0]==='salary') v='$'+v.toLocaleString();
    if(c[0]==='name' && p.core) v='<span class="core-star">★</span> '+v;
    return '<td class="'+(num?'num':'')+'">'+v+'</td>';
  }).join('')+'</tr>').join('');
  const t = $('#ptable'); t.innerHTML = head+body;
  t.querySelectorAll('th').forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; projDir = (projSort===k)? -projDir : -1; projSort=k; renderProj(players,k);
  });
}

$('#dl').addEventListener('click', ()=>{
  if(!lastResult) return;
  const rows = [['G','G','F','F','UTIL','UTIL'].join(',')]
    .concat(lastResult.lineups.map(l => l.upload.map(s=>'"'+s+'"').join(',')));
  const blob = new Blob([rows.join('\n')], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'wnba_lineups_'+(lastResult.slate.date||'slate')+'.csv';
  a.click();
});
</script>
</body>
</html>
"""
