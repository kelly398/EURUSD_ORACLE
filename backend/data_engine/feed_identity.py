
"""Offline versioned MT5 feed labels for the future multi-broker data migration.

Pure functions only. These are deterministic LOCAL routing identifiers, never
independent account verification, source admission, or acquisition authorization.
No database, terminal, collector, clock correction, file writes or orders.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re

from backend.runtime.stage7_universal_mt5_adapter import validate_profile

FEED_ID_RE = re.compile(r'^mt5_eurusd_v1_[0-9a-f]{64}$')
EPOCH_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{3,63}$')
BROKER_SYMBOL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+#-]{0,31}$')
RESERVED_EPOCHS = {'LEGACY_FEED_UNKNOWN', 'UNVERIFIED', 'UNKNOWN', 'HISTORICAL'}


class FeedIdentityError(ValueError):
    """Selected source cannot be assigned an unambiguous local routing label."""


def require_feed_id(feed_id):
    """Check syntax only; this cannot independently verify the account or feed."""
    if type(feed_id) is not str or FEED_ID_RE.fullmatch(feed_id) is None:
        raise FeedIdentityError('Invalid versioned MT5 feed ID.')
    return feed_id


def build_local_feed_identity(profile, *, capture_epoch):
    """Bind a versioned local routing ID to a selected MT5 profile/epoch.

    account_ref is only a local alias, not an authenticated MT5 login.
    capture_epoch is an operator-assigned future identifier, not evidence
    of historical broker time or custody. A different source or epoch
    receives a different identifier.
    """
    validate_profile(profile)

    if (type(capture_epoch) is not str
            or EPOCH_RE.fullmatch(capture_epoch) is None
            or capture_epoch in RESERVED_EPOCHS):
        raise FeedIdentityError('Explicit nonreserved capture epoch required.')

    instrument = profile['instrument']

    identity = {
        'contract': 'EURUSD_ORACLE_MT5_LOCAL_FEED_ID_V1',
        'platform': profile['platform'],
        'profile_id': profile['profile_id'],
        'company_local_claim': profile['broker_name'],
        'server_local_claim': profile['server_name'],
        'environment_local_claim': profile['environment'],
        'account_mode_local_claim': profile['account_mode'],
        'account_alias_local_claim': profile['account_ref'],
        'canonical_symbol': instrument['canonical_symbol'],
        'broker_symbol': instrument['broker_symbol'],
        'timestamp_contract_unverified': profile['timestamp_contract'],
        'capture_epoch_local_label': capture_epoch,
    }

    canonical = json.dumps(
        identity,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    ).encode('utf-8')

    digest = sha256(canonical).hexdigest()

    return {
        'schema_version': 1,
        'feed_id': 'mt5_eurusd_v1_' + digest,
        'identity_sha256': digest,
        'identity_claims': identity,
        'independently_authenticated': False,
        'source_admitted': False,
        'market_data_writable': False,
        'trading_authorized': False,
        'final_action': 'HOLD',
    }


def create_m5_candle_key_v2(*, feed_id, symbol, open_time_utc):
    """A proposed future per-feed M5 uniqueness key; performs no write."""
    require_feed_id(feed_id)

    if type(symbol) is not str or BROKER_SYMBOL_RE.fullmatch(symbol) is None:
        raise FeedIdentityError('Invalid broker candle symbol.')

    if (not isinstance(open_time_utc, datetime)
            or open_time_utc.tzinfo is None
            or open_time_utc.utcoffset() != timedelta(0)):
        raise FeedIdentityError('M5 opening must be an aware UTC datetime.')

    if (open_time_utc.microsecond
            or int(open_time_utc.timestamp()) % 300 != 0):
        raise FeedIdentityError('M5 opening is not five-minute aligned.')

    iso = open_time_utc.astimezone(timezone.utc).isoformat()

    raw = json.dumps(
        ['EURUSD_ORACLE_M5_KEY_V2', feed_id, symbol, iso],
        separators=(',', ':'),
    ).encode('utf-8')

    return sha256(raw).hexdigest()
