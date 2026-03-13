export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const today = new Date().toISOString().split('T')[0];

    // Step 1: Get today's game IDs
    const gamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`,
      { headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY } }
    );
    const gamesData = await gamesRes.json();
    const gameIds = (gamesData.data || []).map(g => g.id);

    if (gameIds.length === 0) {
      return res.status(200).json({ props: [], note: 'No games today', lastUpdated: new Date().toISOString() });
    }

    // Step 2: Fetch props for each game, DraftKings only, points prop only
    const propResults = await Promise.all(
      gameIds.map(id =>
        fetch(
          `https://api.balldontlie.io/v2/odds/player_props?game_id=${id}&prop_type=points&vendors[]=draftkings`,
          { headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY } }
        ).then(r => r.json())
      )
    );

    const props = propResults.flatMap(r =>
      (r.data || [])
        .filter(p => p.market?.type === 'over_under')
        .map(p => ({
          playerId: p.player_id,
          gameId: p.game_id,
          propType: p.prop_type,
          line: parseFloat(p.line_value),
          overOdds: p.market?.over_odds,
          underOdds: p.market?.under_odds,
          vendor: p.vendor,
        }))
    );

    return res.status(200).json({ props, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
