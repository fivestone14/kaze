# The entry point launchd runs: a live read every 5 minutes (collect, range forecast, store, grades, live feed) and
# the nightly (catch-up rebuilds, fits, every open window settled, the trust ledger, parity, backup); by hand it
# rebuilds a moment or a session, backfills bars, or prints the scoreboard, the range pass test or the trust ledger.
import argparse
import sys
import traceback
from datetime import date

from engine import paths, session_clock
from engine.collect import collect, mirai_files
from engine.feed import live_feed
from engine.grade import grade, parity, trust
from engine.jobs import job_log
from engine.push_pull import push_pull_read
from engine.range import range_fit, range_read
from engine.store import backup, components, store, store_queries

BAR_POLL_SECONDS = 5
MISMATCH_LINES_LOGGED = 20
JOB_READ, JOB_NIGHTLY, JOB_BY_HAND = "read", "nightly", "by_hand"
RANGE_FAILED, PUSH_PULL_FAILED, GRADE_FAILED = "range_failed", "push_pull_failed", "grade_failed"
WHOLE_SCOREBOARD = "every graded session"


def main():
    """Pick the run from the command line and do it; a crash is written to the job's log before the job exits 1."""
    parser = argparse.ArgumentParser(description="Kaze read job")
    parser.add_argument("--at", metavar="YYYY-MM-DD HH:MM:SS", help="rebuild one past moment (New York time)")
    parser.add_argument("--rebuild-day", metavar="YYYY-MM-DD", help="rebuild every 5-minute read of a session")
    parser.add_argument("--backfill-bars", action="store_true", help="load every available session's SPX bars")
    parser.add_argument("--nightly", action="store_true", help="missed slots, catch-up rebuilds, closing bars, "
                        "fits, grades, trust ledger, parity, backup, health line")
    parser.add_argument("--nightly-if-due", action="store_true",
                        help="the nightly, once a day, from 17:00 New York on, if it has not run today")
    parser.add_argument("--scoreboard", nargs="?", const=WHOLE_SCOREBOARD, metavar="YYYY-MM-DD",
                        help="print the grades per session and channel (one session, or every graded one)")
    parser.add_argument("--pass-test", action="store_true", help="print the range band's walk-forward pass test")
    parser.add_argument("--trust", action="store_true",
                        help="print the trust ledger: every factor and dial per channel with its verdict and weight")
    args = parser.parse_args()
    job = JOB_NIGHTLY if args.nightly or args.nightly_if_due else JOB_READ
    if args.at or args.rebuild_day or args.backfill_bars or args.scoreboard or args.pass_test or args.trust:
        job = JOB_BY_HAND
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
        elif args.scoreboard:
            run_scoreboard(None if args.scoreboard == WHOLE_SCOREBOARD else args.scoreboard)
        elif args.pass_test:
            run_pass_test()
        elif args.trust:
            run_trust()
        else:
            run_live()
    except Exception as failure:
        log(job, "FAILED\n" + traceback.format_exc().rstrip())
        if job == JOB_READ:
            write_failed_feed(f"{type(failure).__name__}: {failure}")
        sys.exit(1)


def log(job, line):
    """One line to the job's log; echoed only when a person watches a terminal, so launchd never gets a second copy."""
    job_log.write_line(job, line)
    if sys.stdout.isatty():
        print(line)


def write_failed_feed(reason):
    """Tell the phone the live read failed; a failure inside this (a full disk) is one more log line, never a crash."""
    try:
        live_feed.write_failed_feed(reason, mirai_files.session_calendar()[1])
    except Exception:
        log(JOB_READ, "writing the failed feed FAILED\n" + traceback.format_exc().rstrip())


# ---- The live read -------------------------------------------------------------------------------------------------

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
    forecast, new_bars = write_read(conn, snapshot, observations, bars, cut_at, JOB_READ)
    graded = grade_closed_windows(conn, snapshot, day, cut_at, session_clock.now_utc(), forecast)
    live_feed.write_live_feed(snapshot, observations, feed_states, forecast, calendar, half_days, nightly_done_for())
    reason = f", {snapshot['snapshot_status_reason']}" if snapshot["snapshot_status_reason"] else ""
    build = f"{snapshot['row_origin']} b{snapshot['build_attempt_number']}"
    log(JOB_READ, f"live read {slot} ({snapshot['read_lane']}, {build}) "
                  f"cut {session_clock.iso_eastern(cut_at)}: {len(observations)} observations, {new_bars} new bars, "
                  f"status {snapshot['snapshot_status']}{reason}, {band_summary(forecast)}, {graded}, "
                  f"built in {snapshot['build_seconds']} s")


