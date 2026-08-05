// WNBA slate — tonight's games + team pace/ratings merged in.
// WNBA balldontlie has no odds endpoint, so there are no Vegas totals here;
// pace + season ratings stand in for game environment when stacking.
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const BDL = 'https://api.balldontlie.io/wnba/v1';
  const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

  // WNBA plays in summer -> Eastern is EDT (UTC-4), not the NBA code's -5.
  const getSlateDate = () => {
    const et = new Date(Date.now() + (-4 * 3600 * 1000));
    if (et.getUTCHours() < 2) {
      return new Date(et.getTime() - 86400000).toISOString().split('T')[0];
    }
    return et.toISOString().split('T')[0];
  };

  try {
    const date = (req.query && req.query.date) || getSlateDate();
    const season = (req.query && req.query.season) || new Date(date).getUTCFullYear();

    const [gamesRes, paceRes] = await Promise.all([
      fetch(`${BDL}/games?dates[]=${date}&per_page=100`, { headers }),
      fetch(`${BDL}/team_season_advanced_stats?season=${season}&per_page=100`, { headers }),
    ]);
    const gamesData = await gamesRes.json();
    const paceData = await paceRes.json();

    const paceByAbbr = {};
    for (const t of paceData.data || []) {
      const abbr = t.team?.abbreviation;
      if (abbr) paceByAbbr[abbr] = { pace: t.pace, offRating: t.off_rating, defRating: t.def_rating };
    }
    const paces = Object.values(paceByAbbr).map(p => p.pace).filter(Boolean);
    const leagueAvgPace = paces.length ? paces.reduce((a, b) => a + b, 0) / paces.length : null;

    const games = (gamesData.data || []).map(g => {
      const home = g.home_team?.abbreviation;
      const away = g.visitor_team?.abbreviation;
      const combinedPace = leagueAvgPace && paceByAbbr[home]?.pace && paceByAbbr[away]?.pace
        ? +(((paceByAbbr[home].pace + paceByAbbr[away].pace) / 2 / leagueAvgPace)).toFixed(3)
        : null;
      return {
        gameId: g.id, homeTeam: home, awayTeam: away,
        status: g.status, date: g.date,
        homePace: paceByAbbr[home]?.pace ?? null,
        awayPace: paceByAbbr[away]?.pace ?? null,
        paceIndex: combinedPace, // >1 = faster than league avg = better environment
      };
    });

    res.json({ date, season, leagueAvgPace, games, paceByTeam: paceByAbbr, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
}
