export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const API_KEY = '51177dd2-a3a8-4cf6-bb90-4dbc10cde7ee';

    // Get today and yesterday's dates
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    const fmt = d => d.toISOString().split('T')[0]; // YYYY-MM-DD
    const todayStr = fmt(today);
    const yesterdayStr = fmt(yesterday);

    // Fetch yesterday's games to find who played
    const [res1, res2] = await Promise.all([
      fetch(`https://api.balldontlie.io/v1/games?dates[]=${yesterdayStr}&per_page=30`, {
        headers: { 'Authorization': API_KEY }
      }),
      fetch(`https://api.balldontlie.io/v1/games?dates[]=${todayStr}&per_page=30`, {
        headers: { 'Authorization': API_KEY }
      })
    ]);

    const [yesterdayData, todayData] = await Promise.all([res1.json(), res2.json()]);

    // Teams that played yesterday
    const playedYesterday = new Set();
    for (const game of yesterdayData.data || []) {
      playedYesterday.add(game.home_team.abbreviation);
      playedYesterday.add(game.visitor_team.abbreviation);
    }

    // Teams playing today — flag B2B
    const b2bTeams = {};
    for (const game of todayData.data || []) {
      const home = game.home_team.abbreviation;
      const away = game.visitor_team.abbreviation;
      b2bTeams[home] = { isB2B: playedYesterday.has(home) };
      b2bTeams[away] = { isB2B: playedYesterday.has(away) };
    }

    return res.status(200).json({ b2bTeams, todayStr, yesterdayStr });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
