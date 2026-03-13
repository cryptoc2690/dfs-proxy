export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();
  try {
    const headers = { 'Authorization': process.env.BALLDONTLIE_API_KEY };
    const today = new Date().toISOString().split('T')[0];
    const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().split('T')[0];

    // Step 1: tonight's games
    const tonightRes = await fetch(`https://api.balldontlie.io/v1/games?dates[]=${today}&per_page=25`, { headers });
    const tonightData = await tonightRes.json();
    const tonightTeamIds = [...new Set(tonightData.data?.flatMap(g => [g.home_team?.id, g.visitor_team?.id]).filter(Boolean))];

    // Step 2: recent games no team filter
    const recentRes = await fetch(`https://api.balldontlie.io/v1/games?start_date=${sevenDaysAgo}&end_date=${today}&per_page=50`, { headers });
    const recentData = await recentRes.json();
    const statuses = [...new Set(recentData.data?.map(g => g.status))];
    const finalGames = recentData.data?.filter(g => g.status === 'Final') || [];

    // Step 3: test stats on one known-good game
    const testId = 18447701;
    const advRes = await fetch(`https://api.balldontlie.io/v2/stats/advanced?game_ids[]=${testId}&per_page=5`, { headers });
    const advData = await advRes.json();

    return res.status(200).json({
      step1_tonightGameCount: tonightData.data?.length,
      step1_tonightTeamIds: tonightTeamIds,
      step2_allRecentGames: recentData.data?.length,
      step2_statusesFound: statuses,
      step2_finalGamesCount: finalGames.length,
      step2_sampleGame: recentData.data?.[0] ? { id: recentData.data[0].id, status: recentData.data[0].status, date: recentData.data[0].date } : null,
      step3_advStatsCount: advData.data?.length,
      step3_sample: advData.data?.[0] ? { period: advData.data[0].period, name: `${advData.data[0].player?.first_name} ${advData.data[0].player?.last_name}` } : null,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
