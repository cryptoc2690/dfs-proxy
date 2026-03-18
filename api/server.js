const express = require('express');
const app = express();
app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  next();
});

const BDL = 'https://api.balldontlie.io';
const auth = () => ({ 'Authorization': process.env.BALLDONTLIE_API_KEY });

function getSlateDate() {
  const now = new Date();
  const etOffset = -5;
  const etNow = new Date(now.getTime() + (etOffset * 60 * 60 * 1000));
  const etHour = etNow.getUTCHours();
  if (etHour < 2) {
    const yesterday = new Date(etNow.getTime() - 86400000);
    return yesterday.toISOString().split('T')[0];
  }
  return etNow.toISOString().split('T')[0];
}

// ODDS
app.get('/api/odds', async (req, res) => {
  try {
    const today = getSlateDate();
    const [gamesRes, oddsRes] = await Promise.all([
      fetch(`${BDL}/v1/games?dates[]=${today}&per_page=25`, { headers: auth() }),
      fetch(`${BDL}/v2/odds?dates[]=${today}&per_page=100`, { headers: auth() }),
    ]);
    const gamesData = await gamesRes.json();
    const oddsData = await oddsRes.json();
    const gameTeamMap = {};
    for (const g of gamesData.data || []) {
      gameTeamMap[g.id] = { homeTeam: g.home_team?.abbreviation, awayTeam: g.visitor_team?.abbreviation };
    }
    const byGame = {};
    for (const o of oddsData.data || []) {
      if (!byGame[o.game_id] || o.vendor === 'draftkings') byGame[o.game_id] = o;
    }
    const games = Object.values(byGame).map(o => {
      const teams = gameTeamMap[o.game_id] || {};
      const total = parseFloat(o.total_value) || null;
      const spread = parseFloat(o.spread_home_value) || null;
      return {
        homeTeam: teams.homeTeam || null,
        awayTeam: teams.awayTeam || null,
        total, spread,
        impliedHome: total && spread !== null ? (total / 2) - (spread / 2) : null,
        impliedAway: total && spread !== null ? (total / 2) + (spread / 2) : null,
        isBlowout: spread !== null && Math.abs(spread) >= 8,
        moneylineHome: o.moneyline_home_odds,
        moneylineAway: o.moneyline_away_odds,
        vendor: o.vendor,
      };
    }).filter(g => g.homeTeam && g.awayTeam);
    res.json({ games, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// SCHEDULE / B2B
app.get('/api/nba-schedule', async (req, res) => {
  try {
    const today = getSlateDate();
    const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];
    const [todayRes, yestRes] = await Promise.all([
      fetch(`${BDL}/v1/games?dates[]=${today}&per_page=25`, { headers: auth() }),
      fetch(`${BDL}/v1/games?dates[]=${yesterday}&per_page=25`, { headers: auth() }),
    ]);
    const todayData = await todayRes.json();
    const yestData = await yestRes.json();
    const todayTeams = new Set();
    for (const g of todayData.data || []) {
      if (g.home_team?.abbreviation) todayTeams.add(g.home_team.abbreviation);
      if (g.visitor_team?.abbreviation) todayTeams.add(g.visitor_team.abbreviation);
    }
    const yestTeams = new Set();
    for (const g of yestData.data || []) {
      if (g.home_team?.abbreviation) yestTeams.add(g.home_team.abbreviation);
      if (g.visitor_team?.abbreviation) yestTeams.add(g.visitor_team.abbreviation);
    }
    const teams = [...todayTeams].map(abbr => ({ team: abbr, isB2B: yestTeams.has(abbr) }));
    res.json({ teams, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// PACE
app.get('/api/nba-pace', async (req, res) => {
  try {
    const r = await fetch(`${BDL}/nba/v1/team_season_averages/general?season=2025&season_type=regular&type=advanced&per_page=30`, { headers: auth() });
    const data = await r.json();
    const teams = (data.data || []).map(t => ({
      teamAbbr: t.team.abbreviation,
      teamName: t.team.full_name,
      pace: t.stats.pace,
      offRating: t.stats.off_rating,
      defRating: t.stats.def_rating,
      netRating: t.stats.net_rating,
    }));
    res.json({ teams, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// INJURIES
app.get('/api/nba-injuries', async (req, res) => {
  try {
    const r = await fetch(`${BDL}/v1/player_injuries`, { headers: auth() });
    const data = await r.json();
    const injuries = (data.data || []).map(i => ({
      playerName: `${i.player?.first_name} ${i.player?.last_name}`,
      team: i.team?.abbreviation,
      status: i.status,
      description: i.description,
    }));
    res.json({ injuries, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// PROPS
app.get('/api/nba-props', async (req, res) => {
  try {
    const today = getSlateDate();
    const gamesRes = await fetch(`${BDL}/v1/games?dates[]=${today}&per_page=25`, { headers: auth() });
    const gamesData = await gamesRes.json();
    const gameIds = (gamesData.data || []).map(g => g.id);
    if (gameIds.length === 0) return res.json({ props: [], lastUpdated: new Date().toISOString() });
    const propResults = await Promise.all(
      gameIds.map(id =>
        fetch(`${BDL}/v2/odds/player_props?game_id=${id}&prop_type=points&vendors[]=draftkings&per_page=100`, { headers: auth() })
          .then(r => r.json())
      )
    );
    const rawProps = propResults.flatMap(r => (r.data || []).filter(p => p.line_value != null));
    const playerIds = [...new Set(rawProps.map(p => p.player_id).filter(Boolean))];
    const playerNameMap = {};
    for (let i = 0; i < playerIds.length; i += 50) {
      const chunk = playerIds.slice(i, i + 50);
      const params = chunk.map(id => `ids[]=${id}`).join('&');
      const pRes = await fetch(`${BDL}/v1/players?${params}&per_page=100`, { headers: auth() });
      const pData = await pRes.json();
      for (const p of pData.data || []) playerNameMap[p.id] = `${p.first_name} ${p.last_name}`;
    }
    const allProps = rawProps.map(p => ({
      playerName: playerNameMap[p.player_id] || null,
      playerId: p.player_id,
      line: parseFloat(p.line_value),
      propType: p.prop_type,
      vendor: p.vendor,
    })).filter(p => p.playerName);
    const byPlayer = {};
    for (const p of allProps) {
      const key = `${p.playerId}_${p.propType}`;
      if (!byPlayer[key]) byPlayer[key] = [];
      byPlayer[key].push(p);
    }
    const props = Object.values(byPlayer).map(entries => {
      const half = entries.find(e => e.line % 1 === 0.5);
      if (half) return half;
      const sorted = [...entries].sort((a, b) => a.line - b.line);
      return sorted[Math.floor(sorted.length / 2)];
    });
    res.json({ props, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// DVP
app.get('/api/nba-dvp', async (req, res) => {
  try {
    const today = getSlateDate();
    const thirtyDaysAgo = new Date(Date.now() - 30 * 86400000).toISOString().split('T')[0];
    const tonightRes = await fetch(`${BDL}/v1/games?dates[]=${today}&per_page=25`, { headers: auth() });
    const tonightData = await tonightRes.json();
    const tonightTeamIds = new Set();
    const teamIdToAbbr = {};
    for (const g of tonightData.data || []) {
      if (g.home_team?.id) { tonightTeamIds.add(g.home_team.id); teamIdToAbbr[g.home_team.id] = g.home_team.abbreviation; }
      if (g.visitor_team?.id) { tonightTeamIds.add(g.visitor_team.id); teamIdToAbbr[g.visitor_team.id] = g.visitor_team.abbreviation; }
    }
    const recentRes = await fetch(`${BDL}/v1/games?start_date=${thirtyDaysAgo}&end_date=${today}&per_page=100`, { headers: auth() });
    const recentData = await recentRes.json();
    const allRecent = (recentData.data || []).filter(g => g.status === 'Final');
    const defGameIds = new Set();
    const teamDefGames = {};
    for (const teamId of tonightTeamIds) {
      const abbr = teamIdToAbbr[teamId];
      const games = allRecent
        .filter(g => g.home_team?.id === teamId || g.visitor_team?.id === teamId)
        .sort((a, b) => new Date(b.date) - new Date(a.date))
        .slice(0, 15);
      teamDefGames[abbr] = games;
      for (const g of games) defGameIds.add(g.id);
    }
    const statsResults = await Promise.all(
      [...defGameIds].map(id =>
        fetch(`${BDL}/v1/stats?game_ids[]=${id}&per_page=100`, { headers: auth() })
          .then(r => r.json()).then(d => d.data || [])
      )
    );
    const allStats = statsResults.flat();
    const dvpAccum = {};
    const dvpCount = {};
    for (const s of allStats) {
      const gameId = s.game?.id;
      const playerTeamId = s.team?.id;
      if (!gameId || !playerTeamId) continue;
      const game = allRecent.find(g => g.id === gameId);
      if (!game) continue;
      const defTeamId = game.home_team?.id === playerTeamId ? game.visitor_team?.id : game.home_team?.id;
      const defAbbr = teamIdToAbbr[defTeamId];
      if (!defAbbr) continue;
      const pts = s.pts || 0;
      const reb = s.reb || 0;
      const ast = s.ast || 0;
      const stl = s.stl || 0;
      const blk = s.blk || 0;
      const to = s.turnover || 0;
      const fg3 = s.fg3m || 0;
      const min = parseInt(s.min || '0');
      if (min < 10) continue;
      const fp = pts * 1 + reb * 1.25 + ast * 1.5 + stl * 2 + blk * 2 + to * -0.5 + fg3 * 0.5;
      const pos = s.player?.position || '';
      const positions = pos.includes('G') ? ['PG', 'SG'] :
                        pos.includes('F') ? ['SF', 'PF'] :
                        pos.includes('C') ? ['C'] :
                        pos === 'PG' ? ['PG'] :
                        pos === 'SG' ? ['SG'] :
                        pos === 'SF' ? ['SF'] :
                        pos === 'PF' ? ['PF'] : [];
      for (const p of positions) {
        const key = `${defAbbr}::${p}`;
        if (!dvpAccum[key]) { dvpAccum[key] = 0; dvpCount[key] = 0; }
        dvpAccum[key] += fp;
        dvpCount[key]++;
      }
    }
    const dvpMap = {};
    for (const [key, total] of Object.entries(dvpAccum)) {
      const [team, pos] = key.split('::');
      if (!dvpMap[team]) dvpMap[team] = {};
      dvpMap[team][pos.toLowerCase()] = parseFloat((total / dvpCount[key]).toFixed(1));
    }
    let fallback = {};
    try {
      const fbRes = await fetch('https://dfs-proxy.vercel.app/api/nba-dvp');
      fallback = await fbRes.json();
    } catch (e) {}
    const finalDvp = { ...(fallback.dvpMap || fallback), ...dvpMap };
    res.json({ dvpMap: finalDvp, source: 'recent-15games', lastUpdated: new Date().toISOString() });
  } catch (err) {
    try {
      const r = await fetch('https://dfs-proxy.vercel.app/api/nba-dvp');
      const data = await r.json();
      res.json({ ...data, source: 'fallback' });
    } catch (e) { res.status(500).json({ error: err.message }); }
  }
});

// RECENT STATS
app.get('/api/nba-recent-stats', async (req, res) => {
  try {
    const today = getSlateDate();
    const fourteenDaysAgo = new Date(Date.now() - 14 * 86400000).toISOString().split('T')[0];
    const tonightRes = await fetch(`${BDL}/v1/games?dates[]=${today}&per_page=25`, { headers: auth() });
    const tonightData = await tonightRes.json();
    const tonightTeamIds = new Set();
    const teamIdToAbbr = {};
    for (const g of tonightData.data || []) {
      if (g.home_team?.id) { tonightTeamIds.add(g.home_team.id); teamIdToAbbr[g.home_team.id] = g.home_team.abbreviation; }
      if (g.visitor_team?.id) { tonightTeamIds.add(g.visitor_team.id); teamIdToAbbr[g.visitor_team.id] = g.visitor_team.abbreviation; }
    }
    const recentRes = await fetch(`${BDL}/v1/games?start_date=${fourteenDaysAgo}&end_date=${today}&per_page=100`, { headers: auth() });
    const recentData = await recentRes.json();
    const allRecent = (recentData.data || []).filter(g => g.status === 'Final');

    // Track which games belong to which team for absent teammate detection
    const teamGameMap = {}; // teamAbbr -> [gameId, ...]
    const gameIdSet = new Set();
    for (const teamId of tonightTeamIds) {
      const abbr = teamIdToAbbr[teamId];
      const teamGames = allRecent
        .filter(g => g.home_team?.id === teamId || g.visitor_team?.id === teamId)
        .sort((a, b) => new Date(b.date) - new Date(a.date))
        .slice(0, 5);
      teamGameMap[abbr] = teamGames.map(g => g.id);
      for (const g of teamGames) gameIdSet.add(g.id);
    }

    const recentGameIds = [...gameIdSet];
    if (recentGameIds.length === 0) return res.json({ players: [], lastUpdated: new Date().toISOString() });

    const [advResults, regResults] = await Promise.all([
      Promise.all(recentGameIds.map(id =>
        fetch(`${BDL}/v2/stats/advanced?game_ids[]=${id}&per_page=100`, { headers: auth() })
          .then(r => r.json()).then(d => d.data || [])
      )),
      Promise.all(recentGameIds.map(id =>
        fetch(`${BDL}/v1/stats?game_ids[]=${id}&per_page=100`, { headers: auth() })
          .then(r => r.json()).then(d => d.data || [])
      )),
    ]);
    const advancedStats = advResults.flat();
    const regularStats = regResults.flat();

    // Build minutes map AND track team rosters per game
    const minutesMap = {};
    const teamRosterPerGame = {}; // "TEAM::gameId" -> Set of playerNames with >0 minutes
    const playerTeamMap = {}; // playerName -> teamAbbr

    for (const s of regularStats) {
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      const gameId = s.game?.id;
      const mins = parseInt(s.min || '0', 10);
      const teamAbbr = s.team?.abbreviation || '';
      if (name && gameId) {
        minutesMap[`${name}::${gameId}`] = mins;
        if (teamAbbr) playerTeamMap[name] = teamAbbr;
        // Track who played in each game for each team
        const rosterKey = `${teamAbbr}::${gameId}`;
        if (!teamRosterPerGame[rosterKey]) teamRosterPerGame[rosterKey] = new Set();
        if (mins > 5) teamRosterPerGame[rosterKey].add(name);
      }
    }

    // Build absent teammates map: for each team's recent games, find players who
    // regularly play for that team but had 0 minutes in specific games
    // absentMap: "TEAM::gameId" -> [absentPlayerName, ...]
    const teamRegulars = {}; // teamAbbr -> Set of players who played 5+ games
    for (const [key, roster] of Object.entries(teamRosterPerGame)) {
      const [teamAbbr] = key.split('::');
      if (!teamRegulars[teamAbbr]) teamRegulars[teamAbbr] = {};
      for (const name of roster) {
        teamRegulars[teamAbbr][name] = (teamRegulars[teamAbbr][name] || 0) + 1;
      }
    }

    const absentMap = {}; // "TEAM::gameId" -> [absentNames]
    for (const [teamAbbr, gameIds] of Object.entries(teamGameMap)) {
      const regulars = Object.entries(teamRegulars[teamAbbr] || {})
        .filter(([, count]) => count >= 3) // played in 3+ of last 5 games = regular
        .map(([name]) => name);

      for (const gameId of gameIds) {
        const rosterKey = `${teamAbbr}::${gameId}`;
        const playedThisGame = teamRosterPerGame[rosterKey] || new Set();
        const absent = regulars.filter(name => !playedThisGame.has(name));
        absentMap[`${teamAbbr}::${gameId}`] = absent;
      }
    }

    const byPlayer = {};
    for (const s of advancedStats) {
      if (s.period !== 0) continue;
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      if (!name || name === ' ') continue;
      const gameId = s.game?.id;
      const teamAbbr = s.team?.abbreviation || '';
      const absent = absentMap[`${teamAbbr}::${gameId}`] || [];
      if (!byPlayer[name]) byPlayer[name] = [];
      byPlayer[name].push({
        date: s.game?.date,
        gameId,
        teamAbbr,
        minutes: minutesMap[`${name}::${gameId}`] ?? 0,
        usage: parseFloat(((s.usage_percentage || 0) * 100).toFixed(1)),
        absentTeammates: absent.filter(a => a !== name),
      });
    }

    const players = Object.entries(byPlayer).map(([name, games]) => {
      const sorted = games
        .filter(g => g.date)
        .sort((a, b) => new Date(b.date) - new Date(a.date))
        .slice(0, 5);
      if (sorted.length < 1) return null;

      const avgMinutes = sorted.reduce((s, g) => s + g.minutes, 0) / sorted.length;
      const avgUsage = sorted.reduce((s, g) => s + g.usage, 0) / sorted.length;
      const recent2 = sorted.slice(0, 2);
      const prior3 = sorted.slice(2);
      const recentUsage = recent2.reduce((s, g) => s + g.usage, 0) / recent2.length;
      const priorUsage = prior3.length > 0 ? prior3.reduce((s, g) => s + g.usage, 0) / prior3.length : recentUsage;
      const recentMins = recent2.reduce((s, g) => s + g.minutes, 0) / recent2.length;
      const priorMins = prior3.length > 0 ? prior3.reduce((s, g) => s + g.minutes, 0) / prior3.length : recentMins;
      const hasTrend = sorted.length >= 2;

      // Collect all absent teammates during the recent 2 games (the spike window)
      const recentAbsentTeammates = [...new Set(
        recent2.flatMap(g => g.absentTeammates || [])
      )];

      // usageSpike: true only if recent usage is elevated AND
      // the spike is not fully explained by teammate absences
      // (if all recent games had absences, the spike may revert tonight)
      const rawSpike = recentUsage > avgUsage + 4;
      const spikeExplainedByAbsence = rawSpike && recentAbsentTeammates.length > 0 &&
        recent2.every(g => (g.absentTeammates || []).length > 0);

      return {
        playerName: name,
        teamAbbr: sorted[0]?.teamAbbr || '',
        avgMinutes: parseFloat(avgMinutes.toFixed(1)),
        avgUsage: parseFloat(avgUsage.toFixed(1)),
        recentMinutes: parseFloat(recentMins.toFixed(1)),
        recentUsage: parseFloat(recentUsage.toFixed(1)),
        minutesTrend: hasTrend ? parseFloat((recentMins - priorMins).toFixed(1)) : null,
        usageTrend: hasTrend ? parseFloat((recentUsage - priorUsage).toFixed(1)) : null,
        usageSpike: rawSpike && !spikeExplainedByAbsence,
        usageSpikeRaw: rawSpike,
        spikeExplainedByAbsence,
        recentAbsentTeammates,
        minutesRisk: recentMins < avgMinutes - 4 && avgMinutes >= 25,
        gamesPlayed: sorted.length,
      };
    }).filter(Boolean);

    res.json({ players, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// INJURY REPLACEMENT
app.get('/api/nba-injury-replacement', async (req, res) => {
  try {
    const { player, team } = req.query;
    if (!player || !team) return res.status(400).json({ error: 'player and team required' });
    const searchRes = await fetch(`${BDL}/v1/players?search=${encodeURIComponent(player)}&per_page=10`, { headers: auth() });
    const searchData = await searchRes.json();
    const playerLower = player.toLowerCase().trim();
    const searchResults = searchData.data || [];
    const match = searchResults.find(p => {
      const fullName = `${p.first_name} ${p.last_name}`.toLowerCase().trim();
      const lastName = p.last_name?.toLowerCase().trim();
      const firstName = p.first_name?.toLowerCase().trim();
      return fullName === playerLower ||
        fullName.includes(playerLower) ||
        playerLower.includes(lastName) ||
        (firstName && lastName && playerLower.includes(firstName) && playerLower.includes(lastName));
    }) || (searchResults.length === 1 ? searchResults[0] : null);
    if (!match) return res.json({ replacements: [], message: 'Player not found', searchResults: (searchData.data || []).map(p => p.first_name + ' ' + p.last_name), lastUpdated: new Date().toISOString() });
    const playerId = match.id;
    const today = getSlateDate();
    const seasonStart = '2025-10-01';
    const teamId = match.team?.id || match.team_id;
    if (!teamId) return res.json({ replacements: [], message: 'Team ID not found on player', player: match, lastUpdated: new Date().toISOString() });
    const gamesRes = await fetch(`${BDL}/v1/games?team_ids[]=${teamId}&start_date=${seasonStart}&end_date=${today}&per_page=100`, { headers: auth() });
    const gamesData = await gamesRes.json();
    const allGames = (gamesData.data || []).filter(g => g.status === 'Final');
    if (allGames.length === 0) return res.json({ replacements: [], message: 'No games found', lastUpdated: new Date().toISOString() });
    const allGameIds = allGames.map(g => g.id);
    const statsResults = await Promise.all(
      allGameIds.map(id =>
        fetch(`${BDL}/v1/stats?game_ids[]=${id}&per_page=100`, { headers: auth() })
          .then(r => r.json()).then(d => d.data || [])
      )
    );
    const allStats = statsResults.flat();
    const playerGameMinutes = {};
    for (const s of allStats) {
      if (s.player?.id === playerId) {
        playerGameMinutes[s.game?.id] = parseInt(s.min || '0', 10);
      }
    }
    const dnpGameIds = new Set(
      allGames
        .filter(g => playerGameMinutes[g.id] === 0 || playerGameMinutes[g.id] === undefined)
        .map(g => g.id)
    );
    const playedGameIds = new Set(
      allGames
        .filter(g => (playerGameMinutes[g.id] || 0) > 5)
        .map(g => g.id)
    );
    if (dnpGameIds.size < 2) {
      return res.json({ replacements: [], message: 'Insufficient DNP games for analysis', dnpGames: dnpGameIds.size, lastUpdated: new Date().toISOString() });
    }
    const teammateDnpMinutes = {};
    const teammatePlayedMinutes = {};
    const teammateCounts = { dnp: {}, played: {} };
    for (const s of allStats) {
      if (s.player?.id === playerId) continue;
      const pTeam = s.team?.abbreviation?.toUpperCase();
      if (pTeam !== team.toUpperCase()) continue;
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      const mins = parseInt(s.min || '0', 10);
      const gameId = s.game?.id;
      if (dnpGameIds.has(gameId)) {
        if (!teammateDnpMinutes[name]) { teammateDnpMinutes[name] = 0; teammateCounts.dnp[name] = 0; }
        teammateDnpMinutes[name] += mins;
        teammateCounts.dnp[name]++;
      }
      if (playedGameIds.has(gameId)) {
        if (!teammatePlayedMinutes[name]) { teammatePlayedMinutes[name] = 0; teammateCounts.played[name] = 0; }
        teammatePlayedMinutes[name] += mins;
        teammateCounts.played[name]++;
      }
    }
    const replacements = Object.keys(teammateDnpMinutes)
      .filter(name => teammateCounts.dnp[name] >= 2)
      .map(name => {
        const avgDnpMins = teammateDnpMinutes[name] / teammateCounts.dnp[name];
        const avgPlayedMins = teammatePlayedMinutes[name]
          ? teammatePlayedMinutes[name] / teammateCounts.played[name]
          : avgDnpMins;
        const minutesBump = avgDnpMins - avgPlayedMins;
        return {
          playerName: name,
          avgMinutesWhenOut: parseFloat(avgDnpMins.toFixed(1)),
          avgMinutesNormally: parseFloat(avgPlayedMins.toFixed(1)),
          minutesBump: parseFloat(minutesBump.toFixed(1)),
          dnpGamesAnalyzed: teammateCounts.dnp[name],
          isLikelyBeneficiary: minutesBump >= 4,
        };
      })
      .filter(r => r.avgMinutesWhenOut >= 8)
      .sort((a, b) => b.minutesBump - a.minutesBump);
    res.json({
      injuredPlayer: player,
      team: team.toUpperCase(),
      dnpGamesAnalyzed: dnpGameIds.size,
      replacements,
      lastUpdated: new Date().toISOString()
    });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// LINEUPS — confirmed starters for tonight's games
app.get('/api/nba-lineups', async (req, res) => {
  try {
    const today = getSlateDate();
    const gamesRes = await fetch(`${BDL}/v1/games?dates[]=${today}&per_page=25`, { headers: auth() });
    const gamesData = await gamesRes.json();
    const games = gamesData.data || [];
    if (games.length === 0) {
      return res.json({ players: [], startersByTeam: {}, lineupsConfirmed: false, lastUpdated: new Date().toISOString() });
    }
    const gameIds = games.map(g => g.id);
    const teamContext = {};
    for (const g of games) {
      if (g.home_team?.id) teamContext[g.home_team.id] = { abbreviation: g.home_team.abbreviation, opponent: g.visitor_team?.abbreviation };
      if (g.visitor_team?.id) teamContext[g.visitor_team.id] = { abbreviation: g.visitor_team.abbreviation, opponent: g.home_team?.abbreviation };
    }
    const params = gameIds.map(id => `game_ids[]=${id}`).join('&');
    const lineupRes = await fetch(`${BDL}/v1/lineups?${params}&per_page=100`, { headers: auth() });
    const lineupData = await lineupRes.json();
    const entries = lineupData.data || [];
    const players = entries.map(e => {
      const tc = teamContext[e.team?.id] || {};
      return {
        playerName: `${e.player?.first_name} ${e.player?.last_name}`.trim(),
        team: e.team?.abbreviation || '',
        opponent: tc.opponent || '',
        position: e.position || e.player?.position || '',
        starter: e.starter === true,
      };
    });
    const startersByTeam = {};
    for (const p of players.filter(p => p.starter)) {
      if (!startersByTeam[p.team]) startersByTeam[p.team] = [];
      startersByTeam[p.team].push(p.playerName);
    }
    res.json({ players, startersByTeam, lineupsConfirmed: entries.length > 0, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// NBA OFFICIAL INJURY REPORT — scrapes PDF directly from NBA servers
app.get('/api/nba-injury-report', async (req, res) => {
  try {
    const pdfParse = require('pdf-parse');

    // Build candidate URLs — 15-minute snapshots, try last 8 slots
    const now = new Date();
    const etNow = new Date(now.getTime() + (-5 * 60 * 60 * 1000));

    const candidates = [];
    for (let i = 0; i < 8; i++) {
      const slot = new Date(etNow.getTime() - i * 15 * 60 * 1000);
      const totalMins = slot.getUTCMinutes();
      const snapMin = Math.floor(totalMins / 15) * 15;
      const hour = slot.getUTCHours();
      const hh = String(hour % 12 || 12).padStart(2, '0');
      const mm = String(snapMin).padStart(2, '0');
      const ampm = hour >= 12 ? 'PM' : 'AM';
      const yyyy = slot.toISOString().split('T')[0];
      candidates.push(`https://ak-static.cms.nba.com/referee/injury/Injury-Report_${yyyy}_${hh}_${mm}${ampm}.pdf`);
    }

    // Deduplicate
    const uniqueCandidates = [...new Set(candidates)];

    const headers = {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': 'application/pdf,*/*',
    };

    // Try each URL until one returns a valid PDF
    let pdfBuffer = null;
    let usedUrl = null;
    for (const url of uniqueCandidates) {
      try {
        const r = await fetch(url, { headers });
        if (r.ok) {
          const contentType = r.headers.get('content-type') || '';
          if (contentType.includes('pdf') || contentType.includes('octet-stream')) {
            const arrayBuffer = await r.arrayBuffer();
            pdfBuffer = Buffer.from(arrayBuffer);
            usedUrl = url;
            break;
          }
        }
      } catch (e) { continue; }
    }

    if (!pdfBuffer) {
      return res.status(200).json({
        injuries: [],
        message: 'No injury report PDF found',
        tried: uniqueCandidates,
        lastUpdated: new Date().toISOString()
      });
    }

    // Parse PDF text
    // NBA injury PDF has no spaces between words — everything concatenated
    // Line format: "TeamNameLastName,FirstNameStatusReason" or just "LastName,FirstNameStatusReason"
    const parsed = await pdfParse(pdfBuffer);
    const lines = parsed.text.split('\n').map(l => l.trim()).filter(Boolean);

    // No-space team name to abbreviation
    const teamMap = {
      'GoldenStateWarriors': 'GSW', 'WashingtonWizards': 'WAS',
      'OrlandoMagic': 'ORL', 'AtlantaHawks': 'ATL',
      'PhoenixSuns': 'PHX', 'BostonCeltics': 'BOS',
      'PortlandTrailBlazers': 'POR', 'BrooklynNets': 'BKN',
      'DallasMavericks': 'DAL', 'NewOrleansPelicans': 'NOP',
      'MemphisGrizzlies': 'MEM', 'ChicagoBulls': 'CHI',
      'LosAngelesLakers': 'LAL', 'HoustonRockets': 'HOU',
      'SanAntonioSpurs': 'SAS', 'LAClippers': 'LAC',
      'MiamiHeat': 'MIA', 'CharlotteHornets': 'CHA',
      'OklahomaCityThunder': 'OKC', 'IndianaPacers': 'IND',
      'ClevelandCavaliers': 'CLE', 'MilwaukeeBucks': 'MIL',
      'MinnesotaTimberwolves': 'MIN', 'Philadelphia76ers': 'PHI',
      'DenverNuggets': 'DEN', 'SacramentoKings': 'SAC',
      'TorontoRaptors': 'TOR', 'UtahJazz': 'UTA',
      'DetroitPistons': 'DET', 'NewYorkKnicks': 'NYK',
    };

    // Status keywords in no-space format — longer variants first to avoid partial matches
    const statusKeywords = ['OutForSeason', 'Questionable', 'Doubtful', 'Probable', 'Available', 'Out'];
    const statusLabels = {
      'OutForSeason': 'Out For Season', 'Questionable': 'Questionable',
      'Doubtful': 'Doubtful', 'Probable': 'Probable',
      'Available': 'Available', 'Out': 'Out',
    };

    const injuries = [];
    let currentTeam = '';

    for (const line of lines) {
      // Skip header/page/date lines
      if (/InjuryReport:|Page\d+of\d+|GameDate|NOTYETSUBMITTED/.test(line)) continue;
      if (/^\d{2}\/\d{2}\/\d{4}/.test(line)) continue;

      // Detect team name embedded anywhere in line — update currentTeam
      for (const [noSpace, abbr] of Object.entries(teamMap)) {
        if (line.includes(noSpace)) {
          currentTeam = abbr;
          break;
        }
      }

      // Find status keyword
      const statusFound = statusKeywords.find(s => line.includes(s));
      if (!statusFound || !currentTeam) continue;

      // Extract name portion — everything before status keyword
      let beforeStatus = line.split(statusFound)[0];

      // Strip any team name prefix
      for (const noSpace of Object.keys(teamMap)) {
        if (beforeStatus.includes(noSpace)) {
          const idx = beforeStatus.indexOf(noSpace);
          beforeStatus = beforeStatus.slice(idx + noSpace.length);
          break;
        }
      }

      // Strip matchup/time prefixes
      beforeStatus = beforeStatus.replace(/^\d{2}:\d{2}\(ET\)[A-Z@]+/, '');
      beforeStatus = beforeStatus.replace(/^[A-Z]{2,3}@[A-Z]{2,3}/, '');
      beforeStatus = beforeStatus.trim();

      // Must have comma for "LastName,FirstName" format
      if (!beforeStatus.includes(',')) continue;
      if (beforeStatus.length < 4) continue;

      // Convert "LastName,FirstName" to "FirstName LastName"
      const commaIdx = beforeStatus.indexOf(',');
      const lastName = beforeStatus.slice(0, commaIdx).trim();
      const firstName = beforeStatus.slice(commaIdx + 1).trim();
      if (!firstName || !lastName) continue;

      const playerName = `${firstName} ${lastName}`;
      const reason = (line.split(statusFound)[1] || '').trim();

      injuries.push({
        playerName,
        team: currentTeam,
        status: statusLabels[statusFound] || statusFound,
        description: reason,
      });
    }


    res.json({
      injuries,
      total: injuries.length,
      sourceUrl: usedUrl,
      lastUpdated: new Date().toISOString()
    });

  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// NBA INJURY REPORT DEBUG — shows raw PDF text for parser development
app.get('/api/nba-injury-debug', async (req, res) => {
  try {
    const pdfParse = require('pdf-parse');
    const url = 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-03-16_04_00PM.pdf';
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
      'Accept': 'application/pdf,*/*',
    };
    const r = await fetch(url, { headers });
    const arrayBuffer = await r.arrayBuffer();
    const pdfBuffer = Buffer.from(arrayBuffer);
    const parsed = await pdfParse(pdfBuffer);
    const lines = parsed.text.split('\n').map(l => l.trim()).filter(Boolean);
    res.json({
      rawText: parsed.text.slice(0, 3000),
      lines: lines.slice(0, 80),
      totalLines: lines.length
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Proxy running on port ${PORT}`));
