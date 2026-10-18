# Kaze data map

The one page to look Kaze's data up on. Everything Kaze keeps lives in one SQLite file, `kaze.sqlite`, in the folder
`KAZE_DATA` points to (set in `.env`, never in the repo), beside a small live feed for the phone and the jobs' logs.
Mirai's files under `KAZE_MIRAI_HOME` are the source of every market number; Kaze reads them and never writes there.

## The store in one picture

Fifteen tables on four pillars, defined by two registries. Every value Kaze stores has the same shape: **what was
measured** (a registered measure), **about what** (a subject key such as `symbol=$VIX` or `level_type=call_wall`),
**the value** (one of four typed columns), **why it is blank** when it is blank, and **when it became known**.

| Pillar | Table | What one row is | Filled in Phase 1? |
|---|---|---|---|
| Registry | `registry_measures` | one measure Kaze may store: name, plain meaning, unit, lane (how far / which way / health ...), the fact it carries, its subjects, source feed and blank rule | yes, 87 rows, seeded from `engine/measures.py` on every open |
| Registry | `registry_components` | one versioned thing: a feed, the Schwab login, the collector, a read lane | yes, 14 rows |
| Collect | `collect_snapshots` | one cut: a read at a moment (or a nightly check), with its slot, lane, origin (`live` or `rebuilt`), builder commit, health counts and content hash | yes |
| Collect | `collect_observations` | one measure x subject at one cut, with its feed's status and age at the cut | yes, 99 per read |
| Collect | `collect_market_prices` | one price record: for now, one SPX 1-minute bar with the moment it was known | yes |
| Collect | `collect_calendar_and_events` | one dated fact: session, scheduled event, halt, outage, headline | empty; events are read straight from Mirai's calendar at each cut |
| Train | `train_feature_set_columns`, `train_examples`, `train_splits` | the frozen recipe of a model row, the model-ready rows, the walk-forward day roles | empty until there is something to train |
| Reads | `reads_decisions`, `reads_decision_values`, `reads_trade_legs` | every decision (range forecast, Claude read, combined call, trade candidate), its numbers, its legs | empty until Phase 2 (range engine) |
| Results | `results_outcomes`, `results_outcome_values`, `results_evaluations` | what happened after each read, how each decision graded, the trust weights earned | empty until Phase 3 (grading) |

The exact columns are in `engine/store_schema.sql` (rendered from the 2026-10-16 store design; Parquet partition keys
became plain indexed columns). Rules every table follows:

- **Blank, never 0.** A measure with no value gets a `blank_reason` from a fixed list (`feed_missing`, `not_formed_yet`,
  `none_left_today`, `not_known_at_read`, ...). The writer refuses a blank without a reason.
- **Registered or refused.** The writer refuses an unregistered measure, a subject kind the measure does not allow, a
  value in the wrong column, a number outside the measure's range, or a word outside its list.
- **Point in time.** `cut_at` is the market moment a row describes; `known_at` is when Kaze wrote it. Bars with no
  writer stamp are known 62 seconds after the bar ends (`known_at_rule = bar_end_plus_62_seconds`).
- **Live or rebuilt.** `row_origin` is `live` for rows written during the session and `rebuilt` for rows built later
  from Mirai's files as they stood at the cut. The nightly check compares the two.

## Reads and cuts

A read slot is a New York minute on the 5-minute grid, 09:35 to 15:55 (77 slots on a full day; 09:35 to 12:55 on a
13:00 half day). The slot's **cut** is the last second of the minute before it: the 12:35 slot reads through the 12:34
bar, so the live job (fired at 12:35:00) waits up to 100 seconds for that bar to land before it builds. Slots at :05
and :35 are the 30-minute reads, lane `headline_30`; the rest are lane `range_every_5_minutes`. A `--at` rebuild uses
the exact second given as its cut and carries no slot.

`read_id` is `<lane>:<cut in New York time>`; `snapshot_id` is `read|<cut in UTC>|<origin>|kaze_collector@1|b1`.

## What a read stores (the registry, by group)

The 55 measures written on every read, with their subjects:

| Group | Measures |
|---|---|
| clock | `minutes_since_session_open`, `minutes_until_session_close` |
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
| feed and login health | `feed_status`, `feed_age_seconds` (feed = each of the nine feeds), `rows_received_today_count` (bars, quotes, breadth), `login_state`, `login_hours_until_expiry`, `login_expires_at` (login = schwab; live reads only), `are_needed_feeds_all_ok` (consumer = range_engine) |
| nightly check | `reads_compared_count`, `values_mismatched_count`, `rebuild_identical_portion` |

