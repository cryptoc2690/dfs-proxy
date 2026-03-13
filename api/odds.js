export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const today = new Date().toISOString().split('T')[0];
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

    // Step 1: fetch today's games to build game_id → team map
    const gamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`,
      { headers }
    );
    const gamesData = await gamesRes.json();
    const gameTeamMap = {};
    for (const g of gamesData.data || []) {
      gameTeamMap[g.id] = {
        homeTeam: g.home_team?.abbreviation,
        awayTeam: g.visitor_team?.abbreviation,
      };
    }

    // Step 2: fetch odds
    const oddsRes = await fetch(
      `https://api.balldontlie.io/v2/odds?dates[]=${today}&per_page=100`,
      { headers }
    );
    const oddsData = await oddsRes.json();

    // Step 3: deduplicate — one entry per game_id, prefer DraftKings
    const byGame = {};
    for (const o of oddsData.data || []) {
      const gid = o.game_id;
      if (!byGame[gid] || o.vendor === 'draftkings') {
        byGame[gid] = o;
      }
    }

    const games = Object.values(byGame).map(o => {
      const teams = gameTeamMap[o.game_id] || {};
      const total = parseFloat(o.total_value) || null;
      const spread = parseFloat(o.spread_home_value) || null;
      return {
        homeTeam: teams.homeTeam || null,
        awayTeam: teams.awayTeam || null,
        total,
        spread,
        impliedHome: total && spread !== null ? (total / 2) - (spread / 2) : null,
        impliedAway: total && spread !== null ? (total / 2) + (spread / 2) : null,
        isBlowout: spread !== null && Math.abs(spread) >= 8,
        moneylineHome: o.moneyline_home_odds,
        moneylineAway: o.moneyline_away_odds,
        vendor: o.vendor,
      };
    }).filter(g => g.homeTeam && g.awayTeam);

    return res.status(200).json({ games, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
