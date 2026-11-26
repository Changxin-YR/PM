"""Prepare and verify the exact synthetic material used in native pilot egress."""
import json
from contextlib import ExitStack
from datetime import timedelta
from hashlib import sha256
from subprocess import SubprocessError
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from scripts import pilot_local as local
from scripts import pilot_seed as seed

REPORT_CODE = 'SIM-EXEC-001'
REPORT_FILE = 'model-report-evidence.json'
DRILL_CODES = frozenset(('SIM-EXEC-001-DRILL', 'SIM-NEW-001-DRILL'))
SUBJECT = 'SIM-REPORT-REAL-01'
FIELDS = {'effort_person_days': '2.50', 'milestone_progress': [], 'requirement_progress': [],
          'risks_and_blockers': []}


def _fixture_project(conn, cfg, code, item, receipt):
    pid = UUID(item['id'])
    creation = receipt['steps']['project']
    if (receipt['dataset'], receipt['seed'], receipt['batch'], creation['status'],
            creation['data']['projectId'], creation['body']['code']) != (
            seed.DATASET, seed.SEED, cfg['id'], 200, str(pid), code):
        raise ValueError('Synthetic project provenance does not match the protected seed receipt.')
    if conn.execute('SELECT current_database(), current_schema()').fetchone() != (local.DB_NAME, local.schema(cfg)):
        raise ValueError('Not the dedicated synthetic pilot schema.')
    project = conn.execute('''SELECT p.stage, p.revision FROM projects p
        JOIN organizations o ON o.id=p.org_id WHERE p.id=%s AND p.code=%s AND o.name=%s AND p.description=%s''',
        (pid, code, seed.ORG, creation['body']['description'])).fetchone()
    if project is None:
        raise ValueError('Synthetic project identity does not match the dedicated pilot database.')
    return project


def closing_material(code, explicit_id=None):
    if code not in DRILL_CODES:
        raise local.ModelConfigError('Select an explicit archived model drill code.')
    try:
        cfg = local.settings()
        local.verify_database(cfg)
        item = local.read('rehearsals.json', {})[code]
        receipt = local.read(f'{code}-receipts.json')
        pid = UUID(item['id'])
        if explicit_id is not None and pid != UUID(explicit_id):
            raise ValueError('Drill registration UUID does not match the archived fixture.')
        step = receipt['steps']['retrospective-answers']
        with local.db_connect(cfg, 'api') as conn:
            stage, rev = _fixture_project(conn, cfg, code, item, receipt)
            retro = conn.execute('SELECT id, revision, answers FROM project_retrospectives WHERE project_id=%s',
                                 (pid,)).fetchone()
        if (stage != 'archived' or receipt.get('rehearsalComplete') is not True or step['status'] != 200
                or retro is None or retro[2] != step['body']['answers']):
            raise ValueError('The archived retrospective does not match its synthetic rehearsal receipt.')
        encoded = json.dumps(retro[2], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return {'code': code, 'id': str(pid), 'mode': 'closing', 'revision': rev,
                'sourceRef': f'retrospective:{retro[0]}@{retro[1]}', 'sourceHash': sha256(encoded).hexdigest(),
                'answers': retro[2]}
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, SubprocessError, psycopg.Error):
        raise local.ModelConfigError('Cannot verify the registered archived drill against its isolated source.') from None


def register_closing(code, explicit_id):
    material = closing_material(code, explicit_id)
    scopes = local.read('model-closing-scope.json', {})
    local.save('model-closing-scope.json', scopes | {code: material['id']})
    return material


def registered_closing(code):
    try:
        pid = local.read('model-closing-scope.json', {})[code]
        if not isinstance(pid, str):
            raise TypeError('Expected registered UUID.')
    except (OSError, KeyError, TypeError, ValueError):
        raise local.ModelConfigError('Register the archived drill with its exact code and UUID first.') from None
    return closing_material(code, pid)


def _active_queue(conn):
    return [{'runId': str(rid), 'projectId': str(pid), 'reportId': str(report_id), 'revision': rev, 'status': status}
            for rid, pid, report_id, rev, status in conn.execute('''SELECT id, project_id, report_id, report_revision,
                status FROM report_analysis_runs WHERE status IN ('queued', 'running') ORDER BY id''')]


