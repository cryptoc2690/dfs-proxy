export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

    // Get last 10 days of games to ensure we capture 5 per team
    const today = new Date();
    const tenDaysAgo = new Date(today - 10 * 86400000).toISOString().split('T')[0];
    const todayStr = today.toISOString().split('T')[0];

    const gamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?start_date=${tenDaysAgo}&end_date=${todayStr}&per_page=50`,
      { headers }
    );
    const gamesData = await gamesRes.json();
    const gameIds = (gamesData.data || []).map(g => g.id);

    if (gameIds.length === 0) {
      return res.status(200).json({ players: [], lastUpdated: new Date().toISOString() });
    }

    // Fetch advanced stats for all recent games in parallel (chunks of 10)
    const chunks = [];
    for (let i = 0; i < gameIds.length; i += 10) {
      chunks.push(gameIds.slice(i, i + 10));
    }

    const allStats = [];
    for (const chunk of chunks) {
      const params = chunk.map(id => `game_ids[]=${id}`).join('&');
      const statsRes = await fetch(
        `https://api.balldontlie.io/v2/stats/advanced?${params}&per_page=200`,
        { headers }
      );
      const statsData = await statsRes.json();
      allStats.push(...(statsData.data || []));
    }

    // Group by player, keep last 5 games per player
    const byPlayer = {};
    for (const s of allStats) {
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      if (!name || name === ' ') continue;
      if (!byPlayer[name]) byPlayer[name] = [];
      byPlayer[name].push({
        date: s.game?.date,
        minutes: s.min || 0,
        usage: s.usage_percentage || 0,
        pts: s.pts || 0,
        dkfp: s.dkfp || null,
      });
    }

    // Sort by date desc, take last 5, compute trends
    const players = Object.entries(byPlayer).map(([name, games]) => {
      const sorted = games
        .filter(g => g.date)
        .sort((a, b) => new Date(b.date) - new Date(a.date))
        .slice(0, 5);

      if (sorted.length === 0) return null;

      const avgMinutes = sorted.reduce((s, g) => s + g.minutes, 0) / sorted.length;
      const avgUsage = sorted.reduce((s, g) => s + g.usage, 0) / sorted.length;

      // Trend: compare most recent 2 games vs prior 3
      const recent2 = sorted.slice(0, 2);
      const prior3 = sorted.slice(2);
      const recentUsage = recent2.reduce((s, g) => s + g.usage, 0) / Math.max(recent2.length, 1);
      const priorUsage = prior3.length > 0
        ? prior3.reduce((s, g) => s + g.usage, 0) / prior3.length
        : recentUsage;
      const usageTrend = recentUsage - priorUsage; // positive = usage rising

      const recentMins = recent2.reduce((s, g) => s + g.minutes, 0) / Math.max(recent2.length, 1);
      const priorMins = prior3.length > 0
        ? prior3.reduce((s, g) => s + g.minutes, 0) / prior3.length
        : recentMins;
      const minutesTrend = recentMins - priorMins;

      return {
        playerName: name,
        avgMinutes: parseFloat(avgMinutes.toFixed(1)),
        avgUsage: parseFloat(avgUsage.toFixed(1)),
        recentUsage: parseFloat(recentUsage.toFixed(1)),
        usageTrend: parseFloat(usageTrend.toFixed(1)),
        recentMinutes: parseFloat(recentMins.toFixed(1)),
        minutesTrend: parseFloat(minutesTrend.toFixed(1)),
        gamesPlayed: sorted.length,
        games: sorted,
      };
    }).filter(Boolean);

    return res.status(200).json({ players, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
