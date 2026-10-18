# Kaze data map

The one page to look Kaze's data up on. Everything Kaze keeps lives in one SQLite file, `kaze.sqlite`, in the folder
`KAZE_DATA` points to (set in `.env`, never in the repo), beside a small live feed for the phone and the jobs' logs.
Mirai's files under `KAZE_MIRAI_HOME` are the source of every market number; Kaze reads them and never writes there.
Both folders must exist: the engine refuses to start (exit 1, one clear line) when `.env` is missing, a variable is
blank, or either path is not a folder.

## The design and what is built of it

The target is the four-pillar store of 2026-10-16: fifteen tables defined by two registries, every value stored long as
measure x subject x value x blank reason x known_at. `engine/store.py` creates only the tables and columns the phases
so far write (schema version 5, kept by hand with `NOT NULL` on keys and `CHECK` lists rendered from the registry's
word lists); each phase adds its tables through the next numbered schema step, applied in one transaction when a store
is opened, and a step that changes a word list rebuilds the table in place (rows kept, children still pointing at it).
At every open the store refuses to run if any table's text differs from the code's, so a fresh store and a migrated
one are identical. The full design, for reference, is:

| Pillar | Table | What one row is | Built |
|---|---|---|---|
| Registry | `registry_measures` | one measure Kaze may store: name, plain meaning, unit, lane, the fact it carries, subjects, source feed, blank rule | Phase 1; seeded from `engine/registry.py` on every open (a name dropped from the code is dropped from the table, refused while rows still use it) |
| Registry | `registry_components` | one versioned thing: a feed, the Schwab login, the collector, the range engine, a read lane | Phase 1 |
| Collect | `collect_snapshots` | one cut: a read at a moment, a nightly check, or a nightly history fit, with its slot, lane, origin, attempt, builder commit, source-file byte lengths, health counts, content hash | Phase 1; steps 2 and 3 rebuilt it for the `nightly_history_fit` kind and the `thin` status |
| Collect | `collect_observations` | one measure x subject at one cut, with its feed's status and age at the cut | Phase 1 |
| Collect | `collect_market_prices` | one price record; for now one SPX 1-minute bar with the moment it was known | Phase 1 (bar columns only) |
| Collect | `collect_calendar_and_events` | one dated fact: a scheduled event or a half-day session, with when the station first held it | Phase 1 (events and half days) |
| Train | `train_feature_set_columns`, `train_examples`, `train_splits` | the frozen recipe of a model row, the model-ready rows, the walk-forward day roles | later |
| Reads | `reads_decisions`, `reads_decision_values` | one decision header (for now a `range_forecast` per channel) and every number, word or id inside it | Phase 2 (the columns the range engine fills) |
| Reads | `reads_trade_legs` | the legs of a trade-shaped decision | later |
| Results | `results_outcomes`, `results_outcome_values` | one outcome header (a `market_window`: what happened after a read over one horizon; a `forecast_grade`: how one decision graded against it) and every fact or grade inside it | Phase 3 (the columns the window and grade rules fill) |
| Results | `results_evaluations` | the trust weights earned and the skill judgements made over many results | later (Phase 4's trust ledger) |

The table text lives in `engine/store_schema.py`; `engine/store.py` shapes, checks, writes and reads rows. Since
schema step 5 the three header tables (`collect_snapshots`, `reads_decisions`, `results_outcomes`) carry an integer
rowid primary key beside their design text id (unique), and the three long value tables (`collect_observations`,
`reads_decision_values`, `results_outcome_values`) hang off them by that rowid with one unique index on the design's
own grain (parent, measure, version, subject, and the value role or value origin) and no text id or header column of
their own: a value row's design id (`<parent id>|<measure>|<version>|<subject>[|<role>]`) and its day, origin and
scope are read off its parent. That cut the store by about 60% (see Storage). Kaze's own additions to the design's
columns: on `collect_snapshots`, `newest_input_market_at` (the newest input any observation of the cut used; the store
refuses it past `cut_at`) and `source_files_json_text` (the byte length of each source file at a live read, so a
rebuild of that cut reads only what the live read could have seen); on `reads_decisions` and `results_outcomes`,
`grade_start_rule_name` beside the design's `grade_start_rule_version` (the name `at_read` and the version `1`, so a
second start rule or version can never pool with the first); on `results_outcomes`, `was_window_clipped` (the window
was capped at the close, so the scoreboard can split that slice without reading the decision).

