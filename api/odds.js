export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const today = new Date().toISOString().split('T')[0];
    
    // Try both param formats
    const r = await fetch(
      `https://api.balldontlie.io/v1/betting_odds?start_date=${today}&end_date=${today}`,
      { headers: { 'Authorization': process.env.BALLDONTLIE_API_KEY } }
    );
    const data = await r.json();
    
    // Return raw so we can see the actual structure
    return res.status(200).json({ raw: data, testedDate: today });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
