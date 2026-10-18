# The market clock: New York time, session open and close, holidays and half days, and the 5-minute read slots.
# Every "is the market open", "which slot is this" or "when does the slot cut" question in the engine is answered here.
import time as clock
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
UTC = timezone.utc
SESSION_OPEN = time(9, 30)
FULL_DAY_CLOSE = time(16, 0)
HALF_DAY_CLOSE = time(13, 0)
FIRST_SLOT_MINUTES_AFTER_OPEN = 5
SLOT_MINUTES = 5
SLOT_CUT_SECOND = 59
BAR_LENGTH_SECONDS = 60
LATE_LAUNCH_MINUTES = 1
BAR_WAIT_SECONDS = 100
HEADLINE_SLOT_MINUTES = (5, 35)
LANE_HEADLINE = "headline_30"
LANE_EVERY_5 = "range_every_5_minutes"
READ_LANES = {LANE_HEADLINE: 30, LANE_EVERY_5: SLOT_MINUTES}
NIGHTLY_HOUR_EASTERN = 17

# NYSE full-day closures, kept by hand (Mirai's calendar lists none); the clock refuses to answer past the last listed
# day, and the nightly and the phone warn from HOLIDAY_WARNING_DAYS before it. Extend the list every autumn.
HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}
HOLIDAYS_LISTED_THROUGH = date(2027, 12, 31)
HOLIDAY_WARNING_DAYS = 30


def now_utc():
    """The current moment, timezone-aware UTC, whole seconds."""
    return datetime.now(UTC).replace(microsecond=0)


def to_eastern(moment):
    """The same moment on the New York clock."""
    return moment.astimezone(EASTERN)


def to_utc(moment):
    """The same moment on the UTC clock."""
    return moment.astimezone(UTC)


def today_eastern():
    """Today's date on the New York clock, whatever zone the box runs in."""
    return to_eastern(now_utc()).date()


def eastern_datetime(day, clock_time):
    """A New York date plus a wall-clock time as an aware datetime."""
    return datetime.combine(day, clock_time, tzinfo=EASTERN)


def eastern_from_hhmm(day, text):
    """A New York date plus an 'HH:MM' string as an aware datetime."""
    hour, minute = (int(part) for part in text.split(":"))
    return eastern_datetime(day, time(hour, minute))


def parse_typed_eastern(text):
    """Parse 'YYYY-MM-DD HH:MM:SS' typed on the command line as New York time."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=EASTERN)


def parse_iso(text):
    """An ISO timestamp with offset (Mirai's files or the store) as an aware datetime."""
    return datetime.fromisoformat(text)


def is_trading_day(day):
    """True on a weekday that is not a listed holiday; refuses a day past the holiday list's horizon."""
    if day > HOLIDAYS_LISTED_THROUGH:
        raise ValueError(f"the holiday list in session_clock.py ends {HOLIDAYS_LISTED_THROUGH}; extend it for {day}")
    return day.weekday() < 5 and day not in HOLIDAYS


def holiday_horizon_warning(day):
    """A warning phrase when the holiday list ends within the warning window of a day, else None."""
    days_left = (HOLIDAYS_LISTED_THROUGH - day).days
    if days_left > HOLIDAY_WARNING_DAYS:
        return None
    return f"the holiday list in session_clock.py ends {HOLIDAYS_LISTED_THROUGH} ({days_left} days left)"


def latest_trading_day(day):
    """The day itself when it is a trading day, else the nearest trading day before it."""
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def next_trading_day(day):
    """The first trading day after this one."""
    day += timedelta(days=1)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return day


def session_open(day):
    """09:30 New York on this day."""
    return eastern_datetime(day, SESSION_OPEN)


def session_close(day, half_days):
    """The close on this day: 13:00 New York on a listed half day, else 16:00."""
    return eastern_datetime(day, HALF_DAY_CLOSE if day in half_days else FULL_DAY_CLOSE)


def session_length_minutes(day, half_days):
    """Minutes in the session: 390 on a full day, 210 on a half day."""
    return int((session_close(day, half_days) - session_open(day)).total_seconds() // 60)


def minute_of_session(moment, day):
    """Which minute of the session a moment falls in, counting from 0 at the open (12:35:59 is minute 185)."""
    return int((moment - session_open(day)).total_seconds() // 60)


def minutes_since_open(cut_at):
    """Whole session minutes finished by the cut (the 12:35:59 cut has 186 of them)."""
    return minute_of_session(cut_at, to_eastern(cut_at).date()) + 1


def bar_deadline(cut_at):
    """The last moment a read at this cut waits for its own bar: the minute after the cut plus the bar wait."""
    return cut_at + timedelta(seconds=1 + BAR_WAIT_SECONDS)


def day_slots(day, half_days):
    """Every scheduled slot of a session as 'HH:MM', 09:35 to 15:55 on a full day (77 slots)."""
    moment = session_open(day) + timedelta(minutes=FIRST_SLOT_MINUTES_AFTER_OPEN)
    close = session_close(day, half_days)
    slots = []
    while moment < close:
        slots.append(moment.strftime("%H:%M"))
        moment += timedelta(minutes=SLOT_MINUTES)
    return slots


def slot_at(moment):
    """The 'HH:MM' slot a launch serves: its own minute on the 5-minute grid, or the one before when a minute late."""
    local = to_eastern(moment)
    for minutes_late in range(LATE_LAUNCH_MINUTES + 1):
        candidate = local - timedelta(minutes=minutes_late)
        if candidate.minute % SLOT_MINUTES == 0:
            return candidate.strftime("%H:%M")
    return None


def is_read_slot(day, slot, half_days):
    """True when the slot is one of the session's scheduled reads (so the day trades and the slot is inside it)."""
    return slot is not None and is_trading_day(day) and slot in day_slots(day, half_days)


def slot_start(day, slot):
    """The first second of the slot minute, when launchd fires the read."""
    return eastern_from_hhmm(day, slot)


def cut_for_slot(day, slot):
    """The cut a slot reads: the last second of its own minute (12:35 cuts at 12:35:59 and includes the 12:35 bar)."""
    return slot_start(day, slot) + timedelta(seconds=SLOT_CUT_SECOND)


def slot_for_cut(cut_at):
    """The slot whose cut this moment is, or None when it does not sit on the grid's cut second."""
    local = to_eastern(cut_at)
    if local.second != SLOT_CUT_SECOND or local.minute % SLOT_MINUTES != 0:
        return None
    return local.strftime("%H:%M")


def lane_for_slot(slot):
    """headline_30 at :05 and :35, the 5-minute lane everywhere else and for unscheduled moments."""
    if slot is not None and int(slot[3:]) in HEADLINE_SLOT_MINUTES:
        return LANE_HEADLINE
    return LANE_EVERY_5


def iso_utc(moment):
    """A moment written the way the store keeps every timestamp: ISO 8601 in UTC, whole seconds."""
    return to_utc(moment).replace(microsecond=0).isoformat()


def iso_eastern(moment):
    """A moment written on the New York clock for the phone and the logs."""
    return to_eastern(moment).replace(microsecond=0).isoformat()


def sleep_seconds(seconds):
    """Pause the job (kept here so every wait in the engine goes through the clock)."""
    clock.sleep(seconds)


def file_modified_at(path):
    """When a file was last changed, as an aware UTC moment."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).replace(microsecond=0)
