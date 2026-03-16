export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

  const getSlateDate = () => {
    const now = new Date();
    const etOffset = -5;
    const etNow = new Date(now.getTime() + (etOffset * 60 * 60 * 1000));
    const etHour = etNow.getUTCHours();
    if (etHour < 2) {
      const yesterday = new Date(etNow.getTime() - 86400000);
      return yesterday.toISOString().split('T')[0];
    }
    return etNow.toISOString().split('T')[0];
  };

  try {
    const today = getSlateDate();

    // Step 1: Get tonight's game IDs
    const gamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`,
      { headers }
    );
    const gamesData = await gamesRes.json();
    const games = gamesData.data || [];

    if (games.length === 0) {
      return res.status(200).json({ 
        players: [], 
        games: [],
        lastUpdated: new Date().toISOString() 
      });
    }

    const gameIds = games.map(g => g.id);

    // Build team context map
    const teamContext = {};
    for (const g of games) {
      if (g.home_team?.id) {
        teamContext[g.home_team.id] = {
          abbreviation: g.home_team.abbreviation,
          gameId: g.id,
          opponent: g.visitor_team?.abbreviation
        };
      }
      if (g.visitor_team?.id) {
        teamContext[g.visitor_team.id] = {
          abbreviation: g.visitor_team.abbreviation,
          gameId: g.id,
          opponent: g.home_team?.abbreviation
        };
      }
    }

    // Step 2: Get lineups for all tonight's games
    const params = gameIds.map(id => `game_ids[]=${id}`).join('&');
    const lineupRes = await fetch(
      `https://api.balldontlie.io/v1/lineups?${params}&per_page=100`,
      { headers }
    );
    const lineupData = await lineupRes.json();
    const lineupEntries = lineupData.data || [];

    if (lineupEntries.length === 0) {
      return res.status(200).json({
        players: [],
        games: games.map(g => ({
          homeTeam: g.home_team?.abbreviation,
          awayTeam: g.visitor_team?.abbreviation,
          gameId: g.id,
          status: g.status
        })),
        lineupsConfirmed: false,
        lastUpdated: new Date().toISOString()
      });
    }

    // Step 3: Format output — one entry per player
    const players = lineupEntries.map(entry => {
      const playerName = `${entry.player?.first_name} ${entry.player?.last_name}`.trim();
      const teamId = entry.team?.id;
      const tc = teamContext[teamId] || {};

      return {
        playerName,
        team: entry.team?.abbreviation || tc.abbreviation || '',
        opponent: tc.opponent || '',
        position: entry.position || entry.player?.position || '',
        starter: entry.starter === true,
        gameId: entry.game_id,
      };
    });

    // Group starters by team for easy reference
    const startersByTeam = {};
    for (const p of players) {
      if (!p.starter) continue;
      if (!startersByTeam[p.team]) startersByTeam[p.team] = [];
      startersByTeam[p.team].push(p.playerName);
    }

    return res.status(200).json({
      players,
      startersByTeam,
      lineupsConfirmed: true,
      gamesWithLineups: [...new Set(players.map(p => p.gameId))].length,
      lastUpdated: new Date().toISOString()
    });

  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
