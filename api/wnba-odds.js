// WNBA betting odds -> implied team totals for tonight's slate.
// Implied total drives game-environment scaling and stack targeting.
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

  try {
    const date = req.query?.date || getSlateDate();
    const gamesRes = await fetch(`${BDL}/games?dates[]=${date}&per_page=100`, { headers });
    const gamesData = await gamesRes.json();
    const games = gamesData.data || [];

    const out = [];
    for (const g of games) {
      const oddsRes = await fetch(`${BDL}/odds?game_id=${g.id}`, { headers });
      const oddsData = await oddsRes.json();
      const rows = oddsData.data || [];
      const row = rows.find(r => String(r.vendor).toLowerCase() === 'draftkings') || rows[0];
      const total = row ? num(first(row, 'total_value', 'total', 'over_under', 'game_total')) : null;
      const spreadHome = row ? num(first(row, 'spread_home_value', 'spread_home', 'home_spread', 'spread')) : null;
      const home = g.home_team?.abbreviation, away = g.visitor_team?.abbreviation;
      let impliedHome = null, impliedAway = null;
      if (total != null) {
        impliedHome = spreadHome != null ? total / 2 - spreadHome / 2 : total / 2;
        impliedAway = spreadHome != null ? total / 2 + spreadHome / 2 : total / 2;
      }
      out.push({
        gameId: g.id, homeTeam: home, awayTeam: away,
        total, spreadHome,
        impliedHome: impliedHome != null ? +impliedHome.toFixed(1) : null,
        impliedAway: impliedAway != null ? +impliedAway.toFixed(1) : null,
        vendor: row?.vendor || null,
      });
    }
    res.json({ date, games: out, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
}
