# Backfills the push/pull inputs over the sessions Mirai holds SPX bars for: the daily bars of $SPX and $TNX, each
# session's SPX and SPY bars, and every read of each session rebuilt so the new rows (true open, same-day open
# interest, the leaders' and breadth's change, urgent orders) exist for history. Rows are history_rebuilt and a
# rebuilt cut is replaced, so the job can run again; it never touches a live row.
#   python3 -m engine.jobs.backfill_push_pull_inputs [--from YYYY-MM-DD] [--to YYYY-MM-DD]
import argparse
import traceback
from datetime import date

from engine import paths, session_clock
from engine.collect import mirai_files
from engine.grade import grade
from engine.jobs import read_job
from engine.store import store


def sessions_held(first_day, last_day):
    """The trading days in the span that Mirai holds an SPX bar file for, oldest first."""
    folder = paths.mirai_data(paths.MIRAI_FILES["spx_bars"]).parent
    days = sorted({date.fromisoformat(path.name[:10]) for path in folder.glob("*.jsonl*")})
    return [day for day in days if first_day <= day <= last_day and session_clock.is_trading_day(day)]


def run(first_day, last_day):
    """Daily bars, then each session's bars and rebuilt reads with its windows settled; one log line per session."""
    calendar, half_days = mirai_files.session_calendar()
    conn = store.open_store()
    now = session_clock.now_utc()
    today = session_clock.latest_trading_day(session_clock.to_eastern(now).date())
    daily = read_job.write_daily_bars(conn, today, half_days, now)
    read_job.log(read_job.JOB_BY_HAND, f"backfill of the push/pull inputs: {daily} daily bars added")
    sessions = sessions_held(first_day, min(last_day, today))
    for day in sessions:
        if day == today and now < session_clock.session_close(day, half_days):
            break
        bars = read_job.write_session_bars(conn, day, "history_rebuilt", half_days, now)
        reads, read_bars = read_job.rebuild_session(conn, day, calendar, half_days)
        windows, grades = grade.close_windows(conn, day, now, now)
        read_job.log(read_job.JOB_BY_HAND, f"backfilled {day}: {reads} reads rebuilt, {bars + read_bars} new bars, "
                                           f"{windows} windows closed ({grades} grades)")
    read_job.log(read_job.JOB_BY_HAND, f"backfill of the push/pull inputs done: {len(sessions)} sessions")


def main():
    parser = argparse.ArgumentParser(description="Backfill the push/pull inputs over the sessions Mirai holds")
    parser.add_argument("--from", dest="first_day", metavar="YYYY-MM-DD", default="2026-07-06",
                        help="first session (default: Mirai's first SPX bar file)")
    parser.add_argument("--to", dest="last_day", metavar="YYYY-MM-DD", default=date.max.isoformat(),
                        help="last session (default: the latest closed one)")
    args = parser.parse_args()
    try:
        run(date.fromisoformat(args.first_day), date.fromisoformat(args.last_day))
    except Exception:
        read_job.log(read_job.JOB_BY_HAND, "FAILED\n" + traceback.format_exc().rstrip())
        raise SystemExit(1)


if __name__ == "__main__":
    main()
