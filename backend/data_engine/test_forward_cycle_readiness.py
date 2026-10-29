
"""C4+D1-D3: local Git history, real forward gates, never Council dispatch."""

from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import event

from backend.data_engine.clock_policy import implementation_sha256
from backend.data_engine.forward_cycle_readiness import (
    ForwardCycleError, _digest, audit_bound_clock_git_history,
    evaluate_forward_cycle_gate, inspect_forward_cycle, run,
)
from backend.data_engine.forward_source_lineage import inspect_forward_source_lineage
from backend.data_engine.test_forward_evidence_health import make_fixture
from backend.data_engine.forward_store import bars, ticks


@pytest.fixture
def demo():
    engine, policy, cutoff = make_fixture()
    try:
        yield engine, policy, cutoff
    finally:
        engine.dispose()


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, check=True).stdout.decode().strip()


@pytest.fixture
def gitrepo(tmp_path):
    repo = tmp_path / "history"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    path = repo / "backend" / "data_engine" / "clock_policy.py"
    path.parent.mkdir(parents=True)
    original = Path(__file__).with_name("clock_policy.py").read_bytes().replace(b"\r\n", b"\n")
    path.write_bytes(original)
    _git(repo, "add", "--", "backend/data_engine/clock_policy.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.org",
         "commit", "-qm", "clock version A")
    revision = _git(repo, "rev-parse", "HEAD")
    path.write_bytes(original + b"\n# test-only revision\n")
    _git(repo, "add", "--", "backend/data_engine/clock_policy.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.org",
         "commit", "-qm", "clock version B")
    return repo, revision, original


def lineage(demo):
    engine, policy, cutoff = demo
    return inspect_forward_source_lineage(
        engine, epoch_id=policy.epoch_id, as_of=cutoff)


def history_from_lineage(source, repo):
    clock = source["clock_lineage"]
    return audit_bound_clock_git_history(
        clock["bound_implementation_sha256"],
        clock["local_implementation_sha256"], repo=repo)


def inspect(demo, gitrepo, **kwargs):
    engine, policy, cutoff = demo
    repo = gitrepo[0]
    return inspect_forward_cycle(engine, epoch_id=policy.epoch_id,
                                 as_of=cutoff, repo=repo, **kwargs)


def test_c4_exact_git_blob_match_recovered(gitrepo):
    repo, revision, original = gitrepo
    digest = sha256(original).hexdigest()
    audit = audit_bound_clock_git_history(digest, sha256(b"new").hexdigest(),
                                          repo=repo)
    assert audit["local_history_status"] == "LOCAL_GIT_BLOB_CANDIDATE_FOUND"
    assert audit["matches"] == [{"revision": revision,
                                "match_type": "EXACT_GIT_BLOB_BYTES"}]
    assert audit["epoch_policy_modified"] is False
    assert audit["historical_utc_certified"] is False


def test_c4_git_history_does_not_authorize_clock_reuse(gitrepo):
    repo, _, original = gitrepo
    digest = sha256(original).hexdigest()
    audit = audit_bound_clock_git_history(digest, sha256(b"different").hexdigest(),
                                          repo=repo)
    assert audit["current_worktree_matches_bound"] is False
    assert audit["execution_authorized"] is False
    assert "MATCH_DOES_NOT_AUTHORIZE_REPLAY_UNDER_DIFFERENT_CODE" in audit["limitations"]


def test_c4_crlf_checkout_equivalence_is_not_exact(gitrepo):
    repo, revision, original = gitrepo
    crlf = original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if crlf == original:
        pytest.skip("Fixture does not have LF line endings")
    audit = audit_bound_clock_git_history(sha256(crlf).hexdigest(),
                                          sha256(b"other").hexdigest(), repo=repo)
    assert audit["matches"] == [{"revision": revision,
                                "match_type": "CRLF_CHECKOUT_EQUIVALENT"}]
    assert audit["historical_utc_certified"] is False


def test_c4_unknown_hash_not_claimed_found(gitrepo):
    audit = audit_bound_clock_git_history("f" * 64, "1" * 64, repo=gitrepo[0])
    assert audit["matches"] == []
    assert audit["local_history_status"] == "NOT_FOUND_IN_BOUNDED_LOCAL_GIT_HISTORY"


def test_c4_unavailable_git_remains_hold(tmp_path):
    audit = audit_bound_clock_git_history("a" * 64, "b" * 64, repo=tmp_path)
    assert audit["local_history_status"] == "LOCAL_GIT_HISTORY_UNAVAILABLE"
    assert audit["execution_authorized"] is False


@pytest.mark.parametrize("bad", [0, 201, True, "100", 3.5])
def test_c4_invalid_git_search_bounds_rejected(bad):
    with pytest.raises(ForwardCycleError, match="max_versions"):
        audit_bound_clock_git_history("a" * 64, "b" * 64,
                                      max_versions=bad)


def test_c4_clock_hash_arguments_must_be_valid():
    with pytest.raises(ForwardCycleError, match="Valid clock"):
        audit_bound_clock_git_history("bad", "a" * 64)


def test_d1_recent_synthetic_feed_locally_timely_but_real_cycle_blocked(demo, gitrepo):
    report = inspect(demo, gitrepo)
    assert report["technical_observation_status"] == "LOCAL_TIMING_CHECKS_PASS_UNATTESTED"
    assert report["technical_blockers"] == []
    assert report["unverified_input_count"] == 13
    assert report["council_seats_run"] == 0
    assert report["council_invoked"] is False
    assert report["real_forward_council_readiness"] == "BLOCKED_UNVERIFIED_EVIDENCE"
    assert report["final_action"] == "HOLD"


