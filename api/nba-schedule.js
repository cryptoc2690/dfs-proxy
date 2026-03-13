export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  try {
    const response = await fetch(
      'https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json'
    );

    if (!response.ok) {
      return res.status(response.status).json({ error: 'Schedule fetch failed' });
    }

    const data = await response.json();
    const gameDates = data.leagueSchedule?.gameDates || [];

    // Build a map of team -> dates played
    const teamDates = {};
    for (const dateObj of gameDates) {
      const date = dateObj.gameDate; // format: "03/13/2026 00:00:00"
      for (const game of dateObj.games || []) {
        const home = game.homeTeam?.teamTricode;
        const away = game.awayTeam?.teamTricode;
        if (home) {
          if (!teamDates[home]) teamDates[home] = [];
          teamDates[home].push(date.split(' ')[0]);
        }
        if (away) {
          if (!teamDates[away]) teamDates[away] = [];
          teamDates[away].push(date.split(' ')[0]);
        }
      }
    }

    // Find today and yesterday
    const today = new Date();
    const todayStr = `${String(today.getMonth()+1).padStart(2,'0')}/${String(today.getDate()).padStart(2,'0')}/${today.getFullYear()}`;
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayStr = `${String(yesterday.getMonth()+1).padStart(2,'0')}/${String(yesterday.getDate()).padStart(2,'0')}/${yesterday.getFullYear()}`;

    // Flag teams playing today that also played yesterday
    const b2bTeams = {};
    for (const [team, dates] of Object.entries(teamDates)) {
      const playsToday = dates.includes(todayStr);
      const playedYesterday = dates.includes(yesterdayStr);
      if (playsToday) {
        b2bTeams[team] = { isB2B: playedYesterday };
      }
    }

    return res.status(200).json({ b2bTeams, todayStr, yesterdayStr });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
```

Check it at:
```
https://dfs-proxy.vercel.app/api/nba-schedule
