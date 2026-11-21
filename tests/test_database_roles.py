from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
import os
from secrets import token_urlsafe
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
import pytest
import sqlalchemy as sa

from pmo.bootstrap import bootstrap
from pmo.database_roles import apply_roles, RoleError
from pmo.health_calculation import TZ, week_key
from pmo.recovery import connection_args, database_url
from test_evidence import binary
from test_facts import fact_path, medium, post, report_path
from test_reports import body, data, sections


@pytest.fixture
def split_roles():
    admin = database_url(os.environ['PMO_TEST_DATABASE_URL'])
    assert admin.host in ('127.0.0.1', '::1'), 'Use a dedicated local test cluster'
    suffix = uuid4().hex
    database = 'pmo_roles_test_' + suffix
    users = {kind: 'pmo_' + kind + '_' + suffix for kind in ('owner', 'api', 'worker')}
    with psycopg.connect(**connection_args(admin), autocommit=True) as control, ExitStack() as cleanup:
        def drop_role(username):
            control.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(username)))

        urls = {}
        for kind, username in users.items():
            password = token_urlsafe(32)
            control.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
                                    'NOBYPASSRLS PASSWORD {}').format(sql.Identifier(username), sql.Literal(password)))
            cleanup.callback(drop_role, username)
            urls[kind] = admin.set(database=database, username=username, password=password)
        control.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(
            sql.Identifier(database), sql.Identifier(users['owner'])))
        cleanup.callback(control.execute, sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
        yield urls


@contextmanager
def serve(url, tmp_path, *, evidence_dir=None, extra_env=None):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'DATABASE_URL': url.render_as_string(hide_password=False),
           'PMO_LOCAL_HTTP': '1', 'PMO_EVIDENCE_DIR': str(evidence_dir or (tmp_path / 'private')),
           'PMO_REPORT_ANALYSIS': '0', 'PMO_ASSISTANT_PREVIEWS': '0', **(extra_env or {})}
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'pmo.api:app', '--host', '127.0.0.1',
                             '--port', str(port), '--no-access-log'], env=env)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/', trust_env=False) as api:
            for _ in range(100):
                try:
                    if api.get(f'http://127.0.0.1:{port}/openapi.json').status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                assert proc.poll() is None
                time.sleep(.1)
            else:
                pytest.fail('API startup timed out')
            yield api
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def denied(conn, statements):
    for statement in statements:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


