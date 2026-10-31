
import hashlib


def create_tick_fingerprint(
    symbol,
    time_msc,
    bid,
    ask,
    last,
    volume,
    flags,
    volume_real
):

    payload = "|".join(
        [
            str(symbol),
            str(int(time_msc)),
            format(float(bid), ".10f"),
            format(float(ask), ".10f"),
            format(float(last), ".10f"),
            format(float(volume), ".10f"),
            str(int(flags)),
            format(float(volume_real), ".10f"),
        ]
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def create_tick_fingerprint_v2(
    *, feed_id, symbol, time_msc, bid, ask, last, volume, flags, volume_real
):
    """Proposed feed-specific tick fingerprint; preserve legacy V1.

    V2 adds a domain-separated feed binding to the existing V1 tuple.
    This function neither stores ticks nor proves source admission.
    """
    from decimal import Decimal, InvalidOperation
    from backend.data_engine.feed_identity import (
        require_feed_id,
        FeedIdentityError,
        BROKER_SYMBOL_RE,
    )

    require_feed_id(feed_id)

    if type(symbol) is not str or BROKER_SYMBOL_RE.fullmatch(symbol) is None:
        raise FeedIdentityError('Invalid tick symbol.')

    if type(time_msc) is not int or not 0 < time_msc <= 4102444800999:
        raise FeedIdentityError('Invalid tick millisecond time.')

    if type(flags) is not int or not 0 <= flags <= 65535:
        raise FeedIdentityError('Invalid tick flags.')

    values = {}

    for name, raw in [
        ('bid', bid),
        ('ask', ask),
        ('last', last),
        ('volume', volume),
        ('volume_real', volume_real),
    ]:
        if isinstance(raw, bool) or type(raw) not in (str, int, float, Decimal):
            raise FeedIdentityError('Invalid numeric type: ' + name)

        try:
            number = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            raise FeedIdentityError('Invalid numeric value: ' + name) from None

        if not number.is_finite() or number < 0:
            raise FeedIdentityError('Nonfinite or negative: ' + name)

        if number.adjusted() > 17 or number.as_tuple().exponent < -10:
            raise FeedIdentityError(
                'Out-of-bounds legacy tuple precision: ' + name
            )

        values[name] = number

    if values['bid'] <= 0 or values['ask'] < values['bid']:
        raise FeedIdentityError('Nonpositive or crossed Bid/Ask.')

    legacy = create_tick_fingerprint(
        symbol=symbol,
        time_msc=time_msc,
        bid=values['bid'],
        ask=values['ask'],
        last=values['last'],
        volume=values['volume'],
        flags=flags,
        volume_real=values['volume_real'],
    )

    payload = (
        b'EURUSD_ORACLE_TICK_V2\x00'
        + feed_id.encode('ascii')
        + b'\x00'
        + legacy.encode('ascii')
    )

    return hashlib.sha256(payload).hexdigest()
