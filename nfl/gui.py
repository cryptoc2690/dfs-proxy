"""The single-page GUI served by nfl/app.py. Plain HTML/CSS/JS, no external
assets, so it works offline and needs no build step."""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NFL Showdown Optimizer</title>
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
</style>
</head>
<body>
<header>
  <span class="ball"></span>
  <h1>NFL Showdown Optimizer</h1>
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
  <summary>Settings — pool &amp; cores, splits, caps, contest fill</summary>
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
      <div class="hint">Positive by default. At matched projection, chalk wins in
        showdown — the small player pool means fading it is just playing worse
        players. This is the opposite of the right setting on a main slate.</div>
    </div>
    <div>
      <label>Max share on one captain — <span id="cv">28</span>%</label>
      <input id="ccap" type="range" min="10" max="60" value="28" style="width:100%">
      <label>Fewest distinct captains</label>
      <input id="mincpt" type="number" value="10" min="1" max="40">
      <div class="hint">The captain is the highest-dispersion call in the format.
        Their own field puts about a fifth of its captaincies on one player and
        uses only ~23 captains across 8,000+ lineups.</div>
    </div>
    <div>
      <label>Contest entries so far</label>
      <input id="entries" type="number" placeholder="from the DK contest page">
      <label>Contest max entries</label>
      <input id="cap" type="number" value="237812">
      <div class="hint">The pool is guaranteed, so if the contest fills under
        84.1% every entry is worth more than it costs. This is bigger than
        anything else on this page and it is free to check.</div>
    </div>
    <div>
      <label>Expected FINAL field size</label>
      <input id="expect" type="number" placeholder="leave blank if unsure">
      <div class="hint">Their pool models a capped 50,000 opponents. If the real
        contest fills past that, duplication is understated by the ratio.</div>
      <label>Min projection for a roster spot</label>
      <input id="minproj" type="number" value="2" step="0.5">
    </div>
    <div>
      <label>Sharp's pool — one name per line</label>
      <textarea id="pool" placeholder="Jaxon Smith-Njigba&#10;Drake Maye&#10;..."></textarea>
      <label>Sharp's cores — one name per line</label>
      <textarea id="cores" placeholder="one per line"></textarea>
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
  </div>
  <div id="out"></div>
</main>

<script>
const $ = s => document.querySelector(s);
const files = {proj:null, field:null, dk:null};
let result = null;

function slot(key, el){
  const set = (name, ok) => {
    el.classList.toggle('loaded', !!ok);
    el.classList.toggle('req', !ok);
    el.querySelector('.fstate').textContent = ok ? name : el.dataset.empty;
    $('#go').disabled = !files.proj;
  };
  el.dataset.empty = el.querySelector('.fstate').textContent;
  const read = f => {
    if(!f) return;
    const r = new FileReader();
    r.onload = e => { files[key] = e.target.result; set(f.name, true); };
    r.readAsText(f);
  };
  el.addEventListener('click', () => {
    const i = document.createElement('input');
    i.type='file'; i.accept='.csv,text/csv';
    i.onchange = () => read(i.files[0]);
    i.click();
  });
  ['dragenter','dragover'].forEach(ev => el.addEventListener(ev, e => {
    e.preventDefault(); el.classList.add('over');
  }));
  ['dragleave','drop'].forEach(ev => el.addEventListener(ev, e => {
    e.preventDefault(); el.classList.remove('over');
  }));
  el.addEventListener('drop', e => read(e.dataTransfer.files[0]));
}
slot('proj', $('#s_proj'));
slot('field', $('#s_field'));
slot('dk', $('#s_dk'));

$('#lean').addEventListener('input', e => {
  const v = e.target.value/100;
  $('#lv').textContent = (v>=0?'+':'') + v.toFixed(2);
});
$('#ccap').addEventListener('input', e => { $('#cv').textContent = e.target.value; });

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
          ownLean: num('#lean',35)/100,
          captainCap: num('#ccap',28)/100,
          minCaptains: num('#mincpt',10),
          minProj: num('#minproj',2),
          entriesAtBuild: num('#entries',0),
          fieldCap: num('#cap',0),
          expectEntries: num('#expect',0),
          pool: $('#pool').value, cores: $('#cores').value
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
  $('#slate').textContent = (d.teams||[]).join(' @ ') + ' · showdown';
  const arms = Object.entries(s.arms).map(([k,v]) => k+' '+v).join(' / ');
  const splits = Object.entries(s.splits).map(([k,v]) => k+' '+v).join('  ');
  h += '<div class="cards">'
    + card(s.n, 'lineups ('+arms+')')
    + card(splits, 'team splits')
    + card(s.captains, 'distinct captains')
    + card('$'+s.salaryLo.toLocaleString()+'–'+s.salaryHi.toLocaleString(), 'salary used')
    + card(s.projAvg, 'avg projection')
    + card(s.ownAvg, 'avg ownership sum')
    + card(s.dupeAvg, 'avg expected duplicates')
    + (s.fill!=null ? card(s.fill+'%', 'contest full') : '')
    + '</div>';
  h += '<div style="font-size:12.5px;color:var(--muted);margin-bottom:6px">Top captains: '
     + s.topCaptains.map(c => esc(c[0])+' '+c[1]).join(' · ') + '</div>';
  h += '<div class="tblwrap"><table><thead><tr>'
     + '<th>#</th><th>arm</th><th>split</th><th>captain</th><th>flex</th>'
     + '<th class="num">salary</th><th class="num">proj</th>'
     + '<th class="num">own</th><th class="num">win%</th><th class="num">dupes</th>'
     + '</tr></thead><tbody>';
  d.lineups.forEach((l,i) => {
    const cpt = l.players[0], flex = l.players.slice(1);
    h += '<tr><td class="num">'+(i+1)+'</td>'
      + '<td><span class="arm '+l.source+'">'+l.source+'</span></td>'
      + '<td>'+l.split+'</td>'
      + '<td class="cpt">'+esc(cpt.name)+' <span style="color:var(--muted)">('+cpt.team+')</span></td>'
      + '<td>'+flex.map(p => esc(p.name)+' <span style="color:var(--muted)">('+p.team+')</span>').join(', ')+'</td>'
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
  a.download = 'DKEntries_showdown_upload.csv';
  a.click();
});
</script>
</body>
</html>
"""
