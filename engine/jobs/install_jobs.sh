#!/usr/bin/env bash
# Links the two Kaze launchd jobs into ~/Library/LaunchAgents and loads them (reloading if already there).
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
agents="$HOME/Library/LaunchAgents"
mkdir -p "$agents"
for label in com.kaze.read com.kaze.nightly; do
  ln -sfn "$here/$label.plist" "$agents/$label.plist"
  launchctl unload "$agents/$label.plist" 2>/dev/null || true
  launchctl load "$agents/$label.plist"
  echo "loaded $label"
done
