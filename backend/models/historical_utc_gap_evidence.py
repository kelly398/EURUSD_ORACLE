
"""Milestone 15: read-only gap evidence for an immutable multiseason registry.

No SQL, inferred replacement candles, broker calendar certification, training,
or execution. Re-audits original exports and reports observed candidate-time
intervals within each index. Months between indexes are excluded, never gaps.
"""

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

from backend.models.consolidate_historical_utc_research import (
    ConsolidationError, ResearchWindow, _canonical, _day, inspect_windows,
)
from backend.models.register_historical_utc_seasons import (
    SeasonRegistryError, VERSION as REGISTRY_VERSION, _no_duplicates,
    build_registry, read_index,
)

M5 = timedelta(minutes=5)
MAX_GAPS = 10_000
MAX_REGISTRY_BYTES = 2_000_000


class GapEvidenceError(RuntimeError):
    """Evidence cannot be trusted; fail without writing anything."""


def _read_published_registry(directory):
    """Verify exact registry bytes and safety state, not source authenticity."""
    root = Path(directory)
    manifest_path = root / 'manifest.json'
    checks_path = root / 'checksums.sha256'
    if (root.is_symlink() or not root.is_dir()
            or root.name[:9] != 'registry_'
            or any(p.is_symlink() or not p.is_file()
                   for p in (manifest_path, checks_path))
            or {p.name for p in root.iterdir()}
            != {'manifest.json', 'checksums.sha256'}):
        raise GapEvidenceError('Unsafe or incomplete published registry.')
    try:
        if (manifest_path.stat().st_size > MAX_REGISTRY_BYTES
                or checks_path.stat().st_size > 200):
            raise GapEvidenceError('Registry files exceed size limits.')
        payload = manifest_path.read_bytes()
        checksum = checks_path.read_bytes()
        expected = (sha256(payload).hexdigest() + '  manifest.json\n').encode('ascii')
        # Future/older platform-compatible metadata may carry CRLF.
        if checksum not in (expected, expected[:-1] + b'\r\n'):
            raise GapEvidenceError('Registry checksum mismatch.')
        manifest = json.loads(payload, object_pairs_hook=_no_duplicates)
        if not isinstance(manifest, dict) or _canonical(manifest) != payload:
            raise GapEvidenceError('Noncanonical registry manifest.')
        digest = manifest.get('registry_sha256')
        unsigned = {k: v for k, v in manifest.items() if k != 'registry_sha256'}
        if (not isinstance(digest, str)
                or len(digest) != 64
                or any(ch not in '0123456789abcdef' for ch in digest)
                or root.name != 'registry_' + digest
                or sha256(_canonical(unsigned)).hexdigest() != digest):
            raise GapEvidenceError('Registry identity mismatch.')
        if (manifest.get('registry_version') != REGISTRY_VERSION
                or manifest.get('research_only') is not True
                or manifest.get('historical_admission') is not False
                or manifest.get('historical_utc_certified') is not False
                or manifest.get('training_authorized') is not False
                or manifest.get('execution_authorized') is not False
                or manifest.get('cross_index_candles_joined') is not False):
            raise GapEvidenceError('Registry safety flags invalid.')
        return manifest
    except GapEvidenceError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, SeasonRegistryError) as exc:
        raise GapEvidenceError('Registry cannot be verified.') from exc


