export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

    // Test single vs multiple
    const [single, multi] = await Promise.all([
      fetch(`https://api.balldontlie.io/v2/stats/advanced?game_ids[]=18447701&per_page=5`, { headers }),
      fetch(`https://api.balldontlie.io/v2/stats/advanced?game_ids[]=18447701&game_ids[]=18447702&per_page=5`, { headers }),
    ]);
    const singleData = await single.json();
    const multiData = await multi.json();

    return res.status(200).json({
      singleCount: singleData.data?.length,
      multiCount: multiData.data?.length,
      multiError: multiData.error || null,
      multiRaw: JSON.stringify(multiData).slice(0, 200),
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
