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

    const sample = await fetch(
      `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/${teamIds[0]}/statistics`
    ).then(r => r.json());

    const allStats = [];
    for (const cat of sample.results?.stats?.categories || []) {
      for (const s of cat.stats || []) {
        allStats.push({ category: cat.name, name: s.name, displayName: s.displayName, value: s.value });
      }
    }

    return res.status(200).json({ debug: allStats });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
