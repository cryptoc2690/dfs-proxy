export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

    const statsRes = await fetch(
      `https://api.balldontlie.io/v2/stats/advanced?game_ids[]=18447701&per_page=3`,
      { headers }
    );
    const statsData = await statsRes.json();
    const first = statsData.data?.[0] || {};

    return res.status(200).json({
      allFields: Object.keys(first),
      fullFirstRecord: first,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
