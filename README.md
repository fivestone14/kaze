# Kaze

A range-first SPX forecast and the Android app that shows it.

Every 5 minutes Kaze reads the market (the :05 and :35 reads are the 30-minute
calls), measures how fast the index is moving right now and turns that into the
range it should end inside: today's tape sets the number, history only shapes it,
and direction stays 50/50 until a reading earns a say.

## Layout

| Folder | What it holds |
|---|---|
| `engine/` | The store, the registry, the collector (Mirai's files in), the range engine (`range_fit.py` fits the history, `range_read.py` works one read out, `width_dials.py` holds the dials), the push readings (`push_read.py`: which way, each with a lean), the grader (`grade.py`: outcomes, grades, the scoreboard), the trust ledger (`trust.py`: what has earned weight, from live graded windows only), the live feed and the read job |
| `engine/jobs/` | The two launchd jobs, their runner and installer |
| `docs/` | The data map |
| `checks/` | Git hooks that block secrets from ever reaching this repo |

Every window is graded from the read (the band sits on the read price; did the end land inside, did the running
average, did price ever leave +/-X) and the scoreboard keeps live and replayed grades apart. Seven push readings
(price trend, straightness, breadth, big tech vs the rest, options buyers, the 10-year yield, Mirai's Claude lean) and
five width dials (Fed window, scheduled release, closing window, acceleration, options vs tape) are on the board at
weight 0; the nightly trust ledger scores each per channel on live graded windows with a test fixed in advance, and a
weight moves from 0 only after 20 live sessions, the whole interval on the right side, the score above a fake
baseline, three nightly runs in a row. The Android app and the print pack arrive with their own phases.

## Setup

    cp .env.example .env            # then fill in the two paths (and, best, KAZE_BACKUP_DIR on another disk)
    git config core.hooksPath checks
    python3 -m pip install numpy    # the one package beyond the standard library (Python 3.9)

Private data lives outside this repo, in the folder `KAZE_DATA` points to.

## Run

    python3 -m engine.read_job                                  # one live read with its range bands (quiet when closed)
    python3 -m engine.read_job --at "2026-10-16 12:35:59"       # rebuild one past moment (New York time)
    python3 -m engine.read_job --rebuild-day 2026-10-16         # rebuild every 5-minute read of a session
    python3 -m engine.read_job --backfill-bars                  # load every session's SPX bars Mirai holds (one-off)
    python3 -m engine.read_job --nightly                        # catch-up rebuilds, fits, grades, ledger, parity, backup
    python3 -m engine.read_job --scoreboard [2026-10-16]        # grades per session and channel, last 20 pooled
    python3 -m engine.read_job --pass-test                      # print the band's walk-forward coverage test
    python3 -m engine.read_job --trust                          # the trust ledger: readings and dials, verdict, weight
    engine/jobs/install_jobs.sh                                 # install the two launchd jobs

On a fresh store run `--backfill-bars`, then `--nightly` (it writes the history fits every read's band needs), then
`--rebuild-day` for each past session you want graded. `live/latest.json` carries `status: failed` with the reason
when a live read crashes. The nightly keeps the last five dated, gzipped copies of the store in `KAZE_BACKUP_DIR`.

`docs/data_map.md` says what the store holds and who writes it.
