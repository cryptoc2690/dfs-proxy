export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
  const today = new Date().toISOString().split('T')[0];
  const seasonStart = '2024-10-01';

  const bdlGet = async (url) => {
    const r = await fetch(url, { headers });
    if (!r.ok) {
      const text = await r.text();
      throw new Error(`${r.status} from ${url}: ${text}`);
    }
    return r.json();
  };

  try {
    // Step 1: Tonight's games
    const tonightData = await bdlGet(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`
    );
    const tonightGames = tonightData.data || [];

    if (tonightGames.length === 0) {
      return res.status(200).json({
        players: [],
        outPlayers: [],
        lastUpdated: new Date().toISOString()
      });
    }

    const tonightTeamIds = new Set(
      tonightGames.flatMap(g => [g.home_team?.id, g.visitor_team?.id]).filter(Boolean)
    );

    const teamIdToAbbr = {};
    const abbrToTeamId = {};
    for (const g of tonightGames) {
      if (g.home_team?.id) {
        teamIdToAbbr[g.home_team.id] = g.home_team.abbreviation;
        abbrToTeamId[g.home_team.abbreviation] = g.home_team.id;
      }
      if (g.visitor_team?.id) {
        teamIdToAbbr[g.visitor_team.id] = g.visitor_team.abbreviation;
        abbrToTeamId[g.visitor_team.abbreviation] = g.visitor_team.id;
      }
    }

    // Step 2: Injuries via own proxy (avoids BDL param requirement)
    const proxyBase = 'https://dfs-proxy.onrender.com';
    const injuryRes = await fetch(`${proxyBase}/api/nba-injuries`);
    const injuryData = await injuryRes.json();
    const allInjuries = injuryData.injuries || [];

    // Filter to tonight's teams, Out only
    const outPlayers = allInjuries.filter(i =>
      i.status === 'Out' && abbrToTeamId[i.team]
    );

    if (outPlayers.length === 0) {
      return res.status(200).json({
        players: [],
        outPlayers: [],
        lastUpdated: new Date().toISOString()
      });
    }

    // Group out player names by team ID
    const outByTeam = {};
    for (const p of outPlayers) {
      const teamId = abbrToTeamId[p.team];
      if (!teamId) continue;
      if (!outByTeam[teamId]) outByTeam[teamId] = [];
      outByTeam[teamId].push(p.playerName);
    }

    // Step 3: This season's game IDs per tonight's team
    const teamGameIds = {};
    await Promise.all([...tonightTeamIds].map(async (teamId) => {
      let allGameIds = [];
      let cursor = null;
      for (let page = 0; page < 6; page++) {
        const url = `https://api.balldontlie.io/v1/games?team_ids[]=${teamId}&start_date=${seasonStart}&end_date=${today}&per_page=100${cursor ? `&cursor=${cursor}` : ''}`;
        const d = await bdlGet(url);
        const games = (d.data || []).filter(g => g.status === 'Final');
        allGameIds = allGameIds.concat(games.map(g => g.id));
        cursor = d.meta?.next_cursor;
        if (!cursor || games.length === 0) break;
      }
      teamGameIds[teamId] = allGameIds;
    }));

    // Step 4: Stats per team
    const results = [];

    for (const [teamId, outNames] of Object.entries(outByTeam)) {
      const gameIds = teamGameIds[teamId] || [];
      if (gameIds.length < 5) continue;

      // Regular stats in batches of 10
      const allRegStats = [];
      for (let i = 0; i < gameIds.length; i += 10) {
        const batch = gameIds.slice(i, i + 10);
        const params = batch.map(id => `game_ids[]=${id}`).join('&');
        const d = await bdlGet(
          `https://api.balldontlie.io/v1/stats?${params}&per_page=100`
        );
        allRegStats.push(...(d.data || []));
      }

      // Advanced stats in batches of 10
      const allAdvStats = [];
      for (let i = 0; i < gameIds.length; i += 10) {
        const batch = gameIds.slice(i, i + 10);
        const params = batch.map(id => `game_ids[]=${id}`).join('&');
        const d = await bdlGet(
          `https://api.balldontlie.io/v2/stats/advanced?${params}&per_page=100&period=0`
        );
        allAdvStats.push(...(d.data || []));
      }

      // Build minutes and usage maps
      const minutesMap = {};
      for (const s of allRegStats) {
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        if (name && s.game?.id !== undefined) {
          minutesMap[`${name}::${s.game.id}`] = parseInt(s.min || '0', 10);
        }
      }

      const usageMap = {};
      for (const s of allAdvStats) {
        if (s.period !== 0) continue;
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        if (name && s.game?.id !== undefined) {
          usageMap[`${name}::${s.game.id}`] = parseFloat(
            ((s.usage_percentage || 0) * 100).toFixed(1)
          );
        }
      }

      // All teammates with enough games
      const teammates = new Set();
      for (const s of allRegStats) {
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        if (name && !outNames.includes(name) && parseInt(s.min || '0', 10) > 0) {
          teammates.add(name);
        }
      }

      // Baseline per teammate
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

      // Uplift per out player, additive
      const uplift = {};
      for (const outName of outNames) {
        const gamesWithMinutes = gameIds.filter(id => (minutesMap[`${outName}::${id}`] || 0) > 5).length;
        if (gamesWithMinutes < 10) continue;

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
            uplift[teammateName] = {
              minutesDelta: 0,
              usageDelta: 0,
              sampleSize: dnpWithTeammate.length,
              outPlayers: []
            };
          }
          uplift[teammateName].minutesDelta += minDelta;
          uplift[teammateName].usageDelta += usageDelta;
          uplift[teammateName].sampleSize = Math.min(uplift[teammateName].sampleSize, dnpWithTeammate.length);
          uplift[teammateName].outPlayers.push(outName);
        }
      }

      // Output meaningful deltas only
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
        name: p.playerName,
        team: p.team,
        status: p.status,
      })),
      lastUpdated: new Date().toISOString(),
    });

  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
