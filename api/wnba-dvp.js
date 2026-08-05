// WNBA defense-vs-position — DK fantasy points allowed to Guards vs Forwards,
// computed from the last ~15 games of each team on tonight's slate. WNBA only
// splits into G/F (no centers). Rank/adjust matchups off league averages.
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const BDL = 'https://api.balldontlie.io/wnba/v1';
  const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
  const getSlateDate = () => {
    const et = new Date(Date.now() + (-4 * 3600 * 1000));
    return (et.getUTCHours() < 2 ? new Date(et - 86400000) : et).toISOString().split('T')[0];
  };
  const fp = s => {
    const v = k => s[k] || 0;
    let f = v('pts') + 1.25 * v('reb') + 1.5 * v('ast') + 2 * v('stl') + 2 * v('blk')
      - 0.5 * v('turnover') + 0.5 * v('fg3m');
    const d = ['pts', 'reb', 'ast', 'stl', 'blk'].filter(k => v(k) >= 10).length;
    if (d >= 3) f += 3; else if (d >= 2) f += 1.5;
    return f;
  };

  try {
    const today = req.query?.date || getSlateDate();
    const season = req.query?.season || new Date(today).getUTCFullYear();
    const start = new Date(Date.now() - 30 * 86400000).toISOString().split('T')[0];

    const tRes = await fetch(`${BDL}/games?dates[]=${today}&per_page=100`, { headers });
    const tData = await tRes.json();
    const teamIds = new Set(), idToAbbr = {};
    for (const g of tData.data || []) {
      for (const t of [g.home_team, g.visitor_team]) {
        if (t?.id) { teamIds.add(t.id); idToAbbr[t.id] = t.abbreviation; }
      }
    }
    if (!teamIds.size) return res.json({ dvpMap: {}, message: 'no games', lastUpdated: new Date().toISOString() });

    const gRes = await fetch(`${BDL}/games?start_date=${start}&end_date=${today}&seasons[]=${season}&team_ids[]=${[...teamIds].join('&team_ids[]=')}&per_page=100`, { headers });
    const gData = await gRes.json();
    const finals = (gData.data || []).filter(g => String(g.status).toLowerCase().includes('final') || g.home_team_score);
    const gameTeams = {};
    for (const g of finals) gameTeams[g.id] = [g.home_team?.id, g.visitor_team?.id];
    const gameIds = finals.map(g => g.id);
    if (!gameIds.length) return res.json({ dvpMap: {}, message: 'no recent finals', lastUpdated: new Date().toISOString() });

    // Fetch box scores in batches.
    const stats = [];
    for (let i = 0; i < gameIds.length; i += 25) {
      const chunk = gameIds.slice(i, i + 25);
      const url = `${BDL}/player_stats?${chunk.map(id => `game_ids[]=${id}`).join('&')}&per_page=100`;
      let cursor = null;
      for (let p = 0; p < 10; p++) {
        const r = await fetch(url + (cursor ? `&cursor=${cursor}` : ''), { headers });
        const d = await r.json();
        stats.push(...(d.data || []));
        cursor = d.meta?.next_cursor;
        if (!cursor) break;
      }
    }

    const acc = {}, cnt = {};
    for (const s of stats) {
      const min = parseInt(String(s.min || '0').split(':')[0], 10);
      if (min < 8) continue;
      const gid = s.game?.id || s.game_id;
      if (!gameTeams[gid]) continue;
      const [home, away] = gameTeams[gid];
      const defId = s.team?.id === home ? away : home;
      const defAbbr = idToAbbr[defId];
      const pos = (s.player?.position || '').toUpperCase();
      const role = pos.includes('G') ? 'G' : (pos ? 'F' : null);
      if (!defAbbr || !role) continue;
      const key = `${defAbbr}::${role}`;
      acc[key] = (acc[key] || 0) + fp(s);
      cnt[key] = (cnt[key] || 0) + 1;
    }

    const dvpMap = {};
    const leagueAcc = { G: 0, F: 0 }, leagueCnt = { G: 0, F: 0 };
    for (const [key, total] of Object.entries(acc)) {
      const [team, role] = key.split('::');
      dvpMap[team] = dvpMap[team] || {};
      dvpMap[team][role] = +(total / cnt[key]).toFixed(1);
      leagueAcc[role] += total; leagueCnt[role] += cnt[key];
    }
    const leagueAvg = {
      G: leagueCnt.G ? +(leagueAcc.G / leagueCnt.G).toFixed(1) : null,
      F: leagueCnt.F ? +(leagueAcc.F / leagueCnt.F).toFixed(1) : null,
    };
    res.json({ dvpMap, leagueAvg, source: 'recent-30d', lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
}
