#!/usr/bin/env bash
# Installs the two Kaze launchd jobs: writes each plist into ~/Library/LaunchAgents with this repo's real path,
# boots the old copy out, waits until it is gone, then bootstraps and enables the new one.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
agents="$HOME/Library/LaunchAgents"
domain="gui/$(id -u)"
mkdir -p "$agents"

wait_until_unloaded() {   # $1: label; gives launchd up to 10 s to finish the bootout
  for _ in $(seq 1 20); do
    launchctl print "$domain/$1" >/dev/null 2>&1 || return 0
    sleep 0.5
  done
  echo "warning: $1 still loaded after bootout" >&2
}

for label in com.kaze.read com.kaze.nightly; do
  target="$agents/$label.plist"
  sed "s#\$HOME/kaze#$repo#g" "$here/$label.plist" > "$target"
  launchctl bootout "$domain/$label" 2>/dev/null || true
  wait_until_unloaded "$label"
  launchctl bootstrap "$domain" "$target"
  launchctl enable "$domain/$label"
  echo "installed $label from $target"
done
