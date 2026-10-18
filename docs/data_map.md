# Kaze data map

The one page to look Kaze's data up on. Everything Kaze keeps lives in one SQLite file, `kaze.sqlite`, in the folder
`KAZE_DATA` points to (set in `.env`, never in the repo), beside a small live feed for the phone and the jobs' logs.
Mirai's files under `KAZE_MIRAI_HOME` are the source of every market number; Kaze reads them and never writes there.

## The design and what Phase 1 builds of it

The target is the four-pillar store of 2026-10-16: fifteen tables defined by two registries, every value stored long as
measure x subject x value x blank reason x known_at. `engine/store.py` creates only the tables and columns Phase 1
writes (schema version 1, kept by hand with `NOT NULL` on keys and `CHECK` lists on word columns); each later phase adds
its tables through the next numbered schema step. The full design, for reference, is:

| Pillar | Table | What one row is | Phase 1 |
|---|---|---|---|
| Registry | `registry_measures` | one measure Kaze may store: name, plain meaning, unit, lane, the fact it carries, subjects, source feed, blank rule | built; seeded from `engine/registry.py` on every open (see `registry_measures` for the list) |
| Registry | `registry_components` | one versioned thing: a feed, the Schwab login, the collector, a read lane | built |
| Collect | `collect_snapshots` | one cut: a read at a moment (or a nightly check) with its slot, lane, origin, attempt, builder commit, source-file byte lengths, health counts, content hash | built |
| Collect | `collect_observations` | one measure x subject at one cut, with its feed's status and age at the cut | built |
| Collect | `collect_market_prices` | one price record; for now one SPX 1-minute bar with the moment it was known | built (bar columns only) |
| Collect | `collect_calendar_and_events` | one dated fact: a scheduled event or a half-day session, with when the station first held it | built (events and half days) |
| Train | `train_feature_set_columns`, `train_examples`, `train_splits` | the frozen recipe of a model row, the model-ready rows, the walk-forward day roles | later |
| Reads | `reads_decisions`, `reads_decision_values`, `reads_trade_legs` | every decision (range forecast, Claude read, combined call, trade candidate), its numbers, its legs | Phase 2 |
| Results | `results_outcomes`, `results_outcome_values`, `results_evaluations` | what happened after each read, how each decision graded, the trust weights earned | Phase 3 |

Two columns are Kaze's own additions to the design's `collect_snapshots`: `newest_input_market_at` (the newest input
any observation of the cut used; the store refuses it past `cut_at`) and `source_files_json_text` (the byte length of
each source file at a live read, so a rebuild of that cut reads only what the live read could have seen).

## Rules every row follows

- **Blank, never 0.** A measure with no value gets a `blank_reason` from the fixed list (`feed_missing`, `feed_stale`,
  `not_formed_yet`, `none_left_today`, `not_known_at_read`, ...). Counts over a window (tape trades and premium,
  headlines, gap markers) are values only while the feed's newest line is inside its stale limit; otherwise they are
  blank with the feed's reason. A table constraint enforces exactly one of the four value columns or a blank reason.
- **Registered or refused.** The writer refuses an unregistered measure, a subject kind the measure does not allow, a
  value in the wrong column, a number outside the measure's range, a word outside its list, and an input newer than the
  cut. Every row dict is checked against the table's columns, so a renamed column fails loudly.
- **Point in time.** `cut_at` is the market moment a row describes; `known_at` is when it became usable. Reads carry
  `known_at = written_at` (rule `written_at`) for every origin until a worst live lag has been measured. Bars seen in
  their own slot are known 62 seconds after the bar ends (`bar_end_plus_62_seconds`); bars that turn up later are
  stamped with the moment they were written (`written_at`); backfilled sessions with the file's change time
  (`file_modified_at`).
- **Origins.** `row_origin` is `live_frozen` (written during the session, first attempt), `live_rebuilt` (a second run
  at the same live cut; the first is never deleted) or `history_rebuilt` (built later from Mirai's files as they stood
  at the cut; re-running replaces it). The nightly compares `live_frozen` with `history_rebuilt`.
- **Calendar as known.** Every read first copies Mirai's calendar into `collect_calendar_and_events` (a fact's
  `known_at` is the calendar file's change time; an earlier copy keeps its first `known_at`), then reads the day's events
  from the store with `known_at <= cut_at`. An event added to the file after a cut is not seen by that cut, live or
  rebuilt.
- **Tape and scan files land late.** Mirai writes tape lines in arrival order and a scan row at the end of its tick, so
  a live read cannot see lines that arrive after the cut while a rebuild can. A live read records each source file's
  byte length; a rebuild of the same cut reads only that prefix of the tape and scan files. For cuts with no live
  twin (a hand `--at`, a day Kaze was not live) the rebuild sees the whole file, so the nightly counts tape and scan
  differences separately as tolerated rather than as mismatches.

