export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const response = await fetch(
      'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams?limit=30'
    );
    const data = await response.json();

    const teamIds = data.sports[0].leagues[0].teams.map(t => t.team.id);

    // Fetch stats for all teams in parallel
    const statsResponses = await Promise.all(
      teamIds.map(id =>
        fetch(`https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/${id}/statistics`)
          .then(r => r.json())
      )
    );

    const teams = statsResponses.map(teamData => {
      const team = teamData.team;
      const stats = teamData.results?.stats?.categories || [];

      const getStat = (name) => {
        for (const cat of stats) {
          for (const s of cat.stats || []) {
            if (s.name === name) return parseFloat(s.value) || 0;
          }
        }
        return null;
      };

      return {
        teamAbbr: team.abbreviation,
        teamName: team.displayName,
        pace: getStat('pace'),
        offRating: getStat('offensiveRating'),
        defRating: getStat('defensiveRating'),
        netRating: getStat('netRating'),
      };
    });

    return res.status(200).json({ teams, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
