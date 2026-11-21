from datetime import UTC, date, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from hashlib import sha256
from uuid import uuid4

from argon2 import PasswordHasher
import httpx
import psycopg
from psycopg import sql
import pytest
import sqlalchemy as sa

from pmo import evidence_storage, recovery, schema_v1 as db
from pmo.health_calculation import TZ, week_key
from pmo.requirement_disputes import t as disputes
from pmo.session_schema import sessions
from test_database_roles import serve
from test_evidence import binary
from test_facts import fact_path, post, proposal, report_path, worksheet
from test_org_accounts import attend, change, command, inv_state, invite
from test_report_analysis import answer, gateway, history, request
from test_reports import body, data, headers, sections
from test_reviews import data_login, member


@pytest.fixture
def recovery_db(tmp_path, monkeypatch):
    admin = recovery.database_url(os.environ['PMO_TEST_DATABASE_URL'])
    assert admin.host in ('127.0.0.1', '::1'), 'Pass a dedicated local test cluster only'
    if (admin.port, admin.username, admin.database) == (55439, 'pmo', 'pmo'):
        tools = recovery.PgTools('pmo104dev-db-1')
    elif shutil.which('pg_dump') and shutil.which('pg_restore'):
        tools = recovery.PgTools()
    else:
        pytest.fail('Install PostgreSQL client tools for the real recovery test')
    source = admin.set(database='pmo_backup_test_' + uuid4().hex)
    engine = sa.create_engine(source, hide_parameters=True)
    originals = tmp_path / 'source-evidence'; originals.mkdir(mode=0o700)
    monkeypatch.setenv('PMO_EVIDENCE_DIR', str(originals))
    with psycopg.connect(**recovery.connection_args(admin), autocommit=True) as control:
        control.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(source.database)))
        try:
            subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', '0016'], check=True,
                           env={**os.environ, 'DATABASE_URL': source.render_as_string(hide_password=False)})
            yield admin, source, engine, originals, tools, control
        finally:
            engine.dispose()
            control.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(source.database)))


