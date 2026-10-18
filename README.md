# Kaze

A range-first SPX forecast and the Android app that shows it.

Every 5 minutes Kaze reads the market (the :05 and :35 reads are the 30-minute
calls), measures how fast the index is moving right now and turns that into the
range it should end inside: today's tape sets the number, history only shapes it,
and direction stays 50/50 until a reading earns a say.

## Layout

| Folder | What it holds |
|---|---|
| `engine/` | The store, the registry, the collector that fills it from Mirai's files, the live feed and the read job |
| `engine/jobs/` | The two launchd jobs, their runner and installer |
| `docs/` | The data map |
| `checks/` | Git hooks that block secrets from ever reaching this repo |

The range engine, grading, dials, the Android app and the print pack arrive with their own phases.

## Setup

    cp .env.example .env            # then fill in the two paths
    git config core.hooksPath checks
    python3 -m pip install numpy    # the one package beyond the standard library (Python 3.9)

Private data lives outside this repo, in the folder `KAZE_DATA` points to.

## Run

    python3 -m engine.read_job                                  # one live read (quiet outside market hours)
    python3 -m engine.read_job --at "2026-10-16 12:35:59"       # rebuild one past moment (New York time)
    python3 -m engine.read_job --rebuild-day 2026-10-16         # rebuild every 5-minute read of a session
    python3 -m engine.read_job --backfill-bars                  # load every session's SPX bars Mirai holds (one-off)
    python3 -m engine.read_job --nightly                        # missed slots, rebuild, parity check, health line
    engine/jobs/install_jobs.sh                                 # install the two launchd jobs

`docs/data_map.md` says what the store holds and who writes it.
