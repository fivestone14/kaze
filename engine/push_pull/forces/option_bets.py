# D · Option bets: who bets which way on the same-day tape. Urgent orders (bursts across strikes and 50-lot prints,
# bullish less bearish over 30 minutes, every trade condition including the legs of complex trades), which the market
# tends to give back; and the burst tilt, near-the-money urgency less far-from-the-money urgency.
from engine.collect import collect
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
