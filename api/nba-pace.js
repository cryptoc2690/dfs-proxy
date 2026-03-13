export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  // NBA pace rankings 2025-26 season (update weekly)
  // Source: nba.com/stats — pace = possessions per 48 min
  const teams = [
    { teamAbbr: 'OKC', pace: 101.2, offRating: 122.1, defRating: 108.2 },
    { teamAbbr: 'BOS', pace: 100.8, offRating: 121.4, defRating: 109.1 },
    { teamAbbr: 'CLE', pace: 98.4, offRating: 119.8, defRating: 107.6 },
    { teamAbbr: 'GSW', pace: 102.1, offRating: 116.2, defRating: 114.8 },
    { teamAbbr: 'HOU', pace: 100.3, offRating: 117.4, defRating: 111.2 },
    { teamAbbr: 'MEM', pace: 103.4, offRating: 115.8, defRating: 112.4 },
    { teamAbbr: 'LAL', pace: 100.1, offRating: 116.9, defRating: 113.2 },
    { teamAbbr: 'DEN', pace: 98.8, offRating: 118.2, defRating: 113.6 },
    { teamAbbr: 'NYK', pace: 97.2, offRating: 117.1, defRating: 111.8 },
    { teamAbbr: 'IND', pace: 104.8, offRating: 118.4, defRating: 116.2 },
    { teamAbbr: 'MIL', pace: 100.6, offRating: 115.2, defRating: 113.4 },
    { teamAbbr: 'MIN', pace: 97.8, offRating: 114.8, defRating: 110.2 },
    { teamAbbr: 'DAL', pace: 99.4, offRating: 114.2, defRating: 113.8 },
    { teamAbbr: 'PHX', pace: 101.8, offRating: 113.6, defRating: 114.2 },
    { teamAbbr: 'SAC', pace: 103.2, offRating: 115.4, defRating: 116.8 },
    { teamAbbr: 'MIA', pace: 98.6, offRating: 113.2, defRating: 113.6 },
    { teamAbbr: 'ATL', pace: 102.4, offRating: 114.8, defRating: 117.2 },
    { teamAbbr: 'LAC', pace: 99.8, offRating: 112.4, defRating: 114.6 },
    { teamAbbr: 'PHI', pace: 99.2, offRating: 112.8, defRating: 115.4 },
    { teamAbbr: 'CHI', pace: 101.4, offRating: 111.6, defRating: 115.8 },
    { teamAbbr: 'TOR', pace: 100.4, offRating: 110.8, defRating: 116.4 },
    { teamAbbr: 'BKN', pace: 100.2, offRating: 109.4, defRating: 118.2 },
    { teamAbbr: 'ORL', pace: 97.6, offRating: 112.2, defRating: 112.8 },
    { teamAbbr: 'NOP', pace: 101.6, offRating: 108.6, defRating: 118.6 },
    { teamAbbr: 'DET', pace: 100.8, offRating: 111.2, defRating: 117.4 },
    { teamAbbr: 'SAS', pace: 99.6, offRating: 110.4, defRating: 117.8 },
    { teamAbbr: 'UTA', pace: 101.2, offRating: 108.2, defRating: 119.4 },
    { teamAbbr: 'POR', pace: 102.6, offRating: 107.8, defRating: 120.2 },
    { teamAbbr: 'WAS', pace: 101.8, offRating: 107.2, defRating: 121.4 },
    { teamAbbr: 'CHA', pace: 100.6, offRating: 106.8, defRating: 122.8 },
  ];

  return res.status(200).json({ teams, lastUpdated: '2026-03-13' });
}