Registered now and written from Phase 2 on: today's speed (`today_typical_1_minute_move_points`, the 5- and 30-minute
half-life variants and their ratio), `typical_swing_points` and `options_implied_swing_points` per horizon, the up and
down legs and `trend_straightness_ratio` for 15/30/60 minutes, the history fits (`band_multiplier` with its
`stretch_recipe_name`, `fit_sessions_count`, `fit_windows_count`, `average_price_shrink_ratio`,
`never_leaves_rung_portion`), the band values of a range forecast, and the outcome values including the grade start
(`grade_start_price_points`, `grade_start_bar_at`, `grade_start_rule_name`).

Every measure's lane and fact group are checked when the registry loads: one fact may reach the pull lane (how far)
or the push lane (which way), never both.

## Feeds (what Kaze reads from Mirai)

| Feed | Mirai file | Stale after | Notes |
|---|---|---|---|
| `spx_bars` | `data/market/spx_price_bars_by_minute/{day}.jsonl` | 180 s | a bar counts once its start is at or before the cut; age = cut minus the newest bar's start |
| `watchlist_quotes` | `data/market/watchlist_quotes_by_minute/{day}.jsonl` | 180 s | newest row with quotes at or before the cut |
| `nyse_breadth` | the quotes rows' `bars` field (`$ADVN`, `$DECN`) | 240 s | |
| `options_scan` | `data/spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl` | 300 s | native-chain scans only; the straddle needs a ruler with quality ok |
| `options_tape` | `data/market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl` | 180 s | the file is in arrival order, so windows are cut by trade time; gap markers are counted |
| `futures_bars` | `data/market/futures_price_bars_by_minute/{day}.jsonl` | 1 day | /ES 1-minute bars only |
| `event_calendar` | `settings/market_event_calendar_by_event.json` | 100 days | also supplies half-day closes; it lists no holidays, so those are in `engine/session_clock.py` |
| `claude_forecast` | `data/spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl` | 40 min | newest live final forecast written at or before the cut |
| `news_headlines` | `data/market/news_headlines_by_article/{day}.jsonl` | 30 min | |
| `schwab_login` | `data/operations/logs/auth_watch_output_log.txt` | 2 days | the auth watch's `left=N.NNd` line plus the log's modified time; live reads only |

Gzipped copies (`.jsonl.gz`) of older days are read the same way.

## Files in `KAZE_DATA`

| Path | What it holds | Written by |
|---|---|---|
| `kaze.sqlite` | the store | every job |
| `live/latest.json` | the newest live read for the phone: `read_id`, `lane`, `slot`, `origin`, `cut_at`, `known_at`, `feeds` (status live / partial / blind and age per feed), `login`, `values` (`measure` or `measure\|subject` keys), `blanks` (key to reason) | the live read job, after each read |
| `logs/read_job_log.txt` | one line per read-job run (including the quiet "market closed" lines) | the read job |
| `logs/nightly_log.txt` | one health line per nightly run and one line per parity mismatch | the nightly job |
| `logs/launchd_read_log.txt`, `logs/launchd_nightly_log.txt` | whatever the jobs print or raise, kept by `engine/jobs/run_job.sh` | launchd |

## Who writes what, and when

| Job | When | Writes |
|---|---|---|
| `python3 -m engine.read_job` (launchd `com.kaze.read`) | every 5 minutes; builds only at the 09:35-15:55 slots on trading days, else one log line | one `live` snapshot, its observations, any new bars, `live/latest.json` |
| `python3 -m engine.read_job --at "YYYY-MM-DD HH:MM:SS"` | by hand | one `rebuilt` snapshot at that New York moment and the bars up to it |
| `python3 -m engine.read_job --rebuild-day YYYY-MM-DD` | by hand | every slot of the session as `rebuilt` rows, plus the whole session's bars |
| `python3 -m engine.read_job --nightly` (launchd `com.kaze.nightly`) | 17:15 New York | the session's closing bars (when Kaze was live that day), one `nightly_check` snapshot per session that has both live and rebuilt reads, and the health line |

`engine/jobs/install_jobs.sh` links the two plists into `~/Library/LaunchAgents` and loads them.
