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
    