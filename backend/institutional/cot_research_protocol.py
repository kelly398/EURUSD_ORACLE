# ============================================================
# EUR/USD ORACLE
# SEAT 22 — COT / INSTITUTIONAL POSITIONING
# DEVELOPMENT RESEARCH PROTOCOL
# ============================================================
#
# This protocol MUST be frozen before inspecting predictive
# Seat 22 development results.
#
# The reserved test split must not be evaluated until the
# analytical role and production scoring policy are frozen.
# ============================================================


PROTOCOL_VERSION = (
    "seat22_cot_development_v1"
)

FEATURE_VERSION = (
    "eurusd_cot_tff_v1"
)


# ============================================================
# EVENT DEFINITION
# ============================================================
#
# One research observation equals one newly available CFTC
# TFF report.
#
# COT information MUST NOT be duplicated across M5 candles
# and treated as independent observations.
# ============================================================

RESEARCH_UNIT = (
    "one_causal_cot_release"
)


# ============================================================
# PRICE ENTRY RULE
# ============================================================
#
# available_at commonly occurs a few seconds after a 5-minute
# boundary, e.g. 19:30:08 UTC.
#
# The M5 bar beginning at 19:30 therefore straddles publication
# and cannot be used as a causal entry observation.
#
# Research starts from the OPEN of the first completed-data M5
# bar whose timestamp is strictly after available_at.
# ============================================================

ENTRY_RULE = (
    "first_m5_bar_start_strictly_after_cot_available_at"
)


# ============================================================
# OUTCOME HORIZONS
# ============================================================
#
# Count completed M5 bars rather than elapsed wall-clock time.
# This naturally traverses FX weekends/closures.
#
# Five trading days is intentionally excluded because it can
# reach the following COT publication.
# ============================================================

M5_BARS_PER_TRADING_DAY = 288

SECONDARY_HORIZON_BARS = (
    288,
    864
)

PRIMARY_HORIZON_BARS = 1152


# ============================================================
# COT HISTORY REQUIREMENT
# ============================================================
#
# 52 prior reports are required for causal annual crowding
# statistics. The current observation is the 53rd record.
# ============================================================

MINIMUM_COT_HISTORY_REPORTS = 53

ROLLING_REFERENCE_REPORTS = 52


# ============================================================
# FROZEN DEVELOPMENT / TEST GOVERNANCE
# ============================================================

DEVELOPMENT_SPLITS = (
    "train",
    "validation"
)

SEALED_SPLIT = (
    "test"
)

TEST_EVALUATED = False


# Readiness audit observed on 2026-10-07.
#
# These counts document the current research sample and make
# its small-sample limitation explicit.
OBSERVED_RELEASE_EVENT_COUNTS = {

    "train":
        37,

    "validation":
        7,

    "test":
        8
}


# ============================================================
# PRE-REGISTERED DIRECTIONAL CANDIDATES
# ============================================================
#
# Each candidate is evaluated independently.
#
# No multivariate model.
# No feature-selection search.
# No threshold optimization.
#
# Direction/sign may be characterized on TRAIN only.
# Validation is used only to check whether the relationship
# remains economically consistent out of sample.
# ============================================================

DIRECTIONAL_CANDIDATE_FEATURES = (

    "asset_manager_net_share",

    "leveraged_money_net_share",

    "asset_manager_net_share_change_1",

    "leveraged_money_net_share_change_1",

    "asset_manager_net_share_change_4",

    "leveraged_money_net_share_change_4",

    "asset_manager_minus_leveraged_net_share"
)


# ============================================================
# CONTEXT / CROWDING FEATURES
# ============================================================
#
# These may prove useful even if Seat 22 does not earn a
# directional Council vote.
# ============================================================

CONTEXT_FEATURES = (

    "dealer_net_share",

    "open_interest_change_1",

    "asset_manager_net_share_z52",

    "leveraged_money_net_share_z52"
)


ALLOW_MULTIVARIATE_MODEL = False

ALLOW_FEATURE_SEARCH = False

ALLOW_THRESHOLD_TUNING = False


# ============================================================
# DEVELOPMENT EVALUATION
# ============================================================
#
# Primary evaluation:
#     4-trading-day forward EUR/USD return.
#
# Secondary:
#     1-day and 3-day returns.
#
# Because the sample is small, effect sizes and sign stability
# matter more than nominal p-values.
# ============================================================

PRIMARY_METRIC = (
    "spearman_rank_information_coefficient"
)

SECONDARY_METRICS = (

    "mean_forward_return_by_feature_half",

    "median_forward_return_by_feature_half"
)


# ============================================================
# PROMOTION GOVERNANCE
# ============================================================
#
# Seat 22 is NOT assumed directional.
#
# Unless development evidence is stable enough to justify a
# frozen directional mapping before the test is opened, the
# production seat remains CONTEXTUAL.
# ============================================================

DEFAULT_PRODUCTION_ROLE = (
    "contextual"
)