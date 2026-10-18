# Kaze data map

The one page to look Kaze's data up on. Everything Kaze keeps lives in one SQLite file, `kaze.sqlite`, in the folder
`KAZE_DATA` points to (set in `.env`, never in the repo), beside a small live feed for the phone and the jobs' logs.
Mirai's files under `KAZE_MIRAI_HOME` are the source of every market number; Kaze reads them and never writes there.
Both folders must exist: the engine refuses to start (exit 1, one clear line) when `.env` is missing, a variable is
blank, or either path is not a folder.

## The design and what is built of it

The target is the four-pillar store of 2026-10-16: fifteen tables defined by two registries, every value stored long as
measure x subject x value x blank reason x known_at. `engine/store.py` creates only the tables and columns the phases
so far write (schema version 2, kept by hand with `NOT NULL` on keys and `CHECK` lists on word columns); each phase adds
its tables through the next numbered schema step, applied in one transaction when a store is opened. The full design,
for reference, is:

| Pillar | Table | What one row is | Built |
|---|---|---|---|
| Registry | `registry_measures` | one measure Kaze may store: name, plain meaning, unit, lane, the fact it carries, subjects, source feed, blank rule | Phase 1; seeded from `engine/registry.py` on every open (a name dropped from the code is dropped from the table) |
| Registry | `registry_components` | one versioned thing: a feed, the Schwab login, the collector, the range engine, a read lane | Phase 1 |
| Collect | `collect_snapshots` | one cut: a read at a moment, a nightly check, or a nightly history fit, with its slot, lane, origin, attempt, builder commit, source-file byte lengths, health counts, content hash | Phase 1; step 2 rebuilt it to allow the `nightly_history_fit` kind |
| Collect | `collect_observations` | one measure x subject at one cut, with its feed's status and age at the cut | Phase 1 |
| Collect | `collect_market_prices` | one price record; for now one SPX 1-minute bar with the moment it was known | Phase 1 (bar columns only) |
| Collect | `collect_calendar_and_events` | one dated fact: a scheduled event or a half-day session, with when the station first held it | Phase 1 (events and half days) |
| Train | `train_feature_set_columns`, `train_examples`, `train_splits` | the frozen recipe of a model row, the model-ready rows, the walk-forward day roles | later |
| Reads | `reads_decisions`, `reads_decision_values` | one decision header (for now a `range_forecast` per channel) and every number, word or id inside it | Phase 2 (the columns the range engine fills) |
| Reads | `reads_trade_legs` | the legs of a trade-shaped decision | later |
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
  at the cut; re-running replaces it, decisions included). The nightly compares `live_frozen` with `history_rebuilt`.
  A decision carries its snapshot's origin and `run_kind` `live` or `history_replay`.
- **Calendar as known.** Every read first copies Mirai's calendar into `collect_calendar_and_events`, then reads the
  day's events from the store with `known_at <= cut_at`, one row per fact (its newest version known at the cut, so a
  new `built` stamp on the file never doubles a count). The very first copy stamps its facts with the calendar's own
  `built` date (the file says they existed then); every later copy stamps new facts with the file's change time, and
  an earlier copy of a fact keeps its first `known_at`. An event added after a cut is not seen by that cut, live or
  rebuilt. A rebuild of a day before the calendar's `built` date finds no calendar and leaves the event measures
  blank `not_known_at_read`.
