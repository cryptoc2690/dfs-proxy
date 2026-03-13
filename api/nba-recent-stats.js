export const config = { maxDuration: 60 };
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
    const today = new Date().toISOString().split('T')[0];
    const sevenDaysAgo = new Date(Date.now() - 14 * 86400000).toISOString().split('T')[0];

    // Tonight's team IDs
    const tonightRes = await fetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`,
      { headers }
    );
    const tonightData = await tonightRes.json();
    const tonightTeamIds = new Set(
      (tonightData.data || []).flatMap(g => [g.home_team?.id, g.visitor_team?.id]).filter(Boolean)
    );

    // Recent final games for tonight's teams only, last 5
    const recentRes = await fetch(
      `https://api.balldontlie.io/v1/games?start_date=${sevenDaysAgo}&end_date=${today}&per_page=50`,
      { headers }
    );
    const recentData = await recentRes.json();
    const recentGameIds = (recentData.data || [])
      .filter(g => g.status === 'Final' &&
        (tonightTeamIds.has(g.home_team?.id) || tonightTeamIds.has(g.visitor_team?.id))
      )
      .sort((a, b) => new Date(b.date) - new Date(a.date))
      .slice(0, 15)
      .map(g => g.id);

    if (recentGameIds.length === 0) {
      return res.status(200).json({ players: [], lastUpdated: new Date().toISOString() });
    }

    // Fetch each game individually in parallel — avoids pagination issues
    const [advResults, regResults] = await Promise.all([
      Promise.all(recentGameIds.map(id =>
        fetch(`https://api.balldontlie.io/v2/stats/advanced?game_ids[]=${id}&per_page=100`, { headers })
          .then(r => r.json())
          .then(d => d.data || [])
      )),
      Promise.all(recentGameIds.map(id =>
        fetch(`https://api.balldontlie.io/v1/stats?game_ids[]=${id}&per_page=100`, { headers })
          .then(r => r.json())
          .then(d => d.data || [])
      )),
    ]);

    const advancedStats = advResults.flat();
    const regularStats = regResults.flat();

    // Minutes map
    const minutesMap = {};
    for (const s of regularStats) {
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      const gameId = s.game?.id;
      const mins = parseInt(s.min || '0', 10);
      if (name && gameId) minutesMap[`${name}::${gameId}`] = mins;
    }

    // Group by player, period 0 only
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
        minutes,
        usage: parseFloat(((s.usage_percentage || 0) * 100).toFixed(1)),
      });
    }

    // Compute trends
    const players = Object.entries(byPlayer).map(([name, games]) => {
      const sorted = games
        .filter(g => g.date)
        .sort((a, b) => new Date(b.date) - new Date(a.date))
        .slice(0, 15);

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

    return res.status(200).json({ players, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return
