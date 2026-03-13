export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const today = new Date().toISOString().split('T')[0];
    const r = await fetch(`https://api.balldontlie.io/v1/player_props?date=${today}&per_page=100`, {
      headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY }
    });
    const data = await r.json();
    const props = (data.data || []).map(p => ({
      playerName: `${p.player?.first_name} ${p.player?.last_name}`,
      statType: p.stat_type,  // 'points', 'rebounds', 'assists'
      line: p.line,
      overOdds: p.over_odds,
      underOdds: p.under_odds,
    }));
    return res.status(200).json({ props, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
