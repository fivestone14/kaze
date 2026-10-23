# Kaze data map

The one page to look Kaze up on: where things live, how the clock runs, what the store holds, how a read is worked
out (with one read worked through to the digit), how windows are graded, how the trust ledger moves a weight, what
the phone is sent, and how the jobs are run, watched, restored and kept. Dated measurements live in `evidence.md`.

Words used throughout: a **read** is one 5-minute pass over Mirai's files at a **cut** (a moment); a **channel** is
one horizon's block (5, 30 or 60 minutes: its swing, bands, dials, direction call and windows); a **window** is the
stretch of market after a read that one channel is graded on.

## 1. Where things live

Everything Kaze keeps is in one SQLite file, `kaze.sqlite`, in the folder `KAZE_DATA` names (`.env`, never the
repo), beside a small live feed for the phone and the jobs' logs. Mirai's files under `KAZE_MIRAI_HOME` are the
source of every market number; Kaze reads them and never writes there. The engine refuses to start (exit 1, one
clear line) when `.env` is missing, `KAZE_MIRAI_HOME` or `KAZE_DATA` is blank or not a folder. `KAZE_BACKUP_DIR` is
checked only by the nightly (section 10), so a cloud folder that is signed out never stops a read.

Under `KAZE_DATA`:

| Path | What it holds | Written by |
|---|---|---|
| `kaze.sqlite` (with `-wal`, `-shm`) | the store | every job |
| `live/latest.json` | the newest live read for the phone (section 9) | the live read job |
| `logs/read_log.txt` | one line per read-job run (the quiet "market closed" lines too) and crashes | the read job |
| `logs/nightly_log.txt` | the health line, parity mismatches, warnings, the hourly "not due" lines | the nightly |
| `logs/by_hand_log.txt` | one line per hand run (`--at`, `--rebuild-day`, `--backfill-bars`, crashes) | hand runs |
| `logs/app_log.txt` | one line per request (route, status, milliseconds; never the token or a body) | the app server |
| `logs/older/<day>/*.gz` | earlier days' logs, kept 14 days | `job_log.py` on the first write of a new day |
| `nightly_done.txt` | the session the nightly last finished | the nightly |
| `backup_in_progress/` | where a backup is copied, checked and gzipped; empty between nightlies | the nightly |
| `backups/kaze-<session>.sqlite.gz` | the nightly copies when `KAZE_BACKUP_DIR` is unset or absent | the nightly |

launchd's own capture files, `/tmp/com.kaze.read.launchd.log`, `.nightly.` and `.app.`, hold only what Python could
not catch (a missing `.env`, an import error, a port already taken).

Mirai's files Kaze reads (the templates are in `engine/paths.py`, the readers in `engine/collect/mirai_files.py`;
gzipped copies of older days read the same way):

| Feed | Mirai file (under `data/` unless said) | Stale after | Cadence |
|---|---|---|---|
| `spx_live_price` | `market/spx_live_price_latest.json` | 10 s | live |
| `spx_bars` | `market/spx_price_bars_by_minute/{day}.jsonl` | 180 s | minute |
| `watchlist_quotes` | `market/watchlist_quotes_by_minute/{day}.jsonl` | 180 s | minute |
| `nyse_breadth` | the quotes rows' `bars` field (`$ADVN`, `$DECN`) | 240 s | minute |
| `options_scan` | `spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl` | 300 s | minute |
| `options_tape` | `market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl` | 180 s | minute |
| `futures_bars` | `market/futures_price_bars_by_minute/{day}.jsonl` | 1 day | once_a_day |
| `event_calendar` | `settings/market_event_calendar_by_event.json` | 100 days | hand_kept |
| `claude_forecast` | `spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl` | 40 min | minute |
| `news_headlines` | `market/news_headlines_by_article/{day}.jsonl` | 30 min | minute |
| `schwab_login` | `operations/logs/auth_watch_output_log.txt` | 2 days | once_a_day |

The cadence (`registry.FEEDS`, also in each feed's component parameters) says how often a feed lands: `live` (a tick
about every second), `minute` (through the session, judged by its age), `once_a_day` (the night's futures, the
login check) or `hand_kept` (the calendar, extended by hand).

