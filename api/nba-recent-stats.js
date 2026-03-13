export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
    const today = new Date();
    const tenDaysAgo = new Date(today - 10 * 86400000).toISOString().split('T')[0];
    const todayStr = today.toISOString().split('T')[0];

    // Step 1: check games
    const gamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?start_date=${tenDaysAgo}&end_date=${todayStr}&per_page=50`,
      { headers }
    );
    const gamesData = await gamesRes.json();
    const gameIds = (gamesData.data || []).map(g => g.id);

    if (gameIds.length === 0) {
      return res.status(200).json({ debug: 'no games found', dateRange: `${tenDaysAgo} to ${todayStr}` });
    }

    // Step 2: try advanced stats on first game only
    const testId = gameIds[0];
    const statsRes = await fetch(
      `https://api.balldontlie.io/v2/stats/advanced?game_ids[]=${testId}&per_page=50`,
      { headers }
    );
    const statsRaw = await statsRes.text();

    return res.status(200).json({
      debug: 'ok',
      gameCount: gameIds.length,
      firstGameId: testId,
      statsStatusCode: statsRes.status,
      statsRawSample: statsRaw.slice(0, 500),
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
