export const config = { maxDuration: 60 };

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
    const today = new Date().toISOString().split('T')[0];
    const seasonStart = '2024-10-01';

    // ── Step 1: Tonight's games and team IDs ──────────────────────────────
    const tonightRes = await fetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`,
      { headers }
    );
    const tonightData = await tonightRes.json();
    const tonightGames = tonightData.data || [];

    if (tonightGames.length === 0) {
      return res.status(200).json({ players: [], lastUpdated: new Date().toISOString() });
    }

    const tonightTeamIds = new Set(
      tonightGames.flatMap(g => [g.home_team?.id, g.visitor_team?.id]).filter(Boolean)
    );

    // Map team abbreviation → team ID
    const teamAbbrToId = {};
    for (const g of tonightGames) {
      if (g.home_team?.abbreviation) teamAbbrToId[g.home_team.abbreviation] = g.home_team.id;
      if (g.visitor_team?.abbreviation) teamAbbrToId[g.visitor_team.abbreviation] = g.visitor_team.id;
    }

    // ── Step 2: Tonight's injured/out players ─────────────────────────────
    const injuryRes = await fetch(
      `https://api.balldontlie.io/v1/player_injuries`,
      { headers }
    );
    const injuryData = await injuryRes.json();
    const outPlayers = (injuryData.data || []).filter(i =>
      i.status === 'Out' &&
      tonightTeamIds.has(i.team?.id)
    );

    if (outPlayers.length === 0) {
      return res.status(200).json({ players: [], lastUpdated: new Date().toISOString() });
    }

    // Group out players by team
    const outByTeam = {};
    for (const p of outPlayers) {
      const teamId = p.team?.id;
      if (!teamId) continue;
      if (!outByTeam[teamId]) outByTeam[teamId] = [];
      outByTeam[teamId].push(`${p.player?.first_name} ${p.player?.last_name}`.trim());
    }

    // ── Step 3: This season's games for tonight's teams ───────────────────
    const teamGameIds = {}; // teamId → [gameId, ...]
    await Promise.all([...tonightTeamIds].map(async (teamId) => {
      let allGames = [];
      let cursor = null;
      let page = 0;
      while (page < 5) { // max 500 games
        const url = `https://api.balldontlie.io/v1/games?team_ids[]=${teamId}&start_date=${seasonStart}&end_date=${today}&per_page=100${cursor ? `&cursor=${cursor}` : ''}`;
        const r = await fetch(url, { headers });
        const d = await r.json();
        const games = (d.data || []).filter(g => g.status === 'Final');
        allGames = allGames.concat(games.map(g => g.id));
        cursor = d.meta?.next_cursor;
        if (!cursor || games.length === 0) break;
        page++;
      }
      teamGameIds[teamId] = allGames;
    }));

    // ── Step 4: For each out player, find their DNP games ────────────────
    // Pull regular stats for each team's games to find DNP games per player
    const results = [];

    for (const [teamId, outNames] of Object.entries(outByTeam)) {
      const gameIds = teamGameIds[teamId] || [];
      if (gameIds.length < 3) continue;

      // Fetch regular stats (for minutes) for this team's games in batches of 10
      const allRegularStats = [];
      for (let i = 0; i < gameIds.length; i += 10) {
        const batch = gameIds.slice(i, i + 10);
        const params = batch.map(id => `game_ids[]=${id}`).join('&');
        const r = await fetch(
          `https://api.balldontlie.io/v1/stats?${params}&per_page=100`,
          { headers }
        );
        const d = await r.json();
        allRegularStats.push(...(d.data || []));
      }

      // Build minutes map: playerName+gameId → minutes
      const minutesMap = {};
      const usageMap = {}; // from advanced stats — populated below
      for (const s of allRegularStats) {
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        const gameId = s.game?.id;
        if (name && gameId) {
          minutesMap[`${name}::${gameId}`] = parseInt(s.min || '0', 10);
        }
      }

      // Fetch advanced stats for usage
      const allAdvancedStats = [];
      for (let i = 0; i < gameIds.length; i += 10) {
        const batch = gameIds.slice(i, i + 10);
        const params = batch.map(id => `game_ids[]=${id}`).join('&');
        const r = await fetch(
          `https://api.balldontlie.io/v2/stats/advanced?${params}&per_page=100&period=0`,
          { headers }
        );
        const d = await r.json();
        allAdvancedStats.push(...(d.data || []));
      }

      for (const s of allAdvancedStats) {
        if (s.period !== 0) continue;
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        const gameId = s.game?.id;
        if (name && gameId) {
          usageMap[`${name}::${gameId}`] = parseFloat(((s.usage_percentage || 0) * 100).toFixed(1));
        }
      }

      // Get all players on this team from the stats
      const teamPlayers = new Set();
      for (const s of allRegularStats) {
        const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
        if (name) teamPlayers.add(name);
      }

      // For each out player, find which games they DNP'd
      const dnpGamesByOut = {}; // outPlayerName → Set of gameIds where they DNP'd
      for (const outName of outNames) {
        const dnpGames = new Set();
        for (const gameId of gameIds) {
          const mins = minutesMap[`${outName}::${gameId}`];
          // DNP = no entry (undefined) OR 0 minutes
          if (mins === undefined || mins === 0) {
            dnpGames.add(gameId);
          }
        }
        // Filter to games where they truly DNP'd (not just didn't play — exclude games
        // where they had no entry because they weren't on the team yet)
        // Only count if they have at least 10 games with minutes this season
        const gamesWithMinutes = gameIds.filter(id => (minutesMap[`${outName}::${id}`] || 0) > 0).length;
        if (gamesWithMinutes >= 10) {
          dnpGamesByOut[outName] = dnpGames;
        }
      }

      if (Object.keys(dnpGamesByOut).length === 0) continue;

      // ── Step 5: Calculate teammate uplift when out players DNP'd ────────
      // For additive approach: calculate uplift per out player separately, then sum

      // Season baseline for each teammate
      const baseline = {}; // playerName → { avgMinutes, avgUsage, gameCount }
      for (const playerName of teamPlayers) {
        if (outNames.includes(playerName)) continue; // skip out players themselves
        const playerGames = gameIds.filter(id => (minutesMap[`${playerName}::${id}`] || 0) > 0);
        if (playerGames.length < 5) continue;

        const avgMin = playerGames.reduce((s, id) => s + (minutesMap[`${playerName}::${id}`] || 0), 0) / playerGames.length;
        const usageGames = playerGames.filter(id => usageMap[`${playerName}::${id}`] !== undefined);
        const avgUsage = usageGames.length > 0
          ? usageGames.reduce((s, id) => s + (usageMap[`${playerName}::${id}`] || 0), 0) / usageGames.length
          : 0;

        baseline[playerName] = { avgMinutes: avgMin, avgUsage, gameCount: playerGames.length };
      }

      // For each teammate, calculate additive uplift from all out players
      const upliftByTeammate = {}; // playerName → { minutesUplift, usageUplift, sampleSize, outPlayers }

      for (const [outName, dnpGames] of Object.entries(dnpGamesByOut)) {
        if (dnpGames.size < 2) continue; // need at least 2 DNP games

        for (const playerName of Object.keys(baseline)) {
          const dnpArr = [...dnpGames];
          const dnpGamesWithPlayer = dnpArr.filter(id => (minutesMap[`${playerName}::${id}`] || 0) > 0);
          if (dnpGamesWithPlayer.length < 2) continue;

          const avgMinWhenOut = dnpGamesWithPlayer.reduce((s, id) => s + (minutesMap[`${playerName}::${id}`] || 0), 0) / dnpGamesWithPlayer.length;
          const minutesDelta = avgMinWhenOut - baseline[playerName].avgMinutes;

          const dnpUsageGames = dnpGamesWithPlayer.filter(id => usageMap[`${playerName}::${id}`] !== undefined);
          const avgUsageWhenOut = dnpUsageGames.length > 0
            ? dnpUsageGames.reduce((s, id) => s + (usageMap[`${playerName}::${id}`] || 0), 0) / dnpUsageGames.length
            : baseline[playerName].avgUsage;
          const usageDelta = avgUsageWhenOut - baseline[playerName].avgUsage;

          if (!upliftByTeammate[playerName]) {
            upliftByTeammate[playerName] = { minutesUplift: 0, usageUplift: 0, sampleSize: dnpGamesWithPlayer.length, outPlayers: [] };
          }

          // Additive: sum uplift from each out player
          upliftByTeammate[playerName].minutesUplift += minutesDelta;
          upliftByTeammate[playerName].usageUplift += usageDelta;
          upliftByTeammate[playerName].sampleSize = Math.min(upliftByTeammate[playerName].sampleSize, dnpGamesWithPlayer.length);
          upliftByTeammate[playerName].outPlayers.push(outName);
        }
      }

      // ── Step 6: Build output — only flag meaningful deltas ───────────────
      for (const [playerName, uplift] of Object.entries(upliftByTeammate)) {
        const base = baseline[playerName];
        const adjustedMinutes = base.avgMinutes + uplift.minutesUplift;
        const adjustedUsage = base.avgUsage + uplift.usageUplift;

        // Only report if delta is meaningful (±3+ minutes OR ±3% usage)
        if (Math.abs(uplift.minutesUplift) < 3 && Math.abs(uplift.usageUplift) < 3) continue;

        const confidence = uplift.sampleSize >= 8 ? 'high' : uplift.sampleSize >= 4 ? 'medium' : 'low';

        results.push({
          playerName,
          team: Object.keys(teamAbbrToId).find(abbr => teamAbbrToId[abbr] === parseInt(teamId)) || '',
          baselineMinutes: parseFloat(base.avgMinutes.toFixed(1)),
          adjustedMinutes: parseFloat(adjustedMinutes.toFixed(1)),
          minutesDelta: parseFloat(uplift.minutesUplift.toFixed(1)),
          baselineUsage: parseFloat(base.avgUsage.toFixed(1)),
          adjustedUsage: parseFloat(adjustedUsage.toFixed(1)),
          usageDelta: parseFloat(uplift.usageUplift.toFixed(1)),
          outPlayers: uplift.outPlayers,
          sampleSize: uplift.sampleSize,
          confidence,
        });
      }
    }

    // Sort by absolute minutes delta descending
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
