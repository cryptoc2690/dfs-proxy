// WNBA recent form — per player, last 5 games: DK fantasy points, minutes and
// usage trends, plus absent-teammate detection (the injury-value engine ported
// from the NBA proxy). WNBA has no lineups feed, so a rising minutes trend +
// an absent regular teammate is the main signal that a cheap player has a path.
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
  const toMin = m => parseInt(String(m || '0').split(':')[0], 10) || 0;
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
    const start = new Date(Date.now() - 21 * 86400000).toISOString().split('T')[0];

    const tRes = await fetch(`${BDL}/games?dates[]=${today}&per_page=100`, { headers });
    const tData = await tRes.json();
    const teamIds = new Set(), idToAbbr = {};
    for (const g of tData.data || []) {
      for (const t of [g.home_team, g.visitor_team]) {
        if (t?.id) { teamIds.add(t.id); idToAbbr[t.id] = t.abbreviation; }
      }
    }
    if (!teamIds.size) return res.json({ players: [], lastUpdated: new Date().toISOString() });

    const gRes = await fetch(`${BDL}/games?start_date=${start}&end_date=${today}&seasons[]=${season}&team_ids[]=${[...teamIds].join('&team_ids[]=')}&per_page=100`, { headers });
    const gData = await gRes.json();
    const finals = (gData.data || []).filter(g => String(g.status).toLowerCase().includes('final') || g.home_team_score);
    const gameDate = {};
    for (const g of finals) gameDate[g.id] = g.date;
    const gameIds = finals.map(g => g.id);
    if (!gameIds.length) return res.json({ players: [], lastUpdated: new Date().toISOString() });

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

    // Roster-per-game (who played 5+ min) to detect absent regulars.
    const rosterPerGame = {}; // team::game -> Set(name)
    const byPlayer = {};      // name -> [{date, min, fp, team, gameId}]
    for (const s of stats) {
      const name = `${s.player?.first_name || ''} ${s.player?.last_name || ''}`.trim();
      const gid = s.game?.id || s.game_id;
      const team = s.team?.abbreviation || '';
      if (!name || !gameDate[gid]) continue;
      const min = toMin(s.min);
      (byPlayer[name] = byPlayer[name] || []).push({
        date: gameDate[gid], min, fp: min > 0 ? fp(s) : 0, team,
        pos: (s.player?.position || '').toUpperCase().includes('G') ? 'G' : 'F', gameId: gid,
      });
      if (min > 5) {
        const k = `${team}::${gid}`;
        (rosterPerGame[k] = rosterPerGame[k] || new Set()).add(name);
      }
    }

    // Regulars per team = played 3+ of the sampled games.
    const teamRegularCounts = {};
    for (const [k, set] of Object.entries(rosterPerGame)) {
      const team = k.split('::')[0];
      teamRegularCounts[team] = teamRegularCounts[team] || {};
      for (const n of set) teamRegularCounts[team][n] = (teamRegularCounts[team][n] || 0) + 1;
    }

    const players = Object.entries(byPlayer).map(([name, games]) => {
      const sorted = games.filter(g => g.date).sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 5);
      if (!sorted.length) return null;
      const team = sorted[0].team;
      const avg = arr => arr.reduce((s, g) => s + g, 0) / (arr.length || 1);
      const mins = sorted.map(g => g.min), fps = sorted.map(g => g.fp);
      const recent2 = sorted.slice(0, 2), prior = sorted.slice(2);
      const regulars = Object.entries(teamRegularCounts[team] || {})
        .filter(([, c]) => c >= 3).map(([n]) => n);
      const absentRecent = [...new Set(recent2.flatMap(g => {
        const played = rosterPerGame[`${team}::${g.gameId}`] || new Set();
        return regulars.filter(n => n !== name && !played.has(n));
      }))];
      return {
        playerName: name, team, position: sorted[0].pos,
        gamesPlayed: sorted.length,
        avgFp: +avg(fps).toFixed(1),
        avgMinutes: +avg(mins).toFixed(1),
        recentMinutes: +avg(recent2.map(g => g.min)).toFixed(1),
        minutesTrend: prior.length ? +(avg(recent2.map(g => g.min)) - avg(prior.map(g => g.min))).toFixed(1) : null,
        fpStdev: +Math.sqrt(avg(fps.map(f => (f - avg(fps)) ** 2))).toFixed(1),
        absentTeammates: absentRecent,
      };
    }).filter(Boolean);

    res.json({ players, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
}