## Reads and cuts

A read slot is a New York minute on the 5-minute grid, 09:35 to 15:55 (77 slots on a full day; 09:35 to 12:55 on a
13:00 half day). The slot's **cut** is the last second of its own minute: the 12:35 slot cuts at 12:35:59 and includes
the 12:35 bar, so it is the worked-out sheet's read (7,691.03, straddle 22.4, 204 minutes left). The live job fires at
12:35:00 and waits up to 100 seconds after 12:36:00 for the 12:35 bar; if the bar never lands the read still writes
with `spx_bars` status `late` and `snapshot_status_reason = cut_bar_missing`. A launch up to a minute after the slot
minute still serves the slot (`slot_status = late`). Slots at :05 and :35 are the 30-minute reads, lane `headline_30`;
the rest are lane `range_every_5_minutes`. A `--at` rebuild uses the exact second given and carries the slot only when
that second is a slot's own cut (HH:M5:59).

`read_id` is `<lane>:<cut in New York time>`; `snapshot_id` is `read|<cut in UTC>|<origin>|kaze_collector@1|b<attempt>`.
A scheduled slot no live read served gets a `snapshot_status = missed` row from the nightly.

## What a read stores

One observation per measure and subject; the registry (`registry_measures`, built from `engine/registry.py`) is the
list. By group:

| Group | Measures |
|---|---|
| clock (no feed) | `minutes_since_session_open`, `minutes_until_session_close` |
| price | `spx_price_points`, `prior_close_points`, `today_open_points`, `session_high_points`, `session_low_points`, `vwap_points`, `range_so_far_points`, `move_points` (since = prior_close, open, 5/15/30/60_minutes) |
| options | `same_day_straddle_mid_points`, `opening_straddle_mid_points` (scan ruler / 0.85), `atm_option_mid_points` (side, strike_points; from the tape), `same_day_atm_iv_calendar_year_pct`, `level_price_points` and `level_gamma_share_portion` (level_type = call_wall, put_wall), `dealer_net_gamma_usd`, `dealer_gamma_sign` |
| VIX family | `vix_calendar_year_pct`, `vix_prior_close_calendar_year_pct` (symbol = $VIX, $VIX1D, $VIX9D, $VIX3M, $VVIX) |
| breadth | `nyse_advancing_count`, `nyse_declining_count`, `nyse_advancing_portion` |
| leaders, rates, dollar, oil | `fund_change_from_prior_close_pct` (symbol = SPY, RSP, QQQ, IWM, SMH, UUP, USO), `sector_funds_up_count` (of 11), `largest_stocks_up_count` (of 9), `ten_year_yield_annual_pct`, `ten_year_yield_change_basis_points` |
| overnight | `overnight_high_points`, `overnight_low_points` (symbol = /ES, 18:00 to 09:30) |
| events | `events_left_today_count`, `minutes_until_next_event` (impact = any), `next_event_kind`, `does_calendar_cover_day` |
| options tape (30-minute window) | `options_trades_last_30_minutes_count`, `options_call_premium_last_30_minutes_usd`, `options_put_premium_last_30_minutes_usd`, `bullish_portion_of_sided_premium`, `tape_gap_markers_last_30_minutes_count`, `feed_coverage_last_30_minutes_portion` |
| headlines (60-minute window) | `headlines_last_60_minutes_count` (kind = all), `feed_coverage_last_60_minutes_portion` (blank: the poller leaves no stamps) |
| Mirai's Claude forecast | `mirai_claude_forecast_pct` and `mirai_claude_base_rate_pct` (horizon = 30_minutes, to_close; side = up, flat, down), `mirai_claude_read_at`; carried at most 40 minutes |
| feed and login health | `feed_status`, `feed_age_seconds` (feed = every feed seen), `rows_received_today_count` (bars, quotes, breadth), `lines_skipped_today_count` (malformed lines per file), `login_state`, `login_hours_until_expiry`, `login_expires_at` (login = schwab; blank `not_known_at_read` on rebuilt reads), `are_needed_feeds_all_ok` (consumer = range_engine) |
| nightly check | `reads_compared_count`, `values_mismatched_count`, `tolerated_mismatches_count`, `rebuild_identical_portion` |

Registered now and written from Phase 2 on: today's speed (`today_typical_1_minute_move_points`, the 5- and 30-minute
half-life variants and their ratio), `typical_swing_points` and `options_implied_swing_points` per horizon, the up and
down legs and `trend_straightness_ratio` for 15/30/60 minutes, the history fits (`band_multiplier` with its
`stretch_recipe_name`, `fit_sessions_count`, `fit_windows_count`, `average_price_shrink_ratio`,
`never_leaves_rung_portion`), the band values of a range forecast, and the outcome values including the grade start
(`grade_start_price_points`, `grade_start_bar_at`, `grade_start_rule_name`). Rows with a blank
`available_since_trade_date` are these.

