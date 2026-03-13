export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const today = new Date().toISOString().split('T')[0];
    const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];
    const tomorrow = new Date(Date.now() + 86400000).toISOString().split('T')[0];

    const [todayRes, yestRes] = await Promise.all([
      fetch(`https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`, {
        headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY }
      }),
      fetch(`https://api.balldontlie.io/v1/games?dates[]=${yesterday}&per_page=25`, {
        headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY }
      }),
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

    const teams = [...todayTeams].map(abbr => ({
      team: abbr,
      isB2B: yestTeams.has(abbr),
    }));

    return res.status(200).json({ teams, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