def _observed_gap(previous, current, before_lineage, after_lineage, index_sha):
    prev_label, prev_time, prev_id = previous
    next_label, next_time, next_id = current
    delta = next_time - prev_time
    if delta < M5 or delta % M5:
        raise GapEvidenceError('Non-M5 or nonmonotonic candidate sequence.')
    if delta == M5:
        return None
    slots = int(delta // M5) - 1
    if prev_label.date() == next_label.date():
        kind = 'SAME_BROKER_DAY_UNVERIFIED'
    elif (prev_label.weekday() == 4 and next_label.weekday() == 0
          and (next_label.date() - prev_label.date()).days >= 3):
        kind = 'WEEKEND_SHAPED_UNVERIFIED'
    else:
        kind = 'CROSS_BROKER_DAY_UNVERIFIED'

    return {
        'index_sha256': index_sha,
        'classification': kind,
        'source_lineage_before': before_lineage,
        'source_lineage_after': after_lineage,
        'previous_source_row_id': prev_id,
        'next_source_row_id': next_id,
        'previous_broker_label': prev_label.isoformat(),
        'next_broker_label': next_label.isoformat(),
        'previous_candidate_utc': prev_time.isoformat(),
        'next_candidate_utc': next_time.isoformat(),
        'first_unobserved_candidate_utc': (prev_time + M5).isoformat(),
        'last_unobserved_candidate_utc': (next_time - M5).isoformat(),
        'unobserved_m5_slots': slots,
        'market_closure_certified': False,
        'tick_archive_proves_cause': False,
    }


def inspect_gap_evidence(registry_directory, index_directories, dataset_root):
    """Verify registry -> indexes -> exports and enumerate only observed gaps.

    Each period is audited independently. Input paths must match exactly the
    indexes in the signed registry, in chronological order. The dataset root
    is a local read-only lookup of content-addressed export directories.
    """
    indexes = tuple(index_directories)
    registered = _read_published_registry(registry_directory)
    if not 2 <= len(indexes) <= 16:
        raise GapEvidenceError('Requires 2 to 16 ordered index paths.')
    base = Path(dataset_root)
    if base.is_symlink() or not base.is_dir():
        raise GapEvidenceError('Dataset root is unsafe or unavailable.')

    try:
        rebuilt = build_registry(indexes)
    except (SeasonRegistryError, OSError) as exc:
        raise GapEvidenceError('Referenced index verification failed.') from exc
    if rebuilt != registered:
        raise GapEvidenceError('Registry and index manifests disagree.')

    gaps = []
    seen_ids = set()
    rows = 0
    for index_directory in indexes:
        try:
            index = read_index(index_directory)
            source_windows = []
            for parent in index['source_windows']:
                folder = base / ('dataset_' + parent['source_lineage_sha256'])
                # A symlink could redirect a content-addressed path elsewhere.
                if folder.is_symlink() or not folder.is_dir():
                    raise GapEvidenceError('Source export directory unavailable or unsafe.')
                source_windows.append(ResearchWindow(
                    _day(parent['broker_label_window_start']),
                    _day(parent['broker_label_window_end_exclusive']),
                    folder,
                ))
            # Reuse Milestone 12, which reuses the full Milestone 06 audit.
            recomputed = inspect_windows(source_windows)
            if recomputed != index:
                raise GapEvidenceError('Index differs from freshly audited exports.')

            previous = None
            previous_lineage = None
            index_rows = 0
            start_gap_count = len(gaps)
            start_missing_count = sum(g['unobserved_m5_slots'] for g in gaps)
            for source_window, parent in zip(source_windows, index['source_windows']):
                lineage = parent['source_lineage_sha256']
                with (source_window.directory / 'candles.jsonl').open('rb') as stream:
                    for raw in stream:
                        entry = json.loads(raw)
                        original = entry['source_row']
                        current = (
                            datetime.fromisoformat(original['broker_label_time']),
                            datetime.fromisoformat(entry['candidate_utc_open']),
                            original['row_id'],
                        )
                        if current[2] in seen_ids:
                            raise GapEvidenceError('Repeated row ID across research periods.')
                        seen_ids.add(current[2])
                        if previous is not None:
                            gap = _observed_gap(
                                previous, current, previous_lineage, lineage,
                                index['index_sha256'],
                            )
                            if gap is not None:
                                if len(gaps) >= MAX_GAPS:
                                    raise GapEvidenceError('Gap count exceeds safety limit.')
                                gaps.append(gap)
                        previous = current
                        previous_lineage = lineage
                        index_rows += 1
            if (index_rows != index['row_count']
                    or len(gaps) - start_gap_count != index['gap_count']
                    or (sum(g['unobserved_m5_slots'] for g in gaps)
                        - start_missing_count)
                    != index['unclassified_missing_m5_slots']):
                raise GapEvidenceError('Observed gaps do not match published index totals.')
            rows += index_rows
        except GapEvidenceError:
            raise
        except (ConsolidationError, SeasonRegistryError, OSError, ValueError,
                TypeError, KeyError, UnicodeError) as exc:
            raise GapEvidenceError('Research export evidence could not be verified.') from exc

    if (rows != registered['row_count']
            or len(gaps) != registered['within_index_gap_count']
            or sum(g['unobserved_m5_slots'] for g in gaps)
            != registered['within_index_unclassified_missing_m5_slots']):
        raise GapEvidenceError('Registry and observed gap totals disagree.')
    counts = {
        kind: sum(g['classification'] == kind for g in gaps)
        for kind in (
            'SAME_BROKER_DAY_UNVERIFIED',
            'WEEKEND_SHAPED_UNVERIFIED',
            'CROSS_BROKER_DAY_UNVERIFIED',
        )
    }
    return {
        'registry_sha256': registered['registry_sha256'],
        'research_only': True,
        'historical_utc_certified': False,
        'training_authorized': False,
        'execution_authorized': False,
        'row_count': rows,
        'index_count': len(indexes),
        'excluded_calendar_days_between_indexes': registered[
            'inter_index_calendar_days_excluded'],
        'excluded_periods_are_observed_gaps': False,
        'observed_gap_count': len(gaps),
        'unclassified_missing_m5_slots': sum(
            g['unobserved_m5_slots'] for g in gaps),
        'classification_counts': counts,
        'gaps': gaps,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(
        description='Read-only EURUSD historical research gap evidence'
    )
    parser.add_argument('--registry', required=True)
    parser.add_argument('--index', action='append', required=True,
                        help='Repeat in registry chronological order')
    parser.add_argument('--dataset-root', default='data/research/historical_utc_v2')
    parser.add_argument('--research-only', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        report = inspect_gap_evidence(args.registry, args.index, args.dataset_root)
    except GapEvidenceError as exc:
        print(f'GAP EVIDENCE BLOCKED: {exc}')
        return 1
    print('EURUSD ORACLE | Historical gap evidence (read only)')
    print(f"REGISTRY VERIFIED: {report['registry_sha256']}")
    print(f"OBSERVATIONS: {report['row_count']} | INDEXES: {report['index_count']}")
    print(f"OBSERVED GAPS: {report['observed_gap_count']}")
    print(f"UNOBSERVED M5 SLOTS: {report['unclassified_missing_m5_slots']}")
    print('EXCLUDED BETWEEN-INDEX DAYS (NOT GAPS): '
          f"{report['excluded_calendar_days_between_indexes']}")
    for number, gap in enumerate(report['gaps'], 1):
        print(f"{number:02d} | {gap['classification']} | "
              f"slots={gap['unobserved_m5_slots']} | "
              f"between {gap['previous_broker_label']} and "
              f"{gap['next_broker_label']} | "
              f"first UTC candidate missing={gap['first_unobserved_candidate_utc']}")
    print('NO FILES WRITTEN | ALL GAP CAUSES UNVERIFIED | '
          'UTC UNCERTIFIED | TRAINING/EXECUTION BLOCKED')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()