Every measure's lane and fact group are checked when the registry loads: one fact may reach the pull lane (how far)
or the push lane (which way), never both.

## Feeds (what Kaze reads from Mirai)

| Feed | Mirai file | Stale after | Notes |
|---|---|---|---|
| `spx_bars` | `data/market/spx_price_bars_by_minute/{day}.jsonl` | 180 s | a bar counts once its start is at or before the cut; age = cut minus the newest bar's start |
| `watchlist_quotes` | `data/market/watchlist_quotes_by_minute/{day}.jsonl` | 180 s | newest row with quotes at or before the cut |
| `nyse_breadth` | the quotes rows' `bars` field (`$ADVN`, `$DECN`) | 240 s | |
| `options_scan` | `data/spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl` | 300 s | native-chain scans only; the straddle needs a ruler with quality ok |
| `options_tape` | `data/market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl` | 180 s | arrival order, so windows are cut by trade time; gap markers are counted; a rebuild reads the live read's byte prefix |
| `futures_bars` | `data/market/futures_price_bars_by_minute/{day}.jsonl` | 1 day | /ES 1-minute bars only |
| `event_calendar` | `settings/market_event_calendar_by_event.json` | 100 days | copied into `collect_calendar_and_events` at every read; also supplies half-day closes; it lists no holidays, so those are in `engine/session_clock.py` (through 2027; the clock refuses later dates) |
| `claude_forecast` | `data/spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl` | 40 min | newest live final forecast written at or before the cut |
| `news_headlines` | `data/market/news_headlines_by_article/{day}.jsonl` | 30 min | |
| `schwab_login` | `data/operations/logs/auth_watch_output_log.txt` | 2 days | the auth watch's `left=N.NNd` line plus the log's modified time; `?` gives `login_state = unknown`; live reads only |

Gzipped copies (`.jsonl.gz`) of older days are read the same way.

## Files in `KAZE_DATA`

| Path | What it holds | Written by |
|---|---|---|
| `kaze.sqlite` (with `-wal` and `-shm` beside it) | the store | every job |
| `live/latest.json` | the newest live read for the phone: `read_id`, `lane`, `slot`, `origin`, `status`, `status_reason`, `cut_at`, `known_at`, `feeds` (status live / partial / blind and age per feed), `login`, `values` (`measure` or `measure\|subject` keys), `blanks` (key to reason) | the live read job, after each read |
| `logs/read_log.txt` | one line per read-job run (including the quiet "market closed" lines) and any crash's traceback | the read job |
| `logs/nightly_log.txt` | one health line per nightly run, one line per parity mismatch, and the hourly "not the nightly hour" lines | the nightly job |
| `logs/rebuild_log.txt` | one line per hand run (`--at`, `--rebuild-day`, `--backfill-bars`) | hand runs |
| `logs/older/<day>/*.gz` | the logs of earlier days, kept 14 days | `engine/job_log.py` when a job first writes on a new day |
| `/tmp/com.kaze.read.launchd.log`, `/tmp/com.kaze.nightly.launchd.log` | only what Python could not catch (a missing `.env`, an import error) | launchd |

## Who writes what, and when

| Job | When | Writes |
|---|---|---|
| `python3 -m engine.read_job` (launchd `com.kaze.read`) | every 5 minutes; builds only at the 09:35-15:55 slots on trading days, else one log line | one `live_frozen` snapshot (or `live_rebuilt` on a rerun), its observations, calendar facts, new bars, `live/latest.json` |
| `python3 -m engine.read_job --at "YYYY-MM-DD HH:MM:SS"` | by hand | one `history_rebuilt` snapshot at that New York moment and the bars up to it |
| `python3 -m engine.read_job --rebuild-day YYYY-MM-DD` | by hand | every slot of the session as `history_rebuilt` rows, plus the whole session's bars |
| `python3 -m engine.read_job --backfill-bars` | by hand, once | every session's SPX bars Mirai holds, as `history_rebuilt` rows known at the file's change time |
| `python3 -m engine.read_job --nightly` (launchd `com.kaze.nightly` runs `--nightly-if-due` hourly at :15 and acts only in the 17:00 New York hour) | 17:15 New York | `missed` rows for unserved slots, the session's closing bars (when Kaze was live), the session rebuilt, one `nightly_check` snapshot per session with both live and rebuilt reads, and the health line |

`engine/jobs/install_jobs.sh` writes the two plists into `~/Library/LaunchAgents` with this repo's real path and
boots them (bootout, wait, bootstrap, enable).