def test_d1_stale_capture_fails_closed(demo, gitrepo):
    engine, policy, cutoff = demo
    report = inspect_forward_cycle(engine, epoch_id=policy.epoch_id,
                                   as_of=cutoff + timedelta(minutes=20),
                                   repo=gitrepo[0])
    assert "CAPTURE_INGESTION_NOT_CURRENT" in report["technical_blockers"]
    assert "LATEST_TICK_NOT_CURRENT" in report["technical_blockers"]
    assert "LAST_CLOSED_M5_NOT_CURRENT" in report["technical_blockers"]
    assert report["council_seats_run"] == 0


def test_d1_missing_latest_tick_blocks(demo, gitrepo):
    engine, _, cutoff = demo
    with engine.begin() as conn:
        conn.execute(ticks.update().where(ticks.c.timestamp == cutoff - timedelta(seconds=2))
                     .values(ingested_at=cutoff + timedelta(seconds=30)))
    report = inspect(demo, gitrepo)
    assert "LATEST_TICK_NOT_CURRENT" in report["technical_blockers"]
    assert report["final_action"] == "HOLD"


def test_d1_missing_latest_bar_blocks(demo, gitrepo):
    engine, _, cutoff = demo
    with engine.begin() as conn:
        conn.execute(bars.delete().where(bars.c.timestamp == cutoff - timedelta(minutes=5)))
    report = inspect(demo, gitrepo)
    assert "LAST_CLOSED_M5_NOT_CURRENT" in report["technical_blockers"]


def test_d2_full_family_names_are_reported(demo, gitrepo):
    r = inspect(demo, gitrepo)
    assert r["unverified_input_families"] == sorted([
        "market_state", "candles", "ticks", "cot_data", "ecb_data",
        "fed_data", "economic_calendar", "cross_asset_data", "calendar_coverage",
        "risk_state", "risk_limits", "reliability", "synthesis_policy",
    ])
    assert "EXTERNAL_COUNCIL_SOURCE_PROVENANCE_UNVERIFIED" in r["real_council_dispatch_blockers"]


def test_d2_matching_git_clock_hash_does_not_admit_sources(demo, gitrepo):
    r = inspect(demo, gitrepo)
    assert r["clock_git_history_status"] == "LOCAL_GIT_BLOB_CANDIDATE_FOUND"
    assert r["unverified_input_count"] == 13
    assert r["execution_authorized"] is False


def test_d3_local_clock_mismatch_blocks_even_when_history_matched(demo, gitrepo, monkeypatch):
    monkeypatch.setattr(
        "backend.data_engine.forward_evidence_health.implementation_sha256",
        lambda: "e" * 64,
    )
    r = inspect(demo, gitrepo)
    assert "BOUND_CLOCK_IMPLEMENTATION_MISMATCH" in r["technical_blockers"]
    assert r["real_forward_admission"] is False


def test_d3_truncated_capture_sample_blocks(demo, gitrepo):
    r = inspect(demo, gitrepo, max_captures=1)
    assert "CAPTURE_SAMPLE_INCOMPLETE" in r["technical_blockers"]
    assert r["council_seats_run"] == 0


def test_d3_invalid_lineage_hash_blocks_before_any_readiness(demo, gitrepo):
    source = lineage(demo)
    clock = history_from_lineage(source, gitrepo[0])
    source["health_blockers"] = ["FAKE"]
    with pytest.raises(ForwardCycleError, match="fingerprint"):
        evaluate_forward_cycle_gate(source, clock)


def test_d3_different_epoch_clock_ids_cannot_be_combined(demo, gitrepo):
    source = lineage(demo)
    clock = history_from_lineage(source, gitrepo[0])
    clock["bound_implementation_sha256"] = "c" * 64
    clock["report_sha256"] = _digest({k: v for k, v in clock.items() if k != "report_sha256"})
    with pytest.raises(ForwardCycleError, match="identities disagree"):
        evaluate_forward_cycle_gate(source, clock)


def test_d3_illegal_real_source_certification_claim_rejected(demo, gitrepo):
    source = lineage(demo)
    clock = history_from_lineage(source, gitrepo[0])
    source["council_input_families"][0]["real_point_in_time_lineage_proven"] = True
    source["report_sha256"] = _digest({k: v for k, v in source.items() if k != "report_sha256"})
    with pytest.raises(ForwardCycleError, match="Unsupported real"):
        evaluate_forward_cycle_gate(source, clock)


def test_d3_report_deterministic_and_recomputes_digest(demo, gitrepo):
    a = inspect(demo, gitrepo)
    b = inspect(demo, gitrepo)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_d3_forward_inspection_select_only(demo, gitrepo):
    engine, _, _ = demo
    statements = []

    def capture(_conn, _cur, sql, _params, _ctx, _many):
        statements.append(sql.strip().upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        inspect(demo, gitrepo)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements and all(s.startswith("SELECT") for s in statements)


def test_d3_cli_read_only_options_required_before_db_connection():
    for args in ([], ["--read-only-db"], ["--git-read-only"]):
        with pytest.raises(SystemExit) as error:
            run(args)
        assert error.value.code == 2


def test_d3_cli_invalid_epoch_rejected_before_db_connection(capsys):
    assert run(["--read-only-db", "--git-read-only", "--epoch-id", "bad",
                "--as-of", "2026-10-27T14:05:00Z"]) == 1
    assert "INSPECTION BLOCKED" in capsys.readouterr().out
