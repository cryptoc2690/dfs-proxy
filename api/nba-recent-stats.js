export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
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

    const chunks = [];
    for (let i = 0; i < gameIds.length; i += 10) {
      chunks.push(gameIds.slice(i, i + 10));
    }

    const advancedStats = [];
    const regularStats = [];

    for (const chunk of chunks) {
      const params = chunk.map(id => `game_ids[]=${id}`).join('&');
      const [advRes, regRes] = await Promise.all([
        fetch(`https://api.balldontlie.io/v2/stats/advanced?${params}&per_page=200`, { headers }),
        fetch(`https://api.balldontlie.io/v1/stats?${params}&per_page=200`, { headers }),
      ]);
      const advData = await advRes.json();
      const regData = await regRes.json();
      advancedStats.push(...(advData.data || []));
      regularStats.push(...(regData.data || []));
    }

    // min is a string like "32" — parse to int
    const minutesMap = {};
    for (const s of regularStats) {
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      const gameId = s.game?.id;
      const mins = parseInt(s.min || '0', 10);
      if (name && gameId) minutesMap[`${name}::${gameId}`] = mins;
    }

    const byPlayer = {};
    for (const s of advancedStats) {
      if (s.period !== 0) continue;
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      if (!name || name === ' ') continue;
      const gameId = s.game?.id;
      const minutes = minutesMap[`${name}::${gameId}`] ?? 0;
      if (!byPlayer[name]) byPlayer[name] = [];
      byPlayer[name].push({
        date: s.game?.date,
        gameId,
        minutes,
        usage: parseFloat(((s.usage_percentage || 0) * 100).toFixed(1)),
        pace: s.pace || 0,
        touches: s.touches || 0,
      });
    }

    const players = Object.entries(byPlayer).map(([name, games]) => {
      const sorted = games
        .filter(g => g.date && g.minutes >= 0)
        .sort((a, b) => new Date(b.date) - new Date(a.date))
        .slice(0, 5);

      if (sorted.length < 2) return null;

      const avgMinutes = sorted.reduce((s, g) => s + g.minutes, 0) / sorted.length;
      const avgUsage = sorted.reduce((s, g) => s + g.usage, 0) / sorted.length;

      const recent2 = sorted.slice(0, 2);
      const prior3 = sorted.slice(2);

      const recentUsage = recent2.reduce((s, g) => s + g.usage, 0) / recent2.length;
      const priorUsage = prior3.length > 0
        ? prior3.reduce((s, g) => s + g.usage, 0) / prior3.length
        : recentUsage;
      const usageTrend = parseFloat((recentUsage - priorUsage).toFixed(1));

      const recentMins = recent2.reduce((s, g) => s + g.minutes, 0) / recent2.length;
      const priorMins = prior3.length > 0
        ? prior3.reduce((s, g) => s + g.minutes, 0) / prior3.length
        : recentMins;
      const minutesTrend = parseFloat((recentMins - priorMins).toFixed(1));

      const usageSpike = recentUsage > avgUsage + 4;
      const minutesRisk = recentMins < avgMinutes - 4 && avgMinutes >= 25;

      return {
        playerName: name,
        avgMinutes: parseFloat(avgMinutes.toFixed(1)),
        avgUsage: parseFloat(avgUsage.toFixed(1)),
        recentMinutes: parseFloat(recentMins.toFixed(1)),
        recentUsage: parseFloat(recentUsage.toFixed(1)),
        minutesTrend,
        usageTrend,
        usageSpike,
        minutesRisk,
        gamesPlayed: sorted.length,
      };
    }).filter(Boolean);

    return res.status(200).json({ players, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
