export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const today = new Date().toISOString().split('T')[0];
    const r = await fetch(
      `https://api.balldontlie.io/v2/odds?date=${today}`,
      { headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY } }
    );
    const data = await r.json();

    // Debug first — paste what comes back
    return res.status(200).json({ raw: data, testedDate: today });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
