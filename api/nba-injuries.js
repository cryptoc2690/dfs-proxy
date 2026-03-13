export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const r = await fetch('https://api.balldontlie.io/v1/player_injuries', {
      headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY }
    });
    const data = await r.json();
    const injuries = (data.data || []).map(i => ({
      playerName: `${i.player?.first_name} ${i.player?.last_name}`,
      team: i.team?.abbreviation,
      status: i.status,   // 'Out', 'Questionable', 'Doubtful'
      description: i.description,
    }));
    return res.status(200).json({ injuries, lastUpdated: new Date().toISOString() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