## Rules every row follows

- **Blank, never 0.** A measure with no value gets a `blank_reason` from the fixed list (`feed_missing`, `feed_stale`,
  `not_formed_yet`, `none_left_today`, `not_known_at_read`, ...). Counts over a window (tape trades and premium,
  headlines, gap markers) are values only while the feed's newest line is inside its stale limit; otherwise they are
  blank with the feed's reason. A table constraint enforces exactly one of the four value columns or a blank reason.
- **Registered or refused.** `available_since_trade_date` on a measure is the first session Kaze served it
  (2026-10-16 for every written measure; rows rebuilt for earlier sessions carry the same recipe), blank for a
  measure no code writes yet. The writer refuses an unregistered measure, a subject kind the measure does not allow, a
  value in the wrong column, a number outside the measure's range, a word outside its list, and an input newer than the
  cut. Every row dict is checked against the table's columns, so a renamed column fails loudly.
- **Point in time.** `cut_at` is the market moment a row describes; `known_at` is when it became usable. Reads carry
  `known_at = written_at` (rule `written_at`) for every origin until a worst live lag has been measured. Bars seen in
  their own slot are known 62 seconds after the bar ends (`bar_end_plus_62_seconds`); bars that turn up later are
  stamped with the moment they were written (`written_at`); backfilled sessions with the file's change time
  (`file_modified_at`). An outcome's `label_resolved_at` is the event time its label became knowable: for a rebuilt
  window the `known_at` of the last-arriving bar inside it (13:07:02 for the 12:35 read's 30-minute window), for a
  live window and for any window with no bars the moment it was written; `written_at` always says when it was written.
  Backfilled bars (`file_modified_at`) carry the Mirai file's change time, so a rebuilt label on a backfilled day
  (every session before 2026-10-16) resolves at that file time, weeks after its window; schema step 6 re-stamped the
  stored rows the same way. A live read, its grading and its phone block use only bars known by the read's own write
  or its cut bar's arrival stamp, whichever is later (`bars_known_by`), so a read sees every bar it stored itself and
  nothing a later process stored. A rebuild run while the session is still open stamps the bars it writes with its
  write time (`written_at`), never `bar_end_plus_62_seconds`, so a live read that follows does not take them for
  bars it saw; a rebuild after the close stamps them by arrival. A rebuild sees every bar of its day.
- **Origins.** `row_origin` is `live_frozen` (written during the session, first attempt), `live_rebuilt` (a second run
  at the same live cut; the first is never deleted) or `history_rebuilt` (built later from Mirai's files as they stood
  at the cut; re-running replaces it, decisions and their outcomes and grades included). The nightly compares
  `live_frozen` with `history_rebuilt`. A decision carries its snapshot's origin and `run_kind` `live` or
  `history_replay`; its outcome and grade carry the same origin, so live and replay grades never mix in a count.
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
A history fit built on fewer than 60 full sessions carries `snapshot_status = thin`. A scheduled slot no live read
served gets a `snapshot_status = missed` row from the nightly, but only when the nightly
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
| range readings (Phase 2, from the store's bars) | `today_typical_1_minute_move_points` (speed: recency-weighted mean absolute 1-minute close change, half-life 15 minutes), `speed_fast_5_minute_half_life_points`, `speed_slow_30_minute_half_life_points`, `speed_fast_over_slow_ratio` (a weight-0 reading), `typical_swing_points` (horizon = 5_minutes, 30_minutes, 60_minutes; speed x the square root of the minutes), `options_implied_swing_points` and `options_over_tape_swing_ratio` (horizon; the straddle left x the square root of window minutes over minutes left, against the tape swing over the same minutes; a check never added in), `path_up_points`, `path_down_points`, `trend_straightness_ratio` (window_minutes = 15, 30, 60; weight-0 push readings) |
| nightly check | `reads_compared_count`, `values_mismatched_count`, `tolerated_mismatches_count`, `rebuild_identical_portion` |
| nightly history fit (Phase 2) | `band_multiplier` and `stretch_recipe_name` (band = 80, average_80; horizon), `fit_sessions_count`, `fit_windows_count`, `average_price_shrink_ratio` (horizon) |
| market window (Phase 3, `results_outcome_values`) | `grade_start_price_points`, `grade_start_bar_at`, `grade_start_rule_name`, `end_price_points`, `average_price_points`, `high_price_points`, `low_price_points`, `end_move_points`, `average_move_points`, `high_move_points`, `low_move_points`, `minutes_covered_count`, `stayed_inside_rung` (rung_points = 5, 10, 15) |
| forecast grade (Phase 3, `results_outcome_values`) | the three grade-start measures again, `was_inside_band` and `band_miss_points` (band = 80, average_80), `stayed_inside_rung` (the channel's own rung), `band_used_ratio` (band = 80; side = upper, lower) |

Every registered measure has a writer; a measure, a word and its schema step arrive together.

Every measure's lane and fact group are checked when the registry loads: one fact may reach the pull lane (how far)
or the push lane (which way), never both.

## The range forecast (Phase 2)

The recipe is the verified worked-out sheet of 2026-10-16 12:35:59; its fixed numbers live in `engine/registry.py`
(the `range_engine` component row carries them) and the 12:35 read reproduces the sheet to six decimals. Bars are read
by minute of the session (`engine/range_fit.py`, `minute_series`): a missing minute is a gap, so a change across it is
left out of the speed, a window starting or ending on it is skipped, and the path windows count real minutes.

1. **Speed** (today) = the recency-weighted mean of |1-minute close change| over the session's bars up to the cut,
   weight 0.5^(minutes ago / 15). The fast (half-life 5) and slow (half-life 30) speeds and `speed_fast_over_slow_ratio`
   are stored as weight-0 readings. Bars come from `collect_market_prices` (today's and history's), never from Mirai's
   files; for one minute with both a live and a rebuilt row the live row is taken (the values come from the same Mirai
   file, so a later Mirai correction is seen only on minutes without a live row).
2. **Swing** = speed x the square root of the horizon's minutes, for 5, 30 and 60.
3. **Stretch** (history) = the 80% mark of |end move| / swing over every window of the prior 60 full sessions, measured
   on the same recipe: speed at the window's start from that day's bars, windows every 5 minutes from minute 30. A full
   session holds a bar for every minute (390, or 210 on a listed half day; the rule `every_minute_held`); the full
   sessions are picked first and the
   last 60 of them taken, so a half day never shrinks the sample. The average band's multiplier is the same fit on
   |average price over the window - start| / swing. Both carry the recipe label (`stretch_recipe_name`) and the
   sample counts.
4. **End band** = read price (the last close at the cut) +/- swing x stretch; **average band** the same with the average
   multiplier. The grade-start rule is `at_read`: the window runs from the end of the read's minute (12:36:00 for the
   12:35 read) for the horizon, capped at the close; a capped window's decision is `clipped`, its band is still the
   full horizon's (as the sheet's recipe has it).
5. **Never-leaves share** = the share of the same history's windows whose farthest excursion (highs and lows) stayed
   inside +/- X in that window's swings, at today's swing; X = 5 / 10 / 15 points for 5 / 30 / 60 minutes. Computed from
   the history windows at read time (the same function the fit uses), so the decimals match the sheet; the owner-rule
   variant repeats it unchanged.
6. **Options check** = straddle left x the square root of (window minutes / minutes left), stored beside the tape swing
   over the same window minutes as `options_over_tape_swing_ratio`; shown, never added in. On a clipped window both
   swings cover the minutes to the close, so the ratio compares like with like.
7. **Dials** all x1.00 (`width_multiplier`, subject `adjustment`, roles `raw` and `applied`; the list is
   `registry.WIDTH_DIALS`): `fast_over_slow` carries the fast/slow ratio as its raw value; `fed_window` carries 2.6 as
   its raw value when the window ends after an `FOMC` decision known at the cut (the Fed window runs from the decision
   to the close). On such a read a second decision tagged `variant_name = owner_rule` (parent = the base decision)
   applies the 2.6 to both bands, so Phase 3 grades the band with and without it. No FOMC day in the store is known in
   time (the calendar was built 2026-09-27), so rebuilt reads before that date never carry the variant.
8. **Direction** = `chance_up_if_moves_probability` 0.5 with `trust_weight_applied_portion` 0: no input counts yet.

**Where it lands.** The readings (1, 2, 6 and the path: `path_up_points`, `path_down_points`,
`trend_straightness_ratio`) are observations on the read's own snapshot. Each read then writes one `reads_decisions`
row per channel (`decision_kind = range_forecast`, producer `range_engine@1`, `run_kind` `live` or `history_replay`,
`horizon_name`, `graded_price_measure = end_price`, `grade_start_rule_name = at_read` version `1`, the grade window,
`decision_status` ok / clipped / blind with its reason) and its `reads_decision_values`: `band_centre_points`, the
speed and swing used, `band_multiplier`, `stretch_recipe_name`, `fit_sessions_count`, `fit_windows_count` and
`fit_snapshot_id` (which fit it used), `band_half_width_points`, `band_low_points` and `band_high_points` (band = 80
and average_80), `chance_stays_inside_rung_probability` (horizon, rung_points), the two dials and the direction. A read
with fewer than two bars is `blind` (`speed_not_formed`, values blank `not_formed_yet`); one with no fit known at its
cut is `blind` (`no_fit_known_at_cut`, values blank `too_few_sessions`); one served by a thin fit stays `ok` (the
band is real) with `decision_status_reason = thin_fit_<n>_of_60`, which the phone block carries with `fit.status`. The
snapshot, its observations, its decisions
and their values are written in one transaction; if the range step itself fails, the snapshot still lands with
`snapshot_status_reason = range_failed` and no decisions, the traceback goes to the job's log, and the live feed says
so. Decision ids follow the design:
`range_forecast|<snapshot_id>|range_engine@1|<run_kind>:<variant or live>|<horizon>|end_price|-|whole|r0`.

**Fits and walk-forward.** The nightly writes one `nightly_history_fit` snapshot per session it serves (builder
`range_engine@1`): `trade_date` is the session served, `cut_at` the close of the last session in its sample, and
`known_at` that close plus 62 seconds (`bar_end_plus_62_seconds`) for a rebuilt fit or the write time for the live one
(always before the served open). A fit built on fewer than 60 full sessions has `snapshot_status = thin` (reason
`<n>_of_60_sessions`); reads still use it and say so. The nightly stores the session's bars once (as live rows when
Kaze was live that day, else rebuilt) before it fits,
then writes a rebuilt fit for every stored session that lacks one, then the live fit for the next trading day unless a
fit with the same or newer data already serves it (so a second nightly on one day adds nothing). A read uses the
newest fit with `trade_date <= its day` and `known_at <= its cut`, so a fit never includes the read's own day;
`--rebuild-day` and `--at` use the same rule. On a fresh store the order is therefore `--backfill-bars`, then
`--nightly` (the fits), then any rebuild.