Notes per feed: the live price is `{price, at, written_at}` (`at` the trade's own time, both ET ISO), rewritten whole at
most once a second by Mirai's lob-collector while its Schwab stream is up (market hours), so out of hours it simply goes
old; it is no read's input, only the phone's (`mirai_files.read_live_price`: price, trade time, age; an absent or
half-written file is None); a bar counts once its start is at or before the cut and its age is the cut minus the newest
bar's start; the quotes feed takes the newest row with quotes at or before the cut; the scan feed takes native-chain
scans only and the straddle needs a ruler with quality ok; the tape is in arrival order, so windows are cut by trade
time, gap markers are counted, and a rebuild reads the live read's byte prefix; the futures file gives /ES 1-minute bars
only; the calendar is copied into the store at every read and supplies the half-day closes (it lists no holidays: those
are in `engine/session_clock.py`); the Claude feed takes the newest live final forecast written at or before the cut;
the login feed reads the auth watch's `left=N.NNd` line plus the log's modified time (`?` gives `login_state =
unknown`), live reads only.

## 2. The clock

New York time throughout (`engine/session_clock.py`). The session opens 09:30 and closes 16:00, 13:00 on a half day
the calendar lists. A read slot is a minute on the 5-minute grid from 09:35 to 15:55 (77 slots on a full day, 09:35
to 12:55 on a half day). The slot's **cut** is the last second of its own minute: the 12:35 slot cuts at 12:35:59 and
includes the 12:35 bar. The live job fires at 12:35:00 and waits up to 100 seconds after 12:36:00 for the 12:35 bar;
if the bar never lands the read still writes with `spx_bars` status `late` and `snapshot_status_reason =
cut_bar_missing`. A launch up to a minute after the slot minute still serves the slot (`slot_status = late`). Slots
at :05 and :35 are the 30-minute reads, lane `headline_30`; the rest are lane `range_every_5_minutes`. The 5-minute
channel follows every read; the 30- and 60-minute channels follow the headline lane.

Holidays are a hand-kept list in `session_clock.py` through `HOLIDAYS_LISTED_THROUGH` (2027-12-31); the clock refuses
to answer past it, and the nightly's health line and the phone's Feeds card warn from 30 days before (section 10).
Every stored timestamp is ISO 8601 in UTC; the phone and the logs get the New York clock.

## 3. The store

Eleven tables in `kaze.sqlite`, their text rendered from the registry's word lists in `engine/store/store_schema.py`
(schema version 10), created or upgraded step by step in one transaction each when a store is opened. At every open
the store refuses to run if any table's text differs from the code's, so a fresh store and a migrated one are the
same file shape. `engine/store/store.py` shapes, checks and writes rows; `store_queries.py` reads them;
`registry.py` lists every measure and word; `components.py` holds the recipe's numbers.

- `registry_measures`: one measure Kaze may store: name, plain meaning, unit, lane, fact group, subjects, feed,
  blank rule.
- `registry_components`: one versioned thing: a feed, the login, the collector, the range engine, a lane, a
  push/pull factor, a dial, a rule, a test.
- `collect_snapshots`: one cut: a read, a nightly check or a nightly history fit, with slot, lane, origin, attempt,
  commit and health counts.
- `collect_observations`: one measure x subject at one cut, with its feed's status and age at the cut.
- `collect_market_prices`: one SPX 1-minute bar with the moment it was known.
- `collect_calendar_and_events`: one dated fact: a scheduled event or a half-day session, with when the station
  first held it.
- `reads_decisions`: one decision header: a `range_forecast` per channel, with its window and status.
- `reads_decision_values`: every number, word or id inside a decision.
- `results_outcomes`: one outcome header: a `market_window` (what happened) or a `forecast_grade` (how a decision
  did).
- `results_outcome_values`: every fact or grade inside an outcome.
- `results_evaluations`: one trust-ledger row: a weight, a score or a baseline for one factor or dial on one
  channel.

The registry is seeded from `registry.py` on every open: a measure dropped from the code is dropped from the table
(refused while rows still use it); a component whose parameters or meaning changed under the same version gets the
next `revision_number`, earlier revisions stay, so a ledger row can be read against the parameters of its night.

The three header tables (`collect_snapshots`, `reads_decisions`, `results_outcomes`) carry an integer rowid primary
key beside their text id (unique); the three long value tables hang off them by that rowid with one unique index on
(parent, measure, version, subject, and the value role or value origin). A value row's day, origin and scope are
read off its parent. Beyond the design, `collect_snapshots` carries `newest_input_market_at` (the newest input any
observation used; refused past `cut_at`) and `source_files_json_text` (each source file's byte length at a live
read); `reads_decisions` and `results_outcomes` carry `grade_start_rule_name` beside the version; `results_outcomes`
carries `was_window_clipped`.

Rules every row follows:

- **Blank, never 0.** A measure with no value gets a `blank_reason` from the fixed list (`feed_missing`,
  `feed_stale`, `not_formed_yet`, `none_left_today`, `not_known_at_read`, `too_few_sessions`, ...). Counts over a
  window (tape trades and premium, headlines, gap markers) are values only while the feed's newest line is inside
  its stale limit. A table constraint enforces exactly one of the four value columns or a blank reason.
- **Registered or refused.** The writer refuses an unregistered measure, a subject kind the measure does not allow,
  a value in the wrong column, a number outside the measure's range, a word outside its list, an input newer than
  the cut, and a row key the table lacks. Every word in a `CHECK` list has a writer.
- **Point in time.** `cut_at` is the market moment a row describes; `known_at` is when it became usable. Reads carry
  `known_at = written_at`. Bars seen in their own slot are known 62 seconds after the bar ends
  (`bar_end_plus_62_seconds`); bars that turn up later are stamped with their write (`written_at`); backfilled
  sessions with the file's change time (`file_modified_at`). An outcome's `label_resolved_at` is when its label
  became knowable: for a rebuilt window the `known_at` of the last-arriving bar inside it, for a live window and
  for any window with no bars the write. A live read, its grading and its phone block use only bars known by the
  read's own write or its cut bar's arrival stamp, whichever is later, so a read sees every bar it stored itself and
  nothing a later process stored. A rebuild sees every bar of its day; a rebuild while the session is open stamps
  the bars it writes with its write time, never by arrival.
- **Origins.** `row_origin` is `live_frozen` (written during the session, first attempt), `live_rebuilt` (a second
  run at the same live cut; the first is never deleted) or `history_rebuilt` (built later from Mirai's files as they
  stood at the cut; re-running replaces it, decisions, outcomes and grades included). A decision carries its
  snapshot's origin and `run_kind` `live` or `history_replay`; its outcome and grade carry the same origin, so live
  and replay grades never mix in a count. Every live-read query goes through one statement of "the live truth": the
  first built live read at each cut.
- **Calendar as known.** Every read first copies Mirai's calendar into `collect_calendar_and_events`, then reads the
  day's events from the store with `known_at <= cut_at`, one row per fact (its newest version known at the cut).
  The first copy stamps its facts with the calendar's own `built` date; later copies stamp new facts with the file's
  change time, and an earlier copy of a fact keeps its first `known_at`. An event added after a cut is not seen by
  that cut, live or rebuilt.
- **Tape and scan files land late.** A live read measures each source file's byte length first, parses only that
  much, and records the lengths; a rebuild of the same cut reads the same prefixes. For cuts with no live twin the
  rebuild sees the whole file, so the nightly's parity check counts tape and scan differences as tolerated.

Ids: `read_id` is `<lane>:<cut in New York time>`; `snapshot_id` is
`read|<cut in UTC>|<origin>|kaze_collector@1|b<attempt>`; a decision id is
`range_forecast|<snapshot_id>|range_engine@1|<run_kind>:<variant or live>|<horizon>|end_price|-|whole|r0`; an
outcome id is `<kind>|<anchor snapshot_id>|<judged decision_id or ->|<horizon>|<graded price or ->|<start rule>@<v>|
<outcome rule>@<v>|-|<row_origin>|b<attempt>`. A value row has no stored id; its design id is
`<parent id>|<measure>|<version>|<subject>[|<role>]`.

## 4. What a read stores

One observation per measure and subject; `registry_measures` is the list. By group:

- clock (no feed): `minutes_since_session_open`, `minutes_until_session_close`
- price: `spx_price_points`, `prior_close_points`, `today_open_points`, `session_high_points`, `session_low_points`,
  `vwap_points`, `range_so_far_points`, `move_points` (since = prior_close, open, 5/15/30/60_minutes)
- options: `same_day_straddle_mid_points`, `opening_straddle_mid_points` (scan ruler / 0.85),
  `atm_option_mid_points` (side, strike_points; from the tape), `same_day_atm_iv_calendar_year_pct`,
  `level_price_points` and `level_gamma_share_portion` (level_type = call_wall, put_wall), `dealer_net_gamma_usd`,
  `dealer_gamma_sign`
- VIX family: `vix_calendar_year_pct`, `vix_prior_close_calendar_year_pct` (symbol = $VIX, $VIX1D, $VIX9D, $VIX3M,
  $VVIX)
- breadth: `nyse_advancing_count`, `nyse_declining_count`, `nyse_advancing_portion`
- leaders, rates, dollar, oil: `fund_change_from_prior_close_pct` (symbol = SPY, RSP, QQQ, IWM, SMH, UUP, USO),
  `sector_funds_up_count` (of 11), `largest_stocks_up_count` (of 9), `ten_year_yield_annual_pct`,
  `ten_year_yield_change_basis_points`
- overnight: `overnight_high_points`, `overnight_low_points` (symbol = /ES, 18:00 to 09:30)
- events: `events_left_today_count`, `minutes_until_next_event` (impact = any), `next_event_kind`,
  `does_calendar_cover_day`
- options tape (30-minute window): `options_trades_last_30_minutes_count`,
  `options_call_premium_last_30_minutes_usd`, `options_put_premium_last_30_minutes_usd`,
  `bullish_portion_of_sided_premium`, `tape_gap_markers_last_30_minutes_count`,
  `feed_coverage_last_30_minutes_portion`
- headlines (60-minute window): `headlines_last_60_minutes_count` (kind = all)
- Mirai's Claude forecast: `mirai_claude_forecast_pct` and `mirai_claude_base_rate_pct` (horizon = 30_minutes,
  to_close; side = up, flat, down), `mirai_claude_read_at`; carried at most 40 minutes
- feed and login health: `feed_status`, `feed_age_seconds` (feed = every feed seen), `rows_received_today_count`
  (bars, quotes, breadth), `lines_skipped_today_count` (malformed lines per file), `login_state`,
  `login_hours_until_expiry`, `login_expires_at` (login = schwab; blank `not_known_at_read` on rebuilt reads),
  `are_needed_feeds_all_ok` (consumer = range_engine)
