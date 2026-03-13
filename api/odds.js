export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const today = new Date().toISOString().split('T')[0];
    const r = await fetch(`https://api.balldontlie.io/v1/betting_odds?date=${today}`, {
      headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY }
    });
    const data = await r.json();
    const games = (data.data || []).map(g => ({
      homeTeam: g.home_team?.abbreviation,
      awayTeam: g.visitor_team?.abbreviation,
      total: g.total_value,
      spread: g.spread_home_value,
      impliedHome: g.total_value ? (g.total_value / 2) - (g.spread_home_value / 2) : null,
      impliedAway: g.total_value ? (g.total_value / 2) + (g.spread_home_value / 2) : null,
      isBlowout: Math.abs(g.spread_home_value || 0) >= 8,
      moneylineHome: g.moneyline_home_odds,
      moneylineAway: g.moneyline_away_odds,
    }));
    return res.status(200).json({ games, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