**Pass test** (`--pass-test`). For every session with 60 full sessions behind it, the band is refitted on those
sessions and every 5-minute read of the day (09:35 on, windows capped at the close) is checked: did the window's end
(and its average) land inside that read's band? The day coverages are resampled 20,000 times (fixed seed, enough for
the interval to settle); PASS when the 95% interval of the mean contains 0.80 and sits inside 0.75-0.85; an interval
that holds 0.80 but breaks a bound is printed as FAIL, not yet decidable, because 14 days cannot bound coverage to
+/-0.05. Clipped windows are reported as their own slice. The same is printed with a 20-session lookback for
information only. On 2026-10-18 at HEAD (14 test days, 2026-09-29 to 2026-10-16): the headline 30-minute end band
passes (mean 0.7987, interval 0.7653-0.8340), the 5-minute end band passes (0.8006, 0.7755-0.8228) and the 5-minute
average band passes (0.7950, 0.7690-0.8237); the 60-minute end band (0.8089, 0.7607-0.8544), the 30-minute average band
(0.8126, 0.7764-0.8506) and the 60-minute average band (0.8071, 0.7607-0.8516) hold 0.80 but cross the 0.85 ceiling
and are not yet decidable until more test days exist. Nothing is tuned to the test.

## Outcomes and grades (Phase 3)