- range readings, from the store's bars (section 5): `today_typical_1_minute_move_points`,
  `speed_fast_5_minute_half_life_points`, `speed_slow_30_minute_half_life_points`, `speed_acceleration_ratio`,
  `typical_swing_points` (horizon), `options_implied_swing_points` and `options_over_tape_swing_ratio` (horizon),
  `path_up_points`, `path_down_points`, `trend_straightness_ratio` (window_minutes = 15, 30, 60),
  `level_distance_from_price_points` (level_type; the wall less the read price, a width input with no dial yet)
- push/pull factors (section 8): `push_pull_lean_probability` and `push_pull_factor_text` (factor = `price_trend`,
  `straightness`, `breadth`, `big_tech_vs_rest`, `options_buyers`, `yield_change`, `mirai_claude`)
- nightly check: `reads_compared_count`, `values_mismatched_count`, `tolerated_mismatches_count`,
  `rebuild_identical_portion`
- nightly history fit: `stretch_multiplier` and `stretch_recipe_name` (band = 80, average_80; horizon),
  `fit_sessions_count`, `fit_windows_count`, `average_price_shrink_ratio` (horizon)
- market window (`results_outcome_values`): `grade_start_price_points`, `grade_start_bar_at`,
  `grade_start_rule_name`, `end_price_points`, `average_price_points`, `high_price_points`, `low_price_points`,
  `end_move_points`, `average_move_points`, `high_move_points`, `low_move_points`, `minutes_covered_count`,
  `stayed_inside_rung` (rung_points = 5, 10, 15)
- forecast grade (`results_outcome_values`): the three grade-start measures again, `was_inside_band` and
  `band_miss_points` (band = 80, average_80), `stayed_inside_rung` (the channel's own rung), `band_used_ratio`
  (band = 80; side = upper, lower)
- trust ledger (`results_evaluations`): `trust_weight_portion`, `brier_skill_portion`,
  `interval_score_gain_points`, `band_coverage_portion`

Every measure's lane and fact group are checked when the registry loads: one fact may reach the `range` lane (how
far price travels) or the `push_pull` lane (which way: the factors that push price up or pull it down), never both.
Push and pull only ever describe price: a factor pushing it up or pulling it down. How far price travels is the
range, never a push or a pull. Schema step 11 moved every stored row onto these words.
The 12:35:59 read of 2026-10-16 wrote 145 observations as a rebuilt read (147 live, with the two Schwab-login feed
rows) and 123 decision values.

## 5. The range recipe

The fixed numbers live in `engine/store/components.py` (the `range_engine` component row carries them). Bars are
read by minute of the session (`engine/range/range_fit.py`, `minute_series`): a missing minute is a gap, so a
change across it is left out of the speed, a window starting or ending on it is skipped, and the path windows
count real minutes.

1. **Speed** (today) = the recency-weighted mean of |1-minute close change| over the session's bars up to the cut,
   weight 0.5^(minutes ago / 15). The fast (half-life 5) and slow (half-life 30) speeds and their ratio
   (`speed_acceleration_ratio`, the acceleration dial's input) are stored beside it. Bars come from
   `collect_market_prices`, never from Mirai's files; for one minute with both a live and a rebuilt row the live row
   is taken.
2. **Swing** = speed x the square root of the horizon's minutes, for 5, 30 and 60.
3. **Stretch** (history) = the 80% mark of |end move| / swing over every window of the prior 60 full sessions,
   measured on the same recipe: speed at the window's start from that day's bars, windows every 5 minutes from
   minute 30. A full session holds a bar for every minute (390, or 210 on a listed half day; the rule
   `every_minute_held`); the full sessions are picked first and the last 60 of them taken, so a half day never
   shrinks the sample. The average band's multiplier is the same fit on |average price over the window - start| /
   swing. Both carry the recipe label (`stretch_recipe_name`) and the sample counts.
4. **End band** = read price (the last close at the cut) +/- swing x stretch x the dials (step 7); **average band**
   the same with the average multiplier. The grade-start rule is `at_read`: the window runs from the end of the
   read's minute (12:36:00 for the 12:35 read) for the horizon, capped at the close; a capped window's decision is
   `clipped`, its band is still the full horizon's.
5. **Never-leaves share** = the share of the same history's windows whose farthest excursion (highs and lows) stayed
   inside +/- X in that window's swings, at today's swing; X = 5 / 10 / 15 points for 5 / 30 / 60 minutes,
   computed from the history windows at read time.
6. **Options check** = straddle left x the square root of (window minutes / minutes left), stored beside the tape
   swing over the same window minutes as `options_over_tape_swing_ratio`; the verdict `options_vs_tape_check_word`
   is `agree` when the two swings sit within a ratio of 1.25 of each other either way, else `differ`. Shown, never
   added in. On a clipped window both swings cover the minutes to the close.
7. **Dials** (`width_multiplier`, subject `adjustment`, roles `raw` and `applied`; the list and order is
   `components.WIDTH_DIALS`, the code `engine/range/width_dials.py`): each dial's raw value is measured on every
   read and its applied value is 1 + weight x (raw - 1), where the weight is the one the trust ledger holds for that
   dial and channel (none yet, so every applied value is 1.00). The band's half-width is swing x stretch x the
   product of the applied dials other than the Fed held under the cap, x the Fed dial's applied value on top (the
   Fed dial sits outside the cap, which is fitted on ordinary days). `adjustment = all_dials` stores the raw product
   and the multiplier the band used. On a read whose window ends after an `FOMC` decision known at the cut a second
   decision tagged `variant_name = owner_rule` (parent = the base decision) applies the Fed dial in full (applied
   2.6 with trust 1.0) to both bands, so the grader scores the band with and without it. No FOMC day in the store
   was known in time (the calendar was built 2026-09-27), so rebuilt reads before that date never carry the variant.
