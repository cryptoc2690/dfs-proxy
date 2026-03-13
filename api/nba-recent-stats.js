export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

    // Test one of the actual recent game IDs
    const testId = 18447771;
    const [advRes, regRes] = await Promise.all([
      fetch(`https://api.balldontlie.io/v2/stats/advanced?game_ids[]=${testId}&per_page=10`, { headers }),
      fetch(`https://api.balldontlie.io/v1/stats?game_ids[]=${testId}&per_page=10`, { headers }),
    ]);
    const advData = await advRes.json();
    const regData = await regRes.json();

    return res.status(200).json({
      advCount: advData.data?.length,
      advError: advData.error || null,
      regCount: regData.data?.length,
      regError: regData.error || null,
      advSample: advData.data?.[0] ? {
        period: advData.data[0].period,
        name: `${advData.data[0].player?.first_name} ${advData.data[0].player?.last_name}`,
        date: advData.data[0].game?.date,
      } : null,
      regSample: regData.data?.[0] ? {
        min: regData.data[0].min,
        name: `${regData.data[0].player?.first_name} ${regData.data[0].player?.last_name}`,
      } : null,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
