export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const today = new Date().toISOString().split('T')[0];
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

    // Step 1: get today's game IDs
    const gamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`,
      { headers }
    );
    const gamesData = await gamesRes.json();
    const gameIds = (gamesData.data || []).map(g => g.id);
    if (gameIds.length === 0) {
      return res.status(200).json({ props: [], note: 'No games today', lastUpdated: new Date().toISOString() });
    }

    // Step 2: fetch props for all games (points only, draftkings)
    const propResults = await Promise.all(
      gameIds.map(id =>
        fetch(
          `https://api.balldontlie.io/v2/odds/player_props?game_id=${id}&prop_type=points&vendors[]=draftkings`,
          { headers }
        ).then(r => r.json())
      )
    );

    const rawProps = propResults.flatMap(r =>
      (r.data || []).filter(p => p.line_value != null)
    );

    // Step 3: collect unique player IDs and resolve names in one batch
    const playerIds = [...new Set(rawProps.map(p => p.player_id).filter(Boolean))];
    let playerNameMap = {};
    if (playerIds.length > 0) {
      // BDL supports ids[] batch param, max ~100 at a time
      const idChunks = [];
      for (let i = 0; i < playerIds.length; i += 50) {
        idChunks.push(playerIds.slice(i, i + 50));
      }
      for (const chunk of idChunks) {
        const params = chunk.map(id => `ids[]=${id}`).join('&');
        const pRes = await fetch(
          `https://api.balldontlie.io/v1/players?${params}&per_page=50`,
          { headers }
        );
        const pData = await pRes.json();
        for (const p of pData.data || []) {
          playerNameMap[p.id] = `${p.first_name} ${p.last_name}`;
        }
      }
    }

// Step 4: build props with resolved names
    const allProps = rawProps.map(p => ({
      playerName: playerNameMap[p.player_id] || null,
      playerId: p.player_id,
      gameId: p.game_id,
      propType: p.prop_type,
      line: parseFloat(p.line_value),
      overOdds: p.over_odds,
      underOdds: p.under_odds,
      vendor: p.vendor,
    })).filter(p => p.playerName);

    // Step 5: deduplicate — one primary line per player
    // Primary = half-point line (x.5) if exists, else median whole number line
    const byPlayer = {};
    for (const p of allProps) {
      const key = `${p.playerId}_${p.propType}`;
      if (!byPlayer[key]) byPlayer[key] = [];
      byPlayer[key].push(p);
    }

    const props = Object.values(byPlayer).map(entries => {
      const halfLine = entries.find(e => e.line % 1 === 0.5);
      if (halfLine) return halfLine;
      const sorted = [...entries].sort((a, b) => a.line - b.line);
      return sorted[Math.floor(sorted.length / 2)];
    });

    return res.status(200).json({ props, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
