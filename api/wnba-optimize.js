// WNBA DraftKings optimizer — one endpoint base44 can call.
//
// POST the DraftKings salary CSV (as {csv:"..."} JSON, or raw text/csv body)
// and this returns finished GPP lineups. Everything runs server-side on
// Vercel (which can reach balldontlie): pull player props + game odds +
// injuries + season stats, build projections (props-first), then construct
// and Monte-Carlo simulate a diverse, stacked, leverage-aware lineup set.
//
// Query/body options: n (lineups, default 20), stack (min game stack, 2),
// maxPerTeam (4), maxExposure (0.6), leverage (0.35), sims (3000),
// season (year), date (YYYY-MM-DD slate override).
//
// Returns { slate, players, lineups, warnings } — lineups[i].upload is the
// DK order [G,G,F,F,UTIL,UTIL] as "Name (ID)".

const BDL = 'https://api.balldontlie.io/wnba/v1';
const CAP = 50000, ROSTER = 6, MIN_G = 2, MIN_F = 2, MIN_SALARY = 3000;

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
  const warnings = [];

  try {
    const body = await readBody(req);
    const opt = { ...(req.query || {}), ...(body.options || {}) };
    const csv = body.csv || (typeof body === 'string' ? body : null) || body.raw;
    if (!csv) return res.status(400).json({ error: 'Provide the DraftKings CSV as {"csv":"..."} or a raw CSV body.' });

    const n = int(opt.n, 20), stack = int(opt.stack, 2), maxPerTeam = int(opt.maxPerTeam, 4);
    const maxExposure = num(opt.maxExposure, 0.6), leverage = num(opt.leverage, 0.35);
    const sims = int(opt.sims, 3000);

    let players = parseDkCsv(csv);
    if (!players.length) return res.status(400).json({ error: 'No players parsed from CSV.' });
    const slateDate = opt.date || modeDate(players);
    const season = int(opt.season, new Date(slateDate).getUTCFullYear());

    // ---- pull balldontlie data (best-effort; each failure just degrades) ----
    const data = await gather(headers, slateDate, season, warnings);
    projectPlayers(players, data, warnings);

    const playable = players.filter(p => p.proj > 0);
    if (playable.length < ROSTER) return res.status(400).json({ error: 'Not enough playable players after projections.' });

    // ---- build + simulate ----
    const candidates = buildCandidates(playable, { count: Math.max(120, n * 8), stack, maxPerTeam });
    if (!candidates.length) return res.status(422).json({ error: 'Could not build valid lineups under the constraints.', warnings });
    simulateAndScore(candidates, playable, { sims, leverage });
    const lineups = selectFinal(candidates, { n, maxExposure });

    res.json({
      slate: { date: slateDate, season, games: [...new Set(players.map(p => p.game))] },
      source: data.propsCount ? 'props-first' : (data.seasonCount ? 'season-stats' : 'csv-only'),
      warnings,
      players: playable
        .sort((a, b) => b.proj - a.proj)
        .map(p => ({ name: p.name, team: p.team, pos: p.pos, salary: p.salary, game: p.game,
                     proj: r1(p.proj), floor: r1(p.floor), ceil: r1(p.ceil), own: r1(p.own), notes: p.notes })),
      lineups: lineups.map((l, i) => ({
        rank: i + 1, salary: l.salary, proj: r1(l.proj), simCeiling: r1(l.ceiling), simMean: r1(l.mean),
        totalOwn: r1(l.totalOwn), stacks: l.stacks,
        players: dkOrder(l.players).map(p => ({ slot: p.__slot, name: p.name, team: p.team, pos: p.pos, salary: p.salary, proj: r1(p.proj) })),
        upload: dkOrder(l.players).map(p => `${p.name} (${p.id})`),
      })),
    });
  } catch (err) {
    res.status(500).json({ error: err.message, warnings });
  }
}

