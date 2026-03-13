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

app.get('/api/odds', async (req, res) => {
  try {
    const today = new Date().toISOString().split('T')[0];
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

app.get('/api/nba-schedule', async (req, res) => {
  try {
    const today = new Date().toISOString().split('T')[0];
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

app.get('/api/nba-props', async (req, res) => {
  try {
    const today = new Date().toISOString().split('T')[0];
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
      const pRes = await fetch(`${BDL}/v1/players?${params}&per_page=50`, { headers: auth() });
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

app.get('/api/nba-dvp', async (req, res) => {
  try {
    const r = await fetch('https://dfs-proxy.vercel.app/api/nba-dvp');
    const data = await r.json();
    res.json(data);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/nba-recent-stats', async (req, res) => {
  try {
    const today = new Date().toISOString().split('T')[0];
    const sevenDaysAgo = new Date(Date.now() - 14 * 86400000).toISOString().split('T')[0];
    
    const tonightRes = await fetch(`${BDL}/v1/games?dates[]=${today}&per_page=25`, { headers: auth() });
    const tonightData = await tonightRes.json();
    const tonightTeamIds = new Set(
      (tonightData.data || []).flatMap(g => [g.home_team?.id, g.visitor_team?.id]).filter(Boolean)
    );

    const recentRes = await fetch(`${BDL}/v1/games?start_date=${sevenDaysAgo}&end_date=${today}&per_page=50`, { headers: auth() });
    const recentData = await recentRes.json();
    const recentGameIds = (recentData.data || [])
      .filter(g => g.status === 'Final' && (tonightTeamIds.has(g.home_team?.id) || tonightTeamIds.has(g.visitor_team?.id)))
      .sort((a, b) => new Date(b.date) - new Date(a.date))
      .slice(0, 5)
      .map(g => g.id);

    if (recentGameIds.length === 0) {
      return res.json({ players: [], lastUpdated: new Date().toISOString() });
    }

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

    const minutesMap = {};
    for (const s of regularStats) {
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      const gameId = s.game?.id;
      if (name && gameId) minutesMap[`${name}::${gameId}`] = parseInt(s.min || '0', 10);
    }

    const byPlayer = {};
    for (const s of advancedStats) {
      if (s.period !== 0) continue;
      const name = `${s.player?.first_name} ${s.player?.last_name}`.trim();
      if (!name || name === ' ') continue;
      const gameId = s.game?.id;
      if (!byPlayer[name]) byPlayer[name] = [];
      byPlayer[name].push({
        date: s.game?.date,
        minutes: minutesMap[`${name}::${gameId}`] ?? 0,
        usage: parseFloat(((s.usage_percentage || 0) * 100).toFixed(1)),
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

      return {
        playerName: name,
        avgMinutes: parseFloat(avgMinutes.toFixed(1)),
        avgUsage: parseFloat(avgUsage.toFixed(1)),
        recentMinutes: parseFloat(recentMins.toFixed(1)),
        recentUsage: parseFloat(recentUsage.toFixed(1)),
        minutesTrend: hasTrend ? parseFloat((recentMins - priorMins).toFixed(1)) : null,
        usageTrend: hasTrend ? parseFloat((recentUsage - priorUsage).toFixed(1)) : null,
        usageSpike: recentUsage > avgUsage + 4,
        minutesRisk: recentMins < avgMinutes - 4 && avgMinutes >= 25,
        gamesPlayed: sorted.length,
      };
    }).filter(Boolean);

    res.json({ players, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Proxy running on port ${PORT}`));
