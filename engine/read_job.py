# The entry point launchd runs every 5 minutes: collect one read, then write the live feed. --at rebuilds one past moment,
# --rebuild-day a whole session, --nightly runs the live-versus-rebuilt parity check; outside market hours a live run logs one line.
import argparse
import time
from datetime import date, timedelta

from engine import collect, live_feed, measures, mirai_files, paths, session_clock, store

BAR_WAIT_SECONDS = 100
BAR_POLL_SECONDS = 5
READ_LOG = "read_job_log.txt"
NIGHTLY_LOG = "nightly_log.txt"


def main():
    """Pick the run from the command line and do it."""
    parser = argparse.ArgumentParser(description="Kaze read job")
    parser.add_argument("--at", metavar="YYYY-MM-DD HH:MM:SS", help="rebuild one past moment (New York time)")
    parser.add_argument("--rebuild-day", metavar="YYYY-MM-DD", help="rebuild every 5-minute read of a session")
    parser.add_argument("--nightly", action="store_true", help="parity check and health summary")
    args = parser.parse_args()
    if args.nightly:
        run_nightly()
    elif args.at:
        run_rebuild_moment(session_clock.parse_eastern(args.at))
    elif args.rebuild_day:
        run_rebuild_day(date.fromisoformat(args.rebuild_day))
    else:
        run_live()


def log(file_name, line):
    """Append one timestamped line to a log under $KAZE_DATA/logs and echo it."""
    folder = paths.logs_folder()
    folder.mkdir(parents=True, exist_ok=True)
    stamped = f"{session_clock.iso_eastern(session_clock.now_utc())} {line}"
    with open(folder / file_name, "a") as handle:
        handle.write(stamped + "\n")
    print(stamped)


def run_live():
    """One live read for the slot launchd fired on; outside market hours or off the slot grid it writes nothing."""
    now = session_clock.now_utc()
    calendar = mirai_files.read_event_calendar()
    half_days = mirai_files.half_day_dates(calendar)
    day = session_clock.to_eastern(now).date()
    slot = session_clock.slot_for(now)
    if not session_clock.is_market_open(now, half_days) or slot not in session_clock.day_slots(day, half_days):
        log(READ_LOG, f"market closed or not a read slot at {session_clock.iso_eastern(now)}; nothing written")
        return
    cut_at = session_clock.cut_for_slot(day, slot)
    bar_arrived = wait_for_bar(day, cut_at)
    written_at = session_clock.now_utc()
    snapshot, observations, bars, feeds = collect.build_read(cut_at, "live", written_at, slot, calendar,
                                                             login=mirai_files.read_login_state())
    conn = store.open_store()
    store.write_snapshot(conn, snapshot, observations)
    new_bars = store.write_bars(conn, bars)
    live_feed.write_live_feed(snapshot, observations, feeds)
    log(READ_LOG, f"live read {slot} ({snapshot['read_lane']}) cut {session_clock.iso_eastern(cut_at)}: "
                  f"{len(observations)} observations, {new_bars} new bars, status {snapshot['snapshot_status']}, "
                  f"bar for the cut minute {'arrived' if bar_arrived else 'did not arrive in time'}")


def wait_for_bar(day, cut_at):
    """Wait, polling the bars file, until the bar for the cut's minute has landed or the wait runs out; True if it landed."""
    wanted = cut_at.replace(second=0, microsecond=0)
    deadline = time.monotonic() + BAR_WAIT_SECONDS
    while True:
        bars = mirai_files.read_spx_bars(day, cut_at)
        if bars and bars[-1]["at"] == wanted:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(BAR_POLL_SECONDS)


def rebuild_read(conn, cut_at, slot, calendar):
    """Rebuild one read at a cut (row_origin rebuilt) and write it with its bars; returns the snapshot and bar count."""
    snapshot, observations, bars, feeds = collect.build_read(cut_at, "rebuilt", session_clock.now_utc(), slot, calendar)
    store.write_snapshot(conn, snapshot, observations)
    new_bars = store.write_bars(conn, bars)
    return snapshot, len(observations), new_bars


def run_rebuild_moment(cut_at):
    """Rebuild the read at one past moment; the slot is set when the moment sits on the 5-minute grid's cut second."""
    calendar = mirai_files.read_event_calendar()
    slot = session_clock.slot_for(cut_at + timedelta(seconds=1)) if cut_at.second == 59 else None
    conn = store.open_store()
    snapshot, count, new_bars = rebuild_read(conn, cut_at, slot, calendar)
    log(READ_LOG, f"rebuilt read at {session_clock.iso_eastern(cut_at)}: {count} observations, {new_bars} new bars, "
                  f"status {snapshot['snapshot_status']}, id {snapshot['snapshot_id']}")