def write_read(conn, snapshot, observations, bars, cut_at, job):
    """Store a read: bars, then snapshot, readings, factors and decisions in one transaction; a failed step marked."""
    # A failed range step leaves no decisions; a failed push/pull step leaves the band and decisions standing with
    # the push/pull block empty; either way the read carries the step's word in its status reason.
    new_bars = store.write_bars(conn, bars)
    store.claim_snapshot_id(conn, snapshot)
    forecast, decisions, values = None, (), ()
    try:
        weights = store_queries.trust_weights_known(conn, snapshot["trade_date"], snapshot["cut_at"])
        forecast = range_read.forecast_read(conn, snapshot, observations, cut_at, weights)
        try:
            push_pull_read.apply_push_pull(forecast, push_pull_read.factors(observations, forecast["path_moves"]))
        except Exception:
            log(job, "push/pull step FAILED\n" + traceback.format_exc().rstrip())
            push_pull_read.apply_push_pull(forecast, [])
            store.add_status_reason(snapshot, PUSH_PULL_FAILED)
        observations.extend(range_read.reading_rows(snapshot, observations, forecast))
        decisions, values = range_read.decision_rows(snapshot, forecast)
    except Exception:
        log(job, "range step FAILED\n" + traceback.format_exc().rstrip())
        forecast, decisions, values = None, (), ()
        store.add_status_reason(snapshot, RANGE_FAILED)
    store.write_snapshot(conn, snapshot, observations, decisions, values)
    return forecast, new_bars


def grade_closed_windows(conn, snapshot, day, cut_at, written_at, forecast):
    """After a live read: close the day's ended windows and put the windows block on the forecast."""
    # A crash is logged, stamped grade_failed on the stored read and carried to the phone.
    known_by = store.bars_known_by(snapshot, cut_at)
    try:
        windows, grades = grade.close_windows(conn, day, cut_at, written_at, known_by)
        if forecast is not None:
            forecast[live_feed.WINDOWS] = live_feed.windows_block(conn, day, cut_at, known_by)
    except Exception as failure:
        log(JOB_READ, "grading step FAILED\n" + traceback.format_exc().rstrip())
        store.mark_snapshot_reason(conn, snapshot, GRADE_FAILED)
        if forecast is not None:
            forecast[live_feed.WINDOWS] = live_feed.failed_windows_block(failure)
        return "grading step failed"
    return f"{windows} windows closed ({grades} grades)"


def band_summary(forecast):
    """The headline band in one phrase for a log line."""
    if forecast is None:
        return "range step failed"
    channel = forecast["channels"][components.HEADLINE_HORIZON]
    band = channel["bands"][components.BAND]
    if band["half_width"] is None:
        return f"30-min band {channel['status']} ({channel['reason']})"
    return (f"30-min band {band['low']:.2f} to {band['high']:.2f} (half {band['half_width']:.4f}, "
            f"{channel['status']})")


def wait_for_bar(day, cut_at):
    """Poll the bars file until the bar for the cut's minute has landed or the wait after that minute runs out."""
    wanted = cut_at.replace(second=0, microsecond=0)
    deadline = session_clock.bar_deadline(cut_at)
    while True:
        bars = mirai_files.read_spx_bars(day, cut_at)
        if bars and bars[-1]["at"] == wanted:
            return True
        if session_clock.now_utc() >= deadline:
            return False
        session_clock.sleep_seconds(BAR_POLL_SECONDS)


# ---- Rebuilds and prints by hand -------------------------------------------------------------------------------------

def rebuild_read(conn, cut_at, slot, calendar, half_days):
    """Rebuild one read at a cut (history_rebuilt) with its bars and forecast; returns snapshot, forecast, new bars."""
    snapshot, observations, bars, _ = collect.build_read(
        conn, cut_at, "history_rebuilt", session_clock.now_utc(), slot, "made" if slot else "not_scheduled", calendar,
        half_days)
    forecast, new_bars = write_read(conn, snapshot, observations, bars, cut_at, JOB_BY_HAND)
    return snapshot, forecast, new_bars


