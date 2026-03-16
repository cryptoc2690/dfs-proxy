export const config = { maxDuration: 60 };

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
  const today = new Date().toISOString().split('T')[0];
  const seasonStart = '2024-10-01';

  const bdlFetch = async (url) => {
    const r = await fetch(url, { headers });
    const d = await r.json();
    if (!r.ok) throw new Error(`BDL error at ${url}: ${JSON.stringify(d)}`);
    return d;
  };

  try {
    // ── Step 1: Tonight's games ──────────────────────────────────────────
    const tonightData = await bdlFetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`
    );
    const tonightGames = tonightData.data || [];

    if (tonightGames.length === 0) {
      return res.status(200).json({ players: [], outPlayers: [], lastUpdated: new Date().toISOString() });
    }

    const tonightTeamIds = new Set(
      tonightGames.flatMap(g => [g.home_team?.id, g.visitor_team?.id]).filter(Boolean)
    );

    const teamIdToAbbr = {};
    for (const g of tonightGames) {
      if (g.home_team?.id) teamIdToAbbr[g.home_team.id] = g.home_team.abbreviation;
      if (g.visitor_team?.id) teamIdToAbbr[g.visitor_team.id] = g.visitor_team.abbreviation;
    }

    // ── Step 2: Injured players tonight ─────────────────────────────────
    const injuryData = await bdlFetch(
      `https://api.balldontlie.io/v1/player_injuries?per_page=100`
    );
    const outPlayers = (injuryData.data || []).filter(i =>
      i.status === 'Out' && tonightTeamIds.has(i.team?.id)
    );

    if (outPlayers.length === 0) {
      return res.status(200).json({ players: [], outPlayers: [], lastUpdated: new Date().toISOString() });
    }

    // Group out player names by team ID
    const outByTeam = {};
    for (const p of outPlayers) {
      const teamId = p.team?.id;
      if (!teamId) continue;
      const name = `${p.player?.first_name} ${p.player?.last_name}`.trim();
      if (!outByTeam[teamId]) outByTeam[teamId] = [];
      outByTeam[teamId].push(name);
    }

    // ── Step 3: This season's game IDs per team ──────────────────────────
    const teamGameIds = {};
    await Promise.all([...tonightTeamIds].map(async (teamId) => {
      let allGameIds = [];
      let cursor = null;
      for (let page = 0; page < 6; page++) {
        const url = `https://api.balldontlie.io/v1/games?team_ids[]=${teamId}&start_date=${seasonStart}&end_date=${today}&per_page=100${cursor ? `&cursor=${cursor}` : ''}`;
        const d = await bdlFetch(url);
        const games = (d.data || []).filter(g => g.status === 'Final');
        allGameIds = allGameIds.concat(games.map(g => g.id));
        cursor = d.meta?.next_cursor;
        if (!cursor || games.length === 0) break;
      }
      teamGameIds[teamId] = allGameIds;
    }));

    // ── Step 4: Stats for each team's games ──────────────────────────────
    const results = [];

    for (const [teamId, outNames] of Object.entries(outByTeam)) {
      const gameIds = teamGameIds[teamId] || [];
      if (gameIds.length < 5) continue;

      // Fetch regular stats in batches of 10 game IDs
      const allRegStats = [];
      for (let i = 0; i < gameIds.length; i += 10) {
        const batch = gameIds.slice(i, i + 10);
        const params = batch.map(id => `game_ids[]=${id}`).join('&');
        const d = await bdlFetch(
          `https://api.balldontlie.io/v1/stats?${params}&per_page=100`
        );
        allRegStats.push(...(d.data || []));
      }

      // Fetch advanced stats in batches of 10
      const allAdvStats = [];
      for (let i = 0; i < gameIds.length; i += 10) {
        const batch = gameIds.slice(i, i + 10);
        const params = batch.map(id => `game_ids[]=${id}`).join('&');
        const d = await bdlFetch(
          `https://api.balldontlie.io/v2/stats/advanced?${params}&per_page=100&period=0`
        );
        allAdvStats.push(...(d.data || []));
      }

      // Build lookup maps
      const minutesMap = {};
      for (const s of allRegStats) {
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        if (name && s.game?.id) {
          minutesMap[`${name}::${s.game.id}`] = parseInt(s.min || '0', 10);
        }
      }

      const usageMap = {};
      for (const s of allAdvStats) {
        if (s.period !== 0) continue;
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        if (name && s.game?.id) {
          usageMap[`${name}::${s.game.id}`] = parseFloat(((s.usage_percentage || 0) * 100).toFixed(1));
        }
      }

      // All teammates (players with minutes this season, excluding out players)
      const teammates = new Set();
      for (const s of allRegStats) {
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        if (name && !outNames.includes(name) && (minutesMap[`${name}::${s.game?.id}`] || 0) > 0) {
          teammates.add(name);
        }
      }

      // Season baseline per teammate
      const baseline = {};
      for (const name of teammates) {
        const gamesPlayed = gameIds.filter(id => (minutesMap[`${name}::${id}`] || 0) > 0);
        if (gamesPlayed.length < 5) continue;

        const avgMin = gamesPlayed.reduce((s, id) => s + (minutesMap[`${name}::${id}`] || 0), 0) / gamesPlayed.length;
        const usageGames = gamesPlayed.filter(id => usageMap[`${name}::${id}`] !== undefined);
        const avgUsage = usageGames.length > 0
          ? usageGames.reduce((s, id) => s + (usageMap[`${name}::${id}`] || 0), 0) / usageGames.length
          : 0;

        baseline[name] = { avgMinutes: avgMin, avgUsage, gamesPlayed: gamesPlayed.length };
      }

      // Find DNP games per out player and calculate teammate uplift
      const uplift = {}; // teammate → { minutesDelta, usageDelta, sampleSize, outPlayers }

      for (const outName of outNames) {
        const gamesWithMinutes = gameIds.filter(id => (minutesMap[`${outName}::${id}`] || 0) > 5).length;
        if (gamesWithMinutes < 10) continue; // not enough season data

        const dnpGames = gameIds.filter(id => {
          const mins = minutesMap[`${outName}::${id}`];
          return mins === undefined || mins === 0;
        });

        if (dnpGames.length < 2) continue;

        for (const teammateName of Object.keys(baseline)) {
          const dnpWithTeammate = dnpGames.filter(id => (minutesMap[`${teammateName}::${id}`] || 0) > 0);
          if (dnpWithTeammate.length < 2) continue;

          const avgMinWhenOut = dnpWithTeammate.reduce((s, id) => s + (minutesMap[`${teammateName}::${id}`] || 0), 0) / dnpWithTeammate.length;
          const minDelta = avgMinWhenOut - baseline[teammateName].avgMinutes;

          const usageWhenOut = dnpWithTeammate.filter(id => usageMap[`${teammateName}::${id}`] !== undefined);
          const avgUsageWhenOut = usageWhenOut.length > 0
            ? usageWhenOut.reduce((s, id) => s + (usageMap[`${teammateName}::${id}`] || 0), 0) / usageWhenOut.length
            : baseline[teammateName].avgUsage;
          const usageDelta = avgUsageWhenOut - baseline[teammateName].avgUsage;

          if (!uplift[teammateName]) {
            uplift[teammateName] = { minutesDelta: 0, usageDelta: 0, sampleSize: dnpWithTeammate.length, outPlayers: [] };
          }
          uplift[teammateName].minutesDelta += minDelta;
          uplift[teammateName].usageDelta += usageDelta;
          uplift[teammateName].sampleSize = Math.min(uplift[teammateName].sampleSize, dnpWithTeammate.length);
          uplift[teammateName].outPlayers.push(outName);
        }
      }

      // Build results — only meaningful deltas
      for (const [name, u] of Object.entries(uplift)) {
        if (Math.abs(u.minutesDelta) < 3 && Math.abs(u.usageDelta) < 3) continue;

        const base = baseline[name];
        results.push({
          playerName: name,
          team: teamIdToAbbr[teamId] || String(teamId),
          baselineMinutes: parseFloat(base.avgMinutes.toFixed(1)),
          adjustedMinutes: parseFloat((base.avgMinutes + u.minutesDelta).toFixed(1)),
          minutesDelta: parseFloat(u.minutesDelta.toFixed(1)),
          baselineUsage: parseFloat(base.avgUsage.toFixed(1)),
          adjustedUsage: parseFloat((base.avgUsage + u.usageDelta).toFixed(1)),
          usageDelta: parseFloat(u.usageDelta.toFixed(1)),
          outPlayers: u.outPlayers,
          sampleSize: u.sampleSize,
          confidence: u.sampleSize >= 8 ? 'high' : u.sampleSize >= 4 ? 'medium' : 'low',
        });
      }
    }

    results.sort((a, b) => Math.abs(b.minutesDelta) - Math.abs(a.minutesDelta));

    return res.status(200).json({
      players: results,
      outPlayers: outPlayers.map(p => ({
        name: `${p.player?.first_name} ${p.player?.last_name}`.trim(),
        team: p.team?.abbreviation,
        status: p.status,
      })),
      lastUpdated: new Date().toISOString(),
    });

  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
