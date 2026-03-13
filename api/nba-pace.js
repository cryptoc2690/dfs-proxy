export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const r = await fetch(
      'https://api.balldontlie.io/nba/v1/team_season_averages/general?season=2025&season_type=regular&type=advanced&per_page=30',
      { headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY } }
    );
    const data = await r.json();

    const teams = (data.data || []).map(t => ({
      teamAbbr: t.team.abbreviation,
      teamName: t.team.full_name,
      pace: t.stats.pace,
      offRating: t.stats.off_rating,
      defRating: t.stats.def_rating,
      netRating: t.stats.net_rating,
      paceRank: t.stats.pace_rank,
    }));

    return res.status(200).json({ teams, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
