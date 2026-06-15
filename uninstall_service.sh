#!/usr/bin/env bash
set -euo pipefail
LABEL="com.garnik.visawatcher"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "Service '$LABEL' stopped and removed."