def run_rebuild_day(day):
    """Rebuild every scheduled read of a session, 09:35 to 15:55 on a full day."""
    calendar = mirai_files.read_event_calendar()
    if not session_clock.is_trading_day(day):
        log(READ_LOG, f"{day} is not a trading day; nothing rebuilt")
        return
    conn = store.open_store()
    half_days = mirai_files.half_day_dates(calendar)
    slots = session_clock.day_slots(day, half_days)
    reads, bars_total = 0, 0
    for slot in slots:
        _, _, new_bars = rebuild_read(conn, session_clock.cut_for_slot(day, slot), slot, calendar)
        reads += 1
        bars_total += new_bars
    bars_total += write_session_bars(conn, day, "rebuilt", half_days)
    log(READ_LOG, f"rebuilt {day}: {reads} reads ({slots[0]} to {slots[-1]}), {bars_total} new bars")


def write_session_bars(conn, day, row_origin, half_days):
    """Append every bar of the session up to the close, so the minutes after the last read are stored too."""
    close = session_clock.session_close(day, half_days)
    return store.write_bars(conn, collect.bar_rows(mirai_files.read_spx_bars(day, close), day, row_origin))


def run_nightly():
    """Compare rebuilt reads with live ones for every session that has both, record the result, and log one health line."""
    conn = store.open_store()
    today = session_clock.latest_trading_day(session_clock.to_eastern(session_clock.now_utc()).date())
    live_reads = len(store.snapshots_on(conn, today.isoformat(), "live"))
    closing_bars = 0
    if live_reads:
        closing_bars = write_session_bars(conn, today, "live", mirai_files.half_day_dates(mirai_files.read_event_calendar()))
    days = store.days_with_live_and_rebuilt_reads(conn)
    summary = []
    for day in days:
        compared, mismatches = store.parity_mismatches(conn, day)
        write_parity_check(conn, day, compared, mismatches)
        summary.append(f"{day} {compared - len(mismatches)}/{compared} values identical")
        for mismatch in mismatches[:20]:
            log(NIGHTLY_LOG, f"mismatch {day} {mismatch['cut_at']} {mismatch['measure_name']} {mismatch['subject_key']}: "
                             f"live {live_side(mismatch)} rebuilt {rebuilt_side(mismatch)}")
    rebuilt_reads = len(store.snapshots_on(conn, today.isoformat(), "rebuilt"))
    log(NIGHTLY_LOG, f"nightly {today}: {live_reads} live reads, {rebuilt_reads} rebuilt reads, "
                     f"{store.bar_count(conn, today.isoformat(), 'live')} live bars ({closing_bars} added at the close); parity: "
                     f"{'; '.join(summary) if summary else 'no session with both live and rebuilt reads yet'}")


def write_parity_check(conn, day, compared, mismatches):
    """A nightly_check snapshot for the session with the parity measures as its observations."""
    cut_at = session_clock.session_close(date.fromisoformat(day))
    snapshot = collect.new_snapshot("nightly_check", cut_at, "live", session_clock.now_utc())
    snapshot["snapshot_status"] = "built"
    identical = (compared - len(mismatches)) / compared if compared else None
    observations = [
        store.new_observation(snapshot, "reads_compared_count", compared),
        store.new_observation(snapshot, "values_mismatched_count", len(mismatches)),
        store.new_observation(snapshot, "rebuild_identical_portion", identical,
                              subject_key=measures.subject_key(table_column="collect_observations.value"),
                              blank_reason=None if identical is not None else "not_applicable"),
    ]
    store.write_snapshot(conn, snapshot, observations)


def live_side(mismatch):
    """The live value of a mismatch as text."""
    return mismatch["live_number"] if mismatch["live_number"] is not None else \
        mismatch["live_text"] or mismatch["live_bool"] or mismatch["live_at"] or f"blank:{mismatch['live_blank']}"


def rebuilt_side(mismatch):
    """The rebuilt value of a mismatch as text."""
    return mismatch["rebuilt_number"] if mismatch["rebuilt_number"] is not None else \
        mismatch["rebuilt_text"] or mismatch["rebuilt_bool"] or mismatch["rebuilt_at"] or f"blank:{mismatch['rebuilt_blank']}"


if __name__ == "__main__":
    main()
