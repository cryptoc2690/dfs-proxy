export default async function handler(req, res) {
  // Allow requests from your Base44 app
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  try {
    const API_KEY = 'e2c928001a9cb5f43f528f06ade029bb';
    const url = `https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey=${API_KEY}&regions=us&markets=h2h,spreads,totals&oddsFormat=american`;

    const response = await fetch(url);
    if (!response.ok) {
      return res.status(response.status).json({ error: 'Odds API error', status: response.status });
    }

    const data = await response.json();

    // Transform into clean game objects with just what we need
    const games = data.map(game => {
      const homeTeam = game.home_team;
      const awayTeam = game.away_team;
      const commenceTime = game.commence_time;

      let total = null;
      let homeSpread = null;

      // Find totals and spreads from bookmakers — prefer DraftKings, fall back to first available
      const bookmaker = game.bookmakers?.find(b => b.key === 'draftkings') || game.bookmakers?.[0];
      if (bookmaker) {
        const totalsMarket = bookmaker.markets?.find(m => m.key === 'totals');
        if (totalsMarket) {
          const overOutcome = totalsMarket.outcomes?.find(o => o.name === 'Over');
          if (overOutcome) total = overOutcome.point;
        }

        const spreadsMarket = bookmaker.markets?.find(m => m.key === 'spreads');
        if (spreadsMarket) {
          const homeOutcome = spreadsMarket.outcomes?.find(o => o.name === homeTeam);
          if (homeOutcome) homeSpread = homeOutcome.point;
        }
      }

      // Compute implied totals
      const homeImplied = total && homeSpread !== null
        ? Math.round(((total / 2) - (homeSpread / 2)) * 10) / 10
        : null;
      const awayImplied = total && homeImplied !== null
        ? Math.round((total - homeImplied) * 10) / 10
        : null;

      // Blowout flag
      const isBlowout = homeSpread !== null && Math.abs(homeSpread) >= 10;
      const favoredTeam = homeSpread !== null
        ? (homeSpread < 0 ? homeTeam : awayTeam)
        : null;
      const underdogTeam = homeSpread !== null
        ? (homeSpread < 0 ? awayTeam : homeTeam)
        : null;

      return {
        homeTeam,
        awayTeam,
        commenceTime,
        total,
        homeSpread,
        homeImplied,
        awayImplied,
        isBlowout,
        favoredTeam,
        underdogTeam,
        spreadMagnitude: homeSpread !== null ? Math.abs(homeSpread) : null,
      };
    });

    return res.status(200).json({ games });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
