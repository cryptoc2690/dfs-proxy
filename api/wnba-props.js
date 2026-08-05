// WNBA player props -> market-implied stat lines per player, the strongest
// single projection input. Consolidates all vendor lines to a median per
// (player, market). Field names are read defensively (_first) because the
// live-odds JSON shape isn't pinned; adjust the marketMap/keys if a real
// record differs.
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
  const first = (o, ...ks) => { for (const k of ks) if (o?.[k] != null) return o[k]; return null; };
  const num = v => (v == null ? null : (isNaN(+v) ? null : +v));
  const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); const n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };
  const marketMap = {
    points: 'pts', player_points: 'pts', pts: 'pts',
    rebounds: 'reb', player_rebounds: 'reb', reb: 'reb',
    assists: 'ast', player_assists: 'ast', ast: 'ast',
    threes: 'fg3m', three_pointers_made: 'fg3m', player_threes: 'fg3m', fg3m: 'fg3m',
    steals: 'stl', blocks: 'blk',
    points_rebounds_assists: 'pra', pra: 'pra',
  };

  try {
    const date = req.query?.date || getSlateDate();
    const gamesRes = await fetch(`${BDL}/games?dates[]=${date}&per_page=100`, { headers });
    const games = (await gamesRes.json()).data || [];

    const collected = {}; // playerId -> { stat -> [lines] }
    const names = {};
    for (const g of games) {
      const r = await fetch(`${BDL}/odds/player_props?game_id=${g.id}`, { headers });
      const rows = (await r.json()).data || [];
      for (const row of rows) {
        const pid = first(row, 'player_id', 'playerId');
        const market = String(first(row, 'market', 'prop_type', 'stat_type', 'name', 'market_key') || '').toLowerCase().trim();
        const stat = marketMap[market];
        const line = num(first(row, 'line_value', 'line', 'threshold', 'value', 'over_under', 'point'));
        if (pid == null || !stat || line == null) continue;
        (collected[pid] = collected[pid] || {});
        (collected[pid][stat] = collected[pid][stat] || []).push(line);
        if (row.player_name) names[pid] = row.player_name;
      }
    }
    const props = Object.entries(collected).map(([pid, stats]) => {
      const lines = {};
      for (const [stat, arr] of Object.entries(stats)) lines[stat] = median(arr);
      return { playerId: +pid, playerName: names[pid] || null, lines };
    });
    res.json({ date, props, total: props.length, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
}