def rebuild_and_settle(day, build):
    """A hand rebuild on a trading day: open the store, run the build given, settle the day's ended windows, log."""
    if not session_clock.is_trading_day(day):
        log(JOB_BY_HAND, f"{day} is not a trading day; nothing rebuilt")
        return
    calendar, half_days = mirai_files.session_calendar()
    conn = store.open_store()
    summary = build(conn, calendar, half_days)
    now = session_clock.now_utc()
    windows, grades = grade.close_windows(conn, day, now, now)
    log(JOB_BY_HAND, f"{summary}, {windows} windows closed ({grades} grades)")


def run_rebuild_moment(cut_at):
    """Rebuild the read at one past moment; it carries a slot when the moment is a slot's own cut."""
    def build(conn, calendar, half_days):
        """One rebuilt read at the moment, summed up for the log."""
        snapshot, forecast, new_bars = rebuild_read(conn, cut_at, session_clock.slot_for_cut(cut_at), calendar,
                                                    half_days)
        return (f"rebuilt read at {session_clock.iso_eastern(cut_at)} (id {snapshot['snapshot_id']}): {new_bars} new "
                f"bars, status {snapshot['snapshot_status']}, {band_summary(forecast)}")
    rebuild_and_settle(session_clock.to_eastern(cut_at).date(), build)


def run_rebuild_day(day):
    """Rebuild every scheduled read of a session with the whole session's bars, then grade its ended windows."""
    def build(conn, calendar, half_days):
        """The session's bars and every slot rebuilt, summed up for the log."""
        bars_total = write_session_bars(conn, day, "history_rebuilt", half_days, session_clock.now_utc())
        reads, read_bars = rebuild_session(conn, day, calendar, half_days)
        return f"rebuilt {day}: {reads} reads, {bars_total + read_bars} new bars"
    rebuild_and_settle(day, build)


def run_scoreboard(day):
    """Print the scoreboard for one session (or every graded session) and the pooled last 20 sessions."""
    print("\n".join(grade.scoreboard_lines(store.open_store(), day)))


def run_trust():
    """Print the trust ledger: every reading and dial per channel with its sample, score, verdict and weight."""
    print("\n".join(trust.ledger_lines(store.open_store())))


def run_pass_test():
    """Print the band's pass test: coverage walk-forward at the 60-session lookback, then 20 for information."""
    conn = store.open_store()
    for lookback, heading in ((components.FIT_LOOKBACK_SESSIONS, "Pass test"),
                              (range_fit.INFORMATION_LOOKBACK_SESSIONS, "For information only")):
        print("\n".join(range_fit.pass_test_lines(conn, lookback, heading)))
        print()


def rebuild_session(conn, day, calendar, half_days):
    """Every slot of a session as history_rebuilt rows; returns the read count and the bars the reads added."""
    reads, bars_total = 0, 0
    for slot in session_clock.day_slots(day, half_days):
        bars_total += rebuild_read(conn, session_clock.cut_for_slot(day, slot), slot, calendar, half_days)[2]
        reads += 1
    return reads, bars_total


def write_session_bars(conn, day, row_origin, half_days, written_at):
    """Store every bar of the session up to the close not stored yet, stamped by arrival when it came after its slot."""
    close = session_clock.session_close(day, half_days)
    bars = mirai_files.read_spx_bars(day, close)
    return store.write_bars(conn, collect.session_bar_rows(conn, bars, day, row_origin, written_at, written_at))


def run_backfill_bars():
    """Load every SPX session Mirai holds (plain or gzipped) as history_rebuilt bars known at the file's change time."""
    conn = store.open_store()
    folder = paths.mirai_data(paths.MIRAI_FILES["spx_bars"]).parent
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
    log(JOB_BY_HAND, f"backfilled bars: {sessions} sessions found, {bars_total} new bars stored")


# ---- The nightly ---------------------------------------------------------------------------------------------------

def nightly_done_for():
    """The session the nightly last finished, from its marker file, or None."""
    marker = paths.nightly_done_file()
    return marker.read_text().strip() if marker.exists() else None


