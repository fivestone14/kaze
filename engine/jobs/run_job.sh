#!/usr/bin/env bash
# launchd's one way into Kaze: go to the repo, find the data folder from .env, run the job and keep its output there.
# launchd expands nothing in its log paths, so the job's own output file is chosen here, under $KAZE_DATA/logs.
set -u
cd "$HOME/kaze" || exit 1
job="${1:?usage: run_job.sh read|nightly}"
data_dir="$(grep '^KAZE_DATA=' .env | cut -d= -f2-)"
data_dir="${data_dir/#\~/$HOME}"
mkdir -p "$data_dir/logs"
case "$job" in
  read) flags="" ;;
  nightly) flags="--nightly" ;;
  *) echo "unknown job $job" >&2; exit 2 ;;
esac
exec /usr/bin/python3 -m engine.read_job $flags >> "$data_dir/logs/launchd_${job}_log.txt" 2>&1
