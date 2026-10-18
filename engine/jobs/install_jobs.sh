#!/usr/bin/env bash
# Installs the three Kaze launchd jobs (the read, the nightly, the phone server): checks the box can run them first
# (.env present, numpy importable by /usr/bin/python3, the engine importing), then writes each plist into
# ~/Library/LaunchAgents with this repo's real path, boots the old copy out, waits until it is gone, bootstraps and
# enables the new one, and prints how to look at each job.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
agents="$HOME/Library/LaunchAgents"
domain="gui/$(id -u)"

if [[ ! -f "$repo/.env" ]]; then
  echo "install_jobs: $repo/.env is missing; copy .env.example and fill it in first" >&2
  exit 1
fi
preflight="import numpy; from engine import paths; from engine.store import registry"
if ! (cd "$repo" && /usr/bin/python3 -c "$preflight"); then
  echo "install_jobs: /usr/bin/python3 cannot import numpy or the engine (see the lines above); nothing installed" >&2
  exit 1
fi
mkdir -p "$agents"

wait_until_unloaded() {   # $1: label; gives launchd up to 10 s to finish the bootout
  for _ in $(seq 1 20); do
    launchctl print "$domain/$1" >/dev/null 2>&1 || return 0
    sleep 0.5
  done
  echo "warning: $1 still loaded after bootout" >&2
}

for label in com.kaze.read com.kaze.nightly com.kaze.app; do
  target="$agents/$label.plist"
  sed "s#\$HOME/kaze#$repo#g" "$here/$label.plist" > "$target"
  launchctl bootout "$domain/$label" 2>/dev/null || true
  wait_until_unloaded "$label"
  launchctl bootstrap "$domain" "$target"
  launchctl enable "$domain/$label"
  echo "installed $label from $target"
done
echo "to look at a job:   launchctl print $domain/com.kaze.read | head -3   (same for .nightly and .app)"
echo "the jobs' own logs: \$KAZE_DATA/logs/read_log.txt, nightly_log.txt, app_log.txt"
echo "what Python could not catch: /tmp/com.kaze.read.launchd.log, .nightly., .app."