def test_joint_recovery_keeps_only_snapshot_objects(recovery_db, tmp_path, monkeypatch):
    admin, source, engine, originals, tools, control = recovery_db
    png = binary('png')
    owner, verifier, customer = uuid4(), uuid4(), uuid4()
    password = uuid4().hex
    preupgrade_session, preupgrade_csrf = uuid4().hex, uuid4().hex
    with engine.begin() as conn:
        org = conn.scalar(db.organizations.insert().values(name='Recovery fixture', status='active').returning(db.organizations.c.id))
        digest = PasswordHasher(time_cost=1, memory_cost=8192).hash(password)
        for person, username in ((owner, 'fixture-pm'), (verifier, 'fixture-reviewer'), (customer, 'fixture-customer')):
            account = conn.scalar(db.user_accounts.insert().values(username=username, password_hash=digest, is_active=True)
                                  .returning(db.user_accounts.c.id))
            conn.execute(db.persons.insert().values(id=person, created_by=person, name=username, org_id=org,
                                                   is_active=True, user_id=account))
            if person == owner:
                conn.execute(db.user_roles.insert().values(user_id=account, role='system_admin'))
                conn.execute(sessions.insert().values(account_id=account, token_hash=sha256(preupgrade_session.encode()).hexdigest(),
                    csrf_hash=sha256(preupgrade_csrf.encode()).hexdigest(), expires_at=datetime.now(UTC)+timedelta(hours=1)))
        project = conn.scalar(db.projects.insert().values(org_id=org, code='DRILL', name='Recovery fixture', stage='drafting',
                             revision=1, initiator_id=owner, created_by=owner, pm_id=owner).returning(db.projects.c.id))
        member(conn, project, owner, owner, 'pm')
        member(conn, project, verifier, owner, 'reviewer')
        customer_assignment = member(conn, project, customer, owner, 'client_representative')
        contact = conn.scalar(db.stakeholders.insert().values(project_id=project, created_by=owner, name='Fixture client',
                              type='customer', linked_person_id=customer).returning(db.stakeholders.c.id))
        requirement = conn.scalar(db.requirements.insert().values(project_id=project, created_by=owner,
            desc='Existing export scope', priority='P1', acceptance_criteria='Export contains verified totals',
            status='baseline', baseline_version=1, planned_confirm_at=date(2026, 11, 20)).returning(db.requirements.c.id))
        milestone = conn.scalar(db.milestones.insert().values(project_id=project, created_by=owner, name='Deliver', ordinal=1,
                                planned_date=date(2026, 10, 1), is_key=True).returning(db.milestones.c.id))
        legacy_report = conn.scalar(db.reports.insert().values(project_id=project, created_by=owner, author_id=owner,
            type='weekly', period='2026-W40', period_start=date(2026, 9, 28), period_end=date(2026, 10, 5),
            version_no=1, is_current=True, is_current_confirmed=True, status='confirmed', structured_fields={},
            narrative='Preserve legacy report text', submitted_at=datetime(2026, 10, 5, tzinfo=UTC)).returning(db.reports.c.id))
        old_snapshot = conn.scalar(db.health_snapshots.insert().values(project_id=project, as_of=datetime(2026, 10, 5, tzinfo=UTC),
            data_trust='trusted', input_hash='a'*64, input_revisions={}, results={'health': 'green'},
            rule_version='legacy-synthetic', source_refs=[f'report:{legacy_report}@1']).returning(db.health_snapshots.c.id))
        old_fact = conn.scalar(db.facts.insert().values(project_id=project, report_id=legacy_report, created_by=owner,
            subject='Legacy synthetic fact', predicate='time', value='unknown', as_of=datetime(2026, 10, 5, tzinfo=UTC),
            quote='Legacy source', source_ref=f'report:{legacy_report}@1', trust='low', validation_status='candidate').returning(db.facts.c.id))

    def upload(content=png, resource='milestone', resource_id=milestone, name='record.png', mime='image/png'):
        key = uuid4().hex
        with engine.begin() as conn:
            eid = conn.scalar(db.evidence_files.insert().values(project_id=project, original_name=name, owner_id=owner,
                              uploaded_by=owner, sha256=sha256(content).hexdigest(), size_bytes=len(content), mime_type=mime,
                              storage_key=key, quarantine_status='pending_review').returning(db.evidence_files.c.id))
            conn.execute(sa.text("""INSERT INTO public.evidence_links (evidence_id, resource_type, resource_id, description)
                                  VALUES (:eid, :resource, :resource_id, 'Recovery fixture')"""),
                         dict(eid=eid, resource=resource, resource_id=resource_id))
            evidence_storage.store(content, key)
        return key

    original = upload()
    with engine.connect() as c:
        assert c.scalar(sa.text('SELECT version_num FROM alembic_version')) == '0016'
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True,
                   env={**os.environ, 'DATABASE_URL': source.render_as_string(hide_password=False)})
    path = f'projects/{project}'
    sheet = worksheet()
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == project).values(stage='executing'))
        original_id = c.scalar(sa.select(db.evidence_files.c.id).where(db.evidence_files.c.storage_key == original))
        # Storage drill fixture only; authenticated dispute writes are exercised in the random-schema HTTP suite.
        dispute = c.scalar(disputes.insert().values(project_id=project, requirement_id=requirement, baseline_version=1,
            requirement_revision=1, customer_id=customer, assignment_id=customer_assignment, stakeholder_id=contact,
            reason='Original synthetic objection', open_request_id=uuid4()).returning(disputes.c.id))
        c.execute(disputes.update().where(disputes.c.id == dispute).values(revision=2, withdrawn_at=sa.func.clock_timestamp(),
            withdrawal_reason='Explicit synthetic withdrawal', withdrawal_request_id=uuid4()))
    with serve(source, tmp_path, evidence_dir=originals, extra_env={'PMO_REPORT_ANALYSIS':'1'}) as pm:
        pm.cookies.set('pmo_session', preupgrade_session, domain='127.0.0.1', path='/api/v1')
        pm.headers['X-CSRF-Token'] = preupgrade_csrf
        identity = pm.get('auth/me')
        assert identity.status_code==200 and identity.json()['personId']==str(owner)
        dispute_path = f'requirements/{requirement}/disputes'
        saved_dispute, = data(pm.get(dispute_path))['items']
        assert saved_dispute['disputeId'] == str(dispute) and saved_dispute['revision'] == 2
        with httpx.Client(base_url=pm.base_url, trust_env=False) as reviewer:
            reviewer.headers['X-CSRF-Token'] = data_login(reviewer, 'fixture-reviewer', password)
            proof = data(reviewer.post(f'evidence/{original_id}/verify', headers=headers(1),
                json={'reason':'Compared the synthetic source bytes', 'sha256':sha256(png).hexdigest()}))
        report_time = datetime.now(TZ)
        report = data(pm.post(path + '/reports', headers=headers(), json=body(period=week_key(report_time.date()),
            narrative='Work took 2.50 days. Synthetic source trail retained after recovery.', structuredFields=sections(milestone_progress=[{
                'milestoneId':str(milestone), 'claim':'progress', 'evidenceRefs':[proof['evidenceRef']]}]))))
        published = data(pm.post(f"reports/{report['reportId']}/confirm", headers=headers(report['revision'])))
        candidate = data(post(pm, published, proposal(published, asOf=report_time.isoformat())))
        att = data(pm.post(path + '/evidence', headers=headers(2), files={'file':('worksheet.png',sheet,'image/png')},
            data={'linkedResourceType':'report','linkedResourceId':report['reportId']}))
        with httpx.Client(base_url=pm.base_url, trust_env=False) as reviewer:
            reviewer.headers['X-CSRF-Token'] = data_login(reviewer, 'fixture-reviewer', password)
            data(reviewer.post(f"evidence/{att['evidenceId']}/verify", headers=headers(att['revision']),
                json={'reason':'Compared the worksheet bytes', 'sha256':sha256(sheet).hexdigest()}))
            fact = data(reviewer.post(fact_path(candidate)+'/verify', headers=headers(candidate['revision']),
                json={'trust':'high','evidenceRef':att['evidenceRef'],'reason':'Row 3 of the synthetic worksheet records 2.50.'}))
        assert fact['validationStatus'] == 'verified' and fact['trust'] == 'high'
        snapshot = data(pm.post(path + '/health/refresh', headers=headers(1)))
        weekly = snapshot['inputs']['weekly']
        assert weekly['receipts'][0]['hash'] == published['contentHash']
        assert weekly['claims'][0]['evidence'][0]['sha256'] == sha256(png).hexdigest()
        assert not weekly['missingEvidence'] and snapshot['snapshotId'] != str(old_snapshot)
        assert data(request(pm, published))['status']=='queued'
        with gateway(answer()) as (url, calls):
            done = subprocess.run([sys.executable,'-m','pmo.report_analysis_worker'], check=True, capture_output=True, text=True,
                env={**os.environ,'DATABASE_URL':source.render_as_string(hide_password=False),
                     'PMO_REPORT_ANALYSIS':'1','PMO_REPORT_MODEL_URL':url,'PMO_REPORT_MODEL_TOKEN':''})
            assert 'processed: 1' in done.stdout and len(calls)==1
        analysis, = history(pm, published)['items']
        assert analysis['status']=='ready' and analysis['requestedBy']==str(owner)
        assert analysis['candidates'][0]['sourceRef']==f"report:{published['reportId']}@{published['revision']}"
        draft = data(pm.post(path+'/reports/weekly-drafts', headers=headers(1), json={'period':published['period']}))
        weekly_basis = draft['weeklyBasis']
        weekly_fact, = weekly_basis['verifiedFacts']
        assert weekly_fact['evidenceRef']==att['evidenceRef'] and weekly_fact['evidenceHash']==sha256(sheet).hexdigest()
        generated = data(pm.patch(report_path(draft), headers=headers(draft['revision']),
            json={'structuredFields':{**draft['structuredFields'],'effort_person_days':'2.50'},'narrative':'PM verified after generation.'}))
        submitted = data(pm.post(report_path(draft)+'/submit', headers=headers(generated['revision']), json={}))
        weekly_published = data(pm.post(report_path(draft)+'/confirm', headers=headers(submitted['revision']), json={}))
        assert weekly_published['weeklyBasis']==weekly_basis and weekly_published['isCurrentConfirmed']
        issued = invite(pm, 'recovery-recruit@example.test')
        invite_receipt = data(issued)
        intake = command(issued.headers['X-PMO-Org-Invitation'])
        with httpx.Client(base_url=pm.base_url, trust_env=False) as recruit:
            enrollment = data(attend(recruit, intake))
            assert recruit.post('auth/login', json={'username':intake['username'],'password':intake['password']}).is_success
            assert not data(change(pm, enrollment['userId'], False))['isActive']
    with engine.connect() as c:
        fact_attachment = c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id==att['evidenceId']))
    failed = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', '0026'], capture_output=True, text=True,
        env={**os.environ, 'DATABASE_URL': source.render_as_string(hide_password=False)})
    assert failed.returncode != 0 and 'Refusing to discard requirement dispute history' in failed.stderr
    book = json.dumps({'schemaVersion': 1, 'projectId': str(project), 'coveredThrough': datetime.now(UTC).isoformat(),
        'lines': [{'type': 'money', 'netActual': '100.00', 'sourceRef': 'SYNTHETIC-GL'},
                  {'type': 'effort', 'netActual': '10.00', 'sourceRef': 'SYNTHETIC-TIMESHEET'}]}).encode()
    external = upload(book, 'project', project, 'statement.json', 'application/json')
    orphan = uuid4().hex; evidence_storage.store(png, orphan)
    late = []
    run = tools.run

    def commit_after_snapshot(url, executable, args, **streams):
        if executable == 'pg_dump' and not late:
            late.append(upload())
        run(url, executable, args, **streams)

    monkeypatch.setattr(tools, 'run', commit_after_snapshot)
    output = tmp_path / 'points'
    targets = tmp_path / 'drill'
    point, manifest = recovery.backup(source, originals, output, tools)
    assert {obj.key for obj in manifest.objects} == {original, external, fact_attachment}
    assert manifest.unreferencedCandidates == 2 and recovery.daily_points(output) == 1
    assert {entry.name for entry in (point / 'evidence').iterdir()} == {original, external, fact_attachment}

    # Run the operator command end to end, not an in-process stand-in for pg_restore.
    cmd = [sys.executable, '-m', 'pmo.recovery']
    if tools.dev_container:
        cmd.append('--dev-compose')
    result = subprocess.run([*cmd, 'restore-drill', str(point), str(targets), '--trusted-backup'],
                            env={**os.environ, 'PMO_RESTORE_ADMIN_URL': admin.render_as_string(hide_password=False)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['result'] == 'verified' and receipt['objects'] == 3 and receipt['revision'] == manifest.revision
    name = receipt['database']
    assert name.startswith('pmo_restore_') and name != source.database
    try:
        with psycopg.connect(**recovery.connection_args(admin.set(database=name))) as restored:
            assert restored.execute('SELECT name FROM public.projects WHERE id = %s', (project,)).fetchone() == ('Recovery fixture',)
            assert restored.execute('SELECT count(*) FROM public.evidence_links WHERE resource_id = %s', (milestone,)).fetchone() == (1,)
            rows = restored.execute('''SELECT f.storage_key, f.sha256, f.size_bytes, l.resource_type, l.resource_id
                FROM public.evidence_files f JOIN public.evidence_links l ON l.evidence_id=f.id''').fetchall()
            assert {(r[0],r[3],str(r[4])) for r in rows} == {(original,'milestone',str(milestone)), (external,'project',str(project)),
                (fact_attachment,'report',report['reportId'])}
            assert restored.execute('SELECT version_num FROM public.alembic_version').fetchone() == (manifest.revision,)
            assert restored.execute('''SELECT narrative, content_hash, submitted_by, confirmed_by, confirmed_at
                FROM public.reports WHERE id=%s''', (legacy_report,)).fetchone() == ('Preserve legacy report text', None, None, None, None)
            assert restored.execute('SELECT results FROM health_snapshots WHERE id=%s', (old_snapshot,)).fetchone() == ({'health':'green'},)
            assert restored.execute('SELECT quote FROM facts WHERE id=%s', (old_fact,)).fetchone() == ('Legacy source',)
            assert restored.execute('''SELECT content_hash,submitted_by,confirmed_by,revision FROM reports WHERE id=%s''',
                (published['reportId'],)).fetchone() == (published['contentHash'], owner, owner, published['revision'])
            monkeypatch.setenv('PMO_EVIDENCE_DIR', str(targets / receipt['evidenceDirectory']))
            assert {r[0]: evidence_storage.checked_bytes(*r[:3]) for r in rows} == {original: png, external: book, fact_attachment: sheet}
        with serve(admin.set(database=name), tmp_path, evidence_dir=targets / receipt['evidenceDirectory']) as pm:
            pm.headers['X-CSRF-Token'] = data_login(pm, 'fixture-pm', password)
            readback = data(pm.get(path + '/health', params={'asOf':snapshot['asOf']}))
            assert readback['historical'] and readback['snapshotId'] == snapshot['snapshotId']
            assert (readback['inputHash'], readback['inputs']['weekly']) == (snapshot['inputHash'], weekly)
            assert data(pm.get(report_path(report)+'/facts'))['items'] == [fact]
            assert history(pm, published)['items']==[analysis]
            assert data(pm.get(dispute_path))['items'] == [saved_dispute]
            assert data(pm.get(report_path(weekly_published)))==weekly_published
            assert inv_state(pm, invite_receipt)=='accepted'
            restored_user, = [row for row in data(pm.get('admin/users'))['items'] if row['userId']==enrollment['userId']]
            assert restored_user['revision']==2 and not restored_user['isActive']
            with httpx.Client(base_url=pm.base_url, trust_env=False) as recruit:
                assert attend(recruit, intake).status_code==403
                assert recruit.post('auth/login', json={'username':intake['username'],'password':intake['password']}).status_code==401
            assert data(pm.get(fact_path(fact)+'/history'))['items'][0]['actorId'] == str(verifier)
            assert data(pm.get(f'reports/{legacy_report}/facts'))['items'] == []
            assert pm.get(f"evidence/{att['evidenceId']}?download=true").content == sheet
        assert {p.name for p in originals.iterdir()} == {original, external, fact_attachment, orphan, late[0]}
    finally:
        control.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        monkeypatch.setenv('PMO_EVIDENCE_DIR', str(originals))

    def databases():
        return control.execute("SELECT datname FROM pg_database WHERE datname LIKE 'pmo_restore_%' ORDER BY datname").fetchall()

    before = databases()
    archived = point / 'evidence' / original
    archived.write_bytes(png[:-1] + bytes([png[-1] ^ 1]))
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_OBJECT_INTEGRITY_FAILED'):
        recovery.restore_drill(admin, point, tmp_path / 'bad-object', tools)
    assert databases() == before and not list((tmp_path / 'bad-object').iterdir())
    archived.write_bytes(png)
    with (point / 'database.dump').open('ab') as out:
        out.write(b'corrupt')
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_MANIFEST_OR_DUMP_MISMATCH'):
        recovery.restore_drill(admin, point, tmp_path / 'bad-dump', tools)
    assert databases() == before

    # A self-consistent checksum does not make an invalid PostgreSQL archive restorable.
    archive = point / 'database.dump'
    archive.write_bytes(b'not a PostgreSQL archive')
    broken = manifest.model_copy(update={'dump': recovery.digest(archive)})
    (point / 'manifest.json').write_text(broken.model_dump_json(), encoding='utf-8')
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_PG_RESTORE_FAILED'):
        recovery.restore_drill(admin, point, tmp_path / 'failed-restore', tools)
    assert databases() == before and not list((tmp_path / 'failed-restore').iterdir())

    (originals / original).unlink()
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_OBJECT_MISSING_OR_UNSAFE'):
        recovery.backup(source, originals, output, tools)
    assert [entry.name for entry in output.iterdir()] == [point.name]


def test_restore_rejects_remote_and_live_evidence_destination(tmp_path, monkeypatch):
    remote = recovery.database_url('postgresql://operator@restore.example.invalid/pmo')
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_TARGET_MUST_BE_ISOLATED_LOOPBACK'):
        recovery.restore_drill(remote, tmp_path / 'missing', tmp_path / 'restore', recovery.PgTools())
    monkeypatch.setenv('PMO_EVIDENCE_DIR', str(tmp_path / 'restore'))
    loopback = remote.set(host='127.0.0.1')
    point = Path(tmp_path / 'archive' / 'point')
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_PATHS_OVERLAP'):
        recovery.restore_drill(loopback, point, tmp_path / 'restore' / 'nested', recovery.PgTools())
    assert not (tmp_path / 'restore').exists()
