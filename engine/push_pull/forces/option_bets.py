# D · Option bets: who bets which way on the same-day tape. Urgent orders (bursts across strikes and 50-lot prints,
# bullish less bearish over 30 minutes, legs of multi-leg packages left out, big prints only when they took the bid or ask), which the market
# tends to give back; the burst tilt, near-the-money urgency less far-from-the-money urgency; and from every trade
# since the open, sorted by its delta: the option sentiment (calls bought less sold, less the same for puts), its
# shift over the last 30 minutes, and the strike reach (how far out of the money calls trade against puts).
from engine.collect import collect, mirai_files
from engine.push_pull.read_facts import Reading


def urgent_orders(read):
    """Signed urgent orders over the last 30 minutes: bursts plus big prints, bought calls and sold puts counting up."""
    total = read.value("urgent_orders_count", kind="total", window_minutes=collect.TAPE_WINDOW_MINUTES)
    if total is None:
        return Reading.blank_for(read.blank_reason("urgent_orders_count", kind="total",
                                                   window_minutes=collect.TAPE_WINDOW_MINUTES))
    return Reading(float(total))


def burst_tilt(read):
    """Near-the-money urgent orders less far-from-the-money ones over the last 30 minutes."""
    near = read.value("urgent_orders_count", kind="near", window_minutes=collect.TAPE_WINDOW_MINUTES)
    far = read.value("urgent_orders_count", kind="far", window_minutes=collect.TAPE_WINDOW_MINUTES)
    if near is None or far is None:
        return Reading.blank_for(read.blank_reason("urgent_orders_count", kind="near",
                                                   window_minutes=collect.TAPE_WINDOW_MINUTES))
    return Reading(float(near - far))


def option_sentiment(read):
    """Contracts bought less sold since the open, calls less puts: above 0 the day's option buyers lean up."""
    value = read.value("option_sentiment_since_open_count")
    if value is None:
        return Reading.blank_for(read.blank_reason("option_sentiment_since_open_count"))
    return Reading(float(value))


def sentiment_shift(read):
    """The option sentiment's change over the last 30 minutes: which way the newest bets turned."""
    value = read.value("option_sentiment_last_30_minutes_count")
    if value is None:
        return Reading.blank_for(read.blank_reason("option_sentiment_last_30_minutes_count"))
    return Reading(float(value))


def strike_reach(read):
    """The share of the day's call contracts traded far out of the money (delta under 0.25) less that share of its
    put contracts: above 0 the day reaches further for upside than for downside."""
    shares = []
    for side in mirai_files.OPTION_SIDES:
        cells = {band: read.value("option_contracts_by_delta_count", delta_band=band, side=side)
                 for _, _, band in mirai_files.DELTA_BANDS}
        if any(value is None for value in cells.values()):
            return Reading.blank_for(read.blank_reason("option_contracts_by_delta_count", delta_band="0_10", side=side))
        total = sum(cells.values())
        if not total:
            return Reading.blank_for("no_activity")
        shares.append(sum(cells[band] for band in mirai_files.FAR_OUT_BANDS) / total)
    return Reading(shares[0] - shares[1])
