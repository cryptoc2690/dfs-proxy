// WNBA injuries — balldontlie player_injuries for the WNBA.
// This is the primary "who's out / who's a risk" feed. WNBA has no confirmed
// -lineups endpoint, so combine this with recent minutes to infer starters.
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const BDL = 'https://api.balldontlie.io/wnba/v1';
  const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

  try {
    // Cursor-paginate the full injury list.
    let cursor = null;
    const rows = [];
    for (let i = 0; i < 20; i++) {
      const url = `${BDL}/player_injuries?per_page=100${cursor ? `&cursor=${cursor}` : ''}`;
      const r = await fetch(url, { headers });
      const data = await r.json();
      rows.push(...(data.data || []));
      cursor = data.meta?.next_cursor;
      if (!cursor) break;
    }
    const injuries = rows.map(i => ({
      playerName: `${i.player?.first_name || ''} ${i.player?.last_name || ''}`.trim(),
      team: i.player?.team_id ?? i.team?.abbreviation ?? null,
      status: i.status,
      description: i.description || i.return_date || '',
    }));
    res.json({ injuries, total: injuries.length, lastUpdated: new Date().toISOString() });
  } catch (err) { res.status(500).json({ error: err.message }); }
}
