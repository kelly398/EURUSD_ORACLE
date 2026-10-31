
"""EURUSD ORACLE Stage 11: offline static Mission Control HTML dashboard.

Provides a usable, accessible, self-contained HTML snapshot over the already
validated Stage 11 offline API view projections. No listening server, JS,
external CSS/fonts, raw source records, model inference, orders, broker or
network access. Every rendered source field is HTML-escaped and allowlisted by
Stage 11's existing adapter. There is no live-refresh or authentication claim.

In-memory rendering is pure. Explicit --output-html only creates a NEW local
HTML snapshot; it never updates a DB, existing file, or research record. The
source evidence remains READ ONLY and research decision FINAL=HOLD.
"""

import argparse
from datetime import timezone
from hashlib import sha256
from html import escape
from pathlib import Path
import re

from backend.runtime.stage11_mission_control_offline_api import (
    API_PREFIX, EPOCH, ROUTES, SAFE_ROLES,
    OfflineMissionControlAPIError, _timestamp,
    build_offline_api_response, read_offline_report,
)

VERSION = 'EURUSD_STAGE11_OFFLINE_STATIC_MISSION_CONTROL_V1'
MAX_HTML_BYTES = 256_000
OUTPUT_BASENAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.html$')
SECTION_LABELS = {
    'overview': 'Overview', 'sources': 'Source families',
    'gates': 'Evidence gates', 'council': 'Council & veto',
    'research': 'Research quality', 'health': 'Runtime health',
    'models': 'Data contracts', 'audit': 'Audit & provenance',
}

