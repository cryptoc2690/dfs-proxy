export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const response = await fetch(
      'https://api.balldontlie.io/v1/teams/advanced_stats?season=2025&per_page=30',
      { headers: { 'Authorization': '51177dd2-a3a8-4cf6-bb90-4dbc10cde7ee' } }
    );

    const data = await response.json();
    const teams = data.data.map(t => ({
      teamId: t.team_id,
      teamName: t.team.full_name,
      teamAbbr: t.team.abbreviation,
      pace: t.pace,
      offRating: t.off_rating,
      defRating: t.def_rating,
    }));

    return res.status(200).json({ teams });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
