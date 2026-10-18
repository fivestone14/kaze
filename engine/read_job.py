# The entry point launchd runs: a live read every 5 minutes (collect, range forecast, store, live feed) and the nightly
# (fits, rebuild, parity); by hand it rebuilds a moment or a session, backfills bars, or prints the range pass test.
import argparse
import sys
import traceback
from datetime import date, timedelta

from engine import collect, job_log, live_feed, mirai_files, paths, range_fit, range_read, registry, session_clock
from engine import store

BAR_WAIT_SECONDS = 100
BAR_POLL_SECONDS = 5
MISMATCH_LINES_LOGGED = 20
JOB_READ, JOB_NIGHTLY, JOB_REBUILD = "read", "nightly", "rebuild"


def main():
    """Pick the run from the command line and do it; a crash is written to the job's log before the job exits 1."""
    parser = argparse.ArgumentParser(description="Kaze read job")
    parser.add_argument("--at", metavar="YYYY-MM-DD HH:MM:SS", help="rebuild one past moment (New York time)")
    parser.add_argument("--rebuild-day", metavar="YYYY-MM-DD", help="rebuild every 5-minute read of a session")
    parser.add_argument("--backfill-bars", action="store_true", help="load every available session's SPX bars")
    parser.add_argument("--nightly", action="store_true",
                        help="missed slots, closing bars, history fits, rebuild, parity, health line")
    parser.add_argument("--nightly-if-due", action="store_true", help="the nightly, only in the 17:00 New York hour")
    parser.add_argument("--pass-test", action="store_true", help="print the range band's walk-forward pass test")
    args = parser.parse_args()
    job = JOB_NIGHTLY if args.nightly or args.nightly_if_due else JOB_READ
    if args.at or args.rebuild_day or args.backfill_bars or args.pass_test:
        job = JOB_REBUILD
    try:
        if args.nightly_if_due:
            run_nightly_if_due()
        elif args.nightly:
            run_nightly()
        elif args.at:
            run_rebuild_moment(session_clock.parse_typed_eastern(args.at))
        elif args.rebuild_day:
            run_rebuild_day(date.fromisoformat(args.rebuild_day))
        elif args.backfill_bars:
            run_backfill_bars()
        elif args.pass_test:
            run_pass_test()
        else:
            run_live()
    except Exception:
        log(job, "FAILED\n" + traceback.format_exc().rstrip())
        sys.exit(1)


def log(job, line):
    """One line to the job's log; echoed only when a person watches a terminal, so launchd never gets a second copy."""
    job_log.write_line(job, line)
    if sys.stdout.isatty():
        print(line)


def run_live():
    """One live read for the slot launchd fired on; outside the session's slots it writes nothing but a log line."""
    now = session_clock.now_utc()
    day = session_clock.to_eastern(now).date()
    calendar, half_days = mirai_files.session_calendar()
    slot = session_clock.slot_at(now)
    if not session_clock.is_read_slot(day, slot, half_days):
        log(JOB_READ, f"market closed or not a read slot at {session_clock.iso_eastern(now)}; nothing written")
        return
    slot_status = "made" if session_clock.to_eastern(now).strftime("%H:%M") == slot else "late"
    cut_at = session_clock.cut_for_slot(day, slot)
    bar_arrived = wait_for_bar(day, cut_at)
    written_at = session_clock.now_utc()
    conn = store.open_store()
    snapshot, observations, bars, feed_states = collect.build_read(
        conn, cut_at, "live_frozen", written_at, slot, slot_status, calendar, half_days,
        login=mirai_files.read_login_state(), bar_arrived=bar_arrived)
    forecast, new_bars = write_read(conn, snapshot, observations, bars, cut_at)
    live_feed.write_live_feed(snapshot, observations, feed_states, forecast)
    reason = f", {snapshot['snapshot_status_reason']}" if snapshot["snapshot_status_reason"] else ""
    build = f"{snapshot['row_origin']} b{snapshot['build_attempt_number']}"
    log(JOB_READ, f"live read {slot} ({snapshot['read_lane']}, {build}) "
                  f"cut {session_clock.iso_eastern(cut_at)}: {len(observations)} observations, {new_bars} new bars, "
                  f"status {snapshot['snapshot_status']}{reason}, {band_summary(forecast)}, "
                  f"built in {snapshot['build_seconds']} s")


def write_read(conn, snapshot, observations, bars, cut_at):
    """Store a built read: its bars, then the range readings with the snapshot, then the range decisions."""
    new_bars = store.write_bars(conn, bars)
    forecast = range_read.forecast_read(conn, snapshot, observations, cut_at)
    observations.extend(range_read.reading_rows(snapshot, forecast))
    store.write_snapshot(conn, snapshot, observations)
    store.write_decisions(conn, *range_read.decision_rows(snapshot, forecast))
    return forecast, new_bars


