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
  label{display:block;font-size:13px;color:var(--muted);margin:12px 0 5px}
  input[type=text],input[type=number],input[type=password],textarea,select{width:100%;background:var(--panel2);
    border:1px solid var(--line);color:var(--text);border-radius:9px;padding:9px 11px;font-size:14px;
    font-family:inherit}
  textarea{resize:vertical;min-height:58px;line-height:1.35}
  select{cursor:pointer;-webkit-appearance:none;appearance:none}
  input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent2)}
  /* ---- file rail: intake collapses to one line once loaded ---- */
  #filerail{display:flex;align-items:stretch;border-bottom:1px solid var(--line);background:var(--panel)}
  .fslot{padding:9px 15px;border-right:1px solid var(--line);cursor:pointer;min-width:0;transition:.15s}
  .fslot b{display:block;font-size:12.5px;color:var(--text);white-space:nowrap}
  .fslot .fstate{display:block;font-size:11px;color:var(--muted);white-space:nowrap}
  .fslot:hover,.fslot.over{background:var(--panel2)}
  .fslot.loaded b{color:var(--good)}
  .fslot.req b{color:var(--accent)}
  .fslot.req::after{content:"";}
  .fmeta{margin-left:auto;align-self:center;padding:0 16px;font-size:11.5px;color:var(--muted);
    font-variant-numeric:tabular-nums;text-align:right}
  @media(max-width:900px){#filerail{flex-wrap:wrap}.fmeta{margin-left:0;padding:8px 15px;width:100%;text-align:left}}

  /* ---- settings drawer ---- */
  #setwrap{border-bottom:1px solid var(--line);background:var(--panel)}
  #setwrap>summary{cursor:pointer;padding:9px 20px;font-size:12.5px;color:var(--muted);list-style:none}
  #setwrap>summary::-webkit-details-marker{display:none}
  #setwrap>summary::before{content:"⚙ ";opacity:.7}
  #setwrap>summary:hover{color:var(--text)}
  #setwrap[open]>summary{color:var(--text);border-bottom:1px solid var(--line)}
  .setgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:22px;padding:16px 20px 20px}
  .setgrid label:first-child{margin-top:0}

  /* ---- the board ---- */
  .board{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);
    align-items:start;gap:0;max-width:none;margin:0;padding:0}
  @media(max-width:1000px){.board{grid-template-columns:1fr}
    .slatecol,.outcol{max-height:none;overflow:visible}}
  .slatecol{padding:14px 18px;border-right:1px solid var(--line);min-width:0;
    overflow:hidden;max-height:calc(100vh - 190px);display:flex;flex-direction:column}
  .outcol{padding:14px 18px;min-width:0;overflow:auto;max-height:calc(100vh - 190px)}
  .colhead{display:flex;align-items:center;gap:10px;margin-bottom:8px}
  .colhead>span:first-child{font-size:12px;text-transform:uppercase;letter-spacing:.6px;
    color:var(--muted);font-weight:600}
  .colhead .count{margin-left:auto;font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums}
  #slatefilter{width:auto;flex:1;max-width:220px;padding:5px 9px;font-size:13px}
  .marklegend{display:flex;flex-wrap:wrap;gap:12px;font-size:11.5px;color:var(--muted);margin-bottom:8px}
  .marklegend span{display:flex;align-items:center;gap:5px}
  .slatewrap{overflow:auto;flex:1;min-height:0;border:1px solid var(--line);border-radius:9px}
  #slatetable{width:100%;border-collapse:collapse;font-size:13px}
  #slatetable th{position:sticky;top:0;background:var(--panel2);z-index:2;text-align:left;
    padding:7px 8px;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;
    color:var(--muted);border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
  #slatetable th:hover{color:var(--text)}
  #slatetable th.mkcol,#slatetable td.mkcol{cursor:default;width:104px}
  #slatetable td{padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
  #slatetable td.num{text-align:right;font-variant-numeric:tabular-nums}
  #slatetable tr:hover td{background:var(--panel2)}
  #slatetable tr.gone td{opacity:.4;text-decoration:line-through}
  #slatetable td.hi{color:var(--good)}
  #slatetable td.lo{color:var(--muted)}
  .mk{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;
    border-radius:5px;border:1px solid var(--line);background:var(--panel2);color:#5c6775;
    font-size:11px;font-style:normal;cursor:pointer;margin-right:3px;user-select:none;transition:.12s}
  .mk:hover{border-color:var(--muted);color:var(--text)}
  .mk.on.core{background:#3a2314;border-color:var(--accent);color:#ff8a5c}
  .mk.on.pool{background:#12303a;border-color:var(--accent2);color:#7ec8ff}
  .mk.on.rm{background:#3a1a1a;border-color:#a04040;color:#e69090}
  .mk.on.cap{background:#3a2f14;border-color:#e0a030;color:#f0c070}
  .marklegend .mk{cursor:default;margin-right:0}
  .boardsum{grid-column:1/-1;border-top:1px solid var(--line);padding-top:14px;margin-top:2px}
  .bgroup{display:flex;align-items:baseline;gap:9px;margin-bottom:7px;flex-wrap:wrap}
  .bgroup>b{font-size:11.5px;color:var(--muted);font-weight:600;min-width:74px}
  .chips{display:flex;flex-wrap:wrap;gap:6px}
  .chip{background:var(--chip);border:1px solid var(--line);border-radius:15px;padding:3px 10px;
    font-size:12.5px;cursor:pointer;user-select:none}
  .chip:hover{border-color:var(--accent);color:var(--accent)}
  .chip::after{content:" ✕";color:var(--muted);font-size:11px}
  /* a core shows as pooled without being a pool member — the engine already
     treats it as in-pool, and adding it would switch the constraint on */
  .mk.on.pool.implied{opacity:.55}

  /* ---- dock: the counts and the two things you actually press ---- */
  #dock{position:sticky;bottom:0;z-index:20;display:flex;align-items:center;gap:16px;
    padding:10px 20px;background:var(--panel);border-top:1px solid var(--line);flex-wrap:wrap}
  #dock .dk{font-size:12.5px;color:var(--muted)}
  #dock .dk b{color:var(--text);font-variant-numeric:tabular-nums}
  #dock .sep{margin-left:auto;text-align:right}
  #dock .dockbtn{width:auto;margin:0;padding:9px 20px;font-size:13.5px}
  .core-star{color:var(--accent2);font-weight:700}
  .offpool{color:var(--accent);font-weight:700}
  .riskdot{color:#e0a030}
  .riskrow td{color:var(--muted)}
  .note{background:#1e2530;border:1px solid #33506e;color:#a8c4e0;border-radius:10px;
    padding:10px 13px;margin-bottom:12px;font-size:13.5px}
  .swapcard{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:8px;background:var(--panel)}
  .swapcard.keep{opacity:.55;font-size:13px;padding:7px 12px}
  .swapcard.err{color:#e08080;font-size:13px}
  .swapcard.skipped{opacity:.5}
  .swaphdr{display:flex;align-items:flex-start;gap:9px;flex-wrap:wrap;
    font-weight:600;margin-bottom:5px}
  .swaphdr .hdrtext{flex:1;min-width:0}
  .swapchk{margin:3px 0 0;width:15px;height:15px;accent-color:var(--accent);cursor:pointer;flex:none}
  .swaptoggle{background:none;border:1px solid var(--line);color:var(--muted);
    border-radius:6px;font:inherit;font-size:11px;padding:2px 8px;cursor:pointer;flex:none}
  .swaptoggle:hover{color:var(--fg);border-color:var(--muted)}
  .cmp{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:9px}
  .cmp h5{margin:0 0 5px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
  .cmp table{width:100%;border-collapse:collapse;font-size:12px}
  .cmp td{padding:3px 4px;border-bottom:1px solid var(--line);white-space:nowrap}
  .cmp td.slot{color:var(--muted);width:34px;font-size:10px}
  .cmp td.num{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
  .cmp tr.diff td{background:rgba(255,138,90,.10)}
  .cmp tr.diff td.slot{color:var(--accent)}
  .cmp .lockmark{color:var(--muted);font-size:10px}
  .cmp tfoot td{border-bottom:none;padding-top:6px;color:var(--fg);font-weight:600}
  @media(max-width:900px){ .cmp{grid-template-columns:1fr} }
  .swaprow{font-size:13px;padding:2px 0}
  .swaprow.out{color:#e88}
  .swaprow.in{color:var(--good)}
  .gain{color:var(--accent);font-weight:700}
  .muted{color:var(--muted);font-weight:400;font-size:12px}
  input:disabled{opacity:.5}
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
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;margin-bottom:14px}
  .card table td.slot,.card table td.stat,.card table td.sal,
  .card table td.pr{white-space:nowrap}
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

<!-- Files live in one rail across the top. Cold start it fills the screen;
     once loaded it collapses, because a fresh LineStar pull mid-slate has to
     be one click rather than a trip to another screen. -->
<div id="filerail">
  <div class="fslot req" id="f-slate" data-for="file">
    <b>LineStar</b><span class="fstate">required — drop or click</span>
    <input id="file" type="file" accept=".csv" hidden>
  </div>
  <div class="fslot" id="f-min" data-for="minfile">
    <b>Daily projections</b><span class="fstate">minutes + stuffer floor</span>
    <input id="minfile" type="file" accept=".csv" hidden>
  </div>
  <div class="fslot" id="f-dk" data-for="swapfile">
    <b>DK entries</b><span class="fstate">real DK IDs · unlocks late swap</span>
    <input id="swapfile" type="file" accept=".csv" hidden>
  </div>
  <div class="fslot" id="f-con" data-for="confile">
    <b>Contest standings</b><span class="fstate">your live rank + field ownership</span>
    <input id="confile" type="file" accept=".csv" hidden>
  </div>
  <div class="fmeta" id="fmeta">no slate loaded</div>
</div>

<details id="setwrap">
  <summary id="setsum">Settings</summary>
  <div class="setgrid">
    <div>
      <label>Lineups</label><input id="n" type="number" value="20" min="1" max="150">
      <label>Min game stack</label><input id="stack" type="number" value="2" min="1" max="4">
      <label>Minimum cores per lineup</label>
      <select id="mincores">
        <option value="0">0 — no requirement</option>
        <option value="1" selected>1 — every lineup built around ≥1 core</option>
        <option value="2">2 — every lineup built around ≥2</option>
      </select>
      <label>Off-pool players allowed per lineup</label>
      <select id="offpool">
        <option value="0" selected>0 — build only from the pool</option>
        <option value="1">1 — allow one, only if the data earns it</option>
        <option value="2">2 — allow up to two</option>
      </select>
    </div>
    <div>
      <label>Max exposure — <span id="expv">60</span>%</label>
      <input id="exp" class="slider" type="range" min="10" max="100" value="60">
      <label>Cap the 🔒 players at <span id="capv">30</span>% <span id="capwho" class="muted"></span></label>
      <input id="cappct" class="slider" type="range" min="5" max="60" value="30">
      <div class="hint">Applies to the players you mark 🔒 <b>on the slate row</b> — there is no
        list to type here. Everyone else stays at your max exposure. Reins in one heavy play
        without capping the whole board.</div>
    </div>
    <div>
      <label>Ownership lean — <span id="levv">+0.35</span> <span class="muted">(− fade · + consensus)</span></label>
      <input id="lev" class="slider" type="range" min="-100" max="100" value="35">
      <div class="hint">Leans <b>toward</b> the field by default. Sorted by within-slate
        ownership, the chalkiest fifth hit the top 1% at 3.0% and cashed 36% against 0.4%
        and 10% for the least-owned, in 19 of 23 contests — and our own lineups averaged the
        42nd ownership percentile against the winning tier's 70th. Kept modest because
        ownership is a proxy for consensus quality, not an edge by itself.</div>
      <label>Stack seeking — <span id="stkv">50</span>% of lineups</label>
      <input id="stk" class="slider" type="range" min="0" max="100" value="50">
      <div class="hint">Builds this share AROUND a correlation stack: 3 from a high-implied
        team, or 5 from the biggest game (which must clear 178 combined). Low-total stacks
        are avoided — those finish worse than not stacking at all.</div>
      <label>Sub-10%-owned players allowed per lineup</label>
      <select id="sub10">
        <option value="1" selected>1 — the default the data supports</option>
        <option value="0">0 — none at all</option>
        <option value="2">2</option>
        <option value="off">No limit</option>
      </select>
      <div class="hint">58% of top-1% lineups carried none, against 37% of the field and 30%
        of ours, and each extra one lowered cash and top-1% even at matched projection.
        Cores are always exempt — your conviction play is never what gets cut.</div>
      <label>Two-game slate shape rules</label>
      <select id="slaterules">
        <option value="on" selected>On — no 3-3 split, majority in the higher-owned game</option>
        <option value="off">Off</option>
      </select>
      <div class="hint">Only bites on a two-game slate. 4-2 beat the balanced 3-3 on cash in
        7 of 7 slates, and loading the higher-owned game won 7 of 7 (29.8% vs 12.3%).</div>
      <label>Late swap fires on</label>
      <select id="newsonly">
        <option value="on" selected>News only — a scratch, a benching, a projection cut</option>
        <option value="off">Anything that scores better (re-optimise freely)</option>
      </select>
      <div class="hint">Swaps forced by news gained 24.5 points an entry and went 15 for 15.
        Re-optimising slots nobody had said anything about averaged 3.4 with 15 of 29
        positive — noise — and one such night cost 125 points across 8 entries.</div>
    </div>
    <div class="boardsum" id="boardsum"></div>
  </div>
</details>

<main class="board">
  <section class="slatecol">
    <div class="colhead">
      <span>Slate</span>
      <input id="slatefilter" type="text" placeholder="filter…" autocomplete="off" disabled>
      <span class="count" id="slatecount"></span>
    </div>
    <div class="marklegend">
      <span><i class="mk on core">★</i> core</span>
      <span><i class="mk on pool">◆</i> pool</span>
      <span><i class="mk on rm">🚫</i> remove</span>
      <span><i class="mk on cap">🔒</i> cap</span>
      <span class="muted">a core is never treated as off-pool</span>
    </div>
    <div class="slatewrap"><table id="slatetable"></table></div>
    <div id="slateempty" class="empty">Drop your LineStar CSV to see the slate.</div>
  </section>

  <section class="outcol">
    <div id="err" class="err" style="display:none"></div>
    <div id="note" class="note" style="display:none"></div>
    <div id="status" class="status" style="display:none"></div>
    <div id="tools" style="display:none;margin-bottom:14px">
      <button id="dl" class="btn ghost" style="width:auto;margin:0;padding:9px 16px">⬇ Download lineups CSV</button>
      <button id="dkfill" class="btn ghost" style="width:auto;margin:0 0 0 8px;padding:9px 16px">⬆ Fill DK entries file</button>
      <input id="dkfile" type="file" accept=".csv" hidden>
      <div class="hint" style="margin-top:6px">Slots these lineups into your DK entries with real
        DK IDs — download and upload straight back to DraftKings.</div>
    </div>
    <div id="swapresults" style="display:none;margin-bottom:14px"></div>
    <div id="cards" class="cards"></div>
    <div id="welcome" class="empty">Drop your LineStar CSV and build.</div>
    <div id="coach" class="coach" style="display:none"></div>
    <details id="expwrap" style="display:none">
      <summary>Exposure — how spread your lineups are</summary>
      <table class="ptable" id="exptable"></table>
    </details>
    <details id="projwrap" style="display:none">
      <summary>Player projections</summary>
      <table class="ptable" id="ptable"></table>
    </details>
  </section>
</main>

<div id="dock">
  <span class="dk"><b id="d-core">0</b> cores</span>
  <span class="dk"><b id="d-pool">0</b> pool</span>
  <span class="dk"><b id="d-rm">0</b> removed</span>
  <span class="dk"><b id="d-cap">0</b> capped</span>
  <span class="dk sep" id="d-set"></span>
  <button id="swapgo" class="btn ghost dockbtn" disabled>🔄 Late swap</button>
  <button id="go" class="btn dockbtn" disabled>Build lineups</button>
</div>

<script>
const $ = s => document.querySelector(s);
let csvText=null, lastResult=null, minText='', swapText='', conText='';
let slate=[], medImplied=0, swapData=null, swapTake=[];
// The four build levers. They used to be four type-aheads you had to spell names
// into; they are now toggles on the slate row itself.
const sel = {core:new Set(), pool:new Set(), remove:new Set(), cap:new Set()};
const GROUPS = [['core','Cores'],['pool','Pool'],['remove','Removed'],['cap','Capped']];

// ---- settings ----
const setSummary = () => {
  $('#setsum').textContent = 'Settings — ' + $('#n').value + ' lineups · ' +
    $('#exp').value + '% max exposure · own lean ' + fmtLean($('#lev').value) +
    ' · stack seeking ' + $('#stk').value + '%';
  $('#d-set').textContent = $('#n').value + ' lineups · ' + $('#exp').value + '% cap';
};
$('#exp').addEventListener('input', e => { $('#expv').textContent = e.target.value; setSummary(); });
const fmtLean = v => (v >= 0 ? '+' : '') + (v/100).toFixed(2);
$('#lev').addEventListener('input', e => { $('#levv').textContent = fmtLean(e.target.value); setSummary(); });
$('#stk').addEventListener('input', e => { $('#stkv').textContent = e.target.value; setSummary(); });
$('#cappct').addEventListener('input', e => $('#capv').textContent = e.target.value);
$('#n').addEventListener('input', setSummary);
setSummary(); paintDock();

// ---- the file rail ----
function csvSplit(line){ const out=[]; let cur='',q=false; for(const ch of line){ if(ch==='"')q=!q; else if(ch===','&&!q){out.push(cur);cur='';} else cur+=ch; } out.push(cur); return out; }

// Parse the whole LineStar row, not just the name: the slate table needs salary,
// projection, ownership and the team's implied total to be useful as a picker.
function parseSlate(text){
  const lines = text.replace(/\r/g,'').split('\n').filter(l=>l.trim());
  if(!lines.length) return [];
  const cols = csvSplit(lines[0]).map(c=>c.trim().toLowerCase());
  const at = n => cols.indexOf(n);
  const iName=at('name'), iTeam=at('team'), iPos=at('position'), iSal=at('salary'),
        iProj=at('projected'), iOwn=at('projown'), iImp=at('vegasimplied');
  const out=[], seen=new Set();
  for(let i=1;i<lines.length;i++){
    const c=csvSplit(lines[i]);
    const nm=(c[iName]||'').trim();
    if(!nm || seen.has(nm)) continue;
    const proj=parseFloat(c[iProj])||0;
    if(proj<=0) continue;           // out / deep bench — nothing to pick
    seen.add(nm);
    out.push({name:nm, team:(c[iTeam]||'').trim(), pos:(c[iPos]||'').trim().split('/')[0],
      salary:parseInt(c[iSal])||0, proj:proj,
      own:parseFloat(c[iOwn])||0, implied:parseFloat(c[iImp])||0});
  }
  return out;
}
function slateMedianImplied(rows){
  const t={}; rows.forEach(r=>{ if(r.implied>0) t[r.team]=r.implied; });
  const v=Object.values(t).sort((a,b)=>a-b);
  return v.length ? v[Math.floor(v.length/2)] : 0;
}
function markSlot(id, name, note){
  const el=$(id); el.classList.add('loaded');
  el.querySelector('b').textContent = '✓ ' + name;
  el.querySelector('.fstate').textContent = note;
}
function railMeta(){
  if(!slate.length){ $('#fmeta').textContent='no slate loaded'; return; }
  const games=[...new Set(slate.map(r=>r.team))].length/2;
  $('#fmeta').textContent = slate.length+' playable · ~'+games+' games · median implied '+
    medImplied.toFixed(1) + (swapText?' · late swap ready':'');
}
function wireSlot(slotId, inputId, handler){
  const slot=$(slotId), input=$(inputId);
  slot.addEventListener('click', ()=>input.click());
  input.addEventListener('change', e=>{ handler(e.target.files[0]); e.target.value=''; });
  ['dragover','dragenter'].forEach(ev=>slot.addEventListener(ev,e=>{e.preventDefault();slot.classList.add('over')}));
  ['dragleave','drop'].forEach(ev=>slot.addEventListener(ev,e=>{e.preventDefault();slot.classList.remove('over')}));
  slot.addEventListener('drop', e=>handler(e.dataTransfer.files[0]));
}
const stamp = () => new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
function readFile(f, done){ if(!f) return; const r=new FileReader(); r.onload=()=>done(r.result); r.readAsText(f); }

wireSlot('#f-slate','#file', f=>readFile(f, txt=>{
  csvText=txt; slate=parseSlate(txt); medImplied=slateMedianImplied(slate);
  markSlot('#f-slate', f.name, 'loaded '+stamp()+' — click to replace');
  $('#f-slate').classList.remove('req');
  $('#go').disabled=false; $('#slatefilter').disabled=false;
  $('#slateempty').style.display='none';
  if(swapText) $('#swapgo').disabled=false;
  renderSlate(); railMeta();
}));
wireSlot('#f-min','#minfile', f=>readFile(f, txt=>{
  minText=txt; markSlot('#f-min', f.name, 'loaded '+stamp()); railMeta();
}));
wireSlot('#f-dk','#swapfile', f=>readFile(f, txt=>{
  swapText=txt; markSlot('#f-dk', f.name, 'loaded '+stamp());
  if(csvText) $('#swapgo').disabled=false;
  railMeta();
}));
wireSlot('#f-con','#confile', f=>readFile(f, txt=>{
  conText=txt; markSlot('#f-con', f.name, 'loaded '+stamp()); railMeta();
}));

// ---- the slate table: every lever is one click on the row ----
let slateSort='proj', slateDir=-1;
const MARKS=[['core','★','core'],['pool','◆','pool'],['remove','🚫','rm'],['cap','🔒','cap']];
function toggleMark(kind, name){
  if(sel[kind].has(name)) sel[kind].delete(name); else sel[kind].add(name);
  renderSlate(); paintDock();
}
function paintDock(){
  $('#d-core').textContent=sel.core.size; $('#d-pool').textContent=sel.pool.size;
  $('#d-rm').textContent=sel.remove.size; $('#d-cap').textContent=sel.cap.size;
  $('#capwho').textContent = sel.cap.size
    ? '— ' + sel.cap.size + ' locked' : '— none locked yet';
  paintBoard();
}
// The picks live on the slate rows, which means a player you marked can scroll
// out of sight. List them here so the set is always visible and clearable.
function paintBoard(){
  const any=GROUPS.some(([k])=>sel[k].size);
  $('#boardsum').innerHTML = !any
    ? '<span class="hint">Nothing picked yet — use ★ ◆ 🚫 🔒 on any slate row.</span>'
    : GROUPS.filter(([k])=>sel[k].size).map(([k,label])=>
        '<div class="bgroup"><b>'+label+'</b><div class="chips">'+
        [...sel[k]].map(n=>'<span class="chip" data-k="'+k+'" data-n="'+
          n.replace(/"/g,'&quot;')+'">'+n+'</span>').join('')+'</div></div>').join('');
  $('#boardsum').querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
    sel[c.dataset.k].delete(c.dataset.n); renderSlate(); paintDock();
  });
}
function renderSlate(){
  if(!slate.length) return;
  const q=$('#slatefilter').value.trim().toLowerCase();
  let rows=slate.filter(r=>!q || r.name.toLowerCase().includes(q) || r.team.toLowerCase().includes(q));
  rows.sort((a,b)=>{
    const av=a[slateSort], bv=b[slateSort];
    return (typeof av==='number' ? (av-bv) : String(av).localeCompare(String(bv)))*slateDir;
  });
  const cols=[['name','Player'],['team','Tm'],['pos','Pos'],['salary','Sal'],
              ['proj','Proj'],['own','Own'],['implied','Impl']];
  const head='<tr><th class="mkcol"></th>'+
    cols.map(c=>'<th data-k="'+c[0]+'"'+(c[0]==='name'?'':' class="num"')+'>'+c[1]+'</th>').join('')+'</tr>';
  const body=rows.map(r=>{
    const marks=MARKS.map(([k,glyph,cls])=>
      '<i class="mk '+cls+((sel[k].has(r.name)||(k==='pool'&&sel.core.has(r.name)))?' on':'')+
      (k==='pool'&&sel.core.has(r.name)&&!sel.pool.has(r.name)?' implied':'')+'" data-k="'+k+
      '" data-n="'+r.name.replace(/"/g,'&quot;')+'" title="'+k+'">'+glyph+'</i>').join('');
    // implied total is the cut the field data keeps splitting on, so it earns a
    // colour: at or above the slate median is where stacking pays.
    const impCls = r.implied>=medImplied ? 'hi' : 'lo';
    return '<tr'+(sel.remove.has(r.name)?' class="gone"':'')+'>'+
      '<td class="mkcol">'+marks+'</td>'+
      '<td>'+r.name+'</td><td class="num">'+r.team+'</td><td class="num">'+r.pos+'</td>'+
      '<td class="num">$'+r.salary.toLocaleString()+'</td>'+
      '<td class="num">'+r.proj.toFixed(1)+'</td>'+
      '<td class="num">'+r.own.toFixed(1)+'%</td>'+
      '<td class="num '+impCls+'">'+(r.implied?r.implied.toFixed(1):'—')+'</td></tr>';
  }).join('');
  const t=$('#slatetable'); t.innerHTML=head+body;
  t.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; slateDir=(slateSort===k)?-slateDir:-1; slateSort=k; renderSlate();
  });
  t.querySelectorAll('.mk').forEach(m=>m.onclick=()=>toggleMark(m.dataset.k, m.dataset.n));
  $('#slatecount').textContent=rows.length+' of '+slate.length;
}
$('#slatefilter').addEventListener('input', renderSlate);

// ---- late swap ----
$('#swapgo').addEventListener('click', runSwap);
async function runSwap(){
  if(!csvText){ showErr('Drop the updated LineStar CSV first — that is where the new projections and live scores come from.'); return; }
  if(!swapText) return;
  const btn=$('#swapgo'); btn.disabled=true; btn.innerHTML='<span class="spin"></span>Simulating…';
  $('#err').style.display='none'; $('#note').style.display='none';
  try{
    const res=await fetch('/api/lateswap',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({csv:csvText, dk:swapText, contest:conText,
        options:{maxExposure:(+$('#exp').value)/100,
                 cores:[...sel.core].join('\n'), pool:[...sel.pool].join('\n'),
                 newsOnly:$('#newsonly').value,
                 minutes:minText}})});
    const data=await res.json();
    if(data.error) showErr(data.error); else renderSwaps(data);
  }catch(e){ showErr(e.message); }
  btn.disabled=false; btn.textContent='🔄 Late swap';
}
function aggrTag(a){
  if(a>=0.62) return '<span style="color:var(--accent)">chasing upside</span>';
  if(a<=0.38) return '<span style="color:var(--good)">protecting</span>';
  return '<span class="muted">neutral</span>';
}
function renderSwaps(d){
  const box=$('#swapresults'); box.style.display='block'; $('#welcome').style.display='none';
  swapData=d;
  // Every proposed change starts accepted; unticking one keeps that entry exactly
  // as you entered it, and the download follows the ticks.
  swapTake=d.swaps.map(s=>!s.keep && !s.error);
  const fieldTxt = d.field
    ? ' · field '+d.field.toLocaleString()+(d.winScore?', ~'+d.winScore+' projected to win':'')
    : ' · no contest file — position estimated from projections';
  const mode = d.newsOnly
    ? ' · <b>news only</b>'+(d.newsCount?' — '+d.newsCount+' player(s) changed':' — nothing has changed')
      +(d.hadBaseline?'':', no logged build to compare against so only scratches are detected')
    : ' · re-optimising freely (news-only off)';
  let html='<div class="coach-h">🔄 Late swap — '+d.changed+' of '+d.entries+
    ' lineups to adjust · '+d.lockedPlayers+' locked'+fieldTxt+mode+'</div>';
  if(d.dkCsv){
    html+='<div class="note" id="swapbar" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"></div>';
  }
  html+=d.swaps.map((s,i)=>{
    if(s.error) return '<div class="swapcard err">#'+s.entryId+': '+s.error+'</div>';
    const pace = s.settled
      ? ' · <span class="muted">all players locked — nothing left to change</span>'
      : s.rank
      ? ' · rank <b>#'+s.rank.toLocaleString()+'</b>, banked '+s.banked+' → '+s.projFinal+' projected · '+aggrTag(s.aggression)
      : (s.banked>0
        ? ' · banked <b>'+s.banked+'</b> vs '+s.expected+' expected ('+(s.pace>=0?'+':'')+s.pace+') · '+aggrTag(s.aggression)
        : '');
    const view = s.before ? '<button class="swaptoggle" data-i="'+i+'">view lineup</button>' : '';
    const why = s.hold ? ' <span class="muted">— '+s.hold+'</span>' : '';
    if(s.keep) return '<div class="swapcard keep" id="sc'+i+'">'+
      '<div class="swaphdr"><span class="hdrtext">#'+s.entryId+' · keep as-is '+
      '<span class="muted">('+s.open+' open, proj '+s.proj+')</span>'+why+pace+'</span>'+view+'</div>'+
      '<div class="cmpwrap" id="cw'+i+'" style="display:none"></div></div>';
    return '<div class="swapcard" id="sc'+i+'">'+
      '<div class="swaphdr">'+
      '<input type="checkbox" class="swapchk" data-i="'+i+'" checked title="apply this swap">'+
      '<span class="hdrtext">#'+s.entryId+
      ' <span class="gain">+'+s.gain+'</span> <span class="muted">'+
      (s.projGain!==undefined?'('+(s.projGain>=0?'+':'')+s.projGain+' proj) ':'')+'→ score '+s.score+
      ', $'+s.salary.toLocaleString()+'</span>'+pace+'</span>'+view+'</div>'+
      s.out.map(p=>swapRow(p,'−','out')).join('')+s.in.map(p=>swapRow(p,'+','in')).join('')+
      '<div class="cmpwrap" id="cw'+i+'" style="display:none"></div></div>';
  }).join('');
  box.innerHTML=html;
  box.querySelectorAll('.swaptoggle').forEach(b=>b.onclick=()=>toggleCmp(+b.dataset.i,b));
  box.querySelectorAll('.swapchk').forEach(c=>c.onchange=()=>{
    swapTake[+c.dataset.i]=c.checked;
    $('#sc'+c.dataset.i).classList.toggle('skipped',!c.checked);
    paintSwapBar();
  });
  paintSwapBar();
}
function swapRow(p,sign,cls){
  return '<div class="swaprow '+cls+'">'+sign+' '+p.name+
    (p.offPool?' <span class="offpool" title="not in your pool">◇</span>':'')+
    ' <span class="muted">$'+p.salary.toLocaleString()+' · '+p.proj+' proj · '+p.own+'%</span></div>';
}
// Side-by-side: what you entered vs what this would become, whole roster, so a
// swap is never a name change with no context.
function cmpTable(title, roster, other){
  const names=new Set((other||[]).map(p=>p.name));
  const rows=roster.map(p=>{
    const diff=!names.has(p.name);
    return '<tr'+(diff?' class="diff"':'')+'>'+
      '<td class="slot">'+p.slot+'</td>'+
      '<td>'+p.name+(p.locked?' <span class="lockmark">🔒</span>':'')+
        (p.core?' <span class="core-star">★</span>':'')+
        (p.offPool?' <span class="offpool">◇</span>':'')+'</td>'+
      '<td class="num">'+p.team+'</td>'+
      '<td class="num">$'+p.salary.toLocaleString()+'</td>'+
      '<td class="num">'+(p.scored!=null?p.scored+' pt':p.proj+' pr')+'</td>'+
      '<td class="num">'+p.own+'%</td></tr>';
  }).join('');
  const sal=roster.reduce((a,p)=>a+p.salary,0);
  const proj=roster.reduce((a,p)=>a+(p.scored!=null?p.scored:p.proj),0);
  return '<div><h5>'+title+'</h5><table><tbody>'+rows+'</tbody><tfoot><tr>'+
    '<td class="slot"></td><td>total</td><td class="num"></td>'+
    '<td class="num">$'+sal.toLocaleString()+'</td>'+
    '<td class="num">'+proj.toFixed(1)+'</td><td class="num"></td>'+
    '</tr></tfoot></table></div>';
}
function toggleCmp(i,btn){
  const w=$('#cw'+i), s=swapData.swaps[i];
  if(w.style.display!=='none'){ w.style.display='none'; btn.textContent='view lineup'; return; }
  if(!w.innerHTML && s.before){
    w.innerHTML='<div class="cmp">'+cmpTable('You entered',s.before,s.after)+
      cmpTable(s.keep?'Unchanged':'Proposed',s.after,s.before)+'</div>';
  }
  w.style.display='block'; btn.textContent='hide lineup';
}
function paintSwapBar(){
  const bar=$('#swapbar'); if(!bar||!swapData) return;
  let take=0, gain=0;
  swapData.swaps.forEach((s,i)=>{ if(swapTake[i]){ take++; gain+=s.gain||0; } });
  bar.innerHTML='<b>+'+gain.toFixed(1)+' simulated ceiling across '+take+' lineup'+
    (take===1?'':'s')+'.</b>'+
    '<button id="swapdl" class="btn ghost" style="width:auto;margin:0;padding:8px 14px">'+
    '⬇ Download updated DK file</button>'+
    '<span class="muted">all '+swapData.entries+' entries — upload straight back to DK</span>'+
    (take<swapData.changed
      ? '<span class="muted">'+(swapData.changed-take)+' declined, kept as entered</span>' : '');
  $('#swapdl').onclick=()=>{
    const rows=[swapData.csvHeader];
    swapData.swaps.forEach((s,i)=>{
      const r = swapTake[i] ? s.rowAfter : s.rowBefore;
      if(r) rows.push(r);
    });
    const blob=new Blob([rows.join('\n')+'\n'],{type:'text/csv'});
    const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
    a.download='DKEntries_lateswap.csv'; a.click();
  };
}

$('#go').addEventListener('click', run);
async function run(){
  if(!csvText) return;
  const btn = $('#go'); btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Building…';
  $('#err').style.display='none'; $('#note').style.display='none';
  const options = {
    n:+$('#n').value, stack:+$('#stack').value,
    maxExposure:(+$('#exp').value)/100, ownLean:(+$('#lev').value)/100,
    maxSub10:$('#sub10').value, slateRules:$('#slaterules').value,
    stackShare:(+$('#stk').value)/100,
    cores:[...sel.core].join('\n'), pool:[...sel.pool].join('\n'),
    remove:[...sel.remove].join('\n'), maxOffPool:+$('#offpool').value,
    minCores:+$('#mincores').value,
    capPlayers:[...sel.cap].join('\n'), capPct:+$('#cappct').value, minutes:minText,
  };
  try{
    const res = await fetch('/api/optimize', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({csv:csvText||'', options})});
    const data = await res.json();
    if(data.error){ showErr(data.error); }
    else { lastResult = data; render(data); }
  }catch(e){ showErr(e.message); }
  btn.disabled=false; btn.textContent='Build lineups';
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
      ' · ◇ off-pool</div>';
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
      }
    return '<td class="'+(num?'num':'')+'">'+(v===undefined?'':v)+'</td>';
  }).join('')+'</tr>').join('');
  const t = $('#ptable'); t.innerHTML = head+body;
  t.querySelectorAll('th').forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; projDir = (projSort===k)? -projDir : -1; projSort=k; renderProj(players,k);
  });
}

$('#dl').addEventListener('click', ()=>{
  if(!lastResult) return;
  const rows = [['G','G','F','F','F','UTIL'].join(',')]
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
