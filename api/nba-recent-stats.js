export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
    const today = new Date();
    const tenDaysAgo = new Date(today - 10 * 86400000).toISOString().split('T')[0];
    const todayStr = today.toISOString().split('T')[0];

    const gamesRes = await fetch(
      `https://api.balldontlie.io/v1/games?start_date=${tenDaysAgo}&end_date=${todayStr}&per_page=50`,
      { headers }
    );
    const gamesData = await gamesRes.json();
    const gameIds = (gamesData.data || []).map(g => g.id);

    // Test just first 2 games
    const testIds = gameIds.slice(0, 2);
    const params = testIds.map(id => `game_ids[]=${id}`).join('&');

    const [advRes, regRes] = await Promise.all([
      fetch(`https://api.balldontlie.io/v2/stats/advanced?${params}&per_page=200`, { headers }),
      fetch(`https://api.balldontlie.io/v1/stats?${params}&per_page=200`, { headers }),
    ]);
    const advData = await advRes.json();
    const regData = await regRes.json();

    const periods = [...new Set((advData.data || []).map(s => s.period))];
    const withPeriod0 = (advData.data || []).filter(s => s.period === 0).length;
    const regWithMin = (regData.data || []).filter(s => parseInt(s.min || '0') > 0).length;

    return res.status(200).json({
      gameIds: testIds,
      totalAdvRecords: advData.data?.length,
      periodsFound: periods,
      advRecordsPeriod0: withPeriod0,
      totalRegRecords: regData.data?.length,
      regRecordsWithMinutes: regWithMin,
      sampleAdv: advData.data?.[0] ? { period: advData.data[0].period, name: `${advData.data[0].player?.first_name} ${advData.data[0].player?.last_name}` } : null,
      sampleReg: regData.data?.[0] ? { min: regData.data[0].min, name: `${regData.data[0].player?.first_name} ${regData.data[0].player?.last_name}` } : null,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
