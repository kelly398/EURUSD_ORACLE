from dataclasses import dataclass


# ============================================================
# OFFICIAL ECB SDMX RATE SOURCE
# ============================================================

ECB_RATE_SOURCE = (
    "ECB_SDMX_FM"
)

ECB_CENTRAL_BANK = (
    "ECB"
)

ECB_SDMX_RATE_BASE_URL = (
    "https://data-api.ecb.europa.eu/"
    "service/data/FM/"
)

ECB_REQUEST_INCLUDE_HISTORY = True

ECB_REQUEST_FORMAT = (
    "csvdata"
)


# ============================================================
# SUPPORTED KEY POLICY-RATE SERIES
# ============================================================

@dataclass(
    frozen=True
)
class ECBRateSeriesSpec:

    rate_name: str

    provider_series_id: str

    series_key: str


ECB_RATE_SERIES = (

    ECBRateSeriesSpec(

        rate_name=(
            "deposit_facility_rate"
        ),

        provider_series_id="DFR",

        series_key=(
            "B.U2.EUR.4F.KR.DFR.LEV"
        )
    ),

    ECBRateSeriesSpec(

        rate_name=(
            "main_refinancing_operations_rate"
        ),

        provider_series_id="MRR_FR",

        series_key=(
            "B.U2.EUR.4F.KR.MRR_FR.LEV"
        )
    ),

    ECBRateSeriesSpec(

        rate_name=(
            "marginal_lending_facility_rate"
        ),

        provider_series_id="MLFR",

        series_key=(
            "B.U2.EUR.4F.KR.MLFR.LEV"
        )
    ),
)


# ============================================================
# REQUIRED SDMX CSV CONTRACT
# ============================================================

ECB_RATE_REQUIRED_COLUMNS = (

    "KEY",

    "TIME_PERIOD",

    "OBS_VALUE",

    "ACTION",

    "VALID_FROM",

    "VALID_TO"
)


# ============================================================
# CAUSAL PROVENANCE CONTRACT
# ============================================================

ECB_RATE_REFERENCE_DATE_FIELD = (
    "TIME_PERIOD"
)

ECB_RATE_AVAILABLE_AT_FIELD = (
    "VALID_FROM"
)

ECB_RATE_VALUE_FIELD = (
    "OBS_VALUE"
)

ECB_RATE_ACTION_FIELD = (
    "ACTION"
)

ECB_RATE_VALID_TO_FIELD = (
    "VALID_TO"
)


# Current official-history audit:
#
# DFR:
#     69 rows
#
# MRR_FR:
#     48 rows
#
# MLFR:
#     69 rows
#
# Across all audited rows:
#
# ACTION == Replace
# OBS_VALUE non-empty
# VALID_FROM non-empty
# VALID_TO empty
# no duplicate logical observation versions
#
# Acquisition MUST fail closed if the official
# source later exposes semantics outside this
# frozen v1 contract.

ECB_RATE_ALLOWED_ACTIONS = (
    "Replace",
)

ECB_RATE_REQUIRE_VALUE = True

ECB_RATE_REQUIRE_VALID_FROM = True

ECB_RATE_REQUIRE_EMPTY_VALID_TO = True


# ============================================================
# LOGICAL RECORD IDENTITY
# ============================================================

ECB_RATE_SOURCE_RECORD_ID_RULE = (
    "KEY|TIME_PERIOD"
)


def build_ecb_rate_source_record_id(
    series_key,
    time_period
):

    if (
        not isinstance(
            series_key,
            str
        )

        or

        not series_key.strip()
    ):

        raise ValueError(
            "series_key must be "
            "non-empty."
        )


    if (
        not isinstance(
            time_period,
            str
        )

        or

        not time_period.strip()
    ):

        raise ValueError(
            "time_period must be "
            "non-empty."
        )


    return (
        series_key.strip()
        + "|"
        + time_period.strip()
    )


# ============================================================
# CONTENT VERSION CONTRACT
# ============================================================
#
# content_hash represents normalized economic
# contents, NOT observation/discovery metadata.
#
# Therefore provenance timestamps and transport
# metadata must not alter economic version
# identity.
# ============================================================

ECB_RATE_CONTENT_HASH_FIELDS = (

    "central_bank",

    "rate_name",

    "series_key",

    "reference_date",

    "value_percent",

    "source"
)


ECB_RATE_CONTENT_HASH_EXCLUDED_FIELDS = (

    "VALID_FROM",

    "VALID_TO",

    "ingested_at",

    "http_last_modified",

    "http_date",

    "http_etag"
)


# ============================================================
# TLS CONTRACT
# ============================================================

ECB_RATE_TLS_CA_PROVIDER = (
    "certifi"
)

ECB_RATE_TLS_VERIFICATION_REQUIRED = True

ECB_RATE_UNVERIFIED_TLS_ALLOWED = False