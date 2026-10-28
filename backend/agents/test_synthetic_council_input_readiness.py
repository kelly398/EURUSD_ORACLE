
"""M53-56: Council input families, causal receipts, rejection and shadow HOLD."""

from datetime import timedelta, timezone
import json

import pytest
from sqlalchemy import event

import backend.agents.synthetic_council_input_readiness as readiness
from backend.agents.synthetic_council_input_readiness import (
    FAMILIES, InputReadinessError, inspect_council_input_readiness,
    integrated_synthetic_readiness, run,
)
from backend.agents.synthetic_forward_variable_replay import make_variable_demo
from backend.data_engine.forward_store import ticks


@pytest.fixture
def demo():
    store, policy, context, engine = make_variable_demo(capture_count=5)
    try:
        yield store, policy, context, engine
    finally:
        engine.dispose()


def modified(context, field, change, *, index=0):
    """Create deliberately untrusted nested context copies for negative tests."""
    members = getattr(context, field)
    item = members[index]
    changed = item.model_copy(update=change)
    return context.model_copy(update={field: (*members[:index], changed, *members[index+1:])})


def test_m53_all_thirteen_council_input_families(demo):
    _, _, ctx, _ = demo
    r = inspect_council_input_readiness(ctx, synthetic_only=True)
    assert r['input_family_count'] == 13
    assert set(r['input_family_counts']) == set(FAMILIES)
    assert all(x > 0 for x in r['input_family_counts'].values())
    assert r['input_family_counts']['reliability'] == 28
    assert r['input_family_counts']['fed_data'] == 2
    assert r['status'] == 'SYNTHETIC_TIMING_CHECKS_PASS_UNATTESTED'
    assert r['blockers'] == []
    assert len(r['input_evidence_sha256']) == 64
    assert all(r[x] is False for x in (
        'synthetic_context_source_independently_verified',
        'macro_version_receipts_independently_verified',
        'calendar_completeness_independently_verified',
        'portfolio_and_risk_limits_independently_attested',
        'market_state_derivation_proven_from_forward_store',
        'training_authorized', 'execution_authorized'))
    assert r['final_action'] == 'HOLD'


def test_m54_known_future_calendar_event_is_not_a_future_receipt(demo):
    _, _, ctx, _ = demo
    assert ctx.economic_calendar[0].scheduled_at > ctx.as_of
    r = inspect_council_input_readiness(ctx, synthetic_only=True)
    assert r['blockers'] == []


@pytest.mark.parametrize('field', ['ecb_data', 'cot_data', 'economic_calendar'])
def test_m54_later_ingestion_is_blocked_for_all_macro_sources(demo, field):
    _, _, ctx, _ = demo
    altered = modified(ctx, field, {'ingested_at': ctx.as_of + timedelta(seconds=1)})
    r = inspect_council_input_readiness(altered, synthetic_only=True)
    assert field.upper() + ':INGESTED_AFTER_AS_OF' in r['blockers']
    assert r['status'] == 'BLOCKED_INPUT_READINESS'


def test_m54_existing_fed_contract_already_blocks_future_receipt(demo):
    _, _, ctx, _ = demo
    altered = modified(ctx, 'fed_data', {'ingested_at': ctx.as_of + timedelta(seconds=1)})
    with pytest.raises(InputReadinessError, match='schema revalidation'):
        inspect_council_input_readiness(altered, synthetic_only=True)


def test_m54_receipt_before_announced_availability_blocks(demo):
    _, _, ctx, _ = demo
    first = ctx.ecb_data[0]
    bad = modified(ctx, 'ecb_data', {'ingested_at': first.available_at - timedelta(seconds=1)})
    r = inspect_council_input_readiness(bad, synthetic_only=True)
    assert 'ECB_DATA:RECEIPT_BEFORE_AVAILABILITY' in r['blockers']


def test_m54_unverified_source_hash_format_blocks(demo):
    _, _, ctx, _ = demo
    bad = modified(ctx, 'ecb_data', {'content_hash': 'Z' * 64})
    r = inspect_council_input_readiness(bad, synthetic_only=True)
    assert 'ECB_DATA:SOURCE_HASH_FORMAT_INVALID' in r['blockers']


