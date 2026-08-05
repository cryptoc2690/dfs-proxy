#!/bin/bash
# Double-click this to launch the WNBA DFS optimizer.
# It starts the local app and opens it in your browser. Close the Terminal
# window (or press Ctrl+C) to stop it.

cd "$(dirname "$0")/wnba" || { echo "Can't find the wnba folder next to this launcher."; read -r; exit 1; }

# Find a working Python 3 (3.8+). Nothing else needs installing.
PY=""
for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2]>=(3,8) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "  Python 3 isn't installed yet — it's a one-time, ~2 minute install."
  echo "  Opening the download page. Get 'macOS 64-bit universal2 installer',"
  echo "  double-click the downloaded .pkg, click through it, then double-click"
  echo "  this launcher again."
  echo ""
  osascript -e 'display dialog "Python 3 is needed (one-time install). I'"'"'ll open the download page — install the macOS .pkg, then double-click this launcher again." buttons {"Open download page"} default button 1' >/dev/null 2>&1
  open "https://www.python.org/downloads/"
  read -r -p "Press Return to close." _
  exit 1
fi

echo "  Launching the WNBA optimizer…  (this window can be minimized)"
exec "$PY" app.py
