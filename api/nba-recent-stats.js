export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };

    const [advRes, regRes] = await Promise.all([
      fetch(`https://api.balldontlie.io/v2/stats/advanced?game_ids[]=18447701&per_page=3`, { headers }),
      fetch(`https://api.balldontlie.io/v1/stats?game_ids[]=18447701&per_page=3`, { headers }),
    ]);
    const advData = await advRes.json();
    const regData = await regRes.json();

    return res.status(200).json({
      advSample: {
        period: advData.data?.[0]?.period,
        usage: advData.data?.[0]?.usage_percentage,
        playerName: `${advData.data?.[0]?.player?.first_name} ${advData.data?.[0]?.player?.last_name}`,
        gameId: advData.data?.[0]?.game?.id,
        gameDate: advData.data?.[0]?.game?.date,
      },
      regSample: {
        min: regData.data?.[0]?.min,
        minType: typeof regData.data?.[0]?.min,
        playerName: `${regData.data?.[0]?.player?.first_name} ${regData.data?.[0]?.player?.last_name}`,
        gameId: regData.data?.[0]?.game?.id,
      },
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
