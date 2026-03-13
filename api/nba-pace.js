export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const url = 'https://stats.nba.com/stats/leaguedashteamstats?Season=2025-26&SeasonType=Regular+Season&MeasureType=Advanced&PerMode=PerGame&LeagueID=00&DateFrom=&DateTo=&Conference=&Division=&GameScope=&GameSegment=&LastNGames=0&Location=&Month=0&OpponentTeamID=0&Outcome=&PORound=&PaceAdjust=N&Period=0&PlayerExperience=&PlayerPosition=&PlusMinus=N&Rank=N&SeasonSegment=&ShotClockRange=&StarterBench=&TeamID=&TwoWay=&VsConference=&VsDivision=';
    
    const response = await fetch(url, {
      headers: {
        'Referer': 'https://www.nba.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'x-nba-stats-origin': 'stats',
        'x-nba-stats-token': 'true'
      }
    });

    if (!response.ok) {
      return res.status(response.status).json({ error: 'NBA API error', status: response.status });
    }

    const data = await response.json();
    const headers = data.resultSets[0].headers;
    const rows = data.resultSets[0].rowSet;

    const teams = rows.map(row => {
      const obj = {};
      headers.forEach((h, i) => { obj[h] = row[i]; });
      return {
        teamId: obj.TEAM_ID,
        teamName: obj.TEAM_NAME,
        teamAbbr: obj.TEAM_ABBREVIATION,
        pace: obj.PACE,
        offRating: obj.OFF_RATING,
        defRating: obj.DEF_RATING,
        netRating: obj.NET_RATING,
      };
    });

    return res.status(200).json({ teams });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
