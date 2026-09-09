"""The single-page GUI served by nfl/app.py. Plain HTML/CSS/JS, no external
assets, so it works offline and needs no build step."""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NFL Optimizer</title>
<style>
  :root{
    --bg:#0e1116; --panel:#171b22; --panel2:#1e232c; --line:#2a313c;
    --text:#e6e9ee; --muted:#9aa4b2; --accent:#4c8dff; --accent2:#ff8a3c;
    --good:#39d98a; --warn:#e0a030; --chip:#232935;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;
    align-items:center;gap:12px}
  header h1{font-size:18px;margin:0;letter-spacing:.3px}
  header .ball{width:24px;height:14px;border-radius:50%/50%;
    background:linear-gradient(135deg,#8b5a2b,#c8843f);border:1px solid #e6d5b8}
  header .sub{color:var(--muted);font-size:13px;margin-left:auto}
  label{display:block;font-size:12.5px;color:var(--muted);margin:12px 0 5px}
  input[type=number],input[type=text],textarea,select{width:100%;
    background:var(--panel2);border:1px solid var(--line);color:var(--text);
    border-radius:8px;padding:8px 10px;font-size:14px;font-family:inherit}
  textarea{resize:vertical;min-height:60px;line-height:1.35}
  input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent)}

  #filerail{display:flex;flex-wrap:wrap;border-bottom:1px solid var(--line);
    background:var(--panel)}
  .fslot{flex:1 1 220px;padding:11px 16px;border-right:1px solid var(--line);
    cursor:pointer;min-width:0;transition:.15s}
  .fslot b{display:block;font-size:12.5px;white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis}
  .fslot .fstate{display:block;font-size:11px;color:var(--muted);
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .fslot:hover,.fslot.over{background:var(--panel2)}
  .fslot.loaded b{color:var(--good)}
  .fslot.req b{color:var(--accent2)}

  details#setwrap{border-bottom:1px solid var(--line);background:var(--panel)}
  details#setwrap>summary{cursor:pointer;padding:9px 22px;font-size:12.5px;
    color:var(--muted);list-style:none}
  details#setwrap>summary::-webkit-details-marker{display:none}
  details#setwrap>summary::before{content:"\2699  ";opacity:.7}
  details#setwrap>summary:hover{color:var(--text)}
  .setgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
    gap:20px;padding:6px 22px 20px}
  .setgrid label:first-child{margin-top:0}
  .hint{font-size:11.5px;color:var(--muted);margin-top:5px;line-height:1.45}

  main{padding:18px 22px;max-width:1180px}
  button{background:var(--accent);color:#06101f;border:0;border-radius:9px;
    padding:11px 22px;font-size:14.5px;font-weight:650;cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  button.alt{background:var(--panel2);color:var(--text);border:1px solid var(--line)}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
  .note{border-radius:9px;padding:9px 12px;margin-bottom:7px;font-size:13.5px;
    border:1px solid var(--line);background:var(--panel)}
  .note.good{border-color:#2c6b4a;background:#14251d;color:#a8e0c4}
  .note.warn{border-color:#7a5a1e;background:#26200f;color:#e8cf9a}
  .note.info{color:#b9c4d2}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:10px;margin:14px 0}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
    padding:11px 13px}
  .card b{display:block;font-size:19px;font-variant-numeric:tabular-nums}
  .card span{font-size:11.5px;color:var(--muted)}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
  th{position:sticky;top:0;background:var(--panel2);text-align:left;padding:7px 8px;
    font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
    border-bottom:1px solid var(--line)}
  td{padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  tbody tr:hover td{background:var(--panel2)}
  .arm{font-size:10.5px;padding:1px 6px;border-radius:9px;border:1px solid var(--line)}
  .arm.mine{color:#7ec8ff;border-color:#2e5f8f}
  .arm.vendor{color:#c8a0e8;border-color:#5b3f7a}
  .cpt{color:var(--accent2);font-weight:650}
  .tblwrap{max-height:60vh;overflow:auto;border:1px solid var(--line);border-radius:9px}
  .spin{display:inline-block;width:13px;height:13px;margin-right:7px;
    border:2px solid rgba(255,255,255,.3);border-top-color:#06101f;border-radius:50%;
    animation:sp .7s linear infinite;vertical-align:-2px}
  @keyframes sp{to{transform:rotate(360deg)}}
  #welcome{color:var(--muted);font-size:14px;line-height:1.7;max-width:760px}
  #welcome code{background:var(--panel2);padding:2px 6px;border-radius:5px;
    font-size:12.5px}
  .fslot.bad b{color:#e08080}
  .fslot.bad .fstate{color:#e08080}

  /* ---- type-ahead picker ---- */
  .pick{position:relative}
  .chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
  .chip{background:var(--chip);border:1px solid var(--line);border-radius:14px;
    padding:2px 9px;font-size:12.5px;cursor:pointer;user-select:none}
  .chip:hover{border-color:#e08080;color:#e08080}
  .chip::after{content:" \00d7";color:var(--muted);font-size:11px}
  .chip.core{border-color:var(--accent2);color:#ffb27a}
  .menu{position:absolute;z-index:40;left:0;right:0;top:100%;margin-top:3px;
    background:var(--panel2);border:1px solid var(--line);border-radius:9px;
    max-height:230px;overflow:auto;display:none;box-shadow:0 8px 22px rgba(0,0,0,.5)}
  .menu.open{display:block}
  .menu div{padding:7px 10px;cursor:pointer;font-size:13px;display:flex;gap:8px}
  .menu div:hover,.menu div.sel{background:#2a3340}
  .menu .mt{color:var(--muted);font-size:11.5px;margin-left:auto;
    font-variant-numeric:tabular-nums}
  .picknote{font-size:11.5px;color:var(--muted);margin-top:5px}
  .sd-only,.cl-only{display:none}
  body.sd .sd-only,body.cl .cl-only{display:block}
  .trio{display:flex;gap:6px}
  .trio input{text-align:right}
</style>
</head>
<body>
<header>
  <span class="ball"></span>
  <h1 id="title">NFL Optimizer</h1>
  <span class="sub" id="slate">drop your files below</span>
</header>

<div id="filerail">
  <div class="fslot req" id="s_proj"><b>1 · Stokastic projections</b>
    <span class="fstate">required — drop or click</span></div>
  <div class="fslot req" id="s_field"><b>2 · Stokastic lineups</b>
    <span class="fstate">the opponent field — drop or click</span></div>
  <div class="fslot req" id="s_dk"><b>3 · DK entries export</b>
    <span class="fstate">needed to upload — drop or click</span></div>
</div>

<details id="setwrap">
  <summary>Settings — pool &amp; cores, splits, caps, contest size</summary>
  <div class="setgrid">
    <div>
      <label>Lineups</label>
      <input id="n" type="number" value="150" min="1" max="150">
      <label>How many are OURS (rest come from their pool)</label>
      <input id="split" type="number" value="75" min="0" max="150">
      <div class="hint">Splitting inside one contest is the only way to compare
        the two without slate luck. Both arms are tagged in the log.</div>
    </div>
    <div>
      <label>Ownership lean — <span id="lv">+0.35</span>
        <span style="color:var(--muted)">(&minus; fade · + consensus)</span></label>
      <input id="lean" type="range" min="-100" max="100" value="35" style="width:100%">
      <div class="hint sd-only">Positive by default. At matched projection, chalk
        wins in showdown — the small player pool means fading it is just playing
        worse players. This is the opposite of the right setting on a main
        slate.</div>
      <div class="hint cl-only">Neutral by default. Ranking on win probability
        already fades chalk hard on a main slate all by itself — these lineups
        land near the field's 12th ownership percentile with this at zero — so
        there is nothing left for a negative lean to do. Move it if your read
        says otherwise.</div>
    </div>
    <div class="sd-only">
      <label>Max share on one captain — <span id="cv">28</span>%</label>
      <input id="ccap" type="range" min="10" max="60" value="28" style="width:100%">
      <label>Fewest distinct captains</label>
      <input id="mincpt" type="number" value="10" min="1" max="40">
      <div class="hint">The captain is the highest-dispersion call in the format.
        Their own field puts about a fifth of its captaincies on one player and
        uses only ~23 captains across 8,000+ lineups.</div>
    </div>
    <div class="cl-only">
      <label>Stack shape — share of lineups at QB+3 / QB+2 / QB+1</label>
      <div class="trio">
        <input id="st3" type="number" value="45" min="0" max="100">
        <input id="st2" type="number" value="40" min="0" max="100">
        <input id="st1" type="number" value="15" min="0" max="100">
      </div>
      <label>Bring-back (a player from your QB's opponent) — <span id="bv">15</span>%</label>
      <input id="bb" type="range" min="0" max="60" value="15" style="width:100%">
      <div class="hint">Stacking is the main lever on a main slate: each extra
        pass-catcher with your QB is worth about +31% relative win probability at
        matched projection, and the field builds QB+3 under 5% of the time. A
        bring-back does the opposite — it raises the floor and cuts the tail, so
        it is kept as a small insurance block.</div>
    </div>
    <div class="cl-only">
      <label>Max share on one QB — <span id="qv">35</span>%</label>
      <input id="qbcap" type="range" min="10" max="100" value="35" style="width:100%">
      <label>Max share on one defense — <span id="dv">30</span>%</label>
      <input id="dstcap" type="range" min="10" max="100" value="30" style="width:100%">
      <div class="hint">QB exposure IS stack exposure — the QB decides the whole
        correlation structure of the lineup — so it binds tighter than the
        player cap.</div>
    </div>
    <div>
      <label>Contest size — max entries</label>
      <input id="cap" type="number" placeholder="e.g. 237812">
      <label>How full it will get — <span id="fv">100</span>%</label>
      <input id="fillpct" type="range" min="10" max="100" value="100"
             style="width:100%">
      <div class="hint">Off the DK contest page. Assumed to fill, which these
        normally do — mark it down only when you are entering days early and
        expect it to stay short. Stokastic models just 50,000 opponents on
        showdown and 10,000 on a main slate, so without this number duplication
        is measured against a field several times too small.</div>
      <label>Min projection for a roster spot</label>
      <input id="minproj" type="number" value="2" step="0.5">
      <div class="hint cl-only">3.0 is the main-slate default; this box starts
        at the showdown value.</div>
    </div>
    <div>
      <label>Sharp's pool</label>
      <div class="pick">
        <input id="poolin" type="text" autocomplete="off" disabled
               placeholder="drop the projections file first">
        <div class="menu" id="poolmenu"></div>
      </div>
      <div class="chips" id="poolchips"></div>
      <div class="picknote" id="poolnote"></div>
      <label>Players from outside the pool, per lineup</label>
      <select id="offpool">
        <option value="0" selected>0 — build only from the pool</option>
        <option value="1">1 — allow one</option>
        <option value="2">2 — allow two</option>
        <option value="none">No limit — the pool is only a shortlist</option>
      </select>
      <div class="picknote sd-only">Only applies if you picked a pool. A hard
        filter is defensible here because the whole showdown board is ~40
        players.</div>
      <div class="picknote cl-only">Only applies if you picked a pool. On a main
        slate a sheet that cannot cover all nine roster seats is treated as a
        shortlist instead of a filter, and the build says so.</div>
    </div>
    <div>
      <label>Sharp's cores</label>
      <div class="pick">
        <input id="corein" type="text" autocomplete="off" disabled
               placeholder="drop the projections file first">
        <div class="menu" id="coremenu"></div>
      </div>
      <div class="chips" id="corechips"></div>
      <div class="picknote">Type a few letters, click or press Enter. Cores count
        as in-pool automatically. Click a chip to remove it.</div>
    </div>
  </div>
</details>

<main>
  <div class="row">
    <button id="go" disabled>Build lineups</button>
    <button id="dl" class="alt" style="display:none">&#11015; Download DK file</button>
    <span id="status" style="color:var(--muted);font-size:13px"></span>
  </div>
  <div id="welcome">
    <p><b>Drop three files above.</b></p>
    <p>1 and 2 come from Stokastic — the projections export and the lineups
      export. <b>Pull them in the same minute:</b> the two carry separate
      ownership snapshots and they drift apart during the day.</p>
    <p>3 is your DK entries export. Enter or reserve your entries on DK first,
      then download it. It is the only file carrying your Entry IDs and DK's
      per-slot player IDs, and without it there is nothing to upload.</p>
    <p>Their lineup file is not a list of picks — it is a model of your
      opponents. We use it to score against and to estimate duplication, and
      build our own lineups on top.</p>
    <p><b>Showdown or main slate is worked out from your files</b>, so the same
      three slots handle both. The settings below change to match.</p>
  </div>
  <div id="out"></div>
</main>

<script>
const $ = s => document.querySelector(s);
const files = {proj:null, field:null, dk:null};
let result = null, roster = [], fmt = 'showdown', fmtFromDk = false;

// Showdown and classic are different games, not two sizes of one, so the page
// has to say which it is reading. The DK entries export is authoritative — its
// roster columns literally spell out the format — and the projections file is
// the fallback, since a showdown board is one game and a main slate a dozen.
function setFmt(f, fromDk){
  if(fmtFromDk && !fromDk) return;        // never downgrade off the DK answer
  fmt = f; if(fromDk) fmtFromDk = true;
  document.body.classList.toggle('sd', f === 'showdown');
  document.body.classList.toggle('cl', f === 'classic');
  $('#title').textContent = f === 'showdown' ? 'NFL Showdown Optimizer'
                                             : 'NFL Main Slate Optimizer';
  // The ownership lean genuinely points opposite ways in the two formats, so
  // the default follows the format until the user touches the slider.
  if(!leanTouched){
    $('#lean').value = f === 'showdown' ? 35 : 0;
    $('#lean').dispatchEvent(new Event('input'));
  }
}

// The browser must not navigate away when a file is dropped anywhere else.
['dragover','drop'].forEach(ev =>
  window.addEventListener(ev, e => e.preventDefault()));

function slot(key, el){
  const state = el.querySelector('.fstate');
  const empty = state.textContent;
  // A REAL input, in the DOM. A detached one built on the fly does not reliably
  // open the picker in Safari, which is why this slot appeared to do nothing.
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.csv,text/csv,text/plain';
  input.style.display = 'none';
  el.appendChild(input);

  const mark = (cls, msg) => {
    el.classList.remove('loaded','req','bad');
    if(cls) el.classList.add(cls);
    state.textContent = msg;
    $('#go').disabled = !files.proj;
  };

  const read = f => {
    if(!f){ return; }
    state.textContent = 'reading ' + f.name + '…';
    const r = new FileReader();
    r.onerror = () => mark('bad', 'could not read that file');
    r.onload = async e => {
      const text = e.target.result;
      try {
        const res = await fetch('/api/check', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({kind:key, text:text})
        });
        const d = await res.json();
        if(d.ok){
          files[key] = text;
          mark('loaded', '✓ ' + f.name + ' — ' + d.msg);
          if(d.format) setFmt(d.format, key === 'dk');
          if(key === 'proj') loadRoster(text);
        } else {
          files[key] = null;
          mark('bad', '✗ ' + f.name + ' — ' + d.msg);
        }
      } catch(err){
        files[key] = text;               // server unreachable: keep it anyway
        mark('loaded', '✓ ' + f.name);
      }
    };
    r.readAsText(f);
  };

  input.addEventListener('change', () => { read(input.files[0]); input.value=''; });
  el.addEventListener('click', e => { if(e.target !== input) input.click(); });
  ['dragenter','dragover'].forEach(ev => el.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation(); el.classList.add('over');
  }));
  ['dragleave','dragend'].forEach(ev => el.addEventListener(ev, e => {
    e.preventDefault(); el.classList.remove('over');
  }));
  el.addEventListener('drop', e => {
    e.preventDefault(); e.stopPropagation(); el.classList.remove('over');
    const dt = e.dataTransfer;
    read(dt.files && dt.files[0]);
  });
  mark('req', empty);
}
slot('proj', $('#s_proj'));
slot('field', $('#s_field'));
slot('dk', $('#s_dk'));

// ---- type-ahead pickers, enabled once the slate is known ----
const sel = {pool:new Set(), core:new Set()};

async function loadRoster(text){
  try{
    const res = await fetch('/api/players', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({proj:text})
    });
    const d = await res.json();
    if(d.error) return;
    roster = d.players || [];
    if(d.format) setFmt(d.format, false);
    $('#slate').textContent = slateLabel(d.teams||[]);
    ['poolin','corein'].forEach(id => {
      const el = $('#'+id);
      el.disabled = false;
      el.placeholder = 'type a name — ' + roster.length + ' players';
    });
  }catch(e){ /* picker just stays disabled */ }
}

function picker(kind, inputId, menuId, chipsId){
  const input = $('#'+inputId), menu = $('#'+menuId), chips = $('#'+chipsId);
  let hits = [], cur = -1;

  const draw = () => {
    chips.innerHTML = [...sel[kind]].map(n =>
      '<span class="chip'+(kind==='core'?' core':'')+'" data-n="'+esc(n)+'">'
      + esc(n)+'</span>').join('');
    chips.querySelectorAll('.chip').forEach(c =>
      c.addEventListener('click', () => { sel[kind].delete(c.dataset.n); draw(); }));
    if(kind === 'pool'){
      const n = sel.pool.size + sel.core.size;
      $('#poolnote').textContent = n
        ? n + ' player(s) in the pool (cores included automatically)'
        : 'Leave empty to build from the whole slate.';
    }
  };

  const close = () => { menu.classList.remove('open'); cur = -1; };

  const show = () => {
    const q = input.value.trim().toLowerCase();
    if(!q){ close(); return; }
    // Rank a match on the start of a NAME above one buried mid-word, so "dr"
    // offers Drake before Rhamondre. Within a tier, higher projection first —
    // roster already arrives sorted that way.
    const score = p => {
      const n = p.name.toLowerCase();
      if(n.startsWith(q)) return 0;
      if(n.split(/[\s.'-]+/).some(w => w.startsWith(q))) return 1;
      return 2;
    };
    hits = roster
      .filter(p => p.name.toLowerCase().includes(q) && !sel[kind].has(p.name))
      .map((p,i) => [score(p), i, p])
      .sort((a,b) => a[0]-b[0] || a[1]-b[1])
      .slice(0, 8).map(x => x[2]);
    if(!hits.length){ close(); return; }
    menu.innerHTML = hits.map((p,i) =>
      '<div data-i="'+i+'"'+(i===cur?' class="sel"':'')+'>'
      + '<span>'+esc(p.name)+'</span>'
      + '<span class="mt">'+p.team+' '+p.pos+' · $'+p.salary.toLocaleString()
      + ' · '+p.proj.toFixed(1)+'</span></div>').join('');
    menu.classList.add('open');
    menu.querySelectorAll('div[data-i]').forEach(d =>
      d.addEventListener('mousedown', e => {
        e.preventDefault(); add(hits[+d.dataset.i]);
      }));
  };

  const add = p => {
    if(!p) return;
    sel[kind].add(p.name);
    if(kind === 'core') sel.pool.delete(p.name);   // a core is already in-pool
    input.value = ''; close(); draw();
    if(kind === 'core') pickers.pool.draw();
    input.focus();
  };

  input.addEventListener('input', () => { cur = -1; show(); });
  input.addEventListener('focus', show);
  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('keydown', e => {
    if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
      e.preventDefault();
      if(!hits.length) return;
      cur = (cur + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length;
      show();
    } else if(e.key === 'Enter'){
      e.preventDefault(); add(hits[cur >= 0 ? cur : 0]);
    } else if(e.key === 'Escape'){ close(); }
  });
  draw();
  return {draw};
}
const pickers = {};
pickers.pool = picker('pool', 'poolin', 'poolmenu', 'poolchips');
pickers.core = picker('core', 'corein', 'coremenu', 'corechips');

function slateLabel(teams){
  if(fmt === 'showdown') return teams.join(' @ ') + ' · showdown';
  return teams.length + ' teams · ' + Math.round(teams.length/2) + ' games · main slate';
}

let leanTouched = false;
$('#lean').addEventListener('input', e => {
  const v = e.target.value/100;
  $('#lv').textContent = (v>=0?'+':'') + v.toFixed(2);
});
$('#lean').addEventListener('change', () => { leanTouched = true; });
$('#ccap').addEventListener('input', e => { $('#cv').textContent = e.target.value; });
$('#qbcap').addEventListener('input', e => { $('#qv').textContent = e.target.value; });
$('#dstcap').addEventListener('input', e => { $('#dv').textContent = e.target.value; });
$('#bb').addEventListener('input', e => { $('#bv').textContent = e.target.value; });
$('#fillpct').addEventListener('input', e => { $('#fv').textContent = e.target.value; });
setFmt('showdown', false);

const num = (sel, d) => { const v = parseFloat($(sel).value); return isNaN(v) ? d : v; };

$('#go').addEventListener('click', async () => {
  const b = $('#go'); b.disabled = true;
  b.innerHTML = '<span class="spin"></span>Simulating…';
  $('#status').textContent = ''; $('#welcome').style.display='none';
  $('#out').innerHTML = ''; $('#dl').style.display='none';
  try {
    const res = await fetch('/api/build', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        proj: files.proj, field: files.field, dk: files.dk,
        options: {
          n: num('#n',150), split: num('#split',75),
          // Deliberately NOT sending the format. The server works it out from
          // the files and says which signal it used; the page's own guess only
          // decides which settings to show.
          ownLean: num('#lean',0)/100,
          captainCap: num('#ccap',28)/100,
          minCaptains: num('#mincpt',10),
          qbCap: num('#qbcap',35)/100,
          dstCap: num('#dstcap',30)/100,
          bringBack: num('#bb',15)/100,
          stackTargets: '3:'+num('#st3',45)+',2:'+num('#st2',40)+',1:'+num('#st1',15),
          minProj: num('#minproj',2),
          fieldCap: num('#cap',0), fillPct: num('#fillpct',100),
          maxOffPool: $('#offpool').value,
          pool: [...sel.pool].join('\n'), cores: [...sel.core].join('\n')
        }
      })
    });
    const d = await res.json();
    result = d;
    render(d);
  } catch(e) {
    $('#out').innerHTML = '<div class="note warn">'+e.message+'</div>';
  }
  b.disabled = false; b.textContent = 'Build lineups';
});

function render(d){
  let h = '';
  (d.notes||[]).forEach(n => {
    h += '<div class="note '+n.type+'">'+esc(n.text)+'</div>';
  });
  if(d.error){
    h += '<div class="note warn"><b>'+esc(d.error)+'</b></div>';
    $('#out').innerHTML = h; return;
  }
  const s = d.summary;
  if(d.format) setFmt(d.format, true);
  $('#slate').textContent = slateLabel(d.teams||[]);
  const arms = Object.entries(s.arms).map(([k,v]) => k+' '+v).join(' / ');
  const splits = Object.entries(s.splits).map(([k,v]) => k+' '+v).join('  ');
  const sd = d.format !== 'classic';
  h += '<div class="cards">'
    + card(s.n, 'lineups ('+arms+')')
    + card(splits, s.shapeLabel || 'shape')
    + card(s.captains, 'distinct ' + (s.headLabel || 'captains'))
    + card('$'+s.salaryLo.toLocaleString()+'–'+s.salaryHi.toLocaleString(), 'salary used')
    + card(s.projAvg, 'avg projection')
    + card(s.ownAvg, 'avg ownership sum')
    + card(s.dupeAvg, 'avg expected duplicates')
    + '</div>';
  h += '<div style="font-size:12.5px;color:var(--muted);margin-bottom:6px">Top '
     + (s.headLabel || 'captains') + ': '
     + s.topCaptains.map(c => esc(c[0])+' '+c[1]).join(' · ') + '</div>';
  h += '<div class="tblwrap"><table><thead><tr>'
     + '<th>#</th><th>arm</th><th>' + (sd ? 'split' : 'stack') + '</th>'
     + (sd ? '<th>captain</th><th>flex</th>' : '<th>QB</th><th>rest of the roster</th>')
     + '<th class="num">salary</th><th class="num">proj</th>'
     + '<th class="num">own</th><th class="num">win%</th><th class="num">dupes</th>'
     + '</tr></thead><tbody>';
  const who = p => esc(p.name)
    + ' <span style="color:var(--muted)">('+p.team+(sd?'':' '+p.slot)+')</span>';
  d.lineups.forEach((l,i) => {
    // Showdown leads with the captain; classic leads with the QB, because that
    // is the seat the whole lineup is built around in each format.
    const lead = sd ? l.players[0]
                    : (l.players.find(p => p.slot === 'QB') || l.players[0]);
    const rest = l.players.filter(p => p !== lead);
    h += '<tr><td class="num">'+(i+1)+'</td>'
      + '<td><span class="arm '+l.source+'">'+l.source+'</span></td>'
      + '<td>'+esc(l.split)+'</td>'
      + '<td class="cpt">'+who(lead)+'</td>'
      + '<td>'+rest.map(who).join(', ')+'</td>'
      + '<td class="num">'+l.salary.toLocaleString()+'</td>'
      + '<td class="num">'+l.proj.toFixed(1)+'</td>'
      + '<td class="num">'+l.ownSum.toFixed(0)+'</td>'
      + '<td class="num">'+(l.win*100).toFixed(2)+'</td>'
      + '<td class="num">'+l.dupes.toFixed(1)+'</td></tr>';
  });
  h += '</tbody></table></div>';
  $('#out').innerHTML = h;
  if(d.dkCsv){
    $('#dl').style.display = '';
    $('#status').textContent = 'Logged '+d.lineups.length+' entries for later comparison.';
  } else {
    $('#status').textContent = 'No DK entries file — nothing to upload.';
  }
}

function card(v, label){
  return '<div class="card"><b>'+esc(String(v))+'</b><span>'+esc(label)+'</span></div>';
}
function esc(s){
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

$('#dl').addEventListener('click', () => {
  if(!result || !result.dkCsv) return;
  const b = new Blob([result.dkCsv], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(b);
  a.download = 'DKEntries_' + (fmt === 'classic' ? 'main' : 'showdown')
             + '_upload.csv';
  a.click();
});
</script>
</body>
</html>
"""
