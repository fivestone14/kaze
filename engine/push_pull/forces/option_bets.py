# D · Option bets: who bets which way on the same-day tape. Urgent orders (bursts across strikes and 50-lot prints,
# bullish less bearish over 30 minutes, legs of multi-leg packages left out, big prints only when they took the bid or ask), which the market
# tends to give back; the burst tilt, near-the-money urgency less far-from-the-money urgency; and from every trade
# since the open, sorted by its delta: the option sentiment (calls bought less sold, less the same for puts, as a
# share of the contracts that took a side), its shift over the last 30 minutes, and the strike reach (how far out of
# the money calls trade against puts).
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
    """Contracts bought less sold since the open, calls less puts, as a share of the contracts that took a side: above
    0 the day's option buyers lean up. A share holds steady when some trades reach the file late or could not be
    called bought or sold (older days' quotes left more of them at the mid), where a count would shrink."""
    return sided_share(read, "option_sentiment_since_open_count", "option_sided_since_open_count")


def sentiment_shift(read):
    """The option sentiment over the last 30 minutes, as a share of that half hour's contracts that took a side: which
    way the newest bets turned."""
    return sided_share(read, "option_sentiment_last_30_minutes_count", "option_sided_last_30_minutes_count")


def sided_share(read, net_measure, sided_measure):
    """A net bought count over the contracts that took a side in the same span; blank with the missing input's reason,
    or no_activity when nothing took a side."""
    net, sided = read.value(net_measure), read.value(sided_measure)
    if net is None or sided is None:
        return Reading.blank_for(read.blank_reason(net_measure if net is None else sided_measure))
    if not sided:
        return Reading.blank_for("no_activity")
    return Reading(net / sided)


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