- **Tape and scan files land late.** Mirai writes tape lines in arrival order and a scan row at the end of its tick, so
  a live read cannot see lines that arrive after the cut while a rebuild can. A live read measures each source file's
  byte length first, parses only that much itself, and records the lengths; a rebuild of the same cut reads the same
  prefixes. For cuts with no live twin (a hand `--at`, a day Kaze was not live) the rebuild sees the whole file, so the
  nightly counts tape and scan differences separately as tolerated rather than as mismatches.

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
A scheduled slot no live read served gets a `snapshot_status = missed` row from the nightly, but only when the nightly
runs on that session's own day; a hand `--nightly` on a later day never marks a past session Kaze never ran.

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
| range readings (Phase 2, from the store's bars) | `today_typical_1_minute_move_points` (speed: recency-weighted mean absolute 1-minute close change, half-life 15 minutes), `speed_fast_5_minute_half_life_points`, `speed_slow_30_minute_half_life_points`, `speed_fast_over_slow_ratio` (acceleration, a weight-0 reading), `typical_swing_points` (horizon = 5_minutes, 30_minutes, 60_minutes; speed x the square root of the minutes), `options_implied_swing_points` and `options_over_tape_swing_ratio` (horizon; the straddle left x the square root of horizon over minutes left, a check never added in), `up_legs_points`, `down_legs_points`, `trend_straightness_ratio` (window_minutes = 15, 30, 60; weight-0 push readings) |
| nightly check | `reads_compared_count`, `values_mismatched_count`, `tolerated_mismatches_count`, `rebuild_identical_portion` |
| nightly history fit (Phase 2) | `band_multiplier` and `stretch_recipe_name` (band = 80, average_80; horizon), `fit_sessions_count`, `fit_windows_count`, `average_price_shrink_ratio` (horizon) |

Registered now and written from Phase 3 on: the outcome values including the grade start (`grade_start_price_points`,
`grade_start_bar_at`, `grade_start_rule_name`). Rows with a blank `available_since_trade_date` are these.

Every measure's lane and fact group are checked when the registry loads: one fact may reach the pull lane (how far)
or the push lane (which way), never both.

## The range forecast (Phase 2)

The recipe is the verified worked-out sheet of 2026-10-16 12:35:59; its fixed numbers live in `engine/registry.py`
(the `range_engine` component row carries them) and the 12:35 read reproduces the sheet to six decimals.

1. **Speed** (today) = the recency-weighted mean of |1-minute close change| over the session's bars up to the cut,
   weight 0.5^(minutes ago / 15). Fast (half-life 5) and slow (half-life 30) speeds and their ratio are stored as
   weight-0 readings. Bars come from `collect_market_prices` (today's and history's; live rows preferred over rebuilt
   ones for the same minute), never from Mirai's files.
2. **Swing** = speed x the square root of the horizon's minutes, for 5, 30 and 60.
3. **Stretch** (history) = the 80% mark of |end move| / swing over every window of the prior 60 sessions, measured on
   the same recipe (speed at the window's start from that day's bars, windows every 5 minutes from minute 30, sessions
   with fewer than 380 bars skipped). The average band's multiplier is the same fit on |average price over the window
   - start| / swing. Both are labelled with the recipe (`stretch_recipe_name`) and the sample counts.
4. **End band** = read price (the last close at the cut) +/- swing x stretch; **average band** the same with the average
   multiplier. The grade-start rule is `at_read`: the window runs from the end of the read's minute (12:36:00 for the
   12:35 read) for the horizon, capped at the close; a capped window's decision is `clipped`.
5. **Never-leaves odds** = the share of the same history's windows whose farthest excursion (highs and lows) stayed
   inside +/- X in that window's swings, at today's swing; X = 5 / 10 / 15 points for 5 / 30 / 60 minutes. Computed from
   the history windows at read time (the same function the fit uses), so the decimals match the sheet.
6. **Options check** = straddle left x the square root of (window minutes / minutes left), stored beside the tape swing
   with their ratio; shown, never added in.
7. **Dials** all x1.00 (`width_multiplier`, roles `raw` and `applied`): `acceleration` carries the fast/slow ratio as
   its raw value; `fed_window` carries 2.6 as its raw value when the window ends after an `FOMC` decision known at the
   cut (the Fed window runs from the decision to the close). On such a read a second decision tagged
   `variant_name = owner_rule` (parent = the base decision) applies the 2.6 to both bands, so Phase 3 grades the band
   with and without it. No FOMC day in the store is known in time (the calendar was built 2026-09-27), so rebuilt
   reads before that date never carry the variant.
8. **Direction** = `chance_up_if_moves_probability` 0.5 with `trust_weight_applied_portion` 0: no input counts yet.

**Where it lands.** The readings (1, 2, 6 and the legs) are observations on the read's own snapshot. Each read then
writes one `reads_decisions` row per channel (`decision_kind = range_forecast`, producer `range_engine@1`, `run_kind`
`live` or `history_replay`, `horizon_name`, `graded_price_measure = end_price`, `grade_start_rule_version = at_read`,
the grade window, `decision_status` ok / clipped / blind with its reason) and its `reads_decision_values`:
`band_centre_points`, the speed and swing used, `band_multiplier`, `stretch_recipe_name`, `fit_sessions_count`,
`fit_windows_count` and `fit_snapshot_id` (which fit it used), `band_half_width_points`, `band_low_points` and
`band_high_points` (band = 80 and average_80), `chance_stays_inside_rung_probability` (horizon, rung_points), the two
dials and the direction. A read with fewer than two bars is `blind` (`speed_not_formed`, values blank
`not_formed_yet`); one with no fit known at its cut is `blind` (`no_fit_known_at_cut`, values blank `too_few_sessions`).
Decision ids follow the design: `range_forecast|<snapshot_id>|range_engine@1|<run_kind>:<variant or live>|<horizon>|end_price|-|whole|r0`.

**Fits and walk-forward.** The nightly writes one `nightly_history_fit` snapshot per session it serves (builder
`range_engine@1`): `trade_date` is the session served, `cut_at` the close of the last session in its sample, and
`known_at` that close plus 62 seconds (`bar_end_plus_62_seconds`) for a rebuilt fit or the write time for the live one
(always before the served open). The first nightly on a store writes a rebuilt fit for every stored session that lacks
one, then every nightly writes the live fit for the next trading day. A read uses the newest fit with
`trade_date <= its day` and `known_at <= its cut`, so a fit never includes the read's own day; `--rebuild-day` and `--at`
use the same rule, which is why `--nightly` must run once before rebuilding a fresh store.