# This is deliberately an HTML document with a non-scriptable CSP. It does not
# use a service worker, fetch(), forms, tracking, CDN assets, or external fonts.
STYLE = r"""
:root{color-scheme:dark;--bg:#090f1a;--layer:#111c2a;--layer2:#162333;
--stroke:#2b3b4d;--text:#f0f5fa;--muted:#aebdcc;--amber:#f5c96d;
--cyan:#91d9e0;--warnbg:#332619;--code:#d3e2f1}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);
color:var(--text);font:15px/1.6 system-ui,-apple-system,'Segoe UI',sans-serif}
a{color:var(--cyan)}.skip{position:absolute;left:-9999px}.skip:focus{left:1rem;top:1rem;
background:var(--layer);padding:1rem;z-index:5}.shell{display:grid;grid-template-columns:235px minmax(0,1fr);
min-height:100vh}.sidebar{position:sticky;top:0;height:100vh;overflow:auto;padding:24px 17px;
border-right:1px solid var(--stroke);background:#0d1725}.identity{font-weight:800;letter-spacing:.09em;
font-size:1rem}.identity small{display:block;font-weight:500;color:var(--muted);font-size:.74rem;
letter-spacing:.12em}.nav-head{font-size:.7rem;text-transform:uppercase;letter-spacing:.12em;
color:var(--muted);margin:35px 9px 12px}.sidebar nav a{display:block;text-decoration:none;
color:var(--muted);padding:9px 12px;margin:4px 0;border-radius:8px}.sidebar nav a:hover,
.sidebar nav a:focus{background:#24354a;color:var(--text);outline:1px solid var(--cyan)}
.side-note{margin-top:25px;border-top:1px solid var(--stroke);padding-top:15px;
color:var(--muted);font-size:.78rem}main{max-width:1450px;width:100%;padding:32px 40px 90px;
margin:auto}.header{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;
flex-wrap:wrap}.eyebrow{color:var(--cyan);letter-spacing:.13em;font-size:.75rem;
text-transform:uppercase;font-weight:700}.header h1{font-size:clamp(1.7rem,3vw,2.6rem);margin:6px 0}
.subtitle,.dim{color:var(--muted)}.time{font-size:.85rem;color:var(--muted);
border:1px solid var(--stroke);border-radius:8px;padding:8px 13px}.hold{margin:25px 0;
border:1px solid #886b39;background:var(--warnbg);border-radius:13px;padding:17px 20px}
.hold strong{color:var(--amber);font-size:1.1rem}.hold p{margin:.3rem 0 0;color:#f1dfba}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:22px 0 31px}
.metric,.panel{border:1px solid var(--stroke);background:var(--layer);border-radius:12px}
.metric{padding:17px 19px}.metric small{display:block;color:var(--muted)}
.metric .number{font-size:2rem;font-weight:750;line-height:1.4}.metric .sub{color:var(--muted);
font-size:.81rem}.section{scroll-margin-top:18px;margin:0 0 35px}.section h2{font-size:1.35rem;
margin:0 0 5px}.section .lead{color:var(--muted);margin:0 0 17px}.panel{padding:20px;
margin:12px 0}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}
.details{display:grid;grid-template-columns:minmax(180px,1fr) 2fr;gap:10px;
margin:0}.details dt{color:var(--muted)}.details dd{margin:0;overflow-wrap:anywhere}
.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;
font-size:.88rem}caption{text-align:left;color:var(--muted);padding:0 0 10px}
th,td{text-align:left;padding:11px 12px;border-bottom:1px solid var(--stroke);vertical-align:top}
th{font-weight:700;color:var(--muted);white-space:nowrap}tbody tr:last-child td{border-bottom:0}
.pill{display:inline-block;font-size:.75rem;font-weight:750;border:1px solid #947238;
background:#382b18;color:#fbe0aa;padding:2px 9px;border-radius:99px}
.unknown{color:var(--amber)}.muted{color:var(--muted)}
.mono{font-family:ui-monospace,Consolas,'Cascadia Code',monospace;
font-size:.82rem;overflow-wrap:anywhere;color:var(--code)}.hint{font-size:.83rem;
color:var(--muted)}.seat-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.seat{text-align:center;background:var(--layer2);border-radius:10px;padding:20px 12px;
border:1px solid var(--stroke)}.seat b{font-size:1.9rem;display:block}
footer{border-top:1px solid var(--stroke);padding-top:24px;color:var(--muted);font-size:.84rem}
@media(max-width:970px){.shell{grid-template-columns:1fr}.sidebar{position:static;height:auto;
border-right:0;border-bottom:1px solid var(--stroke)}.sidebar nav{display:flex;gap:5px;
flex-wrap:wrap}.sidebar nav a{margin:0}.nav-head,.side-note{display:none}
main{padding:22px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:580px){main{padding:18px 13px 60px}.metrics,.grid2,.seat-grid{
grid-template-columns:1fr}.details{grid-template-columns:1fr;gap:1px}.details dd{margin-bottom:13px}
.panel{padding:15px}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""


class OfflineDashboardError(ValueError):
    """Static dashboard source, output path or projection violates safeguards."""


def _e(value):
    """Encode all untrusted text for HTML; never insert raw source HTML."""
    return escape('Not supplied' if value is None else str(value), quote=True)


def _detail_rows(rows):
    parts = ['<dl class="details">']
    for label, value in rows:
        parts.append('<dt>%s</dt><dd>%s</dd>' % (_e(label), _e(value)))
    parts.append('</dl>')
    return ''.join(parts)


def _table(title, headers, rows):
    out = ['<div class="panel table-scroll"><table><caption>%s</caption><thead><tr>'
           % _e(title)]
    out.extend('<th scope="col">%s</th>' % _e(name) for name in headers)
    out.append('</tr></thead><tbody>')
    for row in rows:
        if len(row) != len(headers):
            raise OfflineDashboardError('Table row/heading mismatch.')
        out.append('<tr>')
        out.extend('<td>%s</td>' % _e(value) for value in row)
        out.append('</tr>')
    out.append('</tbody></table></div>')
    return ''.join(out)


def _section(name, lead, body):
    return ('<section id="%s" class="section" aria-labelledby="heading-%s">'
            '<h2 id="heading-%s">%s</h2><p class="lead">%s</p>%s</section>' %
            (_e(name), _e(name), _e(name), _e(SECTION_LABELS[name]), _e(lead), body))


def _panel_pairs(items):
    return '<div class="panel">%s</div>' % _detail_rows(items)


def _projections(*, epoch_id, as_of, report, role):
    panels = {}
    digest = None
    for name in ROUTES:
        response = build_offline_api_response(
            epoch_id=epoch_id, as_of=as_of, report=report, role=role,
            method='GET', route=API_PREFIX + '/' + name,
        )
        if (response['http_status_preview'] != 200
                or response['final_action'] != 'HOLD'
                or response['http_listener_created'] is not False
                or response['network_accessed'] is not False):
            raise OfflineDashboardError('Offline API returned an unsafe response.')
        body = response['body']
        if (not isinstance(body, dict) or body['final_action'] != 'HOLD'
                or body['can_execute_actions'] is not False
                or body['role_authenticated'] is not False
                or body['verified_external_gate_count'] != 0
                or body['verified_source_family_count'] != 0):
            raise OfflineDashboardError('API projection violates HOLD boundary.')
        if digest is None:
            digest = body['local_source_report_sha256']
        elif digest != body['local_source_report_sha256']:
            raise OfflineDashboardError('Mixed source snapshots in one dashboard.')
        panels[name] = body['panel']
    return panels, digest


def render_offline_dashboard(*, epoch_id, as_of, report=None, role='VIEWER'):
    """Pure function. Returns deterministic HTML over validated API projections."""
    try:
        cutoff = _timestamp(as_of)
        panels, digest = _projections(epoch_id=epoch_id, as_of=as_of,
                                      report=report, role=role)
    except (OfflineMissionControlAPIError, TypeError, KeyError, ValueError) as exc:
        raise OfflineDashboardError('Unsafe or incompatible read-only API source.') from exc
    if epoch_id != EPOCH or role not in SAFE_ROLES or type(role) is not str:
        raise OfflineDashboardError('Invalid historical epoch or role preview.')
    o = panels['overview']; s = panels['sources']; g = panels['gates']
    c = panels['council']; r = panels['research']; h = panels['health']
    m = panels['models']; a = panels['audit']
    if (len(s['source_families']) != 13 or len(g['gates']) != 11
            or len(m['types']) != 12 or c['design_seats'] != 31):
        raise OfflineDashboardError('Missing complete mandatory contract inventory.')
    if (a['local_source_report_sha256'] != digest or
            a['original_report_bytes_independently_attested'] is not False):
        raise OfflineDashboardError('Source hash or custody classification mismatch.')
    nav = ''.join('<a href="#%s">%s</a>' % (_e(name), _e(SECTION_LABELS[name]))
                  for name in ROUTES)
    snapshot_text = 'No Stage 11 report supplied' if digest is None else 'Local report, unverified'
    record_count = 'Unknown' if o['local_record_count'] is None else str(o['local_record_count'])
    metric_data = (
        ('Independent gates', '0 / 11', 'No gate independently verified'),
        ('Source families', '0 / 13', 'No family independently admitted'),
        ('Council design', '31', 'No real cycle verified'),
        ('Local records', record_count, 'Not independently validated'),
    )
    metrics = ''.join('<div class="metric"><small>%s</small><div class="number">%s</div>'
                      '<div class="sub">%s</div></div>' % (_e(label), _e(number), _e(sub))
                      for label, number, sub in metric_data)
    main = []
    main.append(_section('overview', 'Research-first status, not an execution terminal.',
                         _panel_pairs([
                             ('Snapshot', snapshot_text),
                             ('Stage 11 snapshot status', o['snapshot_status']),
                             ('Local record count', record_count),
                             ('Latest independently verified EUR/USD price', 'Not available'),
                             ('Independently verified returns', 'Not available'),
                             ('Research decision', o['research_decision']),
                             ('Operational readiness', 'BLOCKED_EXTERNAL_EVIDENCE'),
                         ])))
    main.append(_section('sources', 'Thirteen defined source contracts, zero independent admissions.',
                         _table('Source-family provenance', ['Slot', 'Family', 'Admission'],
                                [(x['slot'], x['label'], x['status'])
                                 for x in s['source_families']]) +
                         '<p class="hint">Local identifiers are not data provenance receipts. '
                         'Last independently verified receipt time is unknown.</p>'))
    main.append(_section('gates', 'Independent acceptance requirements remain unresolved.',
                         _table('External research evidence gates', ['Gate', 'Identifier', 'Verification'],
                                [(x['slot'], x['label'], x['status']) for x in g['gates']]) +
                         '<p class="hint">A local checksum or self-reported approval is not '
                         'independent attestation.</p>'))
    seats = ('<div class="seat-grid"><div class="seat"><b>28</b>Primary analysts</div>'
             '<div class="seat"><b>2</b>Governance seats</div>'
             '<div class="seat"><b>1</b>Synthesis seat</div></div>')
    main.append(_section('council', 'Design roster is not evidence that agents made a live decision.',
                         seats + _panel_pairs([
                             ('Verified real Council cycles', c['real_cycles_dispatched']),
                             ('Most recent independently verified vote', 'Not available'),
                             ('Most recent independently verified consensus', 'Not available'),
                             ('Risk Officer veto', c['risk_veto_state']),
                             ('Model weights promoted', c['model_weights_promoted']),
                             ('Risk veto override permitted', 'No'),
                         ])))
    main.append(_section('research', 'No independent forecast-outcome or costed performance proof.',
                         _panel_pairs([
                             ('Research decision', r['research_decision']),
                             ('Stage 9 independent research exit', 'Not met'),
                             ('Stage 10 independent research exit', 'Not met'),
                             ('Independent forecast/outcome pairs', 'Not available'),
                             ('Verified net P&L after trading costs', 'Not available'),
                             ('Verified calibration', 'Not available'),
                             ('Verified robustness', 'Not available'),
                             ('Scenario paths are actual probabilities', 'No'),
                         ])))
    main.append(_section('health', 'Health has not been inspected or independently attested.',
                         _panel_pairs([
                             ('Runtime health', h['status']),
                             ('Collector running', 'Unknown'),
                             ('Verified latest quote', 'Not available'),
                             ('Verified M5 close', 'Not available'),
                             ('Independently verified UTC clock', 'No'),
                             ('Risk kill switch clear', 'Unknown — do not assume clear'),
                             ('Broker connection', 'Not opened by dashboard'),
                             ('Safety policy', h['safety_policy']),
                         ])))
    main.append(_section('models', 'Twelve read-only data contracts; no trained or promoted model.',
                         _table('Available data-contract slots', ['Slot', 'Contract', 'Classification'],
                                [(x['slot'], x['dto_type'], x['classification'])
                                 for x in m['types']]) +
                         '<p class="hint">This is a contract inventory, not a list of validated '
                         'predictive models or verified performance results.</p>'))
    main.append(_section('audit', 'Local reproducibility does not establish external custody.',
                         _panel_pairs([
                             ('Historical epoch (pinned)', epoch_id),
                             ('Report as-of (UTC)', cutoff.isoformat()),
                             ('Report SHA-256 (local claim)', digest or 'Not supplied'),
                             ('Independently attested source bytes', 'No'),
                             ('Verified audit events', 'None available'),
                             ('Authentication', 'Not implemented — preview only'),
                             ('Role preview', role),
                             ('Report caution', a['diagnostic_caveat']),
                             ('Trading authorization', 'None'),
                         ])))
    html_text = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n'
                 '<meta charset="utf-8">\n'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
                 '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                 'style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">\n'
                 '<meta name="referrer" content="no-referrer">\n'
                 '<title>ORACLE | Offline Mission Control</title>\n<style>\n' + STYLE +
                 '\n</style>\n</head>\n<body>\n'
                 '<a class="skip" href="#main">Skip to main content</a>\n'
                 '<div class="shell"><aside class="sidebar"><div class="identity">EUR/USD ORACLE'
                 '<small>MISSION CONTROL</small></div><div class="nav-head">Diagnostic views</div>'
                 '<nav aria-label="Mission Control sections">' + nav + '</nav>'
                 '<div class="side-note">Offline HTML snapshot<br>No API listener<br>'
                 'No broker or agent operations<br>Not authenticated</div></aside>'
                 '<main id="main"><header class="header"><div><div class="eyebrow">'
                 'Offline research / read-only source</div><h1>Mission Control</h1>'
                 '<div class="subtitle">Evidence status and Council observability, '
                 'with unavailable data explicitly marked.</div></div>'
                 '<div class="time">As of (UTC)<br><strong>' + _e(cutoff.isoformat()) +
                 '</strong></div></header>'
                 '<div class="hold" role="status"><strong>FINAL=HOLD · No trading authorization'
                 '</strong><p>NOT_ENOUGH_EVIDENCE. Independent gates 0/11 and '
                 'admitted source families 0/13. No training, promotion or execution.</p></div>'
                 '<div class="metrics" aria-label="Research readiness summary">' + metrics +
                 '</div>' + ''.join(main) +
                 '<footer><strong>EUR/USD ORACLE</strong> · ' + _e(VERSION) +
                 '<p>Static local snapshot only. Not live. No chart or forecast has been '
                 'fabricated. All operator roles are untrusted display previews; '
                 'there is no login, order control, external verification or automatic refresh.</p>'
                 '</footer></main></div></body></html>\n')
    if len(html_text.encode('utf-8')) > MAX_HTML_BYTES:
        raise OfflineDashboardError('Rendered dashboard exceeds bounded output size.')
    return html_text


def write_new_html_snapshot(path, html_text):
    """One explicit local artifact export; never overwrite any existing file."""
    p = Path(path)
    if not isinstance(html_text, str) or not html_text.startswith('<!DOCTYPE html>'):
        raise OfflineDashboardError('Only the validated static HTML document can be exported.')
    if (not OUTPUT_BASENAME.fullmatch(p.name) or '..' in p.name or '..' in p.parts or
            any(parent.is_symlink() for parent in (p, *p.parents)) or
            not p.parent.is_dir() or p.exists() or p.is_symlink()):
        raise OfflineDashboardError('Output must be a new ordinary .html file in an existing directory.')
    raw = html_text.encode('utf-8')
    if len(raw) > MAX_HTML_BYTES:
        raise OfflineDashboardError('Dashboard exceeds output budget.')
    try:
        with p.open('xb') as stream:
            stream.write(raw)
    except OSError:
        raise OfflineDashboardError('Cannot create new local HTML snapshot.') from None
    return sha256(raw).hexdigest()


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only-dashboard', action='store_true', required=True)
    parser.add_argument('--offline-only', action='store_true', required=True)
    parser.add_argument('--epoch-id', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--role', default='VIEWER')
    parser.add_argument('--report-file')
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--inspect-only', action='store_true')
    choice.add_argument('--output-html')
    args = parser.parse_args(argv)
    try:
        report = None if args.report_file is None else read_offline_report(args.report_file)
        html_text = render_offline_dashboard(
            epoch_id=args.epoch_id, as_of=args.as_of, report=report, role=args.role)
        content_sha = sha256(html_text.encode('utf-8')).hexdigest()
        written = False
        if args.output_html is not None:
            if args.report_file is not None and Path(args.output_html) == Path(args.report_file):
                raise OfflineDashboardError('Cannot overwrite the input report.')
            content_sha = write_new_html_snapshot(args.output_html, html_text)
            written = True
    except (OfflineMissionControlAPIError, OfflineDashboardError, OSError,
            ValueError, TypeError, KeyError, OverflowError):
        print('STAGE 11 DASHBOARD BLOCKED: Unsafe, changed or noncausal offline source/output.')
        return 1
    print('EURUSD ORACLE | Stage 11 offline static Mission Control dashboard')
    print('Source: ' + ('NOT_SUPPLIED' if report is None else 'LOCAL_HASH_MATCH_UNATTESTED'))
    print('Role: ' + args.role + ' (PREVIEW ONLY, NOT AUTHENTICATED)')
    print('Views: 8 | External gates: 0/11 | Source families: 0/13')
    print('HTML created locally: ' + ('YES' if written else 'NO'))
    if written:
        print('Local HTML file: ' + str(args.output_html))
    print('HTML SHA-256: ' + content_sha)
    print('NO SERVER | NO EXTERNAL ASSETS | NO BROKER | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()
