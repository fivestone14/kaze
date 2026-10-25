# What a force may look at when it measures a read: the read's observations by name, its price and swing, the clock,
# the day's bars known by the cut (SPX and SPY), the true open and prior close, the day's high and low, the same-day
# option book the day's reads kept, and the daily closes. A force asks these questions and answers with a Reading;
# it never reaches the store or Mirai's files itself, so every force is point-in-time by construction.
from datetime import timedelta

from engine import session_clock
from engine.collect import collect
from engine.store import components, registry, store, store_queries

# A force's answer: a raw value (with the side it points when its shape needs one), a gate closed (reads 0), or a
# blank with its reason.
class Reading:
    """The raw value of one reading at one read, or why it is blank; side is ±1 for one-sided shapes, else 0."""

    def __init__(self, value=None, side=0, blank=None):
        self.value, self.side, self.blank = value, side, blank

    @classmethod
    def blank_for(cls, reason):
        """A blank reading with its reason (never 0.0 for a missing input)."""
        return cls(blank=reason or "feed_missing")

    @classmethod
    def gate_closed(cls):
        """A reading whose gate is shut: it reads 0, which is a value, not a blank."""
        return cls(value=0.0)


class ReadFacts:
    """The facts of one read, loaded once and shared by every force and context input."""

    def __init__(self, conn, snapshot, observations, forecast, cut_at, half_days):
        self.conn, self.snapshot, self.observations, self.forecast = conn, snapshot, observations, forecast
        self.cut_at, self.half_days = cut_at, half_days
        self.trade_date = snapshot["trade_date"]
        self.day = session_clock.to_eastern(cut_at).date()
        self.cut_iso = session_clock.iso_utc(cut_at)
        # Bars a read may use: known by the cut on a live read; a rebuild of history reads the session's stored bars.
        self.bars_known_by = store.bars_known_by(snapshot, cut_at)
        self.half_day = self.day in half_days
        self.session_length = session_clock.session_length_minutes(self.day, half_days)
        self.minute = session_clock.minute_of_session(cut_at, self.day)
        self._bars, self._spy_bars, self._book, self._daily, self._prior_close = None, None, None, {}, None

    # ---- observations ---------------------------------------------------------------------------------------------------
    def row(self, measure_name, **subject):
        """One observation row of the read, or None."""
        return store.observation_of(self.observations, measure_name, registry.subject_key(**subject))

    def value(self, measure_name, **subject):
        """One observation's value, or None when absent or blank."""
        row = self.row(measure_name, **subject)
        return store.observation_value(row) if row is not None else None

    def blank_reason(self, measure_name, **subject):
        """Why an observation is blank: its stored reason, or feed_missing when the read holds no such row."""
        row = self.row(measure_name, **subject)
        return row["blank_reason"] if row is not None and row["blank_reason"] else "feed_missing"

    # ---- price and clock ------------------------------------------------------------------------------------------------
    @property
    def price(self):
        return self.forecast["price"]

    @property
    def minutes_left(self):
        """Minutes from the cut to the close (the read's own observation)."""
        return self.forecast["minutes_left"]

    def swing(self, horizon=components.HEADLINE_HORIZON):
        """The channel's typical move in points, None before the speed has formed."""
        channel = self.forecast["channels"].get(horizon)
        return channel["swing"] if channel else None

    @property
    def prior_close(self):
        """The quotes' prior close, else the store's (the daily bar, then the last bar) for history before the quotes."""
        if self._prior_close is None:
            self._prior_close = self.value("prior_close_points")
            if self._prior_close is None:
                self._prior_close = collect.stored_prior_session_close(self.conn, self.day)
        return self._prior_close

    @property
    def true_open(self):
        return self.value("true_open_points")

    @property
    def day_high(self):
        return self.value("session_high_points")

    @property
    def day_low(self):
        return self.value("session_low_points")

    @property
    def down_day(self):
        """−1 below the prior close, +1 above, 0 at it or unknown."""
        if self.price is None or self.prior_close is None:
            return 0
        return (self.price > self.prior_close) - (self.price < self.prior_close)

    # ---- bars -----------------------------------------------------------------------------------------------------------
    @property
    def bars(self):
        """SPX 1-minute bars of the session known by the cut, oldest first, each with 'at' and 'close'."""
        if self._bars is None:
            self._bars = [dict(bar, at=session_clock.parse_iso(bar["priced_at"]))
                          for bar in store_queries.session_bars(self.conn, self.trade_date, self.cut_iso,
                                                                self.bars_known_by)]
        return self._bars

    @property
    def spy_bars(self):
        """SPY 1-minute bars of the session known by the cut, oldest first."""
        if self._spy_bars is None:
            self._spy_bars = [dict(bar, at=session_clock.parse_iso(bar["priced_at"]))
                              for bar in store_queries.session_bars(self.conn, self.trade_date, self.cut_iso,
                                                                    self.bars_known_by, registry.SPY_SYMBOL)]
        return self._spy_bars

    def close_at_minute(self, minute):
        """SPX's price at a session minute: the close of the bar that started the minute before; None if not held."""
        if minute <= 0:
            return self.true_open if minute == 0 else None
        wanted = session_clock.session_open(self.day) + timedelta(minutes=minute - 1)
        return next((bar["close"] for bar in self.bars if bar["at"] == wanted), None)

    def move_over(self, minutes):
        """Price now (the bar starting at the read's minute) less the close `minutes` bars earlier; None when that
        bar is not held."""
        earlier = self.close_at_minute(self.minute + 1 - minutes)
        return self.price - earlier if earlier is not None and self.price is not None else None

    def five_minute_closes(self):
        """The session's closes at each 5-minute mark since the open through the cut (minute 5, 10, ...)."""
        closes = []
        for minute in range(5, self.minute + 2, 5):
            close = self.close_at_minute(minute)
            if close is not None:
                closes.append(close)
        return closes

    # ---- the same-day option book and the daily closes --------------------------------------------------------------------
    @property
    def option_book(self):
        """{(side, strike): contracts} of the newest same-day open interest the day's reads kept by the cut."""
        if self._book is None:
            self._book = store_queries.same_day_open_interest_book(self.conn, self.trade_date, self.cut_iso)
        return self._book

    def daily_closes(self, symbol):
        """A symbol's daily closes through the prior session, oldest first (the store's daily bars)."""
        if symbol not in self._daily:
            prior = session_clock.latest_trading_day(self.day - timedelta(days=1)).isoformat()
            self._daily[symbol] = [row["close"] for row in store_queries.daily_bars(self.conn, symbol, prior,
                                                                                   self.bars_known_by)]
        return self._daily[symbol]
