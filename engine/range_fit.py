# The range engine's history side: the speed recipe on minute-indexed bars, the windows of past sessions measured on
# that same recipe, the nightly fit of the band multipliers (one nightly_history_fit cut per session served) and the
# Phase 2 pass test with its day-resampled interval.
import math
from datetime import date, timedelta

import numpy as np

from engine import registry, session_clock, store

FARTHEST = "farthest"
WINDOW_MOVES = (registry.BAND, registry.AVERAGE_BAND, FARTHEST)
BOOTSTRAP_DRAWS = 20000
BOOTSTRAP_SEED = 1
PASS_INTERVAL_PERCENTILES = (2.5, 97.5)
PASS_BAND_BOUNDS = (0.75, 0.85)
INFORMATION_LOOKBACK_SESSIONS = 20
HISTORY_CACHE = {}


# ---- The recipe on one session's bars ------------------------------------------------------------------------------

def minute_series(bars, trade_date):
    """Closes, highs and lows placed by minute of the session (NaN where a bar is missing), up to the last bar held."""
    open_at = session_clock.session_open(date.fromisoformat(trade_date))
    minutes = [int((session_clock.parse_iso(bar["priced_at"]) - open_at).total_seconds() // 60) for bar in bars]
    series = np.full((3, minutes[-1] + 1 if minutes else 0), np.nan)
    for minute, bar in zip(minutes, bars):
        series[:, minute] = (bar["close"], bar["high"], bar["low"])
    return series[0], series[1], series[2]


def speed(closes, half_life_minutes=registry.SPEED_HALF_LIFE_MINUTES):
    """Recency-weighted mean |1-minute close change|, newest weighs 1, halving per half-life; gaps left out."""
    changes = np.abs(np.diff(np.asarray(closes, dtype=float)))
    present = ~np.isnan(changes)
    if not present.any():
        return None
    weights = 0.5 ** (np.arange(changes.size - 1, -1, -1) / half_life_minutes)
    return float((changes[present] * weights[present]).sum() / weights[present].sum())


def swing_points(speed_now, minutes):
    """The typical move over a horizon: speed times the square root of its minutes."""
    return speed_now * math.sqrt(minutes)


def window_moves(closes, highs, lows, start, last):
    """|end move|, |average move| and farthest excursion from the start close to the last minute; None at a gap."""
    if np.isnan(closes[start]) or np.isnan(closes[last]):
        return None
    ahead = slice(start + 1, last + 1)
    start_close = closes[start]
    return {registry.BAND: abs(closes[last] - start_close),
            registry.AVERAGE_BAND: abs(np.nanmean(closes[ahead]) - start_close),
            FARTHEST: max(np.nanmax(highs[ahead]) - start_close, start_close - np.nanmin(lows[ahead]))}


# ---- History: the prior sessions' windows and the multipliers fitted on them ---------------------------------------

def history_before(conn, day, lookback=registry.FIT_LOOKBACK_SESSIONS):
    """The last full sessions before a day, cached: sorted window moves in swings per horizon, sessions, last bar."""
    key = (day, lookback)
    if key not in HISTORY_CACHE:
        HISTORY_CACHE[key] = build_history(conn, day, lookback)
    return HISTORY_CACHE[key]


def build_history(conn, day, lookback):
    """history_before without the cache: each full session's windows, every 5 minutes from minute 30, in swings."""
    sessions = [session for session in store.full_sessions(conn) if session < day.isoformat()][-lookback:]
    table = {name: {move: [] for move in WINDOW_MOVES} for name in registry.HORIZON_MINUTES}
    last_bar_at = None
    longest = max(registry.HORIZON_MINUTES.values())
    for session in sessions:
        bars = store.session_bars(conn, session)
        last_bar_at = bars[-1]["priced_at"]
        closes, highs, lows = minute_series(bars, session)
        # the last start is the longest horizon before the session's last minute, so every window ends on a held bar
        starts = range(registry.FIT_FIRST_WINDOW_MINUTE, closes.size - longest, registry.FIT_WINDOW_STEP_MINUTES)
        for start in starts:
            start_speed = speed(closes[:start + 1])
            if not start_speed:
                continue
            for name, minutes in registry.HORIZON_MINUTES.items():
                moves = window_moves(closes, highs, lows, start, start + minutes)
                if moves is None:
                    continue
                swing = swing_points(start_speed, minutes)
                for move, value in moves.items():
                    table[name][move].append(value / swing)
    windows = {name: {move: np.sort(np.array(values)) for move, values in moves.items()}
               for name, moves in table.items()}
    return {"sessions": sessions, "last_bar_at": last_bar_at, "windows": windows}


def band_multiplier(sorted_ratios):
    """The swings that cover the band's share of past windows: the 80% mark of the sorted ratios, or None if empty."""
    if sorted_ratios.size == 0:
        return None
    return float(sorted_ratios[int(registry.BAND_PORTION * sorted_ratios.size)])


def horizon_fit(end, average, sessions, windows):
    """One horizon's fit in the shape the fit writes and the read reads back: both multipliers, shrink, counts."""
    return {registry.BAND: end, registry.AVERAGE_BAND: average, "shrink": average / end if end else None,
            "sessions": sessions, "windows": windows}


def multipliers_from_history(history):
    """Per horizon, the end and average band multipliers fitted on the history, with the sample counts."""
    return {name: horizon_fit(band_multiplier(moves[registry.BAND]), band_multiplier(moves[registry.AVERAGE_BAND]),
                              len(history["sessions"]), int(moves[registry.BAND].size))
            for name, moves in history["windows"].items()}


def never_leaves_share(history, horizon_name, rung_points, swing):
    """Share of the history's windows whose farthest excursion stayed inside the rung at today's swing."""
    farthest = history["windows"][horizon_name][FARTHEST]
    if farthest.size == 0 or not swing:
        return None
    return float(np.searchsorted(farthest, rung_points / swing, side="left") / farthest.size)


# ---- The nightly fit rows ------------------------------------------------------------------------------------------

def fit_snapshot_rows(conn, served_day, row_origin, written_at):
    """The fit snapshot serving a session with its observations (thin under the lookback), or None with no history."""
    history = history_before(conn, served_day)
    if not history["sessions"]:
        return None
    last_bar_ends_at = session_clock.parse_iso(history["last_bar_at"]) + timedelta(
        seconds=session_clock.BAR_LENGTH_SECONDS)
    snapshot = store.base_snapshot_row(registry.FIT_SNAPSHOT_KIND, session_clock.iso_utc(last_bar_ends_at),
                                       served_day.isoformat(), row_origin, session_clock.iso_utc(written_at),
                                       builder=(registry.RANGE_ENGINE_NAME, registry.RANGE_ENGINE_VERSION))
    if row_origin == "history_rebuilt":
        landed = last_bar_ends_at + timedelta(seconds=registry.BAR_ARRIVAL_SECONDS)
        snapshot.update({"known_at": session_clock.iso_utc(landed), "known_at_rule": registry.BAR_ARRIVAL_RULE})
    if len(history["sessions"]) < registry.FIT_LOOKBACK_SESSIONS:
        snapshot["snapshot_status"] = "thin"
        snapshot["snapshot_status_reason"] = f"{len(history['sessions'])}_of_{registry.FIT_LOOKBACK_SESSIONS}_sessions"
    observations = []
    for name, fit in multipliers_from_history(history).items():
        for band in registry.BAND_PRICE_MEASURE:
            subject = registry.subject_key(band=band, horizon=name)
            observations.append(store.observation_row(snapshot, "band_multiplier", store.rounded(fit[band]), subject))
            observations.append(store.observation_row(snapshot, "stretch_recipe_name", registry.STRETCH_RECIPE[band],
                                                      subject))
        subject = registry.subject_key(horizon=name)
        observations.append(store.observation_row(snapshot, "average_price_shrink_ratio", store.rounded(fit["shrink"]),
                                                  subject))
        observations.append(store.observation_row(snapshot, "fit_sessions_count", fit["sessions"], subject))
        observations.append(store.observation_row(snapshot, "fit_windows_count", fit["windows"], subject))
    return snapshot, observations


def write_fits(conn, next_session, written_at):
    """A rebuilt fit per stored session lacking one, then the next session's live fit unless as fresh a one exists."""
    served = store.sessions_served_by_a_fit(conn)
    rebuilt = 0
    for day, _ in store.sessions_with_bars(conn):
        if day in served:
            continue
        rows = fit_snapshot_rows(conn, date.fromisoformat(day), "history_rebuilt", written_at)
        if rows is not None:
            store.write_snapshot(conn, *rows)
            rebuilt += 1
    rows = fit_snapshot_rows(conn, next_session, "live_frozen", written_at)
    existing = store.newest_fit_serving(conn, next_session.isoformat())
    if rows is None or (existing is not None and existing["cut_at"] >= rows[0]["cut_at"]):
        return rebuilt, 0
    store.write_snapshot(conn, *rows)
    return rebuilt, 1


# ---- The pass test -------------------------------------------------------------------------------------------------

def day_reads(bars, trade_date, fits):
    """Every 5-minute read of a session against the fits: (horizon, band, ended inside, window clipped at the close)."""
    closes, highs, lows = minute_series(bars, trade_date)
    reads = []
    for start in range(session_clock.FIRST_SLOT_MINUTES_AFTER_OPEN, closes.size, session_clock.SLOT_MINUTES):
        read_speed = speed(closes[:start + 1])
        if read_speed is None:
            continue
        for name, minutes in registry.HORIZON_MINUTES.items():
            last = min(start + minutes, closes.size - 1)
            moves = window_moves(closes, highs, lows, start, last)
            if moves is None:
                continue
            swing = swing_points(read_speed, minutes)
            for band in registry.BAND_PRICE_MEASURE:
                if fits[name][band] is not None:
                    reads.append((name, band, moves[band] <= swing * fits[name][band], last < start + minutes))
    return reads


def pass_test(conn, lookback):
    """Walk-forward reads of every session with a full lookback of full sessions behind it, by day."""
    reads_by_day = {}
    for day, _ in store.sessions_with_bars(conn):
        history = history_before(conn, date.fromisoformat(day), lookback)
        if len(history["sessions"]) < lookback:
            continue
        reads_by_day[day] = day_reads(store.session_bars(conn, day), day, multipliers_from_history(history))
    return reads_by_day


def coverage(reads, column, clipped=None):
    """Share of a column's reads that ended inside, over all reads or only the clipped or unclipped ones."""
    picked = [inside for name, band, inside, was_clipped in reads
              if (name, band) == column and (clipped is None or was_clipped == clipped)]
    return (float(np.mean(picked)) if picked else None), len(picked)


def bootstrap_interval(values):
    """Mean coverage and its day-resampled interval: the mean of each draw's days, at the interval's percentiles."""
    draws = np.random.default_rng(BOOTSTRAP_SEED).choice(values, size=(BOOTSTRAP_DRAWS, len(values)), replace=True)
    low, high = np.percentile(draws.mean(axis=1), PASS_INTERVAL_PERCENTILES)
    return float(np.mean(values)), float(low), float(high)


def pass_verdict(low, high):
    """PASS when the interval holds the band's share inside the bounds; holding the share but too wide is undecided."""
    holds_share = low <= registry.BAND_PORTION <= high
    bounded = PASS_BAND_BOUNDS[0] <= low and high <= PASS_BAND_BOUNDS[1]
    if holds_share and bounded:
        return "PASS"
    return "FAIL (not yet decidable: interval wider than the bounds)" if holds_share else "FAIL"


def pass_test_lines(conn, lookback, heading):
    """The pass test printed: per-day coverage per column, then mean, interval and verdict, then the clipped slice."""
    reads_by_day = pass_test(conn, lookback)
    columns = [(name, band) for band in registry.BAND_PRICE_MEASURE for name in registry.HORIZON_MINUTES]
    labels = {registry.BAND: "end", registry.AVERAGE_BAND: "avg"}
    label = {column: f"{labels[column[1]]}{registry.HORIZON_MINUTES[column[0]]}" for column in columns}
    lines = [f"{heading}: {lookback}-session lookback, {BOOTSTRAP_DRAWS} day-resampled draws, seed {BOOTSTRAP_SEED}",
             f"test days: {len(reads_by_day)} - interval width is driven by the number of days",
             "day         " + " ".join(label[column].rjust(8) for column in columns)]
    for day, reads in reads_by_day.items():
        lines.append(f"{day}  " + " ".join(f"{coverage(reads, column)[0]:8.3f}" for column in columns))
    for column in columns:
        values = [coverage(reads, column)[0] for reads in reads_by_day.values()]
        mean, low, high = bootstrap_interval(values)
        lines.append(f"{label[column]}: mean {mean:.4f}, 95% interval {low:.4f} to {high:.4f}, "
                     f"{pass_verdict(low, high)}")
    lines.append("clipped windows (capped at the close) as their own slice, pooled over the test days:")
    for column in columns:
        pooled = [read for reads in reads_by_day.values() for read in reads]
        unclipped, unclipped_count = coverage(pooled, column, clipped=False)
        clipped, clipped_count = coverage(pooled, column, clipped=True)
        clipped_text = f"{clipped:.3f} over {clipped_count}" if clipped is not None else "none"
        lines.append(f"{label[column]}: unclipped {unclipped:.3f} over {unclipped_count} windows, "
                     f"clipped {clipped_text}")
    return lines
