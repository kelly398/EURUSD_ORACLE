
"""Seat 25 F4.4.1: deterministic offline Fed agent checks.

Run: python -m backend.agents.test_fed_agent
No HTTP, database writes, broker orders, or HMM evaluation.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest

from backend.agents.contracts import (
    AgentCategory,
    AgentContext,
    AgentDirection,
    AgentSignalType,
)
from backend.agents.implementations.fed_agent import FedAgent
from backend.macro.fed_rate_protocol import build_fed_content_hash
from backend.macro.macro_schemas import CentralBankRateSnapshot
from backend.market_state.schemas import (
    CandleBar,
    DataQualityStatus,
    MarketStateSnapshot,
)


UTC = timezone.utc
RELEASE = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)
INGESTED = RELEASE + timedelta(minutes=1)
STATEMENT = "monetary20260916a"
EFFECTIVE = date(2026, 9, 17)


def fed_pair(
    *,
    statement=STATEMENT,
    release=RELEASE,
    ingested=INGESTED,
    effective=EFFECTIVE,
    lower=3.75,
    upper=4.0,
):
    return tuple(
        CentralBankRateSnapshot(
            central_bank="FED",
            source="FED_FOMC_RELEASE",
            rate_name=f"federal_funds_target_{bound}",
            source_record_id=f"FOMC:{statement}:{bound}",
            reference_date=effective,
            available_at=release,
            ingested_at=ingested,
            value_percent=value,
            content_hash=build_fed_content_hash(
                statement,
                f"federal_funds_target_{bound}",
                effective,
                format(Decimal(str(lower)), "f"),
                format(Decimal(str(upper)), "f"),
            ),
        )
        for bound, value in (("lower", lower), ("upper", upper))
    )


def make_context(*, as_of=RELEASE + timedelta(minutes=5), fed_data=()):
    candle = CandleBar(
        symbol="EURUSD",
        timeframe="M5",
        timestamp=as_of - timedelta(minutes=5),
        open=1.10,
        high=1.11,
        low=1.09,
        close=1.10,
        tick_volume=100,
        spread_points=10,
        real_volume=0.0,
        source="TEST",
    )
    market_state = MarketStateSnapshot(
        symbol="EURUSD",
        timeframe="M5",
        as_of=as_of,
        data_quality=DataQualityStatus.PASS,
        candle_count=1,
    )
    return AgentContext(
        symbol="EURUSD",
        timeframe="M5",
        as_of=as_of,
        market_state=market_state,
        candles=(candle,),
        fed_data=tuple(fed_data),
        economic_calendar=(),
    )


class FedAgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = FedAgent()

    def test_01_manifest_identity_and_contextual_type(self):
        self.assertEqual(self.agent.agent_id, "fed")
        self.assertEqual(self.agent.agent_name, "Federal Reserve")
        self.assertEqual(self.agent.category, AgentCategory.MACRO)
        self.assertEqual(self.agent.signal_type, AgentSignalType.CONTEXTUAL)

    def test_02_missing_fed_input_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "requires causal Fed"):
            self.agent.run(make_context())

    def test_03_single_decision_contextual_and_zero_confidence(self):
        opinion = self.agent.run(make_context(fed_data=fed_pair()))
        self.assertEqual(opinion.direction, AgentDirection.NEUTRAL)
        self.assertEqual(opinion.score, 0.0)
        self.assertEqual(opinion.confidence, 0.0)
        self.assertEqual(opinion.signal_type, AgentSignalType.CONTEXTUAL)

    def test_04_target_range_and_width_are_factual(self):
        evidence = self.agent.run(make_context(fed_data=fed_pair())).evidence
        self.assertEqual(evidence["latest_lower_target_percent"], 3.75)
        self.assertEqual(evidence["latest_upper_target_percent"], 4.0)
        self.assertEqual(evidence["latest_range_width_bps"], 25.0)
        self.assertEqual(evidence["latest_target_midpoint_percent"], 3.875)

    def test_05_no_transition_claim_without_prior_decision(self):
        evidence = self.agent.run(make_context(fed_data=fed_pair())).evidence
        self.assertEqual(evidence["target_range_change_vs_prior"], "insufficient_history")
        self.assertIsNone(evidence["midpoint_change_vs_prior_bps"])

    def test_06_announcement_precedes_effective_date(self):
        evidence = self.agent.run(make_context(fed_data=fed_pair())).evidence
        self.assertEqual(evidence["effective_date_status"], "future_effective_date")
        self.assertEqual(evidence["latest_effective_date"], "2026-09-17")

    def test_07_effective_date_itself_remains_time_unverified(self):
        context = make_context(
            as_of=datetime(2026, 9, 17, 0, 1, tzinfo=UTC),
            fed_data=fed_pair(),
        )
        evidence = self.agent.run(context).evidence
        self.assertEqual(
            evidence["effective_date_status"],
            "effective_today_time_unverified",
        )

    def test_08_effective_date_passed(self):
        context = make_context(
            as_of=datetime(2026, 9, 18, 12, tzinfo=UTC),
            fed_data=fed_pair(),
        )
        self.assertEqual(
            self.agent.run(context).evidence["effective_date_status"],
            "effective_date_passed",
        )

    def test_09_second_decision_raise_is_factual_not_trade_bias(self):
        previous = fed_pair(
            statement="monetary20260729a",
            release=datetime(2026, 7, 29, 18, tzinfo=UTC),
            ingested=datetime(2026, 7, 29, 18, 1, tzinfo=UTC),
            effective=date(2026, 7, 30),
            lower=3.5,
            upper=3.75,
        )
        opinion = self.agent.run(make_context(fed_data=previous + fed_pair()))
        self.assertEqual(opinion.evidence["fed_decision_count"], 2)
        self.assertEqual(opinion.evidence["target_range_change_vs_prior"], "higher")
        self.assertEqual(opinion.evidence["midpoint_change_vs_prior_bps"], 25.0)
        self.assertEqual(opinion.direction, AgentDirection.NEUTRAL)

    def test_10_second_decision_cut(self):
        previous = fed_pair(
            statement="monetary20260729a",
            release=datetime(2026, 7, 29, 18, tzinfo=UTC),
            ingested=datetime(2026, 7, 29, 18, 1, tzinfo=UTC),
            effective=date(2026, 7, 30),
            lower=4.0,
            upper=4.25,
        )
        opinion = self.agent.run(make_context(fed_data=previous + fed_pair()))
        self.assertEqual(opinion.evidence["target_range_change_vs_prior"], "lower")
        self.assertEqual(opinion.evidence["midpoint_change_vs_prior_bps"], -25.0)

    def test_11_second_decision_hold(self):
        previous = fed_pair(
            statement="monetary20260729a",
            release=datetime(2026, 7, 29, 18, tzinfo=UTC),
            ingested=datetime(2026, 7, 29, 18, 1, tzinfo=UTC),
            effective=date(2026, 7, 30),
            lower=3.75,
            upper=4.0,
        )
        evidence = self.agent.run(make_context(fed_data=previous + fed_pair())).evidence
        self.assertEqual(evidence["target_range_change_vs_prior"], "unchanged")
        self.assertEqual(evidence["midpoint_change_vs_prior_bps"], 0.0)

    def test_12_range_width_change_not_labelled_cut_or_hike(self):
        previous = fed_pair(
            statement="monetary20260729a",
            release=datetime(2026, 7, 29, 18, tzinfo=UTC),
            ingested=datetime(2026, 7, 29, 18, 1, tzinfo=UTC),
            effective=date(2026, 7, 30),
            lower=3.5,
            upper=4.0,
        )
        evidence = self.agent.run(make_context(fed_data=previous + fed_pair())).evidence
        self.assertEqual(evidence["target_range_change_vs_prior"], "mixed_or_width_changed")

    def test_13_bad_f1_hash_rejected_even_with_model_construct(self):
        pair = fed_pair()
        forged = pair[0].model_copy(update={"content_hash": "f" * 64})
        # AgentContext checks causal structure; the agent also checks F1 hashes.
        context = make_context(fed_data=(forged, pair[1]))
        with self.assertRaisesRegex(RuntimeError, "inconsistent rate evidence"):
            self.agent.run(context)

    def test_14_misordered_fed_input_blocked_by_context(self):
        with self.assertRaisesRegex(ValueError, "strictly ordered"):
            make_context(fed_data=fed_pair()[::-1])

    def test_15_no_claimed_certification_or_calendar_interpretation(self):
        evidence = self.agent.run(make_context(fed_data=fed_pair())).evidence
        self.assertFalse(evidence["historical_source_version_certified"])
        self.assertFalse(evidence["historical_replay_admission_authorized"])
        self.assertFalse(evidence["fed_calendar_interpretation_available"])
        self.assertFalse(evidence["directional_mapping_authorized"])
        self.assertFalse(evidence["relative_rate_differential_available"])
        self.assertTrue(evidence["economic_hash_consistency_checked"])

    def test_16_non_agent_context_rejected(self):
        with self.assertRaisesRegex(TypeError, "AgentContext"):
            self.agent.run(object())


if __name__ == "__main__":
    unittest.main(verbosity=2)
