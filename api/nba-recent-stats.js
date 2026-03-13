export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
    const today = new Date().toISOString().split('T')[0];
    const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().split('T')[0];

    // Step 1: get tonight's games to know which teams are playing
    const todayGamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`,
      { headers }
    );
    const todayGamesData = await todayGamesRes.json();
    const tonightTeamIds = new Set();
    for (const g of todayGamesData.data || []) {
      if (g.home_team?.id) tonightTeamIds.add(g.home_team.id);
      if (g.visitor_team?.id) tonightTeamIds.add(g.visitor_team.id);
    }
    if (tonightTeamIds.size === 0) {
      return res.status(200).json({ players: [], note: 'No games tonight', lastUpdated: new Date().toISOString() });
    }

    // Step 2: get recent games for tonight's teams only
    const teamParams = [...tonightTeamIds].map(id => `team_ids[]=${id}`).join('&');
    const recentGamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?start_date=${sevenDaysAgo}&end_date=${today}&${teamParams}&per_page=50`,
      { headers }
    );
    const recentGamesData = await recentGamesRes.json();
    const recentGameIds = (recentGamesData.data || [])
      .filter(g => g.status === 'Final')
      .map(g => g.id)
      .slice(0, 20); // cap at 20 games to stay within timeout

    if (recentGameIds.length === 0) {
      return res.status(200).json({ players: [], note: 'No recent games', lastUpdated: new Date().toISOString() });
    }

    // Step 3: fetch stats sequentially in pairs to avoid timeout
    const advancedStats = [];
    const regularStats = [];

    for (let i = 0; i < recentGameIds.length; i += 5) {
      const chunk = recentGameIds.slice(i, i + 5);
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

    // Step 4: build minutes map
    const minutesMap = {};
    for (const s of regularStats) {
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      const gameId = s.game?.id;
      const mins = parseInt(s.min || '0', 10);
      if (name && gameId) minutesMap[`${name}::${gameId}`] = mins;
    }

    // Step 5: group advanced stats by player
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

    return res.status(200).json({ players, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