Every range decision window is closed from the store's bars and every decision graded against its own bands, under
rules the registry names and versions (`registry_components`: grade-start rule `at_read@1`, outcome rule
`window_from_bars@1`, grade rule `band_grade@1`); a changed rule is a new version and new rows, never an edit.

- **Grade from the read.** The grade-start rule is `at_read` version `1` (`grade_start_rule_name`,
  `grade_start_rule_version` on the decision and on every outcome): the start price is the read's last close (the
  snapshot's `spx_price_points`, `registry.GRADE_START_PRICE_MEASURE`, the price the band is centred on), the start bar
  is the bar that close came from, and the window runs from the end of the read's minute for the horizon, capped at
  the close (the decision's own `grade_window_starts_at` to `grade_window_ends_at`; 12:36:00 to 13:06:00 for the 12:35
  read). Every outcome and grade also stores the start price, its bar and the rule name as values
  (`grade_start_price_points`, `grade_start_bar_at`, `grade_start_rule_name`).
- **The window** (`results_outcomes.outcome_kind = market_window`, one per read and horizon, anchored on the read's
  snapshot, `judged_decision_id` blank): the bars whose minute lies inside the window (12:36 to 13:05, live rows
  preferred, `engine/range_fit.py` `window_facts`, the same measurement the history fit and the pass test use through
  `window_moves`). End price = the close of the window's last bar; average = the mean of the window's closes; high and
  low = the bars' highs and lows; the four moves are signed from the start price; `stayed_inside_rung` per rung (5,
  10, 15 points) says whether the farthest excursion on both sides stayed short of the rung; `minutes_covered_count`
  counts the minutes held (a clipped window holds fewer). `was_window_clipped` is set when the window is shorter than
  its horizon. `label_status` is `resolved`, `partial_bars` (the end bar held but a minute inside never came; the
  reason says `<held>_of_<wanted>_minutes_held`), `bars_missing` (no end bar, reason `end_bar_missing`; every value
  blank `bars_missing`) or `unpriceable` (the read had no price, reason `no_read_price`; the moves blank
  `not_applicable`). Only the first two count toward skill. The one word `bars_missing` is the label status, the
  blank reason and the value status of a window without its bars.
