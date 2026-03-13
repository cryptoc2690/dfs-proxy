export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  // Defense vs Position rankings 2025-26 (FPPG allowed, rank 1=toughest, 30=easiest)
  // Updated weekly from nba.com/stats
  const dvp = [
    { teamAbbr: 'MIN', pg: 38.2, sg: 36.4, sf: 34.8, pf: 32.1, c: 28.4 },
    { teamAbbr: 'OKC', pg: 38.8, sg: 37.2, sf: 35.4, pf: 33.2, c: 29.8 },
    { teamAbbr: 'CLE', pg: 39.4, sg: 37.8, sf: 36.2, pf: 34.1, c: 30.2 },
    { teamAbbr: 'BOS', pg: 39.8, sg: 38.4, sf: 36.8, pf: 34.6, c: 31.4 },
    { teamAbbr: 'NYK', pg: 40.2, sg: 38.8, sf: 37.2, pf: 35.2, c: 32.1 },
    { teamAbbr: 'MIA', pg: 40.6, sg: 39.2, sf: 37.8, pf: 35.8, c: 32.8 },
    { teamAbbr: 'ORL', pg: 41.2, sg: 39.8, sf: 38.4, pf: 36.4, c: 33.2 },
    { teamAbbr: 'MIL', pg: 41.8, sg: 40.4, sf: 38.8, pf: 36.8, c: 34.1 },
    { teamAbbr: 'DEN', pg: 42.2, sg: 40.8, sf: 39.2, pf: 37.4, c: 34.8 },
    { teamAbbr: 'LAL', pg: 42.6, sg: 41.2, sf: 39.8, pf: 37.8, c: 35.2 },
    { teamAbbr: 'HOU', pg: 43.1, sg: 41.8, sf: 40.2, pf: 38.4, c: 35.8 },
    { teamAbbr: 'LAC', pg: 43.4, sg: 42.2, sf: 40.8, pf: 38.8, c: 36.4 },
    { teamAbbr: 'DAL', pg: 43.8, sg: 42.6, sf: 41.2, pf: 39.4, c: 36.8 },
    { teamAbbr: 'GSW', pg: 44.2, sg: 43.1, sf: 41.8, pf: 39.8, c: 37.4 },
    { teamAbbr: 'PHI', pg: 44.6, sg: 43.4, sf: 42.2, pf: 40.2, c: 37.8 },
    { teamAbbr: 'TOR', pg: 45.1, sg: 43.8, sf: 42.6, pf: 40.8, c: 38.4 },
    { teamAbbr: 'CHI', pg: 45.4, sg: 44.2, sf: 43.1, pf: 41.2, c: 38.8 },
    { teamAbbr: 'MEM', pg: 45.8, sg: 44.6, sf: 43.4, pf: 41.8, c: 39.4 },
    { teamAbbr: 'IND', pg: 46.2, sg: 45.1, sf: 43.8, pf: 42.2, c: 39.8 },
    { teamAbbr: 'SAS', pg: 46.6, sg: 45.4, sf: 44.2, pf: 42.6, c: 40.2 },
    { teamAbbr: 'PHX', pg: 47.1, sg: 45.8, sf: 44.6, pf: 43.1, c: 40.8 },
    { teamAbbr: 'SAC', pg: 47.4, sg: 46.2, sf: 45.1, pf: 43.4, c: 41.2 },
    { teamAbbr: 'BKN', pg: 47.8, sg: 46.6, sf: 45.4, pf: 43.8, c: 41.8 },
    { teamAbbr: 'ATL', pg: 48.2, sg: 47.1, sf: 45.8, pf: 44.2, c: 42.2 },
    { teamAbbr: 'DET', pg: 48.6, sg: 47.4, sf: 46.2, pf: 44.6, c: 42.6 },
    { teamAbbr: 'NOP', pg: 49.1, sg: 47.8, sf: 46.6, pf: 45.1, c: 43.1 },
    { teamAbbr: 'UTA', pg: 49.4, sg: 48.2, sf: 47.1, pf: 45.4, c: 43.4 },
    { teamAbbr: 'POR', pg: 49.8, sg: 48.6, sf: 47.4, pf: 45.8, c: 43.8 },
    { teamAbbr: 'WAS', pg: 50.2, sg: 49.1, sf: 47.8, pf: 46.2, c: 44.2 },
    { teamAbbr: 'CHA', pg: 50.8, sg: 49.4, sf: 48.2, pf: 46.6, c: 44.8 },
  ];

  return res.status(200).json({ dvp, lastUpdated: '2026-03-13' });
}
