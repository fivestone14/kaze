#!/usr/bin/env bash
# launchd's one way into Kaze: find the repo from this script's own place, refuse loudly without .env, run the job.
# The job writes its own log under KAZE_DATA/logs; only what Python cannot catch reaches launchd's capture file.
set -u
repo="$(cd "$(dirname "$0")/../.." && pwd)"
job="${1:?usage: run_job.sh read|nightly}"
cd "$repo" || exit 1
if [[ ! -f .env ]]; then
  echo "kaze $job: $repo/.env is missing; copy .env.example and fill in the two paths" >&2
  exit 1
fi
case "$job" in
  read) exec /usr/bin/python3 -m engine.read_job ;;
  nightly) exec /usr/bin/python3 -m engine.read_job --nightly-if-due ;;
  *) echo "kaze: unknown job $job" >&2; exit 2 ;;
esac