- **The grade** (`outcome_kind = forecast_grade`, one per decision, so the owner-rule variant is graded beside its
  base; `judged_decision_id` the decision, `market_outcome_id` its window, `graded_price_measure` copied from the
  decision): `was_inside_band` for band `80` (|end move| <= the end band's half-width) and `average_80` (|average move|
  <= the average band's half-width), `band_miss_points` per band (points past the edge, signed with the move, 0
  inside), `stayed_inside_rung` for the channel's rung (5 / 10 / 15 points for 5 / 30 / 60 minutes; the grade word
  `never_left` is this yes), `band_used_ratio` (band 80; `upper` = high move over the half-width, `lower` = the drop
  over it, each floored at 0; it is a ratio, not a portion, and runs past 1 by exactly how far price broke out that
  side) and the three grade-start values. A blind decision's grade is `unpriceable` with the
  decision's reason and blank values carrying the band's blank reason. Half-widths are the decision's stored values,
  so the owner-rule grade uses the x2.6 band.
- **When.** Each live read closes every window of the day whose end is at or before its cut and whose bars the
  store all holds (the 12:35 read's 30-minute window ends 13:06:00, so the 13:10 read, cut 13:10:59, closes it; a
  5-minute window closes two reads later). A window missing any bar, an end bar or a minute inside, stays open until
  the session is settled (a cut at or after the close), so a bar Mirai fills in late is graded in full and never
  frozen as partial. The nightly settles every open window of every session (after its bar write), as `partial_bars`
  or `bars_missing` when the bars never came; `--rebuild-day` and `--at` settle the rebuilt day's windows the same
  way. The window and its grades are written in one transaction, never replaced except with their rebuilt read.
