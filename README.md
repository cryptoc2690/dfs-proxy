# dfs-proxy

Local DraftKings lineup builders. Pure Python, no installs, one folder per sport.

| Folder | Sport | Run |
|---|---|---|
| `nba/` | NBA classic + showdown (Stokastic) | `python3 nba/app.py` or `NBA-Optimizer.command` |
| `nfl/` | NFL showdown + main slate (Stokastic) | `python3 nfl/app.py` or `NFL-Optimizer.command` |
| `wnba/` | WNBA classic (LineStar) | `python3 wnba/app.py` or `WNBA-Optimizer.command` |

Each has its own README and smoke suite (`python3 <sport>/smoke.py` where present).
