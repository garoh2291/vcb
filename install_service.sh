#!/usr/bin/env bash
# Set up venv + deps and install the launchd background service.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.garnik.visawatcher"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

cd "$DIR"

echo "==> Creating virtualenv (.venv)"
/opt/homebrew/bin/python3 -m venv .venv
PY="$DIR/.venv/bin/python"
"$PY" -m pip install --upgrade pip >/dev/null
echo "==> Installing Python deps"
"$PY" -m pip install -r requirements.txt

echo "==> Installing patchright's Chromium (fallback if system Chrome channel unused)"
"$PY" -m patchright install chromium || true

if [ ! -f "$DIR/.env" ]; then
  echo "!! .env not found. Copy .env.example to .env and fill it before starting."
fi

echo "==> Writing launchd plist to $PLIST_DST"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__PYTHON__#$PY#g" -e "s#__DIR__#$DIR#g" \
  "$DIR/com.garnik.visawatcher.plist" > "$PLIST_DST"

echo "==> Loading service"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo
echo "Done. Service '$LABEL' is loaded and will auto-start on login."
echo "Logs:    tail -f \"$DIR/bot.log\""
echo "Status:  launchctl list | grep visawatcher"
echo "Stop:    ./uninstall_service.sh"
echo
echo "NOTE: if the saved session is ever fully lost, prime it once with:"
echo "      .venv/bin/python capture.py   (stop the service first: ./uninstall_service.sh)"
echo "      Normally not needed — the bot auto-logs in when the session expires."
