# The market clock: New York time, session open and close, holidays and half days, and the 5-minute read slots.
# Every "is the market open" or "which slot is this" question in the engine is answered here.
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
UTC = timezone.utc
SESSION_OPEN = time(9, 30)
FULL_DAY_CLOSE = time(16, 0)
HALF_DAY_CLOSE = time(13, 0)
FIRST_SLOT_MINUTES_AFTER_OPEN = 5
SLOT_MINUTES = 5
HEADLINE_SLOT_MINUTES = (5, 35)
LANE_HEADLINE = "headline_30"
LANE_EVERY_5 = "range_every_5_minutes"

# NYSE full-day closures, kept two years ahead by hand; Mirai's event calendar lists no holidays.
HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}


def now_utc():
    """The current moment, timezone-aware UTC, whole seconds."""
    return datetime.now(UTC).replace(microsecond=0)


def to_eastern(moment):
    """The same moment on the New York clock."""
    return moment.astimezone(EASTERN)


def to_utc(moment):
    """The same moment on the UTC clock."""
    return moment.astimezone(UTC)


def eastern_moment(day, clock):
    """A New York date plus wall-clock time as an aware datetime."""
    return datetime.combine(day, clock, tzinfo=EASTERN)


def parse_eastern(text):
    """Parse 'YYYY-MM-DD HH:MM:SS' typed on the command line as New York time."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=EASTERN)


def is_trading_day(day):
    """True on a weekday that is not a listed holiday; half days still count as trading days."""
    return day.weekday() < 5 and day not in HOLIDAYS


def latest_trading_day(day):
    """The day itself when it is a trading day, else the nearest trading day before it."""
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def session_close(day, half_day_dates=()):
    """The close on this day: 13:00 New York on a listed half day, else 16:00."""
    return eastern_moment(day, HALF_DAY_CLOSE if day in half_day_dates else FULL_DAY_CLOSE)


def session_open(day):
    """09:30 New York on this day."""
    return eastern_moment(day, SESSION_OPEN)


def is_market_open(moment, half_day_dates=()):
    """True when the moment falls inside a trading day's 09:30-16:00 (or 13:00) New York session."""
    local = to_eastern(moment)
    day = local.date()
    return is_trading_day(day) and session_open(day) <= local < session_close(day, half_day_dates)


def minutes_since_open(cut_at):
    """Whole session minutes finished by the cut (the 12:35:59 cut has 186 of them)."""
    local = to_eastern(cut_at)
    return int((local - session_open(local.date())).total_seconds() // 60) + 1


def session_minutes(day, half_day_dates=()):
    """Minutes in the session: 390 on a full day, 210 on a half day."""
    return int((session_close(day, half_day_dates) - session_open(day)).total_seconds() // 60)


def slot_for(moment):
    """The HH:MM read slot a launch moment serves: the minute itself when it sits on the 5-minute grid, else blank."""
    local = to_eastern(moment)
    if local.minute % SLOT_MINUTES != 0:
        return None
    return local.strftime("%H:%M")


def cut_for_slot(day, slot):
    """The cut a slot reads: the last second of the minute before it (slot 12:35 reads through the 12:34 bar)."""
    hour, minute = (int(part) for part in slot.split(":"))
    return eastern_moment(day, time(hour, minute)) - timedelta(seconds=1)


def lane_for_slot(slot):
    """headline_30 at :05 and :35, the 5-minute lane everywhere else; blank when there is no slot."""
    if slot is None:
        return LANE_EVERY_5
    return LANE_HEADLINE if int(slot[3:]) in HEADLINE_SLOT_MINUTES else LANE_EVERY_5


def lane_cadence_minutes(lane):
    """How many minutes apart a lane's reads are."""
    return 30 if lane == LANE_HEADLINE else SLOT_MINUTES


def day_slots(day, half_day_dates=()):
    """Every scheduled slot of a session, 09:35 to 15:55 on a full day (77 slots)."""
    first = session_open(day) + timedelta(minutes=FIRST_SLOT_MINUTES_AFTER_OPEN)
    close = session_close(day, half_day_dates)
    slots = []
    moment = first
    while moment < close:
        slots.append(moment.strftime("%H:%M"))
        moment += timedelta(minutes=SLOT_MINUTES)
    return slots


def iso_utc(moment):
    """A moment written the way the store keeps every timestamp: ISO 8601 in UTC, whole seconds."""
    return to_utc(moment).replace(microsecond=0).isoformat()


def iso_eastern(moment):
    """A moment written on the New York clock for the phone and the logs."""
    return to_eastern(moment).replace(microsecond=0).isoformat()


def parse_iso(text):
    """A stored ISO timestamp back into an aware datetime."""
    return datetime.fromisoformat(text)