- **Ids.** `<kind>|<anchor snapshot_id>|<judged decision_id or ->|<horizon>|<graded price or ->|<start rule>@<v>|
  <outcome rule>@<v>|-|<row_origin>|b<attempt>` (`at_read@1`, `window_from_bars@1`, `band_grade@1`); a value row has
  no stored id, its design id being `<outcome_id>|<measure>|<version>|<subject>`.

**Scoreboard** (`--scoreboard [YYYY-MM-DD]`, `engine/grade.py` over `store.grade_counts`). Per session, run kind
(`live` or `history_replay`, never pooled), channel, variant (`base` or `owner_rule`) and slice (`unclipped` or
`clipped`): ends inside, average inside and never left as yes/graded with the share, only grades under the current
rule versions (`band_grade@1`, start `at_read@1`), only first-built live reads; then the same pooled over the last 20
graded sessions, printed with `pooled` in the session column. What each word counts is the registry's one statement
(`GRADE_WORD_MEASURE`: the measure, its band subject and the value that says yes), which the phone block reads too.
Every 5-minute read is graded, so a day's 30-minute reads pooled over both slices give the pass test's per-day number
(2026-10-16: unclipped 51/71 plus clipped 6/6 = 57/77 = 0.740, the pass test's 0.740); the phone's score for a channel
counts the windows its finished list holds.

**Storage** (measured 2026-10-18 on the real store: 74 sessions of bars, all 74 rebuilt and graded). Under schema
step 4 the file was 1,439 MB, 94% of it the three long value tables whose text ids (a value id carried its outcome
id, which carried the decision id, which carried the snapshot id) were 85% of each row and were stored twice more in
the indexes. Step 5 (integer rowid keys, one unique index per value table, the header-copied columns dropped) took
8.5 s to migrate and a 0.9 s VACUUM, and left 298 MB: observations 82 MB of table pages + 41 MB of index for
733,208 rows, decision values 33 + 24 MB for 376,068, outcome values 24 + 17 MB for 427,350, outcome headers 28 + 15
MB for 34,188 (their text ids are the long ones left), decisions 12 + 5 MB, snapshots 3 + 1 MB. A rebuilt session of
77 reads (9,908 observations, 5,082 decision values, 231 windows and 231 grades with 5,775 outcome values) costs
4.0 MB; a live day writes that once live and once again rebuilt by the nightly, about 8 MB a day, so 100 sessions of
live running add about 0.8 GB. The nightly's gzipped backup is about 12% of the file (35 MB). There is no retention rule
yet; Will decides one at about 100 sessions.

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
| `live/latest.json` | the newest live read for the phone: `read_id`, `lane`, `slot`, `origin`, `status`, `status_reason`, `cut_at`, `known_at`, `feeds` (status live / partial / blind and age per feed), `login`, `range` (the forecast, see below; null with `status_reason = range_failed`), `values` (`measure` or `measure\|subject` keys, the range readings included), `blanks` (key to reason); when the live job crashes it is replaced by `{status: failed, status_reason, failed_at}`, so a stale `cut_at` is never silent | the live read job, after each read |
| `logs/read_log.txt` | one line per read-job run (including the quiet "market closed" lines) and any crash's traceback | the read job |
| `logs/nightly_log.txt` | one health line per nightly run, one line per parity mismatch, the backup warning when the copy shares the store's disk, and the hourly "not due" lines | the nightly job |
| `logs/rebuild_log.txt` | one line per hand run (`--at`, `--rebuild-day`, `--backfill-bars`; a `--scoreboard` or `--pass-test` crash) | hand runs |
| `nightly_done.txt` | the session the nightly last finished (the once-per-session rule reads it) | the nightly job |
| `backup_in_progress/` | where a backup copy is made, checked and gzipped before it moves, finished, to the backup folder; empty between nightlies | the nightly job |
| `backups/kaze-<session>.sqlite.gz` (or the same under `KAZE_BACKUP_DIR` when `.env` sets it) | the last 5 nightly copies of the store: `VACUUM INTO` a dated file, `integrity_check` on it, gzipped, then moved in whole; older copies go only after the new one passed | the nightly job |
| `logs/older/<day>/*.gz` | the logs of earlier days, kept 14 days | `engine/job_log.py` when a job first writes on a new day |
| `/tmp/com.kaze.read.launchd.log`, `/tmp/com.kaze.nightly.launchd.log` | only what Python could not catch (a missing `.env`, an import error) | launchd |

## Who writes what, and when

| Job | When | Writes |
|---|---|---|
| `python3 -m engine.read_job` (launchd `com.kaze.read`) | every 5 minutes; builds only at the 09:35-15:55 slots on trading days, else one log line | one `live_frozen` snapshot (or `live_rebuilt` on a rerun) with its observations and range readings, calendar facts, new bars, three `range_forecast` decisions (plus owner-rule variants in a Fed window) with their values; then the outcome and grades of every window of the day that ended by the cut with all its bars held; then `live/latest.json` (a crash in the grading step is logged, the stored read's `snapshot_status_reason` gains `grade_failed`, the feed's `status_reason` says so and its `windows` block is `{status: failed, reason}`) |
| `python3 -m engine.read_job --at "YYYY-MM-DD HH:MM:SS"` | by hand, trading days only | one `history_rebuilt` snapshot at that New York moment, the bars up to it, its range decisions, and the outcomes and grades of every window of that day ended and still open |
| `python3 -m engine.read_job --rebuild-day YYYY-MM-DD` | by hand | every slot of the session as `history_rebuilt` rows with their range decisions, the whole session's bars, then the session's outcomes and grades (the earlier rebuilt ones replaced with their reads); on a fresh store this is how a past session gets its grades |
| `python3 -m engine.read_job --backfill-bars` | by hand, once | every session's SPX bars Mirai holds, as `history_rebuilt` rows known at the file's change time |
| `python3 -m engine.read_job --nightly` (launchd `com.kaze.nightly` runs `--nightly-if-due` hourly at :15; it acts once per session, on the first launch from 17:00 New York on when `nightly_done.txt` does not name the latest session, so a box asleep at 17:15 catches up at 18:15 and a weekend or holiday launch does nothing; a hand `--nightly` is refused with one line while the session is open and is a no-op once the session is done) | 17:15 New York, or the first :15 after | `missed` rows for unserved slots (on the session's own day), then for today and for every live session that was never rebuilt: the session's bars (closing bars as live when Kaze was live, the whole session as rebuilt) and the session rebuilt; a rebuilt `nightly_history_fit` for every stored session without one and the live fit for the next trading day (unless as fresh a one exists); every open window of every session settled (live and rebuilt); one `nightly_check` snapshot per session with both live and rebuilt reads; the backup; `PRAGMA optimize`; the health line (live reads, missed slots, reads whose grading step failed, bars, rebuilds, fits, what was settled, parity, the backup file); `nightly_done.txt` |
| `python3 -m engine.read_job --scoreboard [YYYY-MM-DD]` | by hand | nothing; prints the grades per session, run kind, channel, variant and clipped slice (one session, or every graded one) and the pooled last 20 sessions |
| `python3 -m engine.read_job --pass-test` | by hand | nothing; prints the band's walk-forward coverage per day and channel, the converged interval, the verdict and the clipped slice |

The `range` block of `latest.json` is the forecast copied whole (floats at six decimals, moments on the New York clock):
`price`, `read_at`, `window_from` (end of the read's minute), `newest_bar_at`, `minutes_in`, `minutes_left`, `speed`,
`speed_fast`, `speed_slow`, `fast_over_slow`, `path` (15 / 30 / 60: `up`, `down`, `straightness`), `straddle`, `fit`
(`id`, `known_at`, `status`; null when none), `fed_decision_at`, `chance_up_if_moves` (0.5), `trust` (0) and
`channels` keyed `5_minutes` / `30_minutes` / `60_minutes`, each with `minutes`, `swing`, `stretch`, `average_stretch`,
`fit_sessions`, `fit_windows`, `rung_points`, `stays_inside_share`, `options_swing`, `options_over_tape`, `clipped`,
`ends_at`, `in_fed_window`, `dials` (`fed_window`, `fast_over_slow`: `raw`, `applied`), `bands` (`80` and `average_80`:
`half_width`, `low`, `high`), `owner_rule_bands` (the same at x2.6; null outside a Fed window), `status` and `reason`;
and, from Phase 3, `windows` (built by `engine/live_feed.py` from the store after the read and its grades are
written; `{status: failed, reason}` when that step crashed). Every channel key is `5_minutes` / `30_minutes` /
`60_minutes`; the 5-minute channel follows every read, the 30- and 60-minute channels the headline-lane reads (:05 and
:35), in `current`, `finished` and `score` alike:

- `current[channel]`: the channel's newest live window, measured the grade's way (`window_facts` over the bars held
  between `window_from` and the cut, never past `ends_at`). `read_id`, `read_at`, `price` (the read price the band and
  the grade start sit on), `window_from`, `ends_at`, `horizon_minutes`, `ended` (the cut has passed `ends_at`: the
  window is finished and only waits for its grade, or its lane missed a slot), `clipped`, `status`, `reason`,
  `rung_points`, `bands` and `owner_rule_bands` (the stored decision values), `minutes_held` (bars held since
  `window_from`, 0 at the read itself), `now` (`price` = the newest close at the cut and `move` from the read price,
  always present; `average` = the running mean of the window's closes, `average_move`, `high`, `low`, `farthest_up`,
  `farthest_down` (how far above and below the read price the window reached, floored at 0; the stored
  `high_move_points` / `low_move_points` keep the sign), null until the window's first bar is held) and `so_far`
  (`ends_inside`: `move` inside the end band's half-width; `average_inside`: `average_move` inside the average band's;
  `never_left`: neither side reached the rung yet; null until there is something to judge). Null when the day has no
  live read yet for that lane.
- `finished[channel]`: today's closed windows of the channel from the lane it follows, newest first, what the Read
  page draws as past-read boxes, gauges and the list: `read_id`, `read_at`, `price`, `window_from`, `ends_at`,
  `clipped`, `minutes_held`, `status` (the window's label status), `half_width` and `average_half_width` (the base
  decision's bands), `end`, `average`, `high`, `low` and their `_move` from the read price, `farthest_up`,
  `farthest_down` (floored at 0), `grades` (`ends_inside`, `average_inside`, `never_left`: true / false, null when
  ungraded) and `band_used` (`upper`, `lower`: the end band's half-widths used on each side, past 1 when price broke
  out).
- `score[channel]`: over that channel's `finished` list, per grade word, `yes` and `graded`.

The grade words and what yes means (`GRADE_WORD_MEASURE`), the window kinds and every list behind them live in
`engine/registry.py` (`OUTCOME_KINDS`, `LABEL_STATUSES`, `VALUE_STATUSES`, `BAND_SIDES`, `WINDOW_SIDES`).

`engine/jobs/install_jobs.sh` writes the two plists into `~/Library/LaunchAgents` with this repo's real path and
boots them (bootout, wait, bootstrap, enable).