def verify_report(codes, *, require_empty_queue=False):
    if list(codes) != [REPORT_CODE]:
        raise local.ModelConfigError(f'The prepared report scope is exactly {REPORT_CODE}.')
    cfg = local.settings()
    local.verify_database(cfg)
    item = local.pilot_manifest()[REPORT_CODE]
    receipt = local.read(f'{REPORT_CODE}-receipts.json')
    expected = local.read(REPORT_FILE)
    if not expected:
        raise local.ModelConfigError('Prepare the synthetic report before activation.')
    payload, saved = expected['input'], expected['receipt']
    if payload['structuredFields'] != FIELDS or payload['narrative'] != _narrative(expected['asOf']):
        raise local.ModelConfigError('Prepared report expectation differs from the synthetic fixture.')
    with local.db_connect(cfg, 'api') as conn:
        _fixture_project(conn, cfg, REPORT_CODE, item, receipt)
        current = conn.execute('''SELECT r.revision, r.content_hash, r.narrative, r.structured_fields, r.status,
            r.submitted_by=p.pm_id, r.period FROM reports r JOIN projects p ON p.id=r.project_id
            WHERE r.id=%s AND r.project_id=%s''', (saved['reportId'], item['id'])).fetchone()
        active = _active_queue(conn)
    if current != (saved['revision'], saved['contentHash'], payload['narrative'], FIELDS, 'submitted', True,
                   payload['period']):
        raise local.ModelConfigError('The prepared report changed or lacks an authenticated PM submission.')
    if active and require_empty_queue:
        raise local.ModelConfigError('Existing queued or running analysis needs operator review before activation.')
    return expected | {'projectId': item['id'], 'activeQueue': active}


def _narrative(as_of):
    return f'截至 {as_of}，{SUBJECT} 接口联调累计投入 2.50 人天。'


def prepare_report():
    if not local.ready() or 'report' in local.read('runtime.json', {}).get('models', {}):
        raise local.ModelConfigError('Prepare the synthetic report while its analysis channel is off.')
    cfg = local.settings()
    local.verify_database(cfg)
    item = local.pilot_manifest()[REPORT_CODE]
    receipt = local.read(f'{REPORT_CODE}-receipts.json')
    with local.db_connect(cfg, 'api') as conn:
        _fixture_project(conn, cfg, REPORT_CODE, item, receipt)
        periods = {row[0] for row in conn.execute("SELECT DISTINCT period FROM reports WHERE project_id=%s AND type='daily'",
                                                  (item['id'],))}
        now = conn.execute('SELECT clock_timestamp()').fetchone()[0].astimezone(ZoneInfo('Asia/Shanghai'))
    state = local.read(REPORT_FILE)
    if state and 'receipt' in state:
        return verify_report([REPORT_CODE])
    if not state:
        day = next((now.date() - timedelta(days=i) for i in range(31)
                    if (now.date() - timedelta(days=i)).isoformat() not in periods), None)
        if day is None:
            raise local.ModelConfigError('No unused daily period in the last 31 days.')
        at = now.replace(year=day.year, month=day.month, day=day.day, hour=0, minute=0, second=0, microsecond=0)
        as_of = at.isoformat()
        state = {'asOf': as_of, 'input': {'reportType': 'daily', 'period': day.isoformat(),
                 'structuredFields': FIELDS, 'narrative': _narrative(as_of)},
                 'idempotencyKey': sha256(f'{cfg["id"]}:{SUBJECT}:{as_of}'.encode()).hexdigest()}
        local.save(REPORT_FILE, state)
    credentials = local.read('synthetic-logins.json')
    with ExitStack() as stack:
        pm = seed.login(stack, credentials, 'pm-a')
        reviewer = seed.login(stack, credentials, 'review-01')
        response = pm.post(f'projects/{item["id"]}/reports', json=state['input'],
                           headers={'Idempotency-Key': state['idempotencyKey']})
        response.raise_for_status()
        row = response.json()['data']
        reviewer.get(f'reports/{row["reportId"]}').raise_for_status()
        state.update(receipt={key: row[key] for key in ('reportId', 'revision', 'contentHash', 'submittedAt', 'submittedBy')},
                     requestId=response.headers.get('X-Request-ID'), expected={'subject': SUBJECT, 'typedValue': '2.50',
                     'unit': 'person-day', 'quotePath': '/narrative', 'asOf': state['asOf']})
        local.save(REPORT_FILE, state)
        reviewer.post('auth/logout').raise_for_status()
        pm.post('auth/logout').raise_for_status()
    return verify_report([REPORT_CODE])
