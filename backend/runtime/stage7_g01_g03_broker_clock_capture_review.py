
"""EURUSD ORACLE: offline G01-G03 broker/UTC/capture evidence review.

Consumes the committed Stage 7 candidate intake and three optional *existing*
historical repository reports. It never certifies broker identity, offset,
original capture custody, historic UTC or gate admission. A local digest is not
an external signature. No network, filesystem writes, collector, agent, broker,
orders, train/promotion or policy migration. FINAL=HOLD unconditionally.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from backend.runtime.stage7_external_evidence_intake import (
    EPOCH, POLICY_SHA256, CandidateIntakeError, _canonical_hash,
    _ensure_directory, _parse_json, _read_regular, _timestamp,
    assess_evidence_candidates,
)

VERSION = 'EURUSD_STAGE7_G01_G03_OFFLINE_BROKER_CLOCK_CAPTURE_REVIEW_V1'
MAX_HISTORY_BYTES = 131072
PINNED_POLICY = 'docs/clock_profiles/hfm_forward_20261027.json'
HISTORY_FILES = {
    'probe': 'docs/clock_profiles/hfm_probe_evidence_20261027.json',
    'clock_audit': 'logs/archive_clock_candidate_audit_20261027.json',
    'capture_health': 'logs/clock_epoch_final_verification.json',
}
GATE_NEXT_STEPS = {
    'G01': (
        'Obtain broker-issued, account-scoped server identity evidence, without passwords.',
        'Independently corroborate the exact server and observation date.',
    ),
    'G02': (
        'Obtain independently timestamped UTC reference and broker-clock pairs.',
        'Measure offset and uncertainty across relevant DST boundaries; review separately.',
    ),
    'G03': (
        'Collect original immutable capture bytes and corresponding capture receipts.',
        'Independently establish receipt timing, hashes, writer identity and custody.',
    ),
}


class G0103ReviewError(ValueError):
    """Fail closed on corrupted or unexpectedly self-certifying local records."""


def _int(value, name, maximum=10**12):
    if type(value) is not int or not 0 <= value <= maximum:
        raise G0103ReviewError('Invalid nonnegative integer: ' + name)
    return value


def _read_named(root, relative):
    """Read only allowlisted, bounded JSON under the chosen historical root."""
    if relative not in (PINNED_POLICY, *HISTORY_FILES.values()):
        raise G0103ReviewError('Non-allowlisted historical record refused.')
    p = root.joinpath(*relative.split('/'))
    if not p.exists() and not p.is_symlink():
        return None, None
    directory = _ensure_directory(p.parent)
    raw = _read_regular(directory, p.name, MAX_HISTORY_BYTES)
    obj = _parse_json(raw)
    if type(obj) is not dict:
        raise G0103ReviewError('Historical JSON must be an object.')
    return obj, sha256(raw).hexdigest()


def _check_policy(root):
    policy, digest = _read_named(root, PINNED_POLICY)
    if policy is None or digest != POLICY_SHA256:
        raise G0103ReviewError('Historical clock policy pin failed.')
    if (policy.get('schema_version') != 1 or
            policy.get('historical_admission') is not False or
            policy.get('symbol') != 'EURUSD'):
        raise G0103ReviewError('Historical clock policy contract changed.')
    for name in ('company_sha256', 'server_sha256', 'evidence_sha256'):
        value = policy.get(name)
        if not isinstance(value, str) or len(value) != 64 or any(
                c not in '0123456789abcdef' for c in value):
            raise G0103ReviewError('Invalid pinned policy fingerprint.')
    return policy


def _probe_summary(root, policy):
    probe, digest = _read_named(root, HISTORY_FILES['probe'])
    if probe is None:
        return {'status': 'NOT_SUPPLIED', 'local_pin_match': None,
                'observation_count': None, 'historical_utc_certified': False}
    if digest != policy['evidence_sha256']:
        raise G0103ReviewError('Pinned historical probe bytes changed.')
    if (probe.get('schema_version') != 1 or probe.get('read_only') is not True or
            probe.get('historical_utc_certified') is not False or
            probe.get('company_sha256') != policy['company_sha256'] or
            probe.get('server_sha256') != policy['server_sha256']):
        raise G0103ReviewError('Pinned probe contradicts the historical policy.')
    observations = probe.get('observations')
    if type(observations) is not list or len(observations) > 64:
        raise G0103ReviewError('Bounded original probe observations required.')
    return {'status': 'ORIGINAL_PROBE_BYTES_MATCH_PIN_UNATTESTED',
            'local_pin_match': True,
            'observation_count': len(observations),
            'broker_company_match_local_claim': probe.get('hfm_company_match') is True,
            'broker_server_identity_independently_attested': False,
            'historical_utc_certified': False}


def _clock_summary(root):
    audit, digest = _read_named(root, HISTORY_FILES['clock_audit'])
    if audit is None:
        return {'status': 'NOT_SUPPLIED', 'candidate_offsets_seconds': None,
                'local_bars_scanned': None, 'historical_utc_certified': False}
    if (audit.get('status') != 'ANALYSIS_ONLY' or
            audit.get('historical_utc_certified') is not False or
            type(audit.get('legacy_rows_modified')) is not int or
            audit['legacy_rows_modified'] != 0 or
            audit.get('models_retrained') is not False):
        raise G0103ReviewError('Historical audit must remain analysis-only and unmodified.')
    scanned = _int(audit.get('legacy_bars_scanned'), 'legacy_bars_scanned')
    offsets = audit.get('candidate_offset_seconds')
    if type(offsets) is not dict or not 0 < len(offsets) <= 8:
        raise G0103ReviewError('Invalid bounded candidate offset distribution.')
    numbers = {}
    for label, count in offsets.items():
        if type(label) is not str or not label.isascii() or not label.isdigit():
            raise G0103ReviewError('Invalid offset key.')
        seconds = int(label)
        if not 0 < seconds <= 86400 or seconds % 60 or seconds in numbers:
            raise G0103ReviewError('Invalid candidate offset in seconds.')
        numbers[seconds] = _int(count, 'offset sample count')
    if sum(numbers.values()) != scanned:
        raise G0103ReviewError('Candidate offset coverage does not match local audit.')
    return {'status': 'HISTORICAL_ANALYSIS_ONLY_UNATTESTED',
            'local_audit_sha256': digest,
            'candidate_offsets_seconds': sorted(numbers),
            'candidate_offset_sample_counts': {str(k): numbers[k] for k in sorted(numbers)},
            'local_bars_scanned': scanned,
            'historical_utc_certified': False,
            'corrected_legacy_rows_written': 0,
            'models_retrained': False,
            'independent_utc_offset_and_uncertainty_verified': False}


def _capture_summary(root, cutoff):
    report, digest = _read_named(root, HISTORY_FILES['capture_health'])
    if report is None:
        return {'status': 'NOT_SUPPLIED', 'reported_capture_batches': None,
                'original_capture_custody_verified': False}
    counts = report.get('counts')
    health = report.get('health_as_of_last_capture')
    if type(counts) is not dict or type(health) is not dict:
        raise G0103ReviewError('Invalid local capture summary schema.')
    if (health.get('epoch_id') != EPOCH or
            health.get('execution_authorized') is not False or
            health.get('legacy_archive_migrated') is not False or
            report.get('continuous_worker_left_running') is not False):
        raise G0103ReviewError('Capture summary would violate historical HOLD scope.')
    checked = _timestamp(health.get('checked_at'))
    if checked > cutoff:
        raise G0103ReviewError('Future local capture health claim refused.')
    mapped = {
        'reported_epochs': _int(counts.get('oracle_utc_feed_epochs'), 'epoch count'),
        'reported_capture_batches': _int(counts.get('oracle_utc_feed_captures'), 'capture count'),
        'reported_ticks': _int(counts.get('oracle_utc_ticks'), 'tick count'),
        'reported_bars': _int(counts.get('oracle_utc_bars'), 'bar count'),
    }
    return {'status': 'ARCHIVED_LOCAL_CAPTURE_SUMMARY_UNATTESTED',
            'local_summary_sha256': digest,
            'local_health_checked_at_utc': checked.isoformat(),
            'local_health_label_not_current': health.get('status') == 'pass',
            **mapped,
            'original_capture_bytes_verified': False,
            'original_capture_custody_verified': False,
            'worker_running_or_restarted': False}


def assess_g01_g03(*, epoch_id, as_of, repository_root, evidence_root,
                   policy_file, manifest=None):
    if epoch_id != EPOCH:
        raise G0103ReviewError('Historical epoch rebinding refused.')
    cutoff = _timestamp(as_of)
    if cutoff > datetime.now(timezone.utc):
        raise G0103ReviewError('Future review cutoff refused.')
    repo = _ensure_directory(repository_root)
    expected_policy = repo.joinpath(*PINNED_POLICY.split('/'))
    if Path(policy_file).absolute() != expected_policy.absolute():
        raise G0103ReviewError('Use the historical policy inside the repository root.')
    # Reuse the committed Stage 7 candidate admission boundaries and policy pin.
    intake = assess_evidence_candidates(
        epoch_id=epoch_id, as_of=as_of, evidence_root=evidence_root,
        manifest=manifest, policy_file=policy_file)
    if (intake['final_action'] != 'HOLD' or
            intake['independent_external_gates_verified'] != 0 or
            intake['independent_source_families_admitted'] != 0 or
            intake['pinned_policy_sha256'] != POLICY_SHA256 or
            intake['report_sha256'] != _canonical_hash({
                k: v for k, v in intake.items() if k != 'report_sha256'})):
        raise G0103ReviewError('Stage 7 intake contract or local hash mismatch.')
    policy = _check_policy(repo)
    probe = _probe_summary(repo, policy)
    clock = _clock_summary(repo)
    capture = _capture_summary(repo, cutoff)
    gates = []
    for gate in ('G01', 'G02', 'G03'):
        candidates = [
            {'artifact_id': c['artifact_id'], 'status': c['status'],
             'local_sha256': c['observed_sha256'],
             'independently_authenticated': False}
            for c in intake['candidates'] if c['gate_id'] == gate
        ]
        gates.append({
            'gate_id': gate, 'gate_name': {
                'G01': 'BROKER_SERVER_IDENTITY',
                'G02': 'INDEPENDENT_BROKER_UTC',
                'G03': 'ORIGINAL_CAPTURE_CUSTODY',
            }[gate],
            'local_candidates': candidates,
            'local_candidate_count': len(candidates),
            'local_hash_matches': sum(c['status'] in (
                'LOCAL_BYTES_MATCH_UNATTESTED',
                'LOCAL_BYTES_MATCH_PUBLICATION_TIME_UNKNOWN') for c in candidates),
            'independently_verified': False,
            'gate_admitted': False,
            'status': 'BLOCKED_INDEPENDENT_EVIDENCE',
            'requirements': list(GATE_NEXT_STEPS[gate]),
        })
    report = {
        'scope': VERSION,
        'historical_epoch_id': EPOCH,
        'as_of_utc': cutoff.isoformat(),
        'original_policy_sha256': POLICY_SHA256,
        'candidate_intake_report_sha256': intake['report_sha256'],
        'candidate_manifest_status': intake['manifest_status'],
        'candidate_intake_total': intake['candidate_count'],
        'historical_broker_probe': probe,
        'historical_clock_candidate_audit': clock,
        'historical_capture_health_summary': capture,
        'g01_g03_evidence_gates': gates,
        'independent_external_gates_verified': 0,
        'independent_source_families_admitted': 0,
        'broker_identity_independently_verified': False,
        'historical_utc_certified': False,
        'capture_custody_independently_verified': False,
        'operator_approval_granted': False,
        'historical_epoch_migrated': False,
        'archive_rows_modified': 0,
        'model_training_executed': False,
        'collector_started': False,
        'broker_connected': False,
        'network_accessed': False,
        'files_written': False,
        'paper_orders_authorized': False,
        'live_orders_authorized': False,
        'research_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
        'final_action': 'HOLD',
    }
    report['report_sha256'] = _canonical_hash(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only-g01-g03', action='store_true', required=True)
    parser.add_argument('--offline-only', action='store_true', required=True)
    parser.add_argument('--epoch-id', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--repository-root', required=True)
    parser.add_argument('--evidence-root', required=True)
    parser.add_argument('--policy-file', required=True)
    parser.add_argument('--manifest')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = assess_g01_g03(
            epoch_id=args.epoch_id, as_of=args.as_of,
            repository_root=args.repository_root,
            evidence_root=args.evidence_root,
            policy_file=args.policy_file, manifest=args.manifest,
        )
    except (G0103ReviewError, CandidateIntakeError, OSError, ValueError,
            TypeError, KeyError, OverflowError):
        print('G01-G03 EVIDENCE REVIEW BLOCKED: local integrity or contract failure.')
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print('EURUSD ORACLE | Integrated G01-G03 offline evidence review')
        for gate in report['g01_g03_evidence_gates']:
            print('%s: %s | local candidates %d | independent admissions 0' % (
                gate['gate_id'], gate['status'], gate['local_candidate_count']))
        print('Historical probe: ' + report['historical_broker_probe']['status'])
        print('Historical clock audit: ' + report['historical_clock_candidate_audit']['status'])
        print('Historical capture summary: ' + report['historical_capture_health_summary']['status'])
        print('Original policy: PIN_MATCH | Historical UTC: NOT CERTIFIED')
        print('Independent gates 0/11 | Source admissions 0/13')
        print('Report SHA-256: ' + report['report_sha256'])
        print('READ ONLY | NO COLLECTOR | NO BROKER | NO ORDERS | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()
