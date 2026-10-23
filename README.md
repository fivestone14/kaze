# Kaze

A range-first SPX forecast and the Android app that shows it.

Every 5 minutes Kaze reads the market (the :05 and :35 reads are the 30-minute
calls), measures how fast the index is moving right now and turns that into the
range it should end inside: today's tape sets the number, history only shapes it,
and which way it goes is the tug-of-war of push/pull factors: each one, read by read, pushes price up or pulls it
down, and the direction call is their average.

## Layout

- `engine/`: `paths.py` (every path Kaze touches, from `.env`) and `session_clock.py` (the market clock), shared
  by every group below.
- `engine/collect/`: the collector. `mirai_files.py` reads each of Mirai's files as it stood at a moment;
  `collect.py` builds one read from them.
- `engine/store/`: the SQLite store. `store.py` shapes and writes rows, `store_queries.py` reads them,
  `store_schema.py` holds the tables and the numbered schema steps, `registry.py` lists every measure and word,
  `components.py` holds the recipe's numbers (dials, factors, trust tests, rules), `backup.py` the nightly copy.
- `engine/range/`: the range engine, how far price travels. `range_fit.py` fits the history, `range_read.py` works
  one read out, `width_dials.py` holds the dials (the Band Wideners).
- `engine/push_pull/`: which way. `push_pull_read.py` holds the push/pull factors (each a lean: above even it pushes
  price up, below it pulls price down) and the direction call.
- `engine/grade/`: the grader (`grade.py`: outcomes, grades, the scoreboard), the parity check (`parity.py`) and the
  trust ledger (`trust.py`: what has earned weight, from live graded windows only).
- `engine/feed/`: the live feed the phone reads after every read (`live_feed.py`).
- `engine/jobs/`: the read job (`read_job.py`: the 5-minute read, the nightly and the hand commands), the job log,
  the three launchd jobs (the read, the nightly, the phone server), their runner and installer.
- `app/server/`: the phone's web server: the screens, the live feed and today's closes behind one token, on
  127.0.0.1.
- `app/web/`: the three screens (Read, Sight, Steps): one page, one stylesheet, one script, the font.
- `app/android/`: the Android shell: a full-screen browser window that opens the server with the token.
- `docs/`: `data_map.md` (what Kaze keeps, how a read is worked out, the phone's contract, operations) and
  `evidence.md` (dated measurements).
- `checks/`: git hooks that block secrets from ever reaching this repo.

Every window is graded from the read: did the end land inside the band, did the running average, did price ever
leave plus or minus X. Seven push/pull factors count at an equal share in the direction call; five width dials sit
on the board at weight 0, and the nightly trust ledger moves a dial's weight only after 20 live sessions pass a test
fixed in advance. `docs/data_map.md` has the recipe,
the grades, the ledger's rules and the phone's data contract; `app/README.md` says what runs where for the phone.

## Setup

    cp .env.example .env                              # then fill in all five keys (see below)
    brew install gitleaks                             # the commit and push hooks call it
    git config core.hooksPath checks
    /usr/bin/python3 -m pip install --user numpy      # the one package beyond the standard library (Python 3.9)

`.env` holds five keys: `KAZE_MIRAI_HOME` and `KAZE_DATA` (folders that must exist), `KAZE_BACKUP_DIR` (optional,
best on another disk or a cloud folder), `KAZE_APP_PORT` and `KAZE_APP_TOKEN` (the phone server refuses to start
without the token, and launchd restarts it every minute until it has one, so set it even before the phone is
built). The jobs run `/usr/bin/python3`, so numpy goes there, not into another `python3` on the PATH. Run every
`python3 -m ...` command below from the repo root. Private data lives outside this repo, in the folder `KAZE_DATA`
points to.

## Run

    python3 -m engine.jobs.read_job                              # one live read (quiet when closed)
    python3 -m engine.jobs.read_job --at "2026-10-16 12:35:59"   # rebuild one past moment (New York time)
    python3 -m engine.jobs.read_job --rebuild-day 2026-10-16     # rebuild every 5-minute read of a session
    python3 -m engine.jobs.read_job --backfill-bars              # load every session's SPX bars Mirai holds
    python3 -m engine.jobs.read_job --nightly                    # rebuilds, fits, grades, ledger, parity, backup
    python3 -m engine.jobs.read_job --scoreboard [2026-10-16]    # grades per session and channel, last 20 pooled
    python3 -m engine.jobs.read_job --pass-test                  # the band's walk-forward coverage test
    python3 -m engine.jobs.read_job --trust                      # the trust ledger: verdicts and weights
    python3 -m app.server.app_server                             # the phone's server by hand (launchd: com.kaze.app)
    engine/jobs/install_jobs.sh                                  # install the three launchd jobs

On a fresh store run `--backfill-bars`, then `--nightly` (it writes the history fits every read's band needs), then
`--rebuild-day` for each past session you want graded.

## Operations

`engine/jobs/install_jobs.sh` installs the three launchd jobs after checking the box can run them. Each job writes
one line per run to `$KAZE_DATA/logs/` (`read_log.txt`, `nightly_log.txt`, `app_log.txt`, `by_hand_log.txt`); only
what Python could not catch lands in `/tmp/com.kaze.*.launchd.log`. The phone says in words when something failed
(`Read Failed`, `Late`, `No Read Yet Today`, `Can't Reach The Mac`). The nightly keeps the last five dated, gzipped
copies of the store in `KAZE_BACKUP_DIR`; the restore recipe, the Monday checklist and the yearly maintenance are
in `docs/data_map.md` under Operations.