def band_summary(forecast):
    """The 30-minute band in one phrase for a log line."""
    channel = forecast["channels"]["30_minutes"]
    if channel["half"] is None:
        return f"30-min band {channel['status']} ({channel['reason']})"
    return f"30-min band {channel['lo']:.2f} to {channel['hi']:.2f} (half {channel['half']:.4f}, {channel['status']})"


def wait_for_bar(day, cut_at):
    """Poll the bars file until the bar for the cut's minute has landed or the wait after that minute runs out."""
    wanted = cut_at.replace(second=0, microsecond=0)
    deadline = cut_at + timedelta(seconds=1 + BAR_WAIT_SECONDS)
    while True:
        bars = mirai_files.read_spx_bars(day, cut_at)
        if bars and bars[-1]["at"] == wanted:
            return True
        if session_clock.now_utc() >= deadline:
            return False
        session_clock.sleep_seconds(BAR_POLL_SECONDS)


def rebuild_read(conn, cut_at, slot, calendar, half_days):
    """Rebuild one read at a cut (history_rebuilt) with its bars and forecast; returns snapshot, forecast, new bars."""
    snapshot, observations, bars, _ = collect.build_read(
        conn, cut_at, "history_rebuilt", session_clock.now_utc(), slot, "made" if slot else "not_scheduled", calendar,
        half_days)
    forecast, new_bars = write_read(conn, snapshot, observations, bars, cut_at)
    return snapshot, forecast, new_bars


def run_rebuild_moment(cut_at):
    """Rebuild the read at one past moment on a trading day; it carries a slot when the moment is a slot's own cut."""
    day = session_clock.to_eastern(cut_at).date()
    if not session_clock.is_trading_day(day):
        log(JOB_REBUILD, f"{day} is not a trading day; nothing rebuilt")
        return
    calendar, half_days = mirai_files.session_calendar()
    conn = store.open_store()
    snapshot, forecast, new_bars = rebuild_read(conn, cut_at, session_clock.slot_for_cut(cut_at), calendar, half_days)
    log(JOB_REBUILD, f"rebuilt read at {session_clock.iso_eastern(cut_at)}: {new_bars} new bars, "
                     f"status {snapshot['snapshot_status']}, {band_summary(forecast)}, id {snapshot['snapshot_id']}")


def run_rebuild_day(day):
    """Rebuild every scheduled read of a session, then store the whole session's bars."""
    if not session_clock.is_trading_day(day):
        log(JOB_REBUILD, f"{day} is not a trading day; nothing rebuilt")
        return
    calendar, half_days = mirai_files.session_calendar()
    conn = store.open_store()
    reads, bars_total = rebuild_session(conn, day, calendar, half_days)
    log(JOB_REBUILD, f"rebuilt {day}: {reads} reads, {bars_total} new bars")


def rebuild_session(conn, day, calendar, half_days):
    """Every slot of a session as history_rebuilt rows plus the session's bars; returns the read and new-bar counts."""
    reads, bars_total = 0, 0
    for slot in session_clock.day_slots(day, half_days):
        new_bars = rebuild_read(conn, session_clock.cut_for_slot(day, slot), slot, calendar, half_days)[2]
        reads += 1
        bars_total += new_bars
    bars_total += write_session_bars(conn, day, "history_rebuilt", half_days, session_clock.now_utc())
    return reads, bars_total


def write_session_bars(conn, day, row_origin, half_days, written_at):
    """Store every bar of the session up to the close not stored yet, stamped by arrival when it came after its slot."""
    close = session_clock.session_close(day, half_days)
    bars = mirai_files.read_spx_bars(day, close)
    return store.write_bars(conn, collect.session_bar_rows(conn, bars, day, row_origin, written_at, written_at))


def run_backfill_bars():
    """Load every SPX session Mirai holds (plain or gzipped) as history_rebuilt bars known at the file's change time."""
    conn = store.open_store()
    folder = paths.mirai_data(mirai_files.SPX_BARS).parent
    sessions, bars_total = 0, 0
    for path in sorted(folder.glob("*.jsonl*")):
        day = path.name[:10]
        known_at = session_clock.iso_utc(session_clock.file_modified_at(path))
        rows = [store.bar_price_row(bar, day, "history_rebuilt",
                                    session_clock.iso_utc(session_clock.parse_iso(bar["ts"])),
                                    known_at, "file_modified_at") for bar in mirai_files.read_jsonl(path)]
        if rows:
            sessions += 1
            bars_total += store.write_bars(conn, rows)
    log(JOB_REBUILD, f"backfilled bars: {sessions} sessions found, {bars_total} new bars stored")