// ---------------- CSV parsing ----------------
function parseDkCsv(text) {
  const lines = String(text).replace(/\r/g, '').split('\n').filter(l => l.trim());
  if (!lines.length) return [];
  const cols = splitCsv(lines[0]);
  const idx = name => cols.findIndex(c => c.trim().toLowerCase() === name.toLowerCase());
  const iPos = idx('Position'), iName = idx('Name'), iId = idx('ID'), iRoster = idx('Roster Position');
  const iSal = idx('Salary'), iGame = idx('Game Info'), iTeam = idx('TeamAbbrev'), iAvg = idx('AvgPointsPerGame'), iStatus = idx('Status');
  const out = [];
  for (let r = 1; r < lines.length; r++) {
    const c = splitCsv(lines[r]);
    if (c.length < cols.length) continue;
    const roster = (c[iRoster] || '').toUpperCase();
    const isGuard = roster.includes('/') ? roster.split('/')[0].includes('G') : (c[iPos] || '').includes('G');
    const gameInfo = c[iGame] || '';
    const gm = gameInfo.match(/([A-Z]{2,4}@[A-Z]{2,4})/);
    const game = gm ? gm[1] : gameInfo.trim();
    const dm = gameInfo.match(/(\d{2})\/(\d{2})\/(\d{4})/);
    const gdate = dm ? `${dm[3]}-${dm[1]}-${dm[2]}` : '';
    const team = (c[iTeam] || '').trim();
    let opp = '';
    if (game.includes('@')) { const [a, b] = game.split('@'); opp = a === team ? b : a; }
    out.push({
      name: (c[iName] || '').trim(), id: (c[iId] || '').trim(), salary: parseInt(c[iSal] || '0', 10) || 0,
      team, opp, game, gameDate: gdate, isGuard: !!isGuard, pos: isGuard ? 'G' : 'F',
      avg: parseFloat(c[iAvg] || '0') || 0, status: (c[iStatus] || '').trim(),
      norm: normName(c[iName] || ''), proj: 0, floor: 0, ceil: 0, own: 0, notes: [],
    });
  }
  return out;
}
function splitCsv(line) {
  const out = []; let cur = '', q = false;
  for (const ch of line) {
    if (ch === '"') q = !q;
    else if (ch === ',' && !q) { out.push(cur); cur = ''; }
    else cur += ch;
  }
  out.push(cur); return out;
}
function normName(s) {
  return (s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[.'-]/g, '').replace(/\s+/g, ' ').trim()
    .replace(/\b(jr|sr|ii|iii|iv)\b/g, '').trim();
}

// ---------------- balldontlie fetch ----------------
async function gather(headers, date, season, warnings) {
  const out = { props: {}, implied: {}, inj: {}, season: {}, pace: {}, propsCount: 0, seasonCount: 0 };
  const j = async (url) => { const r = await fetch(url, { headers }); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); };

  let games = [];
  try { games = (await j(`${BDL}/games?dates[]=${date}&per_page=100`)).data || []; }
  catch (e) { warnings.push(`games: ${e.message}`); }
  const gameIds = games.map(g => g.id);
  const homeAway = {}; for (const g of games) homeAway[g.id] = [g.home_team?.abbreviation, g.visitor_team?.abbreviation];

  // season stats (one shot) — per-game averages for fills, fallback, matching
  try {
    let cursor = null;
    for (let i = 0; i < 30; i++) {
      const d = await j(`${BDL}/player_season_stats?season=${season}&per_page=100${cursor ? `&cursor=${cursor}` : ''}`);
      for (const s of d.data || []) {
        const pl = s.player || s; const nm = normName(`${pl.first_name || s.player_first_name || ''} ${pl.last_name || s.player_last_name || ''}`);
        if (nm) out.season[nm] = s;
      }
      cursor = d.meta?.next_cursor; if (!cursor) break;
    }
    out.seasonCount = Object.keys(out.season).length;
  } catch (e) { warnings.push(`season_stats: ${e.message}`); }

  // pace
  try {
    const d = await j(`${BDL}/team_season_advanced_stats?season=${season}&per_page=100`);
    const paces = {}; for (const t of d.data || []) if (t.team?.abbreviation && t.pace) paces[t.team.abbreviation] = t.pace;
    const vals = Object.values(paces); const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
    if (avg) for (const [a, p] of Object.entries(paces)) out.pace[a] = clamp(p / avg, 0.92, 1.1);
  } catch (e) { warnings.push(`pace: ${e.message}`); }

  // injuries
  try {
    let cursor = null;
    for (let i = 0; i < 20; i++) {
      const d = await j(`${BDL}/player_injuries?per_page=100${cursor ? `&cursor=${cursor}` : ''}`);
      for (const it of d.data || []) { const pl = it.player || {}; out.inj[normName(`${pl.first_name || ''} ${pl.last_name || ''}`)] = it.status || ''; }
      cursor = d.meta?.next_cursor; if (!cursor) break;
    }
  } catch (e) { warnings.push(`injuries: ${e.message}`); }

  // odds -> implied totals
  for (const gid of gameIds) {
    try {
      const rows = (await j(`${BDL}/odds?game_id=${gid}`)).data || [];
      const row = rows.find(x => String(x.vendor).toLowerCase() === 'draftkings') || rows[0];
      const total = row ? numAny(row, ['total_value', 'total', 'over_under', 'game_total']) : null;
      const sh = row ? numAny(row, ['spread_home_value', 'spread_home', 'home_spread', 'spread']) : null;
      const [h, a] = homeAway[gid] || [];
      if (total != null) {
        out.implied[h] = sh != null ? total / 2 - sh / 2 : total / 2;
        out.implied[a] = sh != null ? total / 2 + sh / 2 : total / 2;
      }
    } catch (e) { warnings.push(`odds ${gid}: ${e.message}`); }
  }

  // player props -> per-player market lines
  const marketMap = { points: 'pts', player_points: 'pts', pts: 'pts', rebounds: 'reb', player_rebounds: 'reb', reb: 'reb',
    assists: 'ast', player_assists: 'ast', ast: 'ast', threes: 'fg3m', three_pointers_made: 'fg3m', player_threes: 'fg3m', fg3m: 'fg3m',
    steals: 'stl', blocks: 'blk', points_rebounds_assists: 'pra', pra: 'pra' };
  const byName = {}; // norm name -> {stat:[lines]}
  for (const gid of gameIds) {
    try {
      const rows = (await j(`${BDL}/odds/player_props?game_id=${gid}`)).data || [];
      for (const row of rows) {
        const market = String(firstKey(row, ['market', 'prop_type', 'stat_type', 'name', 'market_key']) || '').toLowerCase().trim();
        const stat = marketMap[market]; if (!stat) continue;
        const line = numAny(row, ['line_value', 'line', 'threshold', 'value', 'over_under', 'point']); if (line == null) continue;
        const nm = normName(firstKey(row, ['player_name', 'player_full_name']) || playerNameFromId(row));
        const key = nm || `id:${firstKey(row, ['player_id', 'playerId'])}`;
        (byName[key] = byName[key] || {}); (byName[key][stat] = byName[key][stat] || []).push(line);
      }
    } catch (e) { warnings.push(`props ${gid}: ${e.message}`); }
  }
  for (const [k, stats] of Object.entries(byName)) { const m = {}; for (const [s, arr] of Object.entries(stats)) m[s] = median(arr); out.props[k] = m; }
  out.propsCount = Object.keys(out.props).length;
  return out;
}
function playerNameFromId() { return ''; } // props rows usually carry player_name; id-only handled by key fallback

// ---------------- projections (props-first) ----------------
function projectPlayers(players, data, warnings) {
  for (const p of players) {
    const st = (p.status || '').toUpperCase();
    const injStatus = data.inj[p.norm] || '';
    if (st === 'OUT' || st === 'O' || /OUT/i.test(injStatus)) { p.proj = p.floor = p.ceil = 0; p.notes.push('OUT — excluded'); continue; }

    const season = data.season[p.norm];
    const sAvg = season ? perGame(season) : null;      // {pts,reb,ast,fg3m,stl,blk,to}
    const props = data.props[p.norm];

    let proj, src;
    if (props && (props.pts != null || props.pra != null)) {
      proj = dkFromProps(props, sAvg); src = 'props'; p.notes.push('market props');
    } else if (sAvg) {
      proj = 0.7 * dkFromStatline(sAvg) + 0.3 * p.avg; src = 'season';
    } else {
      proj = p.avg; src = 'dkavg'; p.notes.push('no bdl match — DK avg');
    }

    if (src !== 'props') { // scale by pace + implied environment (props already priced)
      const pace = data.pace[p.team] || 1;
      const impliedArr = Object.values(data.implied);
      let env = 1;
      if (data.implied[p.team] && impliedArr.length) { const avg = impliedArr.reduce((a, b) => a + b, 0) / impliedArr.length; env = clamp(data.implied[p.team] / avg, 0.9, 1.12); }
      proj = proj * pace * env;
    }

    const band = src === 'dkavg' ? 0.3 : 0.26;
    p.proj = proj;
    p.floor = Math.max(0, proj * (1 - band - 0.02));
    p.ceil = proj * (1 + band + 0.12);
    if ((p.status || '').match(/^(Q|GTD|D|P)/i) || /(QUEST|DOUBT|GTD)/i.test(injStatus)) {
      p.proj *= 0.9; p.floor *= 0.7; p.notes.push('questionable — haircut');
    }
  }
  estimateOwnership(players);
}
function perGame(s) {
  // player_season_stats may already be per-game averages; if totals + games_played present, divide.
  const gp = s.games_played || s.gp || 0;
  const val = k => { const v = s[k]; if (v == null) return 0; return gp && v > 60 ? v / gp : v; };
  return { pts: val('pts'), reb: val('reb'), ast: val('ast'), fg3m: val('fg3m'), stl: val('stl'), blk: val('blk'), to: val('turnover') };
}
function dkFromStatline(a) {
  let fp = a.pts + 1.25 * a.reb + 1.5 * a.ast + 0.5 * a.fg3m + 2 * a.stl + 2 * a.blk - 0.5 * a.to;
  const d = [a.pts, a.reb, a.ast, a.stl, a.blk].filter(v => v >= 9.5).length;
  if (d >= 3) fp += 3; else if (d >= 2) fp += 1.5 * 0.55;
  return fp;
}
function dkFromProps(line, sAvg) {
  const g = k => (line[k] != null ? line[k] : (sAvg ? sAvg[k] : 0)) || 0;
  let pts = g('pts'), reb = g('reb'), ast = g('ast');
  if (line.pra != null && line.pts == null && line.reb == null && line.ast == null) { pts = line.pra * 0.52; reb = line.pra * 0.28; ast = line.pra * 0.2; }
  const fg3 = g('fg3m'), stl = g('stl'), blk = g('blk'), to = (sAvg ? sAvg.to : 0) || 0;
  let fp = pts + 1.25 * reb + 1.5 * ast + 0.5 * fg3 + 2 * stl + 2 * blk - 0.5 * to;
  const near = [pts, reb, ast, stl, blk].filter(v => v >= 9.5).length;
  if (near >= 2) fp += 1.5 * 0.55;
  return fp;
}
function estimateOwnership(players) {
  const playable = players.filter(p => p.proj > 0); if (!playable.length) return;
  const maxVal = Math.max(...playable.map(p => p.proj / (p.salary / 1000))) || 1;
  for (const p of players) {
    if (p.proj <= 0) { p.own = 0; continue; }
    const val = (p.proj / (p.salary / 1000)) / maxVal;
    const cheap = p.salary <= 5000 ? 0.25 : 0, stud = p.salary >= 10000 ? 0.15 : 0;
    p.own = Math.min(0.55 * val + cheap + stud, 1) * 45;
  }
}

// ---------------- lineup construction ----------------
function buildCandidates(pool, { count, stack, maxPerTeam }) {
  const out = [], seen = new Set();
  let seed = 12345;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  let tries = 0;
  while (out.length < count && tries < count * 12) {
    tries++;
    const lu = buildOne(pool, maxPerTeam, rand);
    if (!lu) continue;
    if (stack > 1 && !hasStack(lu, stack)) continue;
    const key = lu.map(p => p.id).sort().join('|');
    if (seen.has(key)) continue;
    seen.add(key); out.push(finalizeLineup(lu));
  }
  return out;
}
function buildOne(pool, maxPerTeam, rand) {
  const template = ['G', 'G', 'F', 'F', 'U', 'U'];
  const picked = [], usedIds = new Set(), teamCount = {};
  let salary = 0;
  for (let i = 0; i < template.length; i++) {
    const slot = template[i], slotsAfter = template.length - i - 1;
    const budgetForThis = CAP - salary - MIN_SALARY * slotsAfter;
    const elig = pool.filter(p => !usedIds.has(p.id) && p.salary <= budgetForThis
      && (slot === 'U' || p.pos === slot) && (teamCount[p.team] || 0) < maxPerTeam);
    // Must keep enough G/F to satisfy minimums among remaining slots.
    const need = remainingNeed(template, i, picked);
    const feasible = elig.filter(p => canComplete(template, i, picked, p, need));
    const choices = feasible.length ? feasible : elig;
    if (!choices.length) return null;
    const p = weightedPick(choices, rand);
    picked.push(p); usedIds.add(p.id); salary += p.salary; teamCount[p.team] = (teamCount[p.team] || 0) + 1;
  }
  const g = picked.filter(p => p.pos === 'G').length, f = picked.length - g;
  if (g < MIN_G || f < MIN_F || salary > CAP) return null;
  return picked;
}
function remainingNeed(template, i, picked) {
  const g = picked.filter(p => p.pos === 'G').length, f = picked.filter(p => p.pos === 'F').length;
  return { g: Math.max(0, MIN_G - g), f: Math.max(0, MIN_F - f) };
}
function canComplete(template, i, picked, cand, need) {
  const g = picked.filter(p => p.pos === 'G').length + (cand.pos === 'G' ? 1 : 0);
  const f = picked.filter(p => p.pos === 'F').length + (cand.pos === 'F' ? 1 : 0);
  const flexLeft = template.length - i - 1; // slots after this one
  const gNeed = Math.max(0, MIN_G - g), fNeed = Math.max(0, MIN_F - f);
  return gNeed + fNeed <= flexLeft;
}
function weightedPick(arr, rand) {
  const w = arr.map(p => Math.pow(Math.max(p.proj, 0.1), 3) * (0.6 + 0.8 * rand()));
  const sum = w.reduce((a, b) => a + b, 0); let x = rand() * sum;
  for (let i = 0; i < arr.length; i++) { x -= w[i]; if (x <= 0) return arr[i]; }
  return arr[arr.length - 1];
}
function hasStack(lu, stack) {
  const byGame = {}; for (const p of lu) byGame[p.game] = (byGame[p.game] || 0) + 1;
  return Object.values(byGame).some(c => c >= stack);
}
function finalizeLineup(players) {
  const byGame = {}; for (const p of players) byGame[p.game] = (byGame[p.game] || 0) + 1;
  return { players, salary: players.reduce((s, p) => s + p.salary, 0), proj: players.reduce((s, p) => s + p.proj, 0),
    totalOwn: players.reduce((s, p) => s + p.own, 0),
    stacks: Object.entries(byGame).filter(([, c]) => c >= 2).map(([g, c]) => `${g}:${c}`) };
}

// ---------------- simulation ----------------
function simulateAndScore(cands, pool, { sims, leverage }) {
  let seed = 98765; const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const gauss = () => { let u = 0, v = 0; while (!u) u = rand(); while (!v) v = rand(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
  const games = [...new Set(pool.map(p => p.game))]; const gIdx = {}; games.forEach((g, i) => gIdx[g] = i);
  // per-game environment multiplier per sim
  const gameMult = games.map(() => Array.from({ length: sims }, () => clamp(1 + 0.1 * gauss(), 0.6, 1.5)));
  // per-player simulated outcomes
  const idRow = {}; const mat = [];
  pool.forEach((p, row) => {
    idRow[p.id] = row; const gm = gameMult[gIdx[p.game]]; const arr = new Float32Array(sims);
    for (let s = 0; s < sims; s++) arr[s] = pert(p.floor, p.proj, p.ceil, rand) * gm[s];
    mat.push(arr);
  });
  for (const c of cands) {
    const rows = c.players.map(p => idRow[p.id]);
    let mean = 0; const totals = new Float32Array(sims);
    for (let s = 0; s < sims; s++) { let t = 0; for (const r of rows) t += mat[r][s]; totals[s] = t; mean += t; }
    mean /= sims; totals.sort();
    c.mean = mean; c.ceiling = totals[Math.floor(sims * 0.85)]; c.p95 = totals[Math.floor(sims * 0.95)];
  }
  const owns = cands.map(c => c.totalOwn); const lo = Math.min(...owns), hi = Math.max(...owns), span = (hi - lo) || 1;
  for (const c of cands) { const on = (c.totalOwn - lo) / span; c.score = c.ceiling * (1 + leverage * (1 - 2 * on)); }
  cands.sort((a, b) => b.score - a.score);
}
function pert(lo, mode, hi, rand) {
  if (hi - lo < 1e-6) return mode; mode = Math.min(Math.max(mode, lo + 1e-6), hi - 1e-6);
  const a = 1 + 4 * (mode - lo) / (hi - lo), b = 1 + 4 * (hi - mode) / (hi - lo);
  return lo + betaSample(a, b, rand) * (hi - lo);
}
function betaSample(a, b, rand) { const x = gammaSample(a, rand), y = gammaSample(b, rand); return x / (x + y); }
function gammaSample(k, rand) { // Marsaglia-Tsang
  if (k < 1) return gammaSample(k + 1, rand) * Math.pow(rand() || 1e-9, 1 / k);
  const d = k - 1 / 3, c = 1 / Math.sqrt(9 * d);
  for (;;) { let x, v; do { x = normal(rand); v = 1 + c * x; } while (v <= 0); v = v * v * v; const u = rand();
    if (u < 1 - 0.0331 * x * x * x * x) return d * v; if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v; }
}
function normal(rand) { let u = 0, v = 0; while (!u) u = rand(); while (!v) v = rand(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }

// ---------------- selection ----------------
function selectFinal(cands, { n, maxExposure }) {
  const cap = Math.max(1, Math.round(maxExposure * n)); const counts = {}; const final = [];
  for (const c of cands) { if (final.length >= n) break; if (c.players.some(p => (counts[p.id] || 0) >= cap)) continue; final.push(c); for (const p of c.players) counts[p.id] = (counts[p.id] || 0) + 1; }
  for (const c of cands) { if (final.length >= n) break; if (!final.includes(c)) final.push(c); }
  return final.slice(0, n);
}
function dkOrder(players) {
  const G = players.filter(p => p.pos === 'G').sort((a, b) => b.proj - a.proj);
  const F = players.filter(p => p.pos === 'F').sort((a, b) => b.proj - a.proj);
  const slotted = [{ ...G[0], __slot: 'G' }, { ...G[1], __slot: 'G' }, { ...F[0], __slot: 'F' }, { ...F[1], __slot: 'F' }];
  const rest = [...G.slice(2), ...F.slice(2)].sort((a, b) => b.proj - a.proj).slice(0, 2).map(p => ({ ...p, __slot: 'UTIL' }));
  return [...slotted, ...rest];
}

// ---------------- misc helpers ----------------
async function readBody(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  if (typeof req.body === 'string' && req.body) { try { return JSON.parse(req.body); } catch { return { csv: req.body }; } }
  const chunks = []; for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks).toString('utf8'); if (!raw) return {};
  try { return JSON.parse(raw); } catch { return { csv: raw }; }
}
function firstKey(o, keys) { for (const k of keys) if (o && o[k] != null) return o[k]; return null; }
function numAny(o, keys) { const v = firstKey(o, keys); return v == null || isNaN(+v) ? null : +v; }
function median(a) { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); const n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; }
function modeDate(players) { const d = players.map(p => p.gameDate).filter(Boolean); if (!d.length) return new Date().toISOString().split('T')[0]; const c = {}; let best = d[0]; for (const x of d) { c[x] = (c[x] || 0) + 1; if (c[x] > (c[best] || 0)) best = x; } return best; }
function int(v, def) { const x = parseInt(v, 10); return isNaN(x) ? def : x; }
function num(v, def) { const x = parseFloat(v); return isNaN(x) ? def : x; }
function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }
function r1(x) { return Math.round(x * 10) / 10; }