def run_nightly_if_due():
    """The nightly once per session: the first launch from 17:00 New York on whose latest session is not done yet."""
    now = session_clock.now_utc()
    session = session_clock.latest_trading_day(session_clock.to_eastern(now).date()).isoformat()
    if session_clock.to_eastern(now).hour < session_clock.NIGHTLY_HOUR_EASTERN or nightly_done_for() == session:
        log(JOB_NIGHTLY, f"nightly not due at {session_clock.iso_eastern(now)} (last done for {nightly_done_for()}); "
                         "nothing done")
        return
    run_nightly()


def run_nightly():
    """The session's close-out, once: missed slots, bars, fits, rebuilds, settled windows, ledger, parity, backup."""
    # Refused while the session is still open, a no-op once the latest session is done (a weekend, a rerun); ends
    # with the health line and the done marker.
    calendar, half_days = mirai_files.session_calendar()
    written_at = session_clock.now_utc()
    today = session_clock.latest_trading_day(session_clock.to_eastern(written_at).date())
    own_session = today == session_clock.to_eastern(written_at).date()
    if own_session and written_at < session_clock.session_close(today, half_days):
        log(JOB_NIGHTLY, f"nightly refused at {session_clock.iso_eastern(written_at)}: the session is open until "
                         f"{session_clock.iso_eastern(session_clock.session_close(today, half_days))}")
        return
    if nightly_done_for() == today.isoformat():
        log(JOB_NIGHTLY, f"nightly already done for {today}; nothing done")
        return
    conn = store.open_store()
    missed = write_missed_slots(conn, today, half_days, written_at) if own_session else 0
    sessions = sorted({today.isoformat(), *store_queries.live_sessions_not_rebuilt(conn)})
    bars_added = {}
    for trade_date in sessions:
        origin = "live_frozen" if store_queries.live_reads_count(conn, trade_date) else "history_rebuilt"
        bars_added[trade_date] = (write_session_bars(conn, date.fromisoformat(trade_date), origin, half_days,
                                                     written_at), origin)
    next_session = session_clock.next_trading_day(today)
    fits_rebuilt, fit_written = range_fit.write_fits(conn, next_session, written_at)
    rebuilt = [f"{trade_date} {rebuild_session(conn, date.fromisoformat(trade_date), calendar, half_days)[0]} reads"
               for trade_date in sessions]
    settled = []
    for trade_date in store_queries.sessions_with_open_windows(conn, session_clock.iso_utc(written_at),
                                                               components.RANGE_DECISION_KIND):
        windows, grades = grade.close_windows(conn, date.fromisoformat(trade_date), written_at, written_at)
        settled.append(f"{trade_date} {windows} windows ({grades} grades)")
    ledger = trust.write_ledger(conn, today, next_session, written_at)
    parity_phrase, mismatch_lines = parity.check_sessions(conn, half_days, written_at, MISMATCH_LINES_LOGGED)
    for line in mismatch_lines:
        log(JOB_NIGHTLY, line)
    packed, pruned, backup_warning = backup.write_backup(conn, today)
    if backup_warning:
        log(JOB_NIGHTLY, f"warning: {backup_warning}")
    conn.execute("PRAGMA optimize")
    live_bars = store_queries.bar_count(conn, today.isoformat(), "live_frozen")
    bars = "; ".join(f"{trade_date} {added} {origin} bars added" for trade_date, (added, origin) in bars_added.items())
    fits = f"{fits_rebuilt} past fits added, fit for {next_session} {'written' if fit_written else 'already there'}"
    warnings = live_feed.horizon_warnings(calendar, next_session)
    live_reads = store_queries.live_reads_count(conn, today.isoformat())
    grade_failures = store_queries.snapshots_with_reason(conn, today.isoformat(), GRADE_FAILED)
    log(JOB_NIGHTLY, f"nightly {today}: {live_reads} live reads, {missed} missed slots marked, {grade_failures} reads "
                     f"with a failed grading step, {live_bars} live bars ({bars}); rebuilt {', '.join(rebuilt)}; "
                     f"{fits}; settled {', '.join(settled) or 'nothing open'}; {ledger}; parity: {parity_phrase}; "
                     f"backup {packed.name} ({pruned} older removed)"
                     + "".join(f"; WARNING: {warning}" for warning in warnings))
    paths.nightly_done_file().write_text(today.isoformat() + "\n")


def write_missed_slots(conn, day, half_days, written_at):
    """A missed snapshot row for every scheduled slot no live read served; returns how many were added."""
    served = store_queries.live_slots_served(conn, day.isoformat())
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


if __name__ == "__main__":
    main()
