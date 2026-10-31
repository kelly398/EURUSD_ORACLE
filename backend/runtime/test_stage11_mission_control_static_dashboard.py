
"""Offline HTML Mission Control dashboard, safety and actual view integration tests."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import json

import pytest

from backend.runtime.stage11_mission_control_offline_api import (
    EPOCH, ROUTES, _digest, read_offline_report,
)
from backend.runtime.test_stage11_mission_control_offline_api import (
    synthetic_stage11_report, z,
)
from backend.runtime.stage11_mission_control_static_dashboard import (
    OfflineDashboardError, MAX_HTML_BYTES, VERSION,
    render_offline_dashboard, write_new_html_snapshot, run,
)

UTC = timezone.utc


def at():
    return datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=20)


def render(report=None, *, t=None, role='VIEWER', epoch=EPOCH):
    return render_offline_dashboard(
        epoch_id=epoch, as_of=z(t if t is not None else at()),
        report=report, role=role)


class DocumentElements(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = []
        self.targets = []
        self.tags = []
        self.hrefs = []
        self.headings = []
        self.captions = []
        self.ths = 0
        self.labels = []

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        self.tags.append(tag)
        if tag == 'section' and 'id' in props:
            self.sections.append(props['id'])
        if tag == 'a' and 'href' in props:
            self.hrefs.append(props['href'])
        if 'id' in props:
            self.targets.append(props['id'])
        if tag == 'th':
            self.ths += 1


def fixture_html(t=None):
    t = t or at()
    doc = synthetic_stage11_report(t)
    return render(doc, t=t), doc, t


def test_offline_no_source_renders_eight_complete_views():
    page = render()
    elements = DocumentElements()
    elements.feed(page)
    assert elements.sections == list(ROUTES)
    assert '#main' in elements.hrefs
    assert all('#'+name in elements.hrefs for name in ROUTES)
    assert 'No Stage 11 report supplied' in page
    assert 'Local records' in page and 'Unknown' in page
    assert 'FINAL=HOLD' in page


def test_offline_report_is_classified_as_local_not_verified():
    page, doc, t = fixture_html()
    assert 'Local report, unverified' in page
    assert 'independently verified' in page.lower()
    assert '0 / 11' in page and '0 / 13' in page
    assert '0/11' in page and '0/13' in page
    assert doc['report_sha256'] in page
    assert 'FINAL=HOLD' in page


def test_offline_renders_13_sources_11_gates_12_contracts():
    page, doc, t = fixture_html()
    for family in doc['source_family_board']:
        assert family['identifier'] in page
    for gate in doc['evidence_gate_board']:
        assert gate['identifier'] in page
    for contract in doc['read_model_inventory']:
        assert contract['record_type'] in page
    elements = DocumentElements()
    elements.feed(page)
    assert elements.ths == 9  # 3 headers for each of 3 tables


def test_offline_read_model_roster_counts_are_not_executions():
    page = render()
    assert '<b>28</b>Primary analysts' in page
    assert '<b>2</b>Governance seats' in page
    assert '<b>1</b>Synthesis seat' in page
    assert 'No real cycle verified' in page
    assert 'NOT_OBSERVED_DO_NOT_ASSUME_CLEAR' in page
    assert 'Risk veto override permitted' in page


def test_offline_unavailable_prices_performance_health_not_fabricated():
    page = render()
    assert 'Latest independently verified EUR/USD price' in page
    assert 'Not available' in page
    assert 'UNKNOWN_NOT_INSPECTED' in page
    assert 'Unknown — do not assume clear' in page
    assert 'Not met' in page
    assert 'NOT_ENOUGH_EVIDENCE' in page


def test_offline_local_count_does_not_claim_prediction_accuracy():
    page, doc, t = fixture_html()
    assert doc['record_count'] == 3
    assert 'Local record count' in page
    assert '>3</div>' in page
    assert 'Not independently validated' in page
    assert 'Verified net P&amp;L after trading costs' in page


def test_offline_hash_consistency_does_not_claim_custody():
    page, doc, t = fixture_html()
    assert 'HASH_CONSISTENCY_IS_NOT_EXTERNAL_CUSTODY' in page
    assert 'Independently attested source bytes' in page
    assert 'Not authenticated' in page
    assert 'no login, order control' in page


def test_offline_no_script_no_network_no_form_no_embedded_media():
    page = render().lower()
    parser = DocumentElements()
    parser.feed(page)
    assert not ({'script', 'form', 'button', 'iframe', 'object', 'img', 'audio',
                  'video', 'input', 'link'} & set(parser.tags))
    assert not any(href.startswith(('http:', 'https:', 'javascript:', 'data:'))
                   for href in parser.hrefs)
    assert "default-src 'none'" in page
    assert 'style-src' in page
    assert 'fetch(' not in page
    assert 'onload=' not in page
    assert 'window.location' not in page


def test_offline_page_is_responsive_and_accessible():
    page = render()
    assert '<html lang="en">' in page
    assert 'name="viewport"' in page
    assert '<main id="main">' in page
    assert 'aria-label="Mission Control sections"' in page
    assert 'aria-labelledby="heading-overview"' in page
    assert '@media(max-width:580px)' in page
    assert '@media(prefers-reduced-motion:reduce)' in page
    assert 'scope="col"' in page
    assert len(page.encode('utf-8')) < MAX_HTML_BYTES


def test_offline_deterministic_same_report_and_cutoff():
    t = at()
    doc = synthetic_stage11_report(t)
    a = render(doc, t=t)
    b = render(doc, t=t)
    assert a == b
    assert sha256(a.encode('utf-8')).hexdigest() == sha256(b.encode('utf-8')).hexdigest()


def test_offline_changing_cutoff_changes_html_without_model_execution():
    first = render(t=at())
    second = render(t=at()+timedelta(seconds=1))
    assert first != second
    assert 'No real cycle verified' in first and 'No real cycle verified' in second


@pytest.mark.parametrize('role', ['VIEWER', 'AUDITOR', 'OPERATOR'])
def test_offline_role_preview_never_becomes_authentication(role):
    page = render(role=role)
    assert role in page
    assert 'Not implemented — preview only' in page
    assert 'No trading authorization' in page
    assert 'No broker or agent operations' in page


@pytest.mark.parametrize('role', ['ADMIN', 'ROOT', 'LIVE_TRADER', '', None, 1])
def test_offline_unknown_role_rejected(role):
    with pytest.raises(OfflineDashboardError):
        render(role=role)


def test_offline_future_review_cutoff_rejected():
    with pytest.raises(OfflineDashboardError):
        render(t=datetime.now(UTC)+timedelta(days=1))


def test_offline_wrong_epoch_rejected():
    with pytest.raises(OfflineDashboardError):
        render(epoch='0'*64)


def test_offline_forged_historical_epoch_not_accepted():
    t = at()
    doc = synthetic_stage11_report(t)
    doc['historical_epoch_id'] = 'f'*64
    doc['report_sha256'] = _digest({k:v for k,v in doc.items() if k != 'report_sha256'})
    with pytest.raises(OfflineDashboardError):
        render(doc, t=t)


def test_offline_report_binds_as_of_strictly():
    t = at()
    doc = synthetic_stage11_report(t)
    with pytest.raises(OfflineDashboardError):
        render(doc, t=t+timedelta(seconds=1))


def test_offline_rehashed_false_price_is_rejected():
    t = at()
    doc = synthetic_stage11_report(t)
    doc['latest_verified_market_price'] = '1.111111'
    doc['report_sha256'] = _digest({k:v for k,v in doc.items() if k != 'report_sha256'})
    with pytest.raises(OfflineDashboardError):
        render(doc, t=t)


def test_offline_tampered_local_digest_is_rejected():
    t = at()
    doc = synthetic_stage11_report(t)
    doc['record_count'] += 1
    with pytest.raises(OfflineDashboardError):
        render(doc, t=t)


def test_offline_extra_sensitive_data_cannot_leak():
    t = at()
    doc = synthetic_stage11_report(t)
    doc['extra_sensitive'] = 'SECRET__DO_NOT_PRINT'
    doc['source_family_board'][0]['email'] = 'admin@secret.invalid'
    doc['read_model_inventory'][0]['password'] = '123SECRET'
    doc['report_sha256'] = _digest({k:v for k,v in doc.items() if k != 'report_sha256'})
    html = render(doc, t=t)
    for token in ('SECRET__DO_NOT_PRINT', 'admin@secret.invalid', '123SECRET'):
        assert token not in html


def test_offline_malicious_html_in_family_id_is_escaped_or_suppressed():
    t = at()
    doc = synthetic_stage11_report(t)
    doc['source_family_board'][0]['identifier'] = '<svg onload=alert(123)>'
    doc['report_sha256'] = _digest({k:v for k,v in doc.items() if k != 'report_sha256'})
    html = render(doc, t=t)
    assert '<svg' not in html
    assert 'onload=' not in html
    assert 'SOURCE_SLOT_01' in html


def test_offline_input_data_not_mutated_by_render():
    t = at()
    doc = synthetic_stage11_report(t)
    saved = json.dumps(doc, sort_keys=True)
    render(doc, t=t)
    assert saved == json.dumps(doc, sort_keys=True)


def test_export_new_html_file_content_digest_matches(tmp_path):
    page = render()
    dest = tmp_path / 'oracle_read_only.html'
    digest = write_new_html_snapshot(dest, page)
    assert digest == sha256(dest.read_bytes()).hexdigest()
    assert dest.read_bytes() == page.encode('utf-8')
    assert dest.read_bytes().startswith(b'<!DOCTYPE html>')


def test_export_never_overwrites_existing_html(tmp_path):
    dest = tmp_path / 'oracle_read_only.html'
    dest.write_text('EXISTING DATA')
    with pytest.raises(OfflineDashboardError):
        write_new_html_snapshot(dest, render())
    assert dest.read_text() == 'EXISTING DATA'


@pytest.mark.parametrize('basename', [
    'evidence.json', '.html', 'two words.html', 'bad:name.html',
    '../secret.html', 'bad..html', 'unsafe.html.py',
])
def test_export_rejects_unsafe_output_names(tmp_path, basename):
    with pytest.raises(OfflineDashboardError):
        write_new_html_snapshot(tmp_path / basename, render())


def test_export_rejects_parent_directory_missing(tmp_path):
    with pytest.raises(OfflineDashboardError):
        write_new_html_snapshot(tmp_path / 'nonexistent' / 'output.html', render())


def test_export_rejects_untrusted_html_payload(tmp_path):
    with pytest.raises(OfflineDashboardError):
        write_new_html_snapshot(tmp_path / 'output.html', '<script>evil()</script>')


def test_export_does_not_create_file_when_html_too_large(tmp_path):
    dest = tmp_path / 'output.html'
    with pytest.raises(OfflineDashboardError):
        write_new_html_snapshot(dest, '<!DOCTYPE html>'+('x'*MAX_HTML_BYTES))
    assert not dest.exists()


def test_export_symlink_destination_refused_if_allowed(tmp_path):
    target = tmp_path / 'target.txt'
    target.write_text('SAFE')
    link = tmp_path / 'output.html'
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip('Host does not allow symlinks')
    with pytest.raises(OfflineDashboardError):
        write_new_html_snapshot(link, render())
    assert target.read_text() == 'SAFE'


def test_export_symlink_parent_directory_refused_if_allowed(tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir()
    link = tmp_path / 'alias'
    try:
        link.symlink_to(actual, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('Host does not allow directory symlinks')
    with pytest.raises(OfflineDashboardError):
        write_new_html_snapshot(link / 'output.html', render())
    assert not (actual / 'output.html').exists()


def test_cli_inspect_only_writes_no_html(tmp_path, capsys):
    t = at()
    assert run(['--read-only-dashboard', '--offline-only', '--epoch-id', EPOCH,
                '--as-of', z(t), '--inspect-only']) == 0
    out = capsys.readouterr().out
    assert 'HTML created locally: NO' in out
    assert 'FINAL=HOLD' in out
    assert list(tmp_path.iterdir()) == []


def test_cli_local_html_export_with_no_source(tmp_path, capsys):
    t = at()
    target = tmp_path / 'oracle_dashboard.html'
    rc = run(['--read-only-dashboard', '--offline-only', '--epoch-id', EPOCH,
              '--as-of', z(t), '--output-html', str(target)])
    assert rc == 0
    assert target.exists()
    assert 'No Stage 11 report supplied' in target.read_text(encoding='utf-8')
    out = capsys.readouterr().out
    assert 'HTML created locally: YES' in out
    assert 'NO SERVER | NO EXTERNAL ASSETS | NO BROKER | FINAL=HOLD' in out


def test_cli_existing_html_blocks_without_touching_source(tmp_path, capsys):
    target = tmp_path / 'exists.html'
    target.write_text('KEEP ME')
    rc = run(['--read-only-dashboard', '--offline-only', '--epoch-id', EPOCH,
              '--as-of', z(at()), '--output-html', str(target)])
    assert rc == 1
    assert target.read_text() == 'KEEP ME'
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_local_export_from_hash_consistent_report(tmp_path, capsys):
    t = at()
    source = tmp_path / 'stage11_original.json'
    doc = synthetic_stage11_report(t)
    source.write_text(json.dumps(doc, sort_keys=True), encoding='utf-8')
    dest = tmp_path / 'oracle_view.html'
    rc = run(['--read-only-dashboard', '--offline-only', '--epoch-id', EPOCH,
              '--as-of', z(t), '--report-file', str(source),
              '--output-html', str(dest)])
    assert rc == 0
    assert sha256(source.read_bytes()).hexdigest() == sha256(
        json.dumps(doc, sort_keys=True).encode('utf-8')).hexdigest()
    assert dest.exists()
    assert doc['report_sha256'] in dest.read_text(encoding='utf-8')
    assert 'LOCAL_HASH_MATCH_UNATTESTED' in capsys.readouterr().out


def test_cli_invalid_source_does_not_export(tmp_path, capsys):
    source = tmp_path / 'bad.json'
    source.write_text('{"final_action":"TRADE"}')
    dest = tmp_path / 'danger.html'
    rc = run(['--read-only-dashboard', '--offline-only', '--epoch-id', EPOCH,
              '--as-of', z(at()), '--report-file', str(source),
              '--output-html', str(dest)])
    assert rc == 1
    assert not dest.exists()
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_no_flags_denied():
    with pytest.raises(SystemExit) as e:
        run(['--epoch-id', EPOCH, '--as-of', z(at()), '--inspect-only'])
    assert e.value.code == 2


def test_cli_must_choose_inspect_or_output():
    with pytest.raises(SystemExit) as e:
        run(['--read-only-dashboard', '--offline-only', '--epoch-id', EPOCH,
             '--as-of', z(at())])
    assert e.value.code == 2


def test_cli_cannot_inspect_and_export_same_call():
    with pytest.raises(SystemExit) as e:
        run(['--read-only-dashboard', '--offline-only', '--epoch-id', EPOCH,
             '--as-of', z(at()), '--inspect-only', '--output-html', 'out.html'])
    assert e.value.code == 2


def test_actual_read_model_export_into_static_dashboard_if_installed(tmp_path, capsys):
    try:
        from backend.runtime.stage11_mission_control_read_models import run as upstream_run
    except ModuleNotFoundError as exc:
        if exc.name == 'backend.runtime.stage11_mission_control_read_models':
            pytest.skip('Original committed read-model module unavailable locally')
        raise
    t = at()
    policy = Path(__file__).resolve().parents[2] / 'docs/clock_profiles/hfm_forward_20261027.json'
    assert upstream_run(['--read-only-mission-control', '--offline-only',
                         '--epoch-id', EPOCH, '--as-of', z(t), '--policy-file',
                         str(policy), '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['final_action'] == 'HOLD'
    html = render_offline_dashboard(epoch_id=EPOCH, as_of=z(t), report=payload)
    parser = DocumentElements()
    parser.feed(html)
    assert parser.sections == list(ROUTES)
    assert 'NOT_ENOUGH_EVIDENCE' in html
    assert payload['report_sha256'] in html
    assert 'FINAL=HOLD' in html
