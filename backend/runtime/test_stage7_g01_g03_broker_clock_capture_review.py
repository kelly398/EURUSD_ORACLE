
"""Stage 7 G01-G03: genuine archived record contracts + synthetic attack tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime import stage7_g01_g03_broker_clock_capture_review as m

UTC = timezone.utc
SOURCE_REPO = Path(__file__).resolve().parents[2]
HISTORY = [m.PINNED_POLICY, *m.HISTORY_FILES.values()]


def z(t):
    return t.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def cutoff():
    return datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / 'repo'
    root = tmp_path / 'evidence'
    repo.mkdir()
    root.mkdir()
    for relative in HISTORY:
        source = SOURCE_REPO / relative
        if not source.exists():
            pytest.fail('Original archived historical source missing: ' + relative)
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    t = cutoff()
    manifest = {'schema_version': 1, 'historical_epoch_id': m.EPOCH,
                'created_at_utc': z(t - timedelta(days=1)), 'artifacts': []}
    (root / 'candidate_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return repo, root, t, manifest


def review(env, *, asof=None, filename='candidate_manifest.json', epoch=m.EPOCH,
           policy=None):
    repo, root, t, _ = env
    return m.assess_g01_g03(
        epoch_id=epoch, as_of=z(t if asof is None else asof),
        repository_root=str(repo), evidence_root=str(root),
        policy_file=str(policy or repo / m.PINNED_POLICY), manifest=filename)


def put(env, doc):
    repo, root, t, _ = env
    (root / 'candidate_manifest.json').write_text(json.dumps(doc), encoding='utf-8')


def candidate(env, gate='G01', suffix='A', data=b'downloaded research evidence'):
    repo, root, t, manifest = env
    name = 'candidate_' + suffix + '.pdf'
    (root / name).write_bytes(data)
    return dict(
        artifact_id='RECORD_' + suffix, gate_id=gate,
        evidence_kind={'G01': 'BROKER_IDENTITY_RECORD',
                       'G02': 'INDEPENDENT_CLOCK_RECORD',
                       'G03': 'RAW_CAPTURE_RECEIPT',
                       'G05': 'SOURCE_FAMILY_PUBLICATION'}[gate],
        file_name=name,
        expected_sha256=sha256(data).hexdigest(),
        origin_url='https://provider.example.org/records',
        published_at_utc=None,
        acquired_at_utc=z(t-timedelta(seconds=30)),
        family_slot=None,
    )


def test_actual_pinned_historical_reports_are_readable(env):
    doc = review(env)
    assert doc['historical_broker_probe']['status'] == 'ORIGINAL_PROBE_BYTES_MATCH_PIN_UNATTESTED'
    assert doc['historical_broker_probe']['local_pin_match'] is True
    assert doc['historical_broker_probe']['observation_count'] == 3
    assert doc['historical_broker_probe']['historical_utc_certified'] is False
    assert doc['historical_clock_candidate_audit']['status'] == 'HISTORICAL_ANALYSIS_ONLY_UNATTESTED'
    assert doc['historical_clock_candidate_audit']['candidate_offsets_seconds'] == [7200, 10800]
    assert doc['historical_clock_candidate_audit']['local_bars_scanned'] == 75528
    assert doc['historical_capture_health_summary']['status'] == 'ARCHIVED_LOCAL_CAPTURE_SUMMARY_UNATTESTED'
    assert doc['historical_capture_health_summary']['reported_capture_batches'] == 10
    assert doc['historical_capture_health_summary']['reported_ticks'] == 1507
    assert doc['historical_capture_health_summary']['reported_bars'] == 6


def test_all_three_gates_hold_without_actual_external_candidates(env):
    report = review(env)
    assert [x['gate_id'] for x in report['g01_g03_evidence_gates']] == ['G01', 'G02', 'G03']
    assert all(not x['independently_verified'] for x in report['g01_g03_evidence_gates'])
    assert all(not x['gate_admitted'] for x in report['g01_g03_evidence_gates'])
    assert all(x['local_candidate_count'] == 0 for x in report['g01_g03_evidence_gates'])
    assert report['historical_utc_certified'] is False
    assert report['final_action'] == 'HOLD'


def test_report_digest_determinism_and_local_integrity(env):
    a, b = review(env), review(env)
    assert a == b
    assert a['report_sha256'] == m._canonical_hash({k: v for k,v in a.items()
                                                      if k != 'report_sha256'})
    assert a['original_policy_sha256'] == m.POLICY_SHA256
    assert a['independent_external_gates_verified'] == 0
    assert a['independent_source_families_admitted'] == 0


def test_original_historical_sources_not_changed(env):
    repo, root, t, manifest = env
    before = {p: sha256((repo/p).read_bytes()).hexdigest() for p in HISTORY}
    review(env)
    after = {p: sha256((repo/p).read_bytes()).hexdigest() for p in HISTORY}
    assert before == after


def test_local_g01_g02_g03_candidates_do_not_approve_any_gate(env):
    repo, root, t, manifest = env
    for i, gate in enumerate(('G01', 'G02', 'G03')):
        manifest['artifacts'].append(candidate(env, gate, str(i+1)))
    put(env, manifest)
    r = review(env)
    assert [x['local_candidate_count'] for x in r['g01_g03_evidence_gates']] == [1, 1, 1]
    assert all(x['local_hash_matches'] == 1 for x in r['g01_g03_evidence_gates'])
    assert all(x['gate_admitted'] is False for x in r['g01_g03_evidence_gates'])
    assert not r['broker_identity_independently_verified']
    assert not r['capture_custody_independently_verified']
    assert not r['operator_approval_granted']


def test_g05_ecb_candidate_does_not_count_toward_g01_g03(env):
    repo, root, t, manifest = env
    manifest['artifacts'].append(candidate(env, 'G05', 'ECB'))
    put(env, manifest)
    report = review(env)
    assert report['candidate_intake_total'] == 1
    assert [x['local_candidate_count'] for x in report['g01_g03_evidence_gates']] == [0,0,0]


def test_unknown_publication_time_is_not_independent_custody(env):
    repo, root, t, manifest = env
    manifest['artifacts'].append(candidate(env, 'G02', 'CLOCK'))
    put(env, manifest)
    record = review(env)['g01_g03_evidence_gates'][1]['local_candidates'][0]
    assert record['status'] == 'LOCAL_BYTES_MATCH_PUBLICATION_TIME_UNKNOWN'
    assert record['independently_authenticated'] is False


def test_local_mismatched_candidate_has_zero_hash_matches(env):
    repo, root, t, manifest = env
    manifest['artifacts'].append(candidate(env, 'G03', 'CAP'))
    put(env, manifest)
    (root / 'candidate_CAP.pdf').write_bytes(b'mutated')
    report = review(env)
    assert report['g01_g03_evidence_gates'][2]['local_hash_matches'] == 0
    assert report['g01_g03_evidence_gates'][2]['local_candidates'][0]['status'] == 'HASH_MISMATCH_QUARANTINED'


def test_report_does_not_echo_sensitive_broker_documents(env):
    repo, root, t, manifest = env
    content = b'BROKER_PASSWORD_42'
    manifest['artifacts'].append(candidate(env, 'G01', 'SECRET', content))
    put(env, manifest)
    public = json.dumps(review(env))
    assert 'BROKER_PASSWORD_42' not in public
    assert 'https://provider.example.org/records' not in public
    assert str(root) not in public
    assert str(repo) not in public


@pytest.mark.parametrize('field,value', [
    ('historical_admission', True), ('symbol', 'GBPUSD'), ('schema_version', 2)
])
def test_tampered_historical_policy_denied(env, field, value):
    repo, root, t, manifest = env
    p = repo / m.PINNED_POLICY
    d = json.loads(p.read_text())
    d[field] = value
    p.write_text(json.dumps(d))
    with pytest.raises((m.G0103ReviewError, m.CandidateIntakeError)):
        review(env)


def test_changed_historical_probe_bytes_denied(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['probe']
    p.write_bytes(p.read_bytes() + b' ')
    with pytest.raises(m.G0103ReviewError, match='probe bytes'):
        review(env)


def test_archived_clock_self_certification_refused(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['clock_audit']
    d = json.loads(p.read_text())
    d['historical_utc_certified'] = True
    p.write_text(json.dumps(d))
    with pytest.raises(m.G0103ReviewError):
        review(env)


def test_archived_clock_fake_archive_migration_refused(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['clock_audit']
    d = json.loads(p.read_text())
    d['legacy_rows_modified'] = 1
    p.write_text(json.dumps(d))
    with pytest.raises(m.G0103ReviewError):
        review(env)


def test_archived_clock_retraining_claim_refused(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['clock_audit']
    d = json.loads(p.read_text())
    d['models_retrained'] = 1
    p.write_text(json.dumps(d))
    with pytest.raises(m.G0103ReviewError):
        review(env)


@pytest.mark.parametrize('offset,count', [('7200', 31017), ('90000', 0), ('garbage', 0)])
def test_archived_clock_bad_offset_distribution_denied(env, offset, count):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['clock_audit']
    d = json.loads(p.read_text())
    d['candidate_offset_seconds'][offset] = count
    p.write_text(json.dumps(d))
    with pytest.raises(m.G0103ReviewError):
        review(env)


def test_archived_capture_forged_operational_permission_denied(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['capture_health']
    d = json.loads(p.read_text())
    d['health_as_of_last_capture']['execution_authorized'] = True
    p.write_text(json.dumps(d))
    with pytest.raises(m.G0103ReviewError):
        review(env)


def test_archived_capture_other_epoch_denied(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['capture_health']
    d = json.loads(p.read_text())
    d['health_as_of_last_capture']['epoch_id'] = 'f'*64
    p.write_text(json.dumps(d))
    with pytest.raises(m.G0103ReviewError):
        review(env)


def test_archived_capture_made_future_denied(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['capture_health']
    d = json.loads(p.read_text())
    d['health_as_of_last_capture']['checked_at'] = z(t + timedelta(days=2))
    p.write_text(json.dumps(d))
    with pytest.raises(m.G0103ReviewError, match='Future'):
        review(env)


def test_archived_capture_summary_not_proof_of_raw_receipts(env):
    summary = review(env)['historical_capture_health_summary']
    assert summary['original_capture_bytes_verified'] is False
    assert summary['original_capture_custody_verified'] is False
    assert summary['worker_running_or_restarted'] is False


@pytest.mark.parametrize('remove', ['probe', 'clock_audit', 'capture_health'])
def test_absent_optional_archive_still_fails_closed(env, remove):
    repo, root, t, manifest = env
    (repo / m.HISTORY_FILES[remove]).unlink()
    report = review(env)
    key = {'probe':'historical_broker_probe',
           'clock_audit':'historical_clock_candidate_audit',
           'capture_health':'historical_capture_health_summary'}[remove]
    assert report[key]['status'] == 'NOT_SUPPLIED'
    assert report['final_action'] == 'HOLD'


def test_archive_symlink_rejected_if_supported(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['clock_audit']
    actual = p.with_name('audit_copy.json')
    p.rename(actual)
    try:
        p.symlink_to(actual)
    except (OSError, NotImplementedError):
        pytest.skip('Symlink privileges unavailable on this host')
    with pytest.raises((m.CandidateIntakeError, m.G0103ReviewError)):
        review(env)


def test_original_policy_pinned_even_without_manifest(env):
    assert review(env, filename=None)['final_action'] == 'HOLD'


def test_clock_audit_missing_all_offsets_denied(env):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['clock_audit']
    d = json.loads(p.read_text())
    d['candidate_offset_seconds'] = {}
    p.write_text(json.dumps(d))
    with pytest.raises(m.G0103ReviewError):
        review(env)


def test_wrong_epoch_or_future_cutoff_denied(env):
    with pytest.raises(m.G0103ReviewError):
        review(env, epoch='0'*64)
    with pytest.raises(m.G0103ReviewError):
        review(env, asof=datetime.now(UTC)+timedelta(days=1))


def test_other_policy_path_denied_even_with_same_bytes(env):
    repo, root, t, manifest = env
    other = repo / 'unrelated.json'
    other.write_bytes((repo/m.PINNED_POLICY).read_bytes())
    with pytest.raises(m.G0103ReviewError):
        review(env, policy=other)


def test_manifest_self_approval_key_rejected(env):
    repo, root, t, manifest = env
    manifest['independent_approval'] = True
    put(env, manifest)
    with pytest.raises(m.CandidateIntakeError):
        review(env)


def test_bogus_broker_candidate_origin_denied(env):
    repo, root, t, manifest = env
    item = candidate(env)
    item['origin_url'] = 'http://not-a-secure-site.invalid'
    manifest['artifacts'].append(item)
    put(env, manifest)
    with pytest.raises(m.CandidateIntakeError):
        review(env)


def test_duplicate_manifest_artifact_denied(env):
    repo, root, t, manifest = env
    item = candidate(env)
    manifest['artifacts'] = [item, deepcopy(item)]
    put(env, manifest)
    with pytest.raises(m.CandidateIntakeError):
        review(env)


def test_cli_read_only_non_json(env, capsys):
    repo, root, t, manifest = env
    rc = m.run(['--read-only-g01-g03', '--offline-only', '--epoch-id', m.EPOCH,
                '--as-of', z(t), '--repository-root', str(repo),
                '--evidence-root', str(root), '--policy-file',
                str(repo / m.PINNED_POLICY), '--manifest', 'candidate_manifest.json'])
    output = capsys.readouterr().out
    assert rc == 0
    assert 'G01: BLOCKED' in output
    assert 'G02: BLOCKED' in output
    assert 'G03: BLOCKED' in output
    assert 'PIN_MATCH' in output and 'FINAL=HOLD' in output


def test_cli_json_contract_valid(env, capsys):
    repo, root, t, manifest = env
    rc = m.run(['--read-only-g01-g03', '--offline-only', '--epoch-id', m.EPOCH,
                '--as-of', z(t), '--repository-root', str(repo),
                '--evidence-root', str(root), '--policy-file',
                str(repo / m.PINNED_POLICY), '--manifest', 'candidate_manifest.json',
                '--json'])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert result['final_action'] == 'HOLD'
    assert result['files_written'] is False
    assert result['network_accessed'] is False
    assert result['independent_external_gates_verified'] == 0
    assert result['archive_rows_modified'] == 0


def test_cli_failure_no_source_change(env, capsys):
    repo, root, t, manifest = env
    p = repo / m.HISTORY_FILES['probe']
    original = sha256(p.read_bytes()).hexdigest()
    p.write_bytes(b'{"historical_utc_certified": true}')
    args = ['--read-only-g01-g03', '--offline-only', '--epoch-id', m.EPOCH,
            '--as-of', z(t), '--repository-root', str(repo),
            '--evidence-root', str(root), '--policy-file', str(repo / m.PINNED_POLICY)]
    assert m.run(args) == 1
    assert 'BLOCKED' in capsys.readouterr().out
    assert sha256(p.read_bytes()).hexdigest() != original


def test_missing_cli_flags_fail_closed(env):
    with pytest.raises(SystemExit) as error:
        m.run(['--as-of', z(cutoff())])
    assert error.value.code == 2


def test_no_runtime_socket_subprocess_or_order_integration():
    import inspect
    source = inspect.getsource(m)
    for text in ('import socket', 'subprocess.', 'requests.', 'http.server',
                 'MetaTrader5', 'import mt5', 'start_collector(', 'place_order('):
        assert text not in source