**Pass test** (`--pass-test`). For every session with a full 60-session lookback behind it, the band is refitted on
those sessions and every 5-minute read of the day (09:35 on, windows capped at the close) is checked: did the window's
end (and its average) land inside that read's band? The day coverages are resampled 1,000 times (fixed seed); PASS when
the 95% interval of the mean contains 0.80 and sits inside 0.75-0.85. The same is printed with a 20-session lookback
for information only. On 2026-10-18 (14 test days, 2026-09-29 to 2026-10-16) every channel passed: end 5/30/60 means
0.801 / 0.799 / 0.809, average 0.795 / 0.813 / 0.807.

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
| `live/latest.json` | the newest live read for the phone: `read_id`, `lane`, `slot`, `origin`, `status`, `status_reason`, `cut_at`, `known_at`, `feeds` (status live / partial / blind and age per feed), `login`, `range` (below), `values` (`measure` or `measure\|subject` keys, the range readings included), `blanks` (key to reason) | the live read job, after each read |

The `range` block: `price` (read price), `read_at`, `window_from` (end of the read's minute), `mins_in`, `mins_left`,
`speed`, `fast`, `slow`, `accel`, `straddle`, `fit` (`id`, `known_at`; null when none), `fed`, `fed_at`, `up_pct` (50),
`trust` (0) and `channels` keyed `5_minutes` / `30_minutes` / `60_minutes`, each with `mins`, `swing`, `stretch`, `half`,
`lo`, `hi`, `avg_stretch`, `avg_half`, `avg_lo`, `avg_hi`, `rung`, `stay_odds` (never-leaves odds for +/- rung),
`opt_swing`, `opt_ratio`, `capped`, `fed_window`, `dials` (`fed_window`, `acceleration`: `raw`, `applied`),
`owner_rule` (the x2.6 band's `half`, `lo`, `hi`, `avg_half`, `avg_lo`, `avg_hi`; null outside a Fed window), `status`
and `ends_at`. Numbers carry six decimals; New York times.
| `logs/read_log.txt` | one line per read-job run (including the quiet "market closed" lines) and any crash's traceback | the read job |
| `logs/nightly_log.txt` | one health line per nightly run, one line per parity mismatch, and the hourly "not the nightly hour" lines | the nightly job |
| `logs/rebuild_log.txt` | one line per hand run (`--at`, `--rebuild-day`, `--backfill-bars`) | hand runs |
| `logs/older/<day>/*.gz` | the logs of earlier days, kept 14 days | `engine/job_log.py` when a job first writes on a new day |
| `/tmp/com.kaze.read.launchd.log`, `/tmp/com.kaze.nightly.launchd.log` | only what Python could not catch (a missing `.env`, an import error) | launchd |

## Who writes what, and when

| Job | When | Writes |
|---|---|---|
| `python3 -m engine.read_job` (launchd `com.kaze.read`) | every 5 minutes; builds only at the 09:35-15:55 slots on trading days, else one log line | one `live_frozen` snapshot (or `live_rebuilt` on a rerun) with its observations and range readings, calendar facts, new bars, three `range_forecast` decisions (plus owner-rule variants in a Fed window) with their values, `live/latest.json` |
| `python3 -m engine.read_job --at "YYYY-MM-DD HH:MM:SS"` | by hand, trading days only | one `history_rebuilt` snapshot at that New York moment, the bars up to it, and its range decisions |
| `python3 -m engine.read_job --rebuild-day YYYY-MM-DD` | by hand | every slot of the session as `history_rebuilt` rows with their range decisions, plus the whole session's bars |
| `python3 -m engine.read_job --backfill-bars` | by hand, once | every session's SPX bars Mirai holds, as `history_rebuilt` rows known at the file's change time |
| `python3 -m engine.read_job --nightly` (launchd `com.kaze.nightly` runs `--nightly-if-due` hourly at :15 and acts only in the 17:00 New York hour) | 17:15 New York | `missed` rows for unserved slots, the session's closing bars (when Kaze was live), a rebuilt `nightly_history_fit` for every stored session without one and the live fit for the next trading day, the session rebuilt, one `nightly_check` snapshot per session with both live and rebuilt reads, and the health line |
| `python3 -m engine.read_job --pass-test` | by hand | nothing; prints the band's walk-forward coverage per day and channel with the verdict |

`engine/jobs/install_jobs.sh` writes the two plists into `~/Library/LaunchAgents` with this repo's real path and
boots them (bootout, wait, bootstrap, enable).
