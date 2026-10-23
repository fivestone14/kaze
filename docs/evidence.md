# Kaze evidence

Dated measurements that back the rules in `data_map.md`. Every number here goes stale with the next nightly;
the data map holds the rules, this page holds what was seen and when. Rerun the command named under each heading
to refresh it.

## The band's pass test (2026-10-18, `--pass-test`)

14 test days, 2026-09-29 to 2026-10-16, 60-session lookback, 20,000 day-resampled draws, seed 1. PASS needs the
95% interval of the mean coverage to hold 0.80 and sit inside 0.75 to 0.85.

| Column | Mean | 95% interval | Verdict |
|---|---|---|---|
| end band, 5 minutes | 0.8006 | 0.7755 to 0.8228 | PASS |
| end band, 30 minutes | 0.7987 | 0.7653 to 0.8340 | PASS |
| end band, 60 minutes | 0.8089 | 0.7607 to 0.8544 | holds 0.80, crosses 0.85: not yet decidable |
| average band, 5 minutes | 0.7950 | 0.7690 to 0.8237 | PASS |
| average band, 30 minutes | 0.8126 | 0.7764 to 0.8506 | holds 0.80, crosses 0.85: not yet decidable |
| average band, 60 minutes | 0.8071 | 0.7607 to 0.8516 | holds 0.80, crosses 0.85: not yet decidable |

Nothing is tuned to the test; the undecidable columns wait for more test days (14 days cannot bound coverage to
plus or minus 0.05).

## The scoreboard check (2026-10-16, `--scoreboard 2026-10-16`)

Every 5-minute read is graded, so a day's 30-minute reads pooled over both slices give the pass test's per-day
number: unclipped 51/71 plus clipped 6/6 = 57/77 = 0.740, the pass test's 0.740 for that day. The same session on
2026-10-18 after the schema step 10 migration: 5 minutes 61/76 + 1/1, 30 minutes 51/71 + 6/6, 60 minutes
41/65 + 12/12 (ends inside, unclipped + clipped), identical to the counts before the step.

## The closing-window re-test (2026-10-17, replayed bars, not the ledger)

Over the history's unclipped windows ending in the last 15 minutes of the session, the 5-minute end band held
57.9%; a time-of-day widening brought it to 77.4%. That is the evidence behind the `closing_window` dial; it is a
replay, so it earns the dial no weight.

## The time-of-day ratios and the cap (2026-10-16, read at 12:35:59)

| Horizon | `closing_window_multiplier` | `release_window_multiplier` | `width_cap_multiplier` |
|---|---|---|---|
| 5 minutes | 1.358 | 1.391 | 1.594 |
| 30 minutes | 1.274 | 1.399 | 1.649 |
| 60 minutes | 1.130 | 0.871 | 1.638 |

The release ratios come from the 10:00 releases since 2026-09-01; the 60-minute one stands below 1 (the fit is
data, there is no floor).

## The push/pull factors on one read (2026-10-16 12:35:59)

Price trend -53 since the close -> lean 0.300; straightness 0.13 with a net of -5.05 -> 0.474; breadth 50% advancing
-> 0.503; QQQ -1.4% against RSP -> 0.301; tape bull share 50% -> 0.500; yield +3.8 bp -> 0.628; Claude 28.5 / 45.5 /
26.0 -> 0.523. The signed leans saturate on an ordinary day (-53 points is already 0.300), which is why a lean is a
tilt, not a calibrated probability. Live, no factor counted before the ledger earned it weight, so the call was 0.5;
since 2026-10-23 every factor with a lean counts at an equal share and the same read rebuilds to 0.461455 (down 54%).

## Storage (2026-10-18, real store: 74 sessions of bars, all rebuilt and graded)

- Before schema step 5 the file was 1,439 MB, 94% of it the three long value tables whose text ids (a value id
  carried its outcome id, which carried the decision id, which carried the snapshot id) were 85% of each row and
  were stored twice more in the indexes.
- Step 5 (integer rowid keys, one unique index per value table, the header-copied columns dropped) took 8.5 s to
  migrate and a 0.9 s VACUUM, and left 298 MB: observations 82 MB of table pages + 41 MB of index for 733,208 rows,
  decision values 33 + 24 MB for 376,068, outcome values 24 + 17 MB for 427,350, outcome headers 28 + 15 MB for
  34,188, decisions 12 + 5 MB, snapshots 3 + 1 MB.
- Step 10 (the unused columns and words dropped, two measures renamed, every table rebuilt) took 3.8 s on the same
  store; the rebuilt tables left the file at 381 MB and a 0.9 s VACUUM brought it to 295 MB.
- One copy of a session is about 4.7 MB (observations 1.92, decision values 1.39, outcome values 0.56, outcomes
  0.47, decisions 0.18, bars 0.14, snapshots 0.05; bytes per row from `dbstat` times one session's rows). A live day
  (live plus the nightly's rebuilt copy) is about 9.4 MB, 100 sessions about 0.94 GB. A read writes 147
  observations live (145 rebuilt) and 123 decision values; the ledger writes 138 rows a night (0.11 MB).
- The nightly's gzipped backup is about 12% of the file (35 MB). There is no retention rule yet; one is due at about
  100 sessions.
