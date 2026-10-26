# The nightly's parity check: every live-truth observation of a session against its rebuilt twin, numbers within the
# stored precision and the rest exactly; tape and scan differences are tolerated because those files land late. The
# result is written as one nightly_check snapshot per session and summed up in the nightly's health line.
from datetime import date

from engine import session_clock
from engine.store import registry, store, store_queries

NUMBER_TOLERANCE = 10 ** -store.STORED_DECIMALS
# The watchlist's minute bars are written by Mirai only after the close: a live read blank on them beside a rebuild
# that read them is expected, not a broken rebuild (live reads take those inputs from the minute quotes since
# 2026-10-26).
TOLERATED_FEEDS = ("options_tape", "options_scan", "watchlist_bars")
PARITY_SUBJECT = "collect_observations.value"


def pair_matches(pair):
    """True when a live value and its rebuilt twin agree (numbers within the stored precision, the rest exactly)."""
    live, rebuilt = pair["live_value_number"], pair["rebuilt_value_number"]
    if live is not None or rebuilt is not None:
        both = live is not None and rebuilt is not None
        return both and abs(live - rebuilt) <= NUMBER_TOLERANCE * max(1.0, abs(live))
    return all(pair[f"live_{column}"] == pair[f"rebuilt_{column}"] for column in registry.VALUE_COLUMNS) \
        and pair["live_blank"] == pair["rebuilt_blank"]


def side_value(pair, side):
    """One side ('live' or 'rebuilt') of a parity pair as text: its value, or blank:<reason>."""
    value = store.observation_value({column: pair[f"{side}_{column}"] for column in registry.VALUE_COLUMNS})
    return value if value is not None else f"blank:{pair[f'{side}_blank']}"


def compare_session(conn, trade_date):
    """Compared count, the mismatches that count, and the tolerated ones, over a session's live and rebuilt reads."""
    mismatches, tolerated, compared = [], [], 0
    for pair in store_queries.parity_pairs(conn, trade_date):
        if pair["rebuilt_blank"] == "not_known_at_read":
            continue
        compared += 1
        if pair_matches(pair):
            continue
        (tolerated if pair["source_feed_name"] in TOLERATED_FEEDS else mismatches).append(dict(pair))
    return compared, mismatches, tolerated


def write_check(conn, day, compared, mismatches, tolerated, half_days, written_at):
    """A nightly_check snapshot for the session with the parity measures as its observations."""
    cut_at = session_clock.session_close(date.fromisoformat(day), half_days)
    snapshot = store.snapshot_row_shared("nightly_check", session_clock.iso_utc(cut_at), day, "live_frozen",
                                         session_clock.iso_utc(written_at))
    identical = (compared - len(mismatches)) / compared if compared else None
    observations = [
        store.observation_row(snapshot, "reads_compared_count", compared),
        store.observation_row(snapshot, "values_mismatched_count", len(mismatches)),
        store.observation_row(snapshot, "tolerated_mismatches_count", len(tolerated)),
        store.observation_row(snapshot, "rebuild_identical_portion", identical,
                              subject_key=registry.subject_key(table_column=PARITY_SUBJECT),
                              blank_reason=None if identical is not None else registry.NOT_APPLICABLE),
    ]
    store.write_snapshot(conn, snapshot, observations)


def check_sessions(conn, half_days, written_at, mismatch_lines_kept):
    """Compare live with rebuilt reads for every session that has both and write the check rows."""
    # Returns the health line's phrase and the mismatch lines worth logging (the first few per session).
    summaries, lines = [], []
    for day in store_queries.days_with_live_and_rebuilt_reads(conn):
        compared, mismatches, tolerated = compare_session(conn, day)
        write_check(conn, day, compared, mismatches, tolerated, half_days, written_at)
        summaries.append(f"{day} {compared - len(mismatches)}/{compared} identical, "
                         f"{len(tolerated)} tape/scan differences tolerated")
        lines.extend(f"mismatch {day} {mismatch['cut_at']} {mismatch['measure_name']} {mismatch['subject_key']}: "
                     f"live {side_value(mismatch, 'live')} rebuilt {side_value(mismatch, 'rebuilt')}"
                     for mismatch in mismatches[:mismatch_lines_kept])
    phrase = "; ".join(summaries) if summaries else "no session with both live and rebuilt reads yet"
    return phrase, lines