def run_nightly_if_due():
    """The nightly, but only in the 17:00 New York hour (launchd fires this hourly, whatever the box's time zone)."""
    now = session_clock.now_utc()
    if session_clock.to_eastern(now).hour != session_clock.NIGHTLY_HOUR_EASTERN:
        log(JOB_NIGHTLY, f"not the nightly hour at {session_clock.iso_eastern(now)}; nothing done")
        return
    run_nightly()


def run_nightly():
    """Missed slots (on the session's own day), closing bars, history fits, rebuild, parity with live, one log line."""
    conn = store.open_store()
    calendar, half_days = mirai_files.session_calendar()
    written_at = session_clock.now_utc()
    today = session_clock.latest_trading_day(session_clock.to_eastern(written_at).date())
    own_session = today == session_clock.to_eastern(written_at).date()
    missed = write_missed_slots(conn, today, half_days, written_at) if own_session else 0
    live_reads = store.live_reads_count(conn, today.isoformat())
    closing_bars = write_session_bars(conn, today, "live_frozen", half_days, written_at) if live_reads else 0
    next_session = session_clock.next_trading_day(today)
    fits_rebuilt, fit_written = range_fit.write_fits(conn, next_session, written_at)
    rebuilt_reads, _ = rebuild_session(conn, today, calendar, half_days)
    summaries = []
    for day in store.days_with_live_and_rebuilt_reads(conn):
        compared, mismatches, tolerated = store.parity_result(conn, day)
        write_parity_check(conn, day, compared, mismatches, tolerated, half_days, written_at)
        summaries.append(f"{day} {compared - len(mismatches)}/{compared} identical, "
                         f"{len(tolerated)} tape/scan differences tolerated")
        for mismatch in mismatches[:MISMATCH_LINES_LOGGED]:
            log(JOB_NIGHTLY, f"mismatch {day} {mismatch['cut_at']} {mismatch['measure_name']} "
                             f"{mismatch['subject_key']}: "
                             f"live {store.parity_side_value(mismatch, 'live')} "
                             f"rebuilt {store.parity_side_value(mismatch, 'rebuilt')}")
    live_bars = store.bar_count(conn, today.isoformat(), "live_frozen")
    parity = "; ".join(summaries) if summaries else "no session with both live and rebuilt reads yet"
    fits = f"{fits_rebuilt} past fits added, fit for {next_session} {'written' if fit_written else 'not possible'}"
    log(JOB_NIGHTLY, f"nightly {today}: {live_reads} live reads, {missed} missed slots marked, "
                     f"{rebuilt_reads} reads rebuilt, "
                     f"{live_bars} live bars ({closing_bars} added at the close); {fits}; parity: {parity}")


def run_pass_test():
    """Print the Phase 2 pass test: band coverage walk-forward at the 60-session lookback, then 20 for information."""
    conn = store.open_store()
    for lookback, heading in ((registry.FIT_LOOKBACK_SESSIONS, "Pass test"),
                              (range_fit.INFORMATION_LOOKBACK_SESSIONS, "For information only")):
        print("\n".join(range_fit.pass_test_lines(conn, lookback, heading)))
        print()


def write_missed_slots(conn, day, half_days, written_at):
    """A missed snapshot row for every scheduled slot no live read served; returns how many were added."""
    served = store.live_slots_served(conn, day.isoformat())
    added = 0
    for slot in session_clock.day_slots(day, half_days):
        if slot in served:
            continue
        snapshot = collect.snapshot_row_for_read(session_clock.cut_for_slot(day, slot), "live_frozen", written_at, slot,
                                             "missed")
        snapshot.update({"snapshot_status": "missed", "snapshot_status_reason": "no_live_read",
                         "slot_lateness_minutes": None})
        added += store.write_missed_snapshot(conn, snapshot)
    return added


def write_parity_check(conn, day, compared, mismatches, tolerated, half_days, written_at):
    """A nightly_check snapshot for the session with the parity measures as its observations."""
    commit, dirty = collect.git_head()
    cut_at = session_clock.session_close(date.fromisoformat(day), half_days)
    snapshot = store.base_snapshot_row("nightly_check", session_clock.iso_utc(cut_at), day, "live_frozen",
                                  session_clock.iso_utc(written_at), commit, dirty)
    identical = (compared - len(mismatches)) / compared if compared else None
    observations = [
        store.observation_row(snapshot, "reads_compared_count", compared),
        store.observation_row(snapshot, "values_mismatched_count", len(mismatches)),
        store.observation_row(snapshot, "tolerated_mismatches_count", len(tolerated)),
        store.observation_row(snapshot, "rebuild_identical_portion", identical,
                              subject_key=registry.subject_key(table_column="collect_observations.value"),
                              blank_reason=None if identical is not None else "not_applicable"),
    ]
    store.write_snapshot(conn, snapshot, observations)


if __name__ == "__main__":
    main()