def test_nonowner_application_and_worker_with_explicit_privileges(split_roles, tmp_path):
    urls = split_roles
    owner_env = {**os.environ, 'DATABASE_URL': urls['owner'].render_as_string(hide_password=False)}
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True, env=owner_env)
    owner_engine = sa.create_engine(urls['owner'], hide_parameters=True)
    password = token_urlsafe(32)
    try:
        with owner_engine.begin() as seed:
            bootstrap(seed, 'Synthetic roles', 'fixture-admin', 'Fixture', password, enable_delegation=True)
            seed.execute(sa.text('GRANT UPDATE (password_hash) ON user_accounts TO '+urls['api'].username))
    finally:
        owner_engine.dispose()

    with psycopg.connect(**connection_args(urls['owner']), autocommit=True) as owner:
        with pytest.raises(RoleError, match='DISTINCT'):
            apply_roles(owner, urls['owner'].username, urls['worker'].username)
        command = [sys.executable, '-m', 'pmo.database_roles', '--dedicated-database',
                   '--api-role', urls['api'].username, '--worker-role', urls['worker'].username]
        process = subprocess.run(command, capture_output=True, text=True, env={**os.environ,
                                 'PMO_MIGRATION_DATABASE_URL': owner_env['DATABASE_URL']})
        assert process.returncode == 0, process.stderr
        # Apply again to prove ACL replacement and guard installation are repeatable.
        apply_roles(owner, urls['api'].username, urls['worker'].username)

        with psycopg.connect(**connection_args(urls['api']), autocommit=True) as api_db:
            assert api_db.execute('SELECT id FROM user_roles FOR SHARE').fetchall()
            assert api_db.execute("SELECT has_table_privilege('requirement_exception_events', 'SELECT,INSERT')").fetchone() == (True,)
            denied(api_db, [
                'CREATE TABLE public.unauthorized(id int)', 'CREATE TEMP TABLE unauthorized(id int)',
                'ALTER TABLE audit_logs DISABLE TRIGGER USER', 'TRUNCATE audit_logs',
                'UPDATE audit_logs SET action=action', 'DELETE FROM audit_logs',
                "INSERT INTO user_roles(user_id,role) SELECT id,'finance_verifier' FROM user_accounts",
                'UPDATE user_roles SET id=id', 'UPDATE persons SET id=id',
                'UPDATE idempotency_keys SET request_hash=request_hash', 'DELETE FROM idempotency_keys',
                'UPDATE appointment_authorities SET org_id=org_id',
                'UPDATE change_approval_grants SET project_id=project_id',
                'UPDATE user_accounts SET password_hash=password_hash',
                'UPDATE cost_entries SET actual=actual', 'UPDATE alembic_version SET version_num=version_num',
                'UPDATE requirement_exception_events SET fingerprint=fingerprint', 'TRUNCATE requirement_exception_events',
                'UPDATE health_snapshots SET input_hash=input_hash', 'DELETE FROM health_appeals',
                'UPDATE facts SET quote=quote', 'UPDATE fact_history SET reason=reason', 'DELETE FROM fact_source_receipts',
                'UPDATE report_analysis_runs SET report_hash=report_hash', 'DELETE FROM report_analysis_runs',
                'UPDATE reports SET weekly_basis=NULL',
                'UPDATE requirement_disputes SET reason=reason', 'DELETE FROM requirement_disputes',
                'UPDATE organization_user_invitations SET code_hash=code_hash', 'UPDATE account_status_changes SET reason=reason',
                'SELECT payload FROM outbox_events', 'UPDATE outbox_events SET status=status',
                sql.SQL('SET ROLE {}').format(sql.Identifier(urls['owner'].username)),
            ])
        with psycopg.connect(**connection_args(urls['worker']), autocommit=True) as worker_db:
            denied(worker_db, ['SELECT password_hash FROM user_accounts', 'SELECT token_hash FROM agent_credentials',
                               'SELECT * FROM evidence_files', 'SELECT * FROM audit_logs', 'UPDATE projects SET name=name',
                               'SELECT * FROM requirement_exception_events', 'SELECT * FROM health_snapshots',
                               'SELECT * FROM project_rule_scan_state',
                               'SELECT * FROM facts', 'SELECT * FROM fact_history',
                               'SELECT * FROM report_analysis_runs',
                               'SELECT * FROM requirement_disputes',
                               'SELECT * FROM organization_user_invitations', 'SELECT * FROM account_status_changes',
                               'UPDATE outbox_events SET payload=payload', 'DELETE FROM outbox_events'])

        with serve(urls['api'], tmp_path, extra_env={'PMO_REPORT_ANALYSIS':'1'}) as api:
            logged = api.post('auth/login', json=dict(username='fixture-admin', password=password))
            assert logged.status_code == 200, logged.text
            api.headers['X-CSRF-Token'] = logged.json()['csrfToken']
            key = lambda rev=1: {'Idempotency-Key': uuid4().hex, 'If-Match': str(rev)}
            draft = api.post('projects', headers=key(), json={'name': 'Restricted runtime', 'code': 'ROLE-SMOKE'})
            assert draft.status_code == 200, draft.text
            path = 'projects/' + draft.json()['data']['projectId']
            assert api.get(path).status_code == 200
            patch = api.patch(path, headers=key(), json={'fields': {'name': 'Restricted revision'}})
            assert patch.status_code == 200, patch.text
            preview_key = key(patch.json()['data']['revision'])
            preview_path = path + '/assistant/suggestions'
            first = api.post(preview_path, headers=preview_key, json={'mode': 'charter'})
            assert data(first)['errorCode'] == 'MODEL_DISABLED'
            assert api.post(preview_path, headers=preview_key, json={'mode': 'charter'}).json() == first.json()
            with psycopg.connect(**connection_args(urls['api']), autocommit=True) as api_db:
                denied(api_db, ["UPDATE idempotency_keys SET response_body='{}' WHERE operation='pmo_project_create'",
                               "UPDATE idempotency_keys SET status='pending' WHERE operation='human_assistant_preview'"])
            stale = api.patch(path, headers=key(), json={'fields': {'name': 'Stale revision'}})
            assert stale.status_code == 409 and stale.json()['code'] == 'VERSION_CONFLICT'
            audit = api.get('admin/audits', params={'requestId': stale.headers['X-Request-ID']})
            assert audit.json()['data']['items'][0]['result'] == 'denied'
            ms = api.post(path + '/milestones', headers=key(), json={'name': 'Evidence target', 'plannedDate': '2026-11-01', 'isKey': True})
            assert ms.status_code == 200, ms.text
            uploaded = api.post(path + '/evidence', headers=key(), files={'file': ('proof.png', binary('png'), 'image/png')},
                                data={'linkedResourceType': 'milestone', 'linkedResourceId': ms.json()['data']['milestoneId']})
            assert uploaded.status_code == 200, uploaded.text
            posting = api.post(path + '/cost-postings', headers=key(), json={'type': 'money', 'amount': '12.50',
                              'period': '2020-01', 'sourceRef': 'synthetic-invoice'})
            assert posting.status_code == 200, posting.text
            assert api.get(path + '/cost-postings').status_code == 200
            collection = api.get('me/agent-tokens').json()['data']
            issue = api.post('me/agent-tokens', headers=key(collection['collectionRevision']), json={
                'allowedProjectIds': [draft.json()['data']['projectId']], 'allowedActions': ['pmo_project_get'],
                'expiresAt': (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            })
            assert issue.status_code == 200, issue.text
            token = issue.json()['data']
            with httpx.Client(base_url=api.base_url, trust_env=False) as agent:
                auth = {'Authorization': 'Bearer ' + issue.headers['X-PMO-New-Token']}
                assert agent.get(path, headers=auth).status_code == 200
                revoke = api.post('me/agent-tokens/' + token['tokenId'] + '/revoke', headers=key(token['revision']),
                                  json={'reason': 'Isolated role check'})
                assert revoke.status_code == 200, revoke.text
                assert agent.get(path, headers=auth).status_code == 401

            # Only seed the project appointment/stage. The report, scan, and notification run as restricted logins.
            pid = draft.json()['data']['projectId']
            with owner.transaction():
                owner.execute("UPDATE projects SET pm_id=initiator_id, stage='executing' WHERE id=%s", (pid,))
                owner.execute('''INSERT INTO project_members(project_id,person_id,assigned_by,role,assignment_status,accepted_at)
                    SELECT id,initiator_id,initiator_id,'pm','accepted',now() FROM projects WHERE id=%s''', (pid,))
            milestone = ms.json()['data']['milestoneId']
            report = data(api.post(path + '/reports', headers=key(), json=body(period=week_key(datetime.now(TZ).date()),
                structuredFields=sections(milestone_progress=[{'milestoneId': milestone, 'claim': 'progress'}]))))
            scan_env = {**os.environ, 'DATABASE_URL': urls['api'].render_as_string(hide_password=False)}
            command = [sys.executable, '-m', 'pmo.rule_scan']
            first_scan = subprocess.run(command, check=True, capture_output=True, text=True, env=scan_env)
            assert '"scanned": 1' in first_scan.stdout
            assert '"scanned": 0' in subprocess.run(command, check=True, capture_output=True, text=True, env=scan_env).stdout
            snapshot = data(api.get(path + '/health'))
            assert snapshot['health'] == 'untrusted' and 'LR06_CLAIM_WITHOUT_EVIDENCE' in snapshot['gaps']
            refreshed = data(api.post(path + '/health/refresh', headers=key(snapshot['projectRevision'])))
            assert refreshed['snapshotId'] == snapshot['snapshotId']
            fact = data(post(api, report))
            assert data(medium(api, fact))['validationStatus'] == 'verified'
            assert data(api.get(fact_path(fact) + '/history'))['total'] == 2
            assert data(api.get(report_path(report) + '/facts'))['items'][0]['quote'] == '2.50'
            analysis = data(api.post(report_path(report)+'/analyze', headers=key(report['revision']), json={}))
            assert analysis['status']=='queued'
            rerun = subprocess.run([sys.executable,'-m','pmo.report_analysis_worker'], check=True,
                capture_output=True,text=True,env={**scan_env,'PMO_REPORT_ANALYSIS':'0'})
            assert 'processed: 1' in rerun.stdout
            completed, = data(api.get(report_path(report)+'/analysis-runs'))['items']
            assert completed['runId']==analysis['runId'] and completed['errorCode']=='MODEL_DISABLED'
            assert completed['status']=='manual' and completed['fallbackTaskId']
            drafts = path+'/reports/weekly-drafts'
            basis_read = data(api.get(path+'/reports'))
            generated = data(api.post(drafts, headers=key(basis_read['weeklyGenerationRevision']), json={'period':report['period']}))
            assert generated['status']=='draft' and generated['weeklyBasis']['health']['health']=='untrusted'
            invite = api.post('admin/users/invitations', headers=key(), json={'email':'synthetic@example.test','role':'sponsor'})
            assert data(invite)['state']=='pending'
            with httpx.Client(base_url=api.base_url, trust_env=False) as enrollee:
                account = data(enrollee.post('auth/invitations/accept', headers=key(1), json={
                    'invitationCode':invite.headers['X-PMO-Org-Invitation'], 'username':'roles-recruit',
                    'displayName':'Role fixture recruit', 'password':token_urlsafe(24)}))
                assert account['revision']==1 and not account['isSystemAdmin']
            saved = data(api.patch(f"admin/users/{account['userId']}/status", headers=key(1),
                json={'isActive':False, 'reason':'Independent least privilege test'}))
            assert not saved['isActive'] and saved['revision']==2
            assert api.post('auth/logout').raise_for_status().json()['data'] == {'loggedOut': True}

        with psycopg.connect(**connection_args(urls['api']), autocommit=True) as runtime:
            denied(runtime, ["""INSERT INTO cost_entries(project_id,created_by,type,category,period_start,period_end,budget,actual,forecast_remaining,baseline_version)
                SELECT id,created_by,'money','forbidden','2020-02-01','2020-02-29',0,99,0,1 FROM projects"""])
        assert owner.execute('SELECT count(*) FROM cost_baseline_history').fetchone() == (1,)
        worker_process = subprocess.run([sys.executable, '-m', 'pmo.outbox'], check=True, capture_output=True, text=True,
                                       env={**os.environ, 'DATABASE_URL': urls['worker'].render_as_string(hide_password=False)})
        assert 'acknowledged' in worker_process.stdout
        assert owner.execute("SELECT count(*) FROM outbox_events WHERE status<>'sent'").fetchone() == (0,)
        assert owner.execute('SELECT count(*) FROM project_event_inbox').fetchone()[0] >= 4
        assert owner.execute('SELECT count(*) FROM organization_event_inbox').fetchone() == (3,)
        assert owner.execute('''SELECT r.rule_id,n.delivery_status,n.recipient_person_id=p.pm_id
            FROM notifications n JOIN rule_events r ON r.id=n.rule_event_id JOIN projects p ON p.id=r.project_id
        ''').fetchall() == [('LR-06', 'delivered', True)]