def test_m54_aware_non_utc_macro_clock_is_rejected(demo):
    _, _, ctx, _ = demo
    local = ctx.ecb_data[0].ingested_at.astimezone(timezone(timedelta(hours=2)))
    r = inspect_council_input_readiness(
        modified(ctx, 'ecb_data', {'ingested_at': local}), synthetic_only=True)
    assert 'ECB_DATA:CLOCK_NOT_UTC' in r['blockers']


@pytest.mark.parametrize('field', [
    'cot_data', 'ecb_data', 'fed_data', 'economic_calendar',
    'cross_asset_data', 'calendar_coverage', 'risk_state',
    'risk_limits', 'reliability', 'synthesis_policy', 'ticks',
])
def test_m55_missing_required_synthetic_input_blocks(demo, field):
    _, _, ctx, _ = demo
    replacement = () if isinstance(getattr(ctx, field), tuple) else None
    bad = ctx.model_copy(update={field: replacement})
    r = inspect_council_input_readiness(bad, synthetic_only=True)
    assert field.upper() + ':ABSENT' in r['blockers']


def test_m55_calendar_coverage_must_contain_event_source(demo):
    _, _, ctx, _ = demo
    coverage = ctx.calendar_coverage.model_copy(update={'sources': ('UNKNOWN',)})
    r = inspect_council_input_readiness(
        ctx.model_copy(update={'calendar_coverage': coverage}), synthetic_only=True)
    assert 'CALENDAR_COVERAGE:EVENT_SOURCE_NOT_COVERED' in r['blockers']


def test_m55_calendar_currency_coverage_must_include_both(demo):
    _, _, ctx, _ = demo
    coverage = ctx.calendar_coverage.model_copy(update={'currencies': ('EUR',)})
    r = inspect_council_input_readiness(
        ctx.model_copy(update={'calendar_coverage': coverage}), synthetic_only=True)
    assert 'CALENDAR_COVERAGE:CURRENCY_COVERAGE_INCOMPLETE' in r['blockers']


def test_m55_reliability_must_cover_all_primary_seats(demo):
    _, _, ctx, _ = demo
    r = inspect_council_input_readiness(
        ctx.model_copy(update={'reliability': ctx.reliability[:-1]}), synthetic_only=True)
    assert 'RELIABILITY:PRIMARY_SEAT_COVERAGE_INCOMPLETE' in r['blockers']


def test_m55_older_risk_snapshot_fails(demo):
    _, _, ctx, _ = demo
    risk = ctx.risk_state.model_copy(update={'as_of': ctx.as_of - timedelta(seconds=1)})
    r = inspect_council_input_readiness(
        ctx.model_copy(update={'risk_state': risk}), synthetic_only=True)
    assert 'RISK_STATE:STATE_NOT_CURRENT' in r['blockers']


def test_m55_risk_kill_switch_blocks(demo):
    _, _, ctx, _ = demo
    risk = ctx.risk_state.model_copy(update={'kill_switch': True})
    r = inspect_council_input_readiness(
        ctx.model_copy(update={'risk_state': risk}), synthetic_only=True)
    assert 'RISK_STATE:RISK_GOVERNANCE_VETO' in r['blockers']


def test_m55_older_last_candle_fails(demo):
    _, _, ctx, _ = demo
    r = inspect_council_input_readiness(
        ctx.model_copy(update={'candles': ctx.candles[:-1]}), synthetic_only=True)
    assert 'CANDLES:LAST_CLOSED_M5_NOT_CURRENT' in r['blockers']


def test_m55_different_council_schema_is_not_silently_admitted(demo):
    _, _, ctx, _ = demo
    with pytest.raises(InputReadinessError, match='schema revalidation'):
        inspect_council_input_readiness(
            modified(ctx, 'cross_asset_data', {'ingested_at': ctx.as_of + timedelta(seconds=2)}),
            synthetic_only=True)


def test_m55_synthetic_opt_in_and_typed_context_required(demo):
    _, _, ctx, _ = demo
    with pytest.raises(InputReadinessError, match='opt-in'):
        inspect_council_input_readiness(ctx)
    with pytest.raises(InputReadinessError, match='Typed'):
        inspect_council_input_readiness({}, synthetic_only=True)