8. **Direction** (not the range: section 8's push/pull factors) = `chance_up_if_moves_probability` = 0.5 + the
   sum over the factors with a lean of share x (lean - 0.5), each an equal share (1 / the count), with
   `trust_weight_applied_portion` the sum of the shares (1, or 0 when no factor has a lean and the call is 0.5 with
   the words `even, no factor has a lean`). Each factor's lean is stored on the decision too, with its share. The
   factors are built after the band in their own step (`engine/push_pull/push_pull_read.py`): if that step crashes
   the read, band and decisions still land, the push/pull block is empty and the read carries
   `snapshot_status_reason = push_pull_failed`.

**One read worked through: 2026-10-16, the 12:35 slot, cut 12:35:59.** Price 7,691.03 (the 12:35 bar's close),
204 minutes left, the fit of 60 sessions and 3,600 windows per horizon.

| Step | 5 minutes | 30 minutes | 60 minutes |
|---|---|---|---|
| 1 speed (points a minute) | 1.494518 | 1.494518 | 1.494518 |
| 1 fast / slow / acceleration | 1.59924 / 1.564107 / 1.022462 | same | same |
| 2 swing = speed x sqrt(minutes) | 3.341843 | 8.185811 | 11.576484 |
| 3 stretch (end band) | 1.568855 | 1.571861 | 1.5472 |
| 3 stretch (average band) | 1.0505 | 0.929031 | 0.918003 |
| 7 dials product, raw / applied | 1.042365 / 1.00 | 1.042365 / 1.00 | 1.042365 / 1.00 |
| 4 end half-width = swing x stretch x 1.00 | 5.242867 | 12.866957 | 17.911137 |
| 4 end band | 7685.79 to 7696.27 | 7678.16 to 7703.90 | 7673.12 to 7708.94 |
| 4 average half-width | 3.510606 | 7.604872 | 10.627247 |
| 5 never leaves +/- 5 / 10 / 15 | 0.608056 | 0.438889 | 0.496389 |
| 6 options swing = 22.4 x sqrt(window / 204) | | 8.590008 | |
| 6 options over tape, verdict | | 1.049378, agree | |
| 8 cap (dials other than the Fed) | 1.594256 | 1.648844 | 1.637784 |
| 8 closing ratio / release ratio | 1.358231 / 1.391491 | 1.273543 / 1.39923 | 1.130402 / 0.87066 |

The raw dials that read: acceleration 1.022462 (fast over slow), options vs tape 1.019466 (1.049378 ^ 0.4); the Fed,
release and closing dials were at rest (1.00); the raw product 1.042365 was stored and applied at 1.00. The seven
leans (step 8): price trend -53 since close -> 0.300, straightness 0.13 net -5.05 -> 0.474, breadth 50% advancing ->
0.503, QQQ -1.4% vs RSP -> 0.301, options buyers 50% -> 0.500, yield +3.8 bp -> 0.628, Claude 28.5 / 45.5 / 26.0 ->
0.523; direction 0.461455, down 54% (the live read said 0.5: until 2026-10-23 no factor counted before the ledger
earned it weight). Every number here is on the phone's Steps screen for that read, and
`--at "2026-10-16 12:35:59"` reproduces them to six decimals.

**Where it lands.** The readings (speeds, swings, the options check, the path moves, the wall distances) are
observations on the read's own snapshot. Each read then writes one `reads_decisions` row per channel
(`decision_kind = range_forecast`, producer `range_engine@1`, `run_kind` `live` or `history_replay`, `horizon_name`,
`graded_price_measure = end_price`, `grade_start_rule_name = at_read` version `1`, the grade window, `decision_status`
ok / clipped / blind with its reason) and its `reads_decision_values`: `band_centre_points`, the speed and swing
used, `stretch_multiplier`, `stretch_recipe_name`, `fit_sessions_count`, `fit_windows_count` and `fit_snapshot_id`,
`band_half_width_points`, `band_low_points` and `band_high_points` (band = 80 and average_80),
`chance_stays_inside_rung_probability` (horizon, rung_points), `closing_window_multiplier`,
`release_window_multiplier` and `width_cap_multiplier` (horizon), the five dials and `all_dials`, every factor's lean
and the direction. A read with fewer than two bars is `blind` (`speed_not_formed`, values blank `not_formed_yet`);
one with no fit known at its cut is `blind` (`no_fit_known_at_cut`, values blank `too_few_sessions`); one served by
a thin fit stays `ok` with `decision_status_reason = thin_fit_<n>_of_60`. The snapshot, its observations, its
decisions and their values are written in one transaction; if the range step itself fails, the snapshot still lands
with `snapshot_status_reason = range_failed` and no decisions, the traceback goes to the job's log, and the live feed
says so.

## 6. Fits, the pass test and the scoreboard

**Fits.** The nightly writes one `nightly_history_fit` snapshot per session it serves (builder `range_engine@1`):
`trade_date` is the session served, `cut_at` the close of the last session in its sample, and `known_at` that close
plus 62 seconds for a rebuilt fit or the write time for the live one (always before the served open). A fit built on
fewer than 60 full sessions has `snapshot_status = thin` (reason `<n>_of_60_sessions`); reads still use it and say
so. The nightly stores the session's bars before it fits, then writes a rebuilt fit for every stored session that
lacks one, then the live fit for the next trading day unless a fit with the same or newer data already serves it. A
read uses the newest fit with `trade_date <= its day` and `known_at <= its cut`, so a fit never includes the read's
own day; `--rebuild-day` and `--at` use the same rule. On a fresh store the order is `--backfill-bars`, then
`--nightly` (the fits), then any rebuild. The time-of-day ratios and the cap (section 8) come from the same history,
computed at read time; nothing is written on the fit snapshot for them.

**Pass test** (`--pass-test`). For every session with 60 full sessions behind it, the band is refitted on those
sessions and every 5-minute read of the day (windows capped at the close) is checked: did the window's end (and its
average) land inside that read's band? The day coverages are resampled 20,000 times (fixed seed); PASS when the 95%
interval of the mean contains 0.80 and sits inside 0.75-0.85; an interval that holds 0.80 but breaks a bound is
printed as FAIL, not yet decidable. Clipped windows are reported as their own slice. The same is printed with a
20-session lookback for information only. Nothing is tuned to the test; the latest run is in `evidence.md`.

**Scoreboard** (`--scoreboard [YYYY-MM-DD]`). Per session, run kind (`live` or `history_replay`, never pooled),
channel, variant (`base` or `owner_rule`) and slice (`unclipped` or `clipped`): ends inside, average inside and
never left as yes/graded with the share, only grades under the current rule versions (`band_grade@1`, start
`at_read@1`), only first-built live reads; then the same pooled over the last 20 graded sessions, printed with
`pooled` in the session column. What each word counts is the registry's one statement (`GRADE_WORD_MEASURE`: the
measure, its band subject and the value that says yes), which the phone block reads too. Every 5-minute read is
graded, so a day's 30-minute reads pooled over both slices give the pass test's per-day number.

## 7. Outcomes and grades

Every range decision window is closed from the store's bars and every decision graded against its own bands, under
rules the registry names and versions (grade-start rule `at_read@1`, outcome rule `window_from_bars@1`, grade rule
`band_grade@1`); a changed rule is a new version and new rows, never an edit.

- **Grade from the read.** The start price is the read's last close (the snapshot's `spx_price_points`, the price
  the band is centred on), the start bar is the bar that close came from, and the window runs from the end of the
  read's minute for the horizon, capped at the close (12:36:00 to 13:06:00 for the 12:35 read). Every outcome and
  grade stores the start price, its bar and the rule name as values.
- **The window** (`outcome_kind = market_window`, one per read and horizon, anchored on the read's snapshot): the
  bars whose minute lies inside the window (12:36 to 13:05, live rows preferred, measured by `window_facts`, the
  same function the history fit and the pass test use). End price = the close of the window's last bar; average =
  the mean of the window's closes; high and low = the bars' highs and lows; the four moves are signed from the
  start price; `stayed_inside_rung` per rung (5, 10, 15 points) says whether the farthest excursion on both sides
  stayed short of the rung; `minutes_covered_count` counts the minutes held. `was_window_clipped` is set when the
  window is shorter than its horizon. `label_status` is `resolved`, `partial_bars` (the end bar held but a minute
  inside never came; reason `<held>_of_<wanted>_minutes_held`), `bars_missing` (no end bar; every value blank
  `bars_missing`) or `unpriceable` (the read had no price; the moves blank `not_applicable`). Only the first two
  count toward skill.
- **The grade** (`outcome_kind = forecast_grade`, one per decision, so the owner-rule variant is graded beside its
  base; `judged_decision_id` the decision, `market_outcome_id` its window): `was_inside_band` for band `80` (|end
  move| <= the end band's half-width) and `average_80` (|average move| <= the average band's), `band_miss_points`
  per band (points past the edge, signed with the move, 0 inside), `stayed_inside_rung` for the channel's rung (the
  grade word `never_left` is this yes), `band_used_ratio` (band 80; `upper` = high move over the half-width, `lower`
  = the drop over it, each floored at 0; above 1 by exactly how far price broke out that side) and the three
  grade-start values. A blind decision's grade is `unpriceable` with the decision's reason. Half-widths are the
  decision's stored values, so the owner-rule grade uses the x2.6 band.
