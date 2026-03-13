export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
    const today = new Date().toISOString().split('T')[0];
    const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().split('T')[0];

    // Step 1: get tonight's team IDs
    const tonightRes = await fetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`,
      { headers }
    );
    const tonightData = await tonightRes.json();
    const tonightTeamIds = new Set(
      (tonightData.data || []).flatMap(g => [g.home_team?.id, g.visitor_team?.id]).filter(Boolean)
    );

    // Step 2: get recent games, take only last 5 unique game IDs
    const recentRes = await fetch(
      `https://api.balldontlie.io/v1/games?start_date=${sevenDaysAgo}&end_date=${today}&per_page=50`,
      { headers }
    );
    const recentData = await recentRes.json();

    // Only keep games involving tonight's teams, last 5 only
    const recentGameIds = (recentData.data || [])
      .filter(g => g.status === 'Final' &&
        (tonightTeamIds.has(g.home_team?.id) || tonightTeamIds.has(g.visitor_team?.id))
      )
      .sort((a, b) => new Date(b.date) - new Date(a.date))
      .slice(0, 5)
      .map(g => g.id);

    if (recentGameIds.length === 0) {
      return res.status(200).json({ players: [], note: 'No recent games for tonight teams', lastUpdated: new Date().toISOString() });
    }

    // Step 3: single fetch for both stat types — no chunking
    const params = recentGameIds.map(id => `game_ids[]=${id}`).join('&');
    const [advRes, regRes] = await Promise.all([
      fetch(`https://api.balldontlie.io/v2/stats/advanced?${params}&per_page=500`, { headers }),
      fetch(`https://api.balldontlie.io/v1/stats?${params}&per_page=500`, { headers }),
    ]);
    const advData = await advRes.json();
    const regData = await regRes.json();

    // Step 4: minutes map
    const minutesMap = {};
    for (const s of regData.data || []) {
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      const gameId = s.game?.id;
      const mins = parseInt(s.min || '0', 10);
      if (name && gameId) minutesMap[`${name}::${gameId}`] = mins;
    }

    // Step 5: group by player, only period 0
    const byPlayer = {};
    for (const s of advData.data || []) {
      if (s.period !== 0) continue;
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      if (!name || name === ' ') continue;
      const gameId = s.game?.id;
      const minutes = minutesMap[`${name}::${gameId}`] ?? 0;
      if (!byPlayer[name]) byPlayer[name] = [];
      byPlayer[name].push({
        date: s.game?.date,
        minutes,
        usage: parseFloat(((s.usage_percentage || 0) * 100).toFixed(1)),
      });
    }

    // Step 6: compute trends
    const players = Object.entries(byPlayer).map(([name, games]) => {
      const sorted = games
        .filter(g => g.date)
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

      const recentMins = recent2.reduce((s, g) => s + g.minutes, 0) / recent2.length;
      const priorMins = prior3.length > 0
        ? prior3.reduce((s, g) => s + g.minutes, 0) / prior3.length
        : recentMins;

      return {
        playerName: name,
        avgMinutes: parseFloat(avgMinutes.toFixed(1)),
        avgUsage: parseFloat(avgUsage.toFixed(1)),
        recentMinutes: parseFloat(recentMins.toFixed(1)),
        recentUsage: parseFloat(recentUsage.toFixed(1)),
        minutesTrend: parseFloat((recentMins - priorMins).toFixed(1)),
        usageTrend: parseFloat((recentUsage - priorUsage).toFixed(1)),
        usageSpike: recentUsage > avgUsage + 4,
        minutesRisk: recentMins < avgMinutes - 4 && avgMinutes >= 25,
        gamesPlayed: sorted.length,
      };
    }).filter(Boolean);

    return res.status(200).json({
      players,
      debug: { recentGameIds, advCount: advData.data?.length, regCount: regData.data?.length },
      lastUpdated: new Date().toISOString()
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