def test_m56_one_source_backed_audit_runs_original_council(demo):
    store, policy, ctx, _ = demo
    r = integrated_synthetic_readiness(store, policy, ctx, capture_count=5,
                                       synthetic_only=True)
    assert r['status'] == 'SYNTHETIC_INPUT_READINESS_SHADOW_HOLD'
    assert r['blockers'] == []
    assert r['council_invoked'] is True
    assert r['council_seats'] == 31
    assert r['replay_report_sha256']
    assert r['all_external_source_attestations_complete'] is False
    assert r['real_forward_admission'] is False
    assert r['training_authorized'] is False
    assert r['execution_authorized'] is False
    assert r['final_action'] == 'HOLD'


def test_m56_deterministic_readiness_and_replay_receipt(demo):
    store, policy, ctx, _ = demo
    a = integrated_synthetic_readiness(store, policy, ctx, capture_count=5, synthetic_only=True)
    b = integrated_synthetic_readiness(store, policy, ctx, capture_count=5, synthetic_only=True)
    assert a == b
    assert len(a['report_sha256']) == 64


def test_m56_missing_macro_never_reaches_audit(demo, monkeypatch):
    store, policy, ctx, _ = demo
    def forbidden(*args, **kwargs):
        raise AssertionError('Replay must not be called')
    monkeypatch.setattr(readiness, 'audit_variable_replay', forbidden)
    r = integrated_synthetic_readiness(store, policy,
        ctx.model_copy(update={'fed_data': ()}), capture_count=5, synthetic_only=True)
    assert r['status'] == 'BLOCKED'
    assert r['council_invoked'] is False
    assert r['council_seats'] == 0


def test_m56_forged_but_time_valid_fixture_cannot_dispatch(demo, monkeypatch):
    store, policy, ctx, _ = demo
    def forbidden(*args, **kwargs):
        raise AssertionError('Forged fixture reached Council replay')
    monkeypatch.setattr(readiness, 'audit_variable_replay', forbidden)
    different = ctx.risk_limits.model_copy(update={'max_spread_pips': 99.0})
    altered = ctx.model_copy(update={'risk_limits': different})
    r = integrated_synthetic_readiness(store, policy, altered, capture_count=5,
                                       synthetic_only=True)
    assert r['blockers'] == ['SYNTHETIC_FIXTURE_IDENTITY_MISMATCH']
    assert r['council_invoked'] is False


def test_m56_source_tamper_blocks_after_input_preflight(demo):
    store, policy, ctx, engine = demo
    with engine.begin() as conn:
        conn.execute(ticks.update().values(bid=2.0))
    r = integrated_synthetic_readiness(store, policy, ctx, capture_count=5,
                                       synthetic_only=True)
    assert r['status'] == 'BLOCKED'
    assert r['council_invoked'] is False
    assert any('TICK_SOURCE_INTEGRITY_INVALID' in x for x in r['blockers'])


def test_m56_review_and_dispatch_issues_select_only(demo):
    store, policy, ctx, engine = demo
    statements = []
    def collect(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.lstrip().upper())
    event.listen(engine, 'before_cursor_execute', collect)
    try:
        integrated_synthetic_readiness(store, policy, ctx,
                                       capture_count=5, synthetic_only=True)
    finally:
        event.remove(engine, 'before_cursor_execute', collect)
    assert statements and all(x.startswith('SELECT') for x in statements)


def test_m56_cli_all_scenarios(capsys):
    assert run(['--synthetic-only', '--scenario', 'all']) == 0
    text = capsys.readouterr().out
    assert 'SCENARIO complete | SYNTHETIC_INPUT_READINESS_SHADOW_HOLD | seats=31' in text
    for name in ('missing_fed', 'late_calendar', 'missing_coverage', 'stale_risk'):
        assert f'SCENARIO {name} | BLOCKED | seats=0' in text
    assert 'ECONOMIC_CALENDAR:INGESTED_AFTER_AS_OF' in text
    assert text.count('FINAL=HOLD') == 5
    assert 'NO SOURCE ATTESTATION' in text


def test_m56_cli_json_and_eight_captures(capsys):
    assert run(['--synthetic-only', '--captures', '8',
                '--scenario', 'complete', '--json']) == 0
    result, = json.loads(capsys.readouterr().out)
    assert result['result']['council_seats'] == 31
    assert result['result']['execution_authorized'] is False
    assert result['result']['final_action'] == 'HOLD'


def test_m56_cli_requires_flag():
    with pytest.raises(SystemExit) as exc:
        run(['--scenario', 'complete'])
    assert exc.value.code == 2