- **When.** Each live read closes every window of the day whose end is at or before its cut and whose bars the store
  all holds (the 12:35 read's 30-minute window ends 13:06:00, so the 13:10 read closes it; a 5-minute window closes
  two reads later). A window missing any bar stays open until the session is settled (a cut at or after the close),
  so a bar Mirai fills in late is graded in full and never frozen as partial. The nightly settles every open window
  of every session, as `partial_bars` or `bars_missing` when the bars never came; `--rebuild-day` and `--at` settle
  the rebuilt day's windows the same way. The window and its grades are written in one transaction, never replaced
  except with their rebuilt read.

## 8. Push/pull factors, dials and the trust ledger

Two questions, two lanes. The **range** (how far price travels over the window) is the band of sections 5 to 7;
the width dials below are the only things that may widen it, and each starts at zero weight, changing the band only
after the trust ledger has written it a weight from live graded windows under a test fixed in advance. **Push and
pull** (which way) is the tug-of-war of the factors: each one, read by read, either pushes price up or pulls it down,
and every factor with a lean counts now at an equal share; the ledger scores each one nightly as evidence.

**Push/pull factors** (which way; `engine/push_pull/push_pull_read.py`). Each read turns inputs already on its
snapshot into a value in words (`push_pull_factor_text`, the exact figure), a short answer from the factor's fixed
words (the phone's, never stored), a lean (`push_pull_lean_probability`): the chance that the move is up if price
moves, 0 to 1, and its side (the phone's, never stored): `push` above the even zone, `pull` below it, `even` inside
it. Share factors lean by their share; signed factors lean by a smooth squash, 0.5 + 0.2 x tanh(value / scale), so
they never lean past 0.3 or 0.7 (the scales are in `components.LEAN_SCALE`):

| Factor | Value in words | Lean |
|---|---|---|
| `price_trend` | SPX move since the prior close, points | squash of that move, scale 10 points |
| `straightness` | net move over total path in the last 30 minutes | 0.5 + 0.2 x straightness, signed by the net move |
| `breadth` | NYSE advancers over advancers plus decliners | that share |
| `big_tech_vs_rest` | QQQ change less RSP change since the prior close | squash of the difference, scale 0.5 points |
| `options_buyers` | share of sided same-day option premium that was bullish | that share |
| `yield_change` | 10-year yield change since its prior close, basis points | squash, scale 5 bp, positive leans up |
| `mirai_claude` | Mirai's Claude 30-minute up over up plus down | that share (a raw input, not Kaze's own channel) |

The phone asks each factor a plain question and shows its answer and side; the words live in `components.py`
(`PUSH_PULL_FACTOR_LABEL`, `PUSH_PULL_ANSWER_WORDS`) and travel on the factor's registry row with the thresholds. A
lean within 0.03 of even (0.47 to 0.53, `EVEN_LEAN_ZONE`) answers Flat or Even, else the factor's sign picks the side;
that is a move under 1.5 points, a QQQ-over-RSP gap under 0.08 points, a yield change under 0.8 bp. The path is
Choppy inside that even zone; leaning one way it is Steady at a straightness of 0.4 or more (`STEADY_STRAIGHTNESS`:
the net move at least 40% of the path walked; a random 30-minute walk scores about 0.18), else Drifting.

| Factor | Question | Answers |
|---|---|---|
| `price_trend` | Since Yesterday | `Up 37 Pts`, `Down 12 Pts` (whole points), `Flat` |
| `straightness` | Path | `Steady Up`, `Steady Down`, `Drifting Up`, `Drifting Down`, `Choppy` |
| `breadth` | Stocks Rising | the share, `45%` |
| `big_tech_vs_rest` | Big Tech | `Ahead`, `Behind`, `Even` (against the average stock) |
| `options_buyers` | Options Bets | `More Calls`, `More Puts`, `Even` |
| `yield_change` | Bond Rates | `Rising`, `Falling`, `Flat` |
| `mirai_claude` | Mirai's Claude | `Leans Up`, `Leans Down`, `Even` |

A factor whose input is blank is blank with that input's reason, no answer and no side, never 0.5 pretending to be
neutral, and its feed stamp is the input's. **Direction** per channel = 0.5 + the sum of share x (lean - 0.5) over
the factors with a lean, each an equal share, the shares summing to 1 (`direction_all_sources`): the average lean,
the net of the pushes and pulls. The ledger's lean test still scores every factor nightly; its weights no longer gate
the call.

**Points and the wind** (the phone's push/pull card; `components.WIND_LEVELS`, `WIND_DOT_STEPS`). A factor's push on
a channel in points is its share x (lean - 0.5) x 2 x the channel's swing: positive pushes price up, negative pulls
it down, and the points sum to the call (net = (chance - 0.5) x 2 x swing, so chance = 0.5 + net / (2 x swing)). On
the 16:35:59 UTC read of 2026-10-23 the 30-minute swing was 5.68 and the net -0.09 points (down 51%). The wind is
the net over the swing: `calm` under 0.03, `light_air` under 0.10, `breeze` under 0.20, else `strong_breeze`, with a
dot for each of 0.03, 0.10, 0.20 and 0.30 reached. Between reads the phone moves one factor, `price_trend`, with the
live SPX price by the rule the feed sends with it (the same squash of the move from the prior close), and sums the
points again, so the card's net, wind and odds follow the tape; every other factor changes only with a read. The
read's stored call stays what the read said.

**Width dials** (`components.WIDTH_DIALS`, in the ledger's scoring order, `engine/range/width_dials.py`; the phone
calls them Band Wideners: Fed Decision Day, Data Release In The Window, Last 15 Minutes Of The Day, Price Speeding Up,
Options Expect More Movement), each stored per read per channel with a raw and an applied value (`width_multiplier`,
subject `adjustment`), applied = 1 + weight x (raw - 1), weight 0 today:

- `fed_window`: raw 2.6 when the window ends after an FOMC decision known at the cut; applied and tested outside the
  cap. The owner's rule, graded with and without on every Fed read.
- `release_window`: on the 5-minute channel 1.4, on the 30- and 60-minute channels the fitted release ratio, a
  ratio either way (the fit is data, there is no floor), when a timed economic release has its own five minutes
  overlapping the window. A release is a calendar event in a `data` tier that is not a Fed kind (`FOMC*`, `FED_*`:
  decisions, minutes, pressers, testimony and speeches never count); 08:30 releases fall before the first window.
- `closing_window`: the fitted closing ratio for an unclipped window ending inside the last 15 minutes before the
  close (5-minute reads 15:40-15:50, 30-minute reads 15:15-15:25, 60-minute reads 14:45-14:55); clipped windows
  never, they are their own graded slice.
- `acceleration`: fast speed (5-minute half-life) over slow speed (30-minute).
- `options_vs_tape`: (options-implied swing over tape swing) ^ 0.4.

The two **time-of-day ratios** and the **cap** come from the same history the fit used, computed at read time and
stored on every decision: `closing_window_multiplier` is the 80% mark of |end move| in swings over the windows on
the 5-minute grid that end inside the last 15 minutes of each full session (three per horizon per session), over
the all-day mark; `release_window_multiplier` the same over the windows a release known at the served day's open
lands in (blank `too_few_sessions` while the history holds no such window); `width_cap_multiplier` the history's
95% mark over its 80% mark, which the product of the dials other than the Fed is held under. The wall distances
(`level_distance_from_price_points`) are stored as width inputs with no dial: a dial arrives with its rule.

**The trust ledger** (`results_evaluations`, `engine/grade/trust.py`) is written by the nightly after the session's
windows are settled, for the next session: one row per judged thing x channel x metric x slice, 138 rows a night.
`evaluation_id` is the run (`trust_ledger|<data-through day>|<judge>@<version>`); `evaluation_kind` is
`trust_weight` (the weight row) or `skill_metric`; `evaluated_kind`, `evaluated_name`, `evaluated_version` name the
factor or dial (`push_pull_factor` / `width_dial`, version 1); `judge_name` and `judge_version` the test
(`lean_brier_test@1`, `dial_band_test@2`); `signal_lane` and `overlap_group_name` are read off the measure the weight
moves; `horizon_name` the channel and `graded_price_measure` `end_price`; `slice_key` is `whole`, `dial_on` /
`dial_off` on the coverage rows, `fake_baseline` on the control row; `metric_name` a `trust_ledger` measure and
`metric_value` its value (`blank_reason` `too_few_sessions` when there is none); `metric_text` holds the verdict:
`passed`, `held`, `held_down`, `failed` or `too_few_sessions`; `metric_interval_low` / `metric_interval_high` with
`interval_method = bootstrap_by_day`; `baseline_name` (`earned_leans` or `undialed_band`) and
`baseline_metric_value`; the sample columns (`sessions_count`, `independent_windows_count` = rows x 5 / horizon
minutes, `rows_count`, `live_frozen_rows_count`, `data_from_trade_date` / `data_through_trade_date`); `trade_date`
the session the row is in force for and `applies_from_at` its open; `test_family_name` the judge and
`test_family_size_count` the real family (21 lean tests, 15 dial tests); `detail_text` says why.

- **The sample is live only:** first-built live reads' base decisions, `run_kind = live`, whose grade counts under
  the current rule, through the session just graded, with their window's end move. Replayed history never counts,
  a day is one vote (every interval is the day-resampled 95% interval of the day means, 20,000 draws, seed 1, the
  pass test's machinery), and a run's rows are judged in the registry's order, each test on top of what earned
  weight earlier in the same run.
- **Leans** (`lean_brier_test@1`), factors in registry order: over the windows that moved more than half the
  channel's rung (2.5 / 5 / 7.5 points) and had the lean, the baseline is the call built from the factors already
  earned (0.5 while none has), and the candidate is that call moved by (lean - 0.5) times the room left under 1;
  each day scores 1 - (Brier with the lean added / Brier of the baseline) (`brier_skill_portion`, mean of the day
  means); the weight is the interval's low end floored at 0 times the room left, so leans never stack past 1.
- **Dials** (`dial_band_test@2`), in the registry's order, each on top of the dials earned before it: over the
  windows the dial touches (raw not 1.00), the end band's half-width is rebuilt from swing x stretch with the earned
  dials, capped (the Fed outside the cap), with and without this dial's raw; each window scores the band's interval
  score (2h + 10 x the points the move landed outside; a proper score for an 80% band) and the gain is without less
  with (`interval_score_gain_points`, mean of the day means); coverage with and without the dial is written beside
  it (`band_coverage_portion`, slices `dial_on` / `dial_off`). The weight is 1: one weight per dial, a proven dial
  at full strength. A Claude width call, when built, will be scored last.
- **What a pass needs:** 20 live sessions in the test's sample (the Fed dial, a pre-registered exception, 20 Fed
  windows over at least 5 FOMC sessions); the interval's low end above 0; the score above the 95th percentile of 200
  fake baselines (seed 1, written on the `fake_baseline` row): for a lean, the same test with each day's leans
  handed to another day's windows; for a dial, the same test with each day's touched windows handed whole to
  another day, as a same-size random set of that day's windows matched on the clipped flag and carrying the first
  day's raws (so a constant raw cannot reproduce the test itself); and the same verdict on the two nightly runs
  before (the hold rule: the first and second passing runs are `held` with weight 0, the third `passed` with the
  weight).
- **On the way down:** an earned weight that fails the test is `held_down`, the weight kept, for two runs and
  returns to 0 (`failed`) on the third failing run in a row; when the whole interval sits below 0 it returns to 0
  at once. A pass while earned is `passed` again with the fresh weight.
- **Not enough live sessions:** every row of that test is blank `too_few_sessions`, the verdict is
  `too_few_sessions` and `detail_text` says how many of 20; no weight moves from 0.
- A rerun of the same run (same judge, same data-through day) replaces its rows in one transaction. The read job
  applies a dial only at a weight the ledger holds (the newest weight per component and channel in force for the
  read's session or earlier and known by the cut) and copies every weight it applied onto the value it moved
  (`trust_weight_applied_portion`; a factor's equal share goes there too). `--trust` prints the newest ledger per
  factor, dial and channel.

## 9. The phone feed

`live/latest.json` is written by the live read job after every read (atomic replace) and read by the phone through
`app/server/app_server.py`, which also serves `/bars.json` (`{trade_date, bars: [{at, close}]}`, the session's
1-minute SPX closes read straight from Mirai's bar file, rebuilt each time that file grows, so the price line moves
every minute between reads) and `/price.stream` (server-sent events, one `data: {"price", "at"}` each time the live
price file changes, polled every 250 ms, a comment line every 15 s, at most four streams at once, each closed after
30 minutes). The keys below are the whole contract: `engine/feed/live_feed.py` writes exactly
these and `app/web/app.js` reads nothing else. Floats are at six decimals, moments on the New York clock.

Top level: `status` (`built`, `blind`, `thin`) and `status_reason` (`cut_bar_missing`, `range_failed`,
`push_pull_failed`, `grade_failed`, comma-joined), `cut_at`, `feeds` (per feed, the live price first: `status` live /
partial / blind, `age_s`, `as_of` (the newest input's moment; for the calendar, the last day it covers), `cadence`
(live, minute, once_a_day, hand_kept) and `note`: the calendar feed's horizon words, null elsewhere; the live price's
row is filled from its file as the feed is written, live inside 10 s), `login` (`state`, `hours_left`,
`expires_at`), `session` (`opens_at`, where the Read chart's time axis may start; `closes_at`,
`next_session_opens_at`: so the phone can tell closed from no read today; `closing_window_from`, 15 minutes
before the close, from when the last-15-minutes band widener can fire), `nightly` (`done_for`, `expected`: the
session before this one), `range` (below; null with `status_reason = range_failed`) and `values` (every
observation of the read by `measure` or `measure|subject` key, blanks left out). When the live job crashes the file is
replaced by `{status: failed, status_reason, failed_at, session}`, so a stale `cut_at` is never silent.

`range`: `price`, `read_at`, `minutes_left`, `speed`, `speed_fast`, `speed_slow`, `straddle`, `releases` (the day's
timed economic releases known at the cut, each `name` and `at`), `fed_decision_at` (the day's Fed decision known at
the cut, null on other days), `band_portion` (0.8, the share the band promises), `push_pull` (one entry per factor in
the registry's order: `label`, the plain question the phone asks; `name`; `answer`, the short answer from the factor's
fixed words, null when blank; `value`, the exact figure in words, shown when the row is tapped; `lean`; `side`
(`push`, `pull`, `even`, null when blank); `reason` when blank; `weight_by_channel`, the factor's share;
`points_by_channel`, its push in points, null when blank or the channel has no swing; `live`, null except on
`price_trend`: `follows` (`spx_live_price`), `since_price` (the prior close), `tilt`, `scale`, `even_zone` and
`words`, the rule the phone moves it by between reads; the list is empty when the push/pull step crashed), `wind`
(`levels`, each `[name, below]` with the last `below` null, and `dot_steps`), `channels` and `windows`.

`channels` is keyed `5_minutes` / `30_minutes` / `60_minutes`; each carries `swing`, `stretch`, `average_stretch`,
`fit_sessions`, `fit_windows`, `rung_points`, `stays_inside_share`, `options_swing`, `options_check`, `clipped`,
`ends_at`, `dials` (the band wideners, keyed `fed_window`, `release_window`, `closing_window`, `acceleration`,
`options_vs_tape`, each `raw`, `applied`, `weight`), `dials_product` (`raw`, `applied`: the wideners' product, the
`×1.00` the phone shows), `direction` (`chance_up_if_moves`, `weight_applied`,
`text`), `bands` (`80` and `average_80`: `half_width`, `low`, `high`), `status` and `reason`.

`windows` is built from the store after the read and its grades are written (`{status: failed, reason}` when that
step crashed); the 5-minute channel follows every read, the 30- and 60-minute channels the headline-lane reads:

- `current[channel]`: the channel's newest live window, measured the grade's way over the bars held between
  `window_from` and the cut. `read_at`, `price`, `window_from`, `ends_at`, `ended`, `clipped`, `status`, `reason`,
  `rung_points`, `bands`, `minutes_held`, `now` (`move` from the read price, `average_move`, `farthest_up`,
  `farthest_down`, floored at 0), `average_inside` (the running average inside its band; null until a bar is held)
  and `chain`: how that read worked its band out, read back from its stored values so Sight and Steps explain the
  read the Read page shows (`speed`, `speed_fast`, `speed_slow`, `swing`, `stretch`, `average_stretch`,
  `fit_sessions`, `fit_windows`, `stays_inside_share`, `dials`, `dials_product`, `straddle`, `minutes_left`,
  `options_swing`, `options_check`, `chance_up_if_moves`, `weight_applied`). Null when the day has no live read yet
  for that lane.
- `running[channel]`: the channel's earlier windows whose read is written but whose window has not ended at the cut
  (the live window's own read left out), newest first, what the Read page draws as dashed boxes and Running rows:
  `read_at`, `price`, `window_from`, `ends_at`, `half_width`, `bands`, `minutes_held`, and so far, measured the
  grade's way over the bars from `window_from` to the cut, `end` (the newest close), `high`, `low`, `end_move`,
  `farthest_up`, `farthest_down`; no grades. Usually 0-1 entries for 5 and 30 minutes, 0-2 for 60 (its windows overlap
  by half).
- `finished[channel]`: today's closed windows of the channel, newest first, what the Read page draws as past-read
  boxes and the list: `read_at`, `price`, `window_from`, `ends_at`, `status` (the window's label status),
  `half_width`, `end`, `high`, `low`, `end_move`, `farthest_up`, `farthest_down`, `grades` (`ends_inside`,
  `average_inside`, `never_left`: true / false, null when ungraded).
- `score[channel]`: over that channel's `finished` list, per grade word, `yes` and `graded`.

What the phone works out itself and the feed does not carry: the percent change in the header (price against
`values.prior_close_points`), the colour of a score (its share against `band_portion`, or `stays_inside_share` for
never left), "N of M past windows" (`stays_inside_share` x `fit_windows`, rounded), the band wideners' gauge angle
and each widener's standing words (Active, Due 10:00, From 15:45, Not Today, Watching · Not Counted Yet, Counted, from
`dials`, `releases`, `fed_decision_at` and `session.closing_window_from`), and the
header's words from `session` and its own clock (`Read 12:35 · Price Live` within eight minutes of `cut_at` while
ticks arrive, `Read 12:35 · Price 12:41:07` once the newest tick is 10 s old, `Late`, `Closed`, `No Read Yet Today`
once `next_session_opens_at` plus thirteen minutes has passed with no newer read), the live price and its percent
change, the live window's move and running average from its minute closes plus the newest tick, and the Feeds card's
words (an age for live and minute feeds, `Night · 09:27` from the futures' `as_of`, `To 12-31` from the calendar's,
`Checked 08:00` from the login's, a grey dot for a once-a-day or hand-kept feed fresh for its kind, `n Of m OK`).

## 10. Operations

### The jobs

| Job | When | What it writes |
|---|---|---|
| `com.kaze.read` | every 5 minutes, all day | a live read at the 09:35-15:55 slots of a trading day, else a log line |
| `com.kaze.nightly` | hourly at :15 (`--nightly-if-due`) | the nightly, once per session, from 17:00 New York on |
| `com.kaze.app` | at login, kept alive | the phone's web server on 127.0.0.1 |

All three run `engine/jobs/run_job.sh` from `/bin/bash -lc` with `TZ=America/New_York`, so the box's own time zone
never matters; only `job_log.py`'s rotation day and the phone's rendering use a local clock, and both take New York
(the phone its own zone, as documented in `app/README.md`).

A **live read** (`read_job.run_live`): the clock says whether the moment is a slot; the job waits for the slot's bar;
`collect.build_read` reads every Mirai file at the cut; `write_read` stores the bars, works the forecast out
(section 5) and writes the snapshot, its observations, decisions and values in one transaction; `close_windows`
grades every window of the day ended by the cut with all its bars held (section 7); `write_live_feed` writes
`latest.json` (section 9); one log line. A crash in the grading step is logged, the stored read's
`snapshot_status_reason` gains `grade_failed` and the feed's `windows` block says so. A crash anywhere else is
logged with its traceback and the feed becomes `{status: failed}`; a failure writing even that is one more log line.
Two reads in one slot (a hand run beside launchd's) make the second `live_rebuilt b2`; the store's live truth and
the parity check keep b1.

The **nightly** (`read_job.run_nightly`, in this order): refused while the session is open; `missed` rows for
unserved slots (on the session's own day only; a hand `--nightly` on a later day never marks a past session); the
session's bars for today and for every live session never rebuilt (closing bars as live when Kaze was live, the
whole session as rebuilt); the history fits (a rebuilt fit per stored session without one, the live fit for the
next trading day); every such session rebuilt (77 `history_rebuilt` reads); every open window of every session
settled; the trust ledger for the next session (138 rows); the parity check (one `nightly_check` snapshot per
session with both live and rebuilt reads, up to 20 mismatch lines logged; tape and scan differences tolerated); the
backup; `PRAGMA optimize`; the health line; `nightly_done.txt`. A box asleep at 17:15 catches up at the next :15 at
or after 17:00 the same day, else the next day's 17:15 (that day's missed slots are then never marked); a weekend
or holiday launch logs "not due". The health line counts live reads, missed slots, reads whose grading step failed,
bars, rebuilds, fits, what was settled, the ledger, parity and the backup file, and ends with `WARNING:` phrases
when the calendar or the holiday list is about to run out (below). The 5-minute job writes about 211 "market
closed" lines a day outside the session; that noise is expected.

**Hand runs** (`by_hand_log.txt`): `--at` rebuilds one moment (its slot when the moment is a slot's own cut,
HH:M5:59) and settles the day's ended windows; `--rebuild-day` rebuilds every slot of a session with the whole
session's bars, then its outcomes and grades (the earlier rebuilt ones replaced with their reads); `--backfill-bars`
loads every session's SPX bars Mirai holds, once; `--scoreboard`, `--pass-test` and `--trust` only print.

### Backup and restore

Every nightly: `VACUUM INTO KAZE_DATA/backup_in_progress/kaze-<session>.sqlite` (a consistent, compacted copy),
`PRAGMA integrity_check` on it, gzip beside it, move finished into the backup folder, keep the last five by name;
older copies go only after the new one passed. The folder is `KAZE_BACKUP_DIR` (best another disk or a cloud
folder); unset, it is `KAZE_DATA/backups` with a warning line every night; set but not a folder tonight (a cloud
folder signed out or evicted), that night's copy lands in `KAZE_DATA/backups` with one warning line and the reads
are never affected. Cloud placeholders (`.kaze-<day>.sqlite.gz.icloud`, an evicted copy) are neither counted nor
removed. The copy needs about 0.35 GB free in `KAZE_DATA` while it is made (the raw copy plus the gzip).

To **restore** a copy (the old `-wal` and `-shm` files must go too, or SQLite replays a stale write-ahead log into
the restored file):

    launchctl bootout gui/$(id -u)/com.kaze.read; launchctl bootout gui/$(id -u)/com.kaze.nightly
    launchctl bootout gui/$(id -u)/com.kaze.app
    cd "$KAZE_DATA" && rm -f kaze.sqlite kaze.sqlite-wal kaze.sqlite-shm
    gunzip -c "$KAZE_BACKUP_DIR/kaze-<session>.sqlite.gz" > kaze.sqlite
    cd ~/kaze && /usr/bin/python3 -m engine.jobs.read_job --trust   # opens the store, upgrades it if needed
    engine/jobs/install_jobs.sh

Then `rm nightly_done.txt` if the restored session is older than the file says, so the next nightly catches up.

### What breaks, and where a person sees it

- `.env` missing or a key blank: every job exits at import. Seen in `/tmp/com.kaze.*.launchd.log`; the phone goes
  `Late`, then `No Read Yet Today`.
- `KAZE_BACKUP_DIR` gone: the reads run; the nightly backs up beside the store. Seen as one `warning:` line in
  `nightly_log.txt`.
- A Mirai file path changes: the feed is `missing`. Seen on the phone's Feeds card: the dot `blind`, the pill
  `9 Of 10 Live`.
- A Mirai field is renamed: the value is blank with a wrong-ish reason, the feed still live. Seen only as `—` on the
  phone; parity cannot see it (both sides read the same file).
- The calendar runs out (2026-12-31): events, releases and half days unknown; the release and Fed dials never fire.
  Seen as the nightly's `WARNING:` from 14 days before, the Feeds card's note, then the calendar feed `blind`.
- The holiday list runs out (2027-12-31): the clock refuses the day; reads fail. Seen as the `WARNING:` from 30 days
  before; past it `read_log.txt` FAILED lines and a `failed` feed whose `session` clock is null (the phone says
  `Read Failed`, never a calm `Closed`).
- The Mac asleep at 17:00: the nightly catches up at the next :15 at or after 17:00. Seen in `nightly_log.txt`; the
  Feeds card says `Nightly Not Done For <session>` until then.
- A full disk: the read fails; even the failed feed may not write. Seen in `read_log.txt`; the phone says `Late`.
- The app server restarts mid-request: one dropped fetch. The phone says `Can't Reach The Mac` for a poll, then
  recovers; the price stream reopens on its own within seconds.
- Mirai's live price stops (the lob-collector down, the Schwab login lapsed): no ticks. The header says
  `Price 12:41:07` (the newest price's time), the Feeds card's `SPX Price` row says `Partial` with its age and the
  pill drops one; the price and the line keep moving each minute from the bar file and the 30 s poll.
- A nightly crash: hourly retries until midnight; no backup, no ledger, no marker. Seen in `nightly_log.txt` and on
  the Feeds card's nightly line.

### Yearly maintenance

- Before 2026-12-31: extend Mirai's event calendar (`covers_through`); the nightly warns from 2026-12-17 and the
  Feeds card says `Calendar Ends 2026-12-31`. Past it every read stores `does_calendar_cover_day = 0`, the News check
  says `Calendar Doesn't Cover Today`, and half days become unknown (a late-November half day would run slots after
  13:00).
- Before 2027-12-01: add the next year's NYSE holidays to `HOLIDAYS` in `engine/session_clock.py` and move
  `HOLIDAYS_LISTED_THROUGH`; the nightly warns from 30 days before.
- At about 100 live sessions: decide a retention rule for the store (none exists; the sizes are in `evidence.md`).

### Install, and the first Monday

1. `cd ~/kaze && git status` clean; `.env` has all five keys and `chmod 600`.
2. `/usr/bin/python3 -m engine.jobs.read_job --trust` prints "no trust ledger yet" (it opens the store, upgrades it
   if needed and seeds the registry, a harmless write).
3. `ls "$KAZE_BACKUP_DIR"` shows the latest copy and the folder is local (no cloud-only icon in Finder).
4. `engine/jobs/install_jobs.sh` (it refuses without `.env` or numpy): three `installed` lines;
   `launchctl print gui/$(id -u)/com.kaze.app | head -3` shows `state = running`;
   `curl -s -o /dev/null -w '%{http_code}\n' -u kaze:$KAZE_APP_TOKEN http://127.0.0.1:8791/latest.json` is 404
   until the first read and 401 without `-u`.
5. The phone: a tunnel to `127.0.0.1:8791`, `app/android/local.properties` with `KAZE_URL` and the token, the APK
   built and installed (`app/README.md`). It should say `No Live Feed Yet` until the first read.
6. Energy settings: the Mac must not sleep during the session; a sleeping Mac reads nothing and the phone says
   `No Read Yet Today`.
7. At 09:40: `tail -3 $KAZE_DATA/logs/read_log.txt` has a `live read 09:35 (headline_30, live_frozen b1) ...
   status built ... 0 windows closed` line, no `FAILED`; the phone header says `Live · 09:35`. The first 5-minute
   window closes on the 09:45 read, the first 30-minute window on the 10:10 read.
8. At 17:30: `tail -5 $KAZE_DATA/logs/nightly_log.txt` ends with one health line for the day (77 live reads, 0
   missed, the ledger's 138 rows all `too_few_sessions`, `parity: <day> A/B identical`, the backup file);
   `cat $KAZE_DATA/nightly_done.txt` is the day; the Feeds card's nightly line turns to `Nightly Done For` on the
   next read. Up to 20 `mismatch` lines above it are expected on the first day (a non-tape, non-scan mismatch is a
   collector bug to chase).
9. Renew the Schwab login on Monday after the close: it expires Tue 10-20 at about 14:43 ET, so the phone's Feeds
   card says `Schwab Login Renew Soon` all Monday (correct, not a fault) and Tuesday's first read shows
   `Good Until Mon 10-26`. The login lapses weekly from then on.
10. Wed 10-28 is an FOMC day (14:00): reads whose window ends after the decision carry an `owner_rule` variant and
    the phone shows Fed Decision Day `From 14:00`, then `Due ×2.6 · Not Counted Yet` (Sight's Band Wideners; the
    base band stays ×1.00 until the ledger gives the Fed widener weight); the first real test of the Fed path.
