from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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
from pmo.audit_store import audit
from pmo.health_calculation import TZ, week_key
from pmo.project_lifecycle import transition_grants
from pmo.requirement_disputes import t as disputes
from pmo.reviews import snapshots as review_snapshots, transitions
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
    archived_name = 'Historical archived milestone retained'
    owner, verifier, customer = uuid4(), uuid4(), uuid4()
    password = uuid4().hex
    preupgrade_session, preupgrade_csrf = uuid4().hex, uuid4().hex
    legacy_request_id = str(uuid4())
    ledger = {}
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
                conn.execute(db.user_roles.insert().values(user_id=account, role='delegator'))
                conn.execute(sessions.insert().values(account_id=account, token_hash=sha256(preupgrade_session.encode()).hexdigest(),
                    csrf_hash=sha256(preupgrade_csrf.encode()).hexdigest(), expires_at=datetime.now(UTC)+timedelta(hours=1)))
                conn.execute(db.audit_logs.insert().values(actor_id=account, actor_type='human', org_id=org, action='auth_login',
                             request_id=legacy_request_id, result='success'))
        project = conn.scalar(db.projects.insert().values(org_id=org, code='DRILL', name='Recovery fixture', stage='drafting',
                             revision=1, initiator_id=owner, created_by=owner, pm_id=owner).returning(db.projects.c.id))
        archived = conn.scalar(db.projects.insert().values(org_id=org, code='DRILL-ARCHIVED', name='Archived recovery fixture',
            stage='archived', revision=5, initiator_id=owner, created_by=owner, pm_id=owner).returning(db.projects.c.id))
        member(conn, archived, owner, owner, 'pm')
        archived_mid = conn.scalar(db.milestones.insert().values(project_id=archived, created_by=owner,
            name=archived_name, ordinal=1, planned_date=date(2020, 3, 7), is_key=True).returning(db.milestones.c.id))
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
        # Legacy budget and source rows exercise both units independently of model output.
        for kind, budget, actual, left in (('money', '100.00', '65.25', '5.10'), ('effort', '10.00', '3.75', '1.25')):
            e, p = db.cost_entries, db.cost_postings
            entry = conn.scalar(e.insert().values(project_id=project, created_by=owner, type=kind,
                category='recovery budget', is_current_baseline=True, baseline_version=1,
                period_start=date(2020, 2, 1), period_end=date(2020, 2, 29), budget=Decimal(budget), actual=0,
                forecast_remaining=Decimal(left)).returning(e.c.id))
            source_fields = dict(project_id=project, created_by=owner, cost_entry_id=entry, type=kind, source_type=kind,
                amount_signed=Decimal(actual), occurred_at=datetime(2020, 2, 15, tzinfo=UTC), period='2020-02',
                source_ref=f'Synthetic 0016 {kind} evidence', source_fingerprint=sha256(kind.encode()).hexdigest())
            verified = conn.scalar(p.insert().values(**source_fields, status='verified', verified_by=verifier,
                verified_at=sa.func.now(), verification_note='Verified 0016 synthetic source').returning(p.c.id))
            pending = conn.scalar(p.insert().values(**source_fields, status='pending').returning(p.c.id))
            ledger[kind] = (entry, verified, pending, budget, actual, left)

    def check_costs(response):
        item = data(response)
        refs = response.json()['meta']['sourceRefs']
        for kind, (entry, verified, pending, budget, actual, left) in ledger.items():
            assert (item[kind]['budget'], item[kind]['actual'], item[kind]['forecastRemaining']) == (budget, actual, left)
            assert f'cost-posting:{verified}' in refs and f'cost-posting:{pending}' not in refs
            assert any(ref.startswith(f'cost-entry:{entry}@') for ref in refs)

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
    history_sql = '''SELECT seq,entry_id,project_id,type,budget,forecast_remaining,is_current,effective_at
                     FROM cost_baseline_history WHERE project_id=%s ORDER BY seq'''
    with engine.connect() as c:
        old_cost_history = [tuple(row) for row in c.exec_driver_sql(history_sql, (project,))]
        assert len(old_cost_history) == 2
    cli = {**os.environ, 'DATABASE_URL': source.render_as_string(hide_password=False)}
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', '0029'], check=True, env=cli)
    with engine.begin() as c:
        # A separate legacy project emulates an old closing withdrawal, not a new approval.
        legacy_project = c.scalar(db.projects.insert().values(org_id=org, code='OLD-WITHDRAW', name='Legacy withdrawal',
            stage='delivering', revision=3, initiator_id=owner, created_by=owner).returning(db.projects.c.id))
        review = c.scalar(db.reviews.insert().values(project_id=legacy_project, created_by=owner, type='closing', status='decided',
            round=1, revision=2, attendees=[], snapshot_hash='b'*64, verdict='withdrawn', minutes='Synthetic old withdrawal')
            .returning(db.reviews.c.id))
        c.execute(review_snapshots.insert().values(review_id=review, rule_version='legacy-synthetic', snapshot_hash='b'*64,
            materials={'reviewers': [{'person_id': str(verifier)}]}, source_refs=[]))
        historical = dict(project_id=legacy_project, review_id=review, actor_type='human', actor_id=owner)
        c.execute(transitions.insert().values(**historical, from_revision=1, to_revision=2,
            from_stage='delivering', to_stage='closing_review', request_id=uuid4()))
        legacy_withdrawal = uuid4()
        c.execute(transitions.insert().values(**historical, from_revision=2, to_revision=3,
            from_stage='closing_review', to_stage='delivering', request_id=legacy_withdrawal))
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True, env=cli)
    with engine.connect() as c:
        assert [tuple(row) for row in c.exec_driver_sql(history_sql, (project,))] == old_cost_history
        assert c.scalar(sa.select(db.milestones.c.name).where(db.milestones.c.id == archived_mid)) == archived_name
        with pytest.raises(sa.exc.IntegrityError, match='Archived project records are read-only'), c.begin_nested():
            c.execute(db.milestones.update().where(db.milestones.c.id == archived_mid).values(name='Unapproved alteration'))
    with pytest.raises(sa.exc.IntegrityError, match='ck_project_stage_closing_rework'), engine.begin() as c:
        # A fresh review ID avoids the existing one-exit-per-review index masking the new check.
        fresh = c.scalar(db.reviews.insert().values(project_id=legacy_project, created_by=owner, type='closing', status='pending',
            round=2, attendees=[], snapshot_hash='c'*64).returning(db.reviews.c.id))
        c.execute(review_snapshots.insert().values(review_id=fresh, rule_version='legacy-synthetic', snapshot_hash='c'*64,
            materials={'reviewers': [{'person_id': str(verifier)}]}, source_refs=[]))
        c.execute(transitions.insert().values(**{**historical, 'review_id': fresh}, from_revision=3, to_revision=4,
            from_stage='closing_review', to_stage='delivering', request_id=uuid4()))
    path = f'projects/{project}'
    sheet = worksheet()
    with engine.begin() as c:
        assert c.execute(sa.select(audit.c.action, audit.c.source_refs).where(
            audit.c.request_id == legacy_request_id)).one() == ('auth_login', None)
        c.execute(db.projects.update().where(db.projects.c.id == project).values(stage='executing'))
        original_id = c.scalar(sa.select(db.evidence_files.c.id).where(db.evidence_files.c.storage_key == original))
        # Storage drill fixture only; authenticated dispute writes are exercised in the random-schema HTTP suite.
        dispute = c.scalar(disputes.insert().values(project_id=project, requirement_id=requirement, baseline_version=1,
            requirement_revision=1, customer_id=customer, assignment_id=customer_assignment, stakeholder_id=contact,
            reason='Original synthetic objection', open_request_id=uuid4()).returning(disputes.c.id))
        c.execute(disputes.update().where(disputes.c.id == dispute).values(revision=2, withdrawn_at=sa.func.clock_timestamp(),
            withdrawal_reason='Explicit synthetic withdrawal', withdrawal_request_id=uuid4()))
    guarded = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', '0026'], capture_output=True, text=True, env=cli)
    assert guarded.returncode != 0 and 'Refusing to discard requirement dispute history' in guarded.stderr
    with serve(source, tmp_path, evidence_dir=originals, extra_env={'PMO_REPORT_ANALYSIS':'1'}) as pm:
        pm.cookies.set('pmo_session', preupgrade_session, domain='127.0.0.1', path='/api/v1')
        pm.headers['X-CSRF-Token'] = preupgrade_csrf
        identity = pm.get('auth/me')
        assert identity.status_code==200 and identity.json()['personId']==str(owner)
        check_costs(pm.get(path + '/costs'))
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
        daily = data(pm.post(path + '/reports', headers=headers(), json=body(
            reportType='daily', period=datetime.now(TZ).date().isoformat(),
            structuredFields=sections(milestone_progress=[{
                'milestoneId':str(milestone), 'claim':'progress', 'evidenceRefs':[proof['evidenceRef']]}]))))
        daily_ref = f"report:{daily['reportId']}@{daily['revision']}"
        snapshot = data(pm.post(path + '/health/refresh', headers=headers(1)))
        weekly = snapshot['inputs']['weekly']
        assert weekly['receipts'][0]['hash'] == published['contentHash']
        assert weekly['claims'][0]['evidence'][0]['sha256'] == sha256(png).hexdigest()
        daily_receipt, = [row for row in weekly['receipts'] if row['source'] == daily_ref]
        daily_claim, = [row for row in weekly['claims'] if row['report'] == daily_ref]
        assert daily_receipt['hash'] == daily['contentHash']
        assert daily_claim['evidence'][0]['sha256'] == sha256(png).hexdigest()
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
        from test_reviews import issue
        token = issue(pm, str(project), ['pmo_project_get'])
        with httpx.Client(base_url=pm.base_url, trust_env=False) as agent:
            read = agent.get(path, headers={'Authorization': 'Bearer ' + token}).raise_for_status()
        read_request = read.headers['X-Request-ID']
        read_audit, = data(pm.get('admin/audits', params={'requestId': read_request}))['items']
        assert read_audit['actorType'] == 'agent' and read_audit['sourceRefs'] == read.json()['meta']['sourceRefs']
        issued = invite(pm, 'recovery-recruit@example.test')
        invite_receipt = data(issued)
        intake = command(issued.headers['X-PMO-Org-Invitation'])
        with httpx.Client(base_url=pm.base_url, trust_env=False) as recruit:
            enrollment = data(attend(recruit, intake))
            assert recruit.post('auth/login', json={'username':intake['username'],'password':intake['password']}).is_success
            assert not data(change(pm, enrollment['userId'], False))['isActive']
    with engine.connect() as c:
        fact_attachment = c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id==att['evidenceId']))
    failed = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', '0028'], capture_output=True, text=True, env=cli)
    assert failed.returncode != 0 and 'Refusing to discard written audit reference history' in failed.stderr
    with engine.begin() as c:
        grants = transition_grants
        assert c.scalar(sa.select(sa.func.count()).select_from(grants)) == 0
        now = datetime.now(UTC)
        values = dict(org_id=org, project_id=project, person_id=verifier, valid_from=now,
                      expires_at=now+timedelta(hours=2), recorded_at=now)
        c.execute(grants.insert().values(**values, from_stage='executing', to_stage='on_hold', source_ref='DRILL-AUTH-1'))
        revoked = c.scalar(grants.insert().values(**values, from_stage='executing', to_stage='terminated',
            source_ref='DRILL-AUTH-2').returning(grants.c.id))
        c.execute(grants.update().where(grants.c.id==revoked).values(revoked_at=sa.func.clock_timestamp(), revocation_ref='DRILL-REVOKE-1'))
        authority_sql = '''SELECT id, org_id, project_id, person_id, from_stage, to_stage, valid_from, expires_at,
            recorded_at, source_ref, revoked_at, revocation_ref FROM project_transition_grants ORDER BY source_ref'''
        saved_authority = c.exec_driver_sql(authority_sql).all()
    guarded = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', '0031'], capture_output=True, text=True, env=cli)
    assert guarded.returncode != 0 and 'Refusing destructive downgrade of transition authority history' in guarded.stderr
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
            assert restored.execute('SELECT name FROM milestones WHERE id=%s', (archived_mid,)).fetchone() == (archived_name,)
            with pytest.raises(psycopg.errors.CheckViolation) as refused, restored.transaction():
                restored.execute('UPDATE milestones SET name=%s WHERE id=%s', ('Unapproved alteration', archived_mid))
            assert refused.value.diag.constraint_name == 'ck_archived_project_read_only'
            assert restored.execute(history_sql, (project,)).fetchall() == old_cost_history
            assert restored.execute(authority_sql).fetchall() == saved_authority
            assert restored.execute('''SELECT project_id, review_id, from_stage, to_stage, from_revision, to_revision
                FROM project_stage_transitions WHERE request_id=%s''', (legacy_withdrawal,)).fetchone() == (
                legacy_project, review, 'closing_review', 'delivering', 2, 3)
            assert restored.execute("SELECT convalidated FROM pg_constraint WHERE conname='ck_project_stage_closing_rework'").fetchone() == (False,)
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
            check_costs(pm.get(path + '/costs'))
            readback = data(pm.get(path + '/health', params={'asOf':snapshot['asOf']}))
            assert readback['historical'] and readback['snapshotId'] == snapshot['snapshotId']
            assert (readback['inputHash'], readback['inputs']['weekly']) == (snapshot['inputHash'], weekly)
            assert daily_ref in readback['sourceRefs']
            assert data(pm.get(report_path(daily)))['contentHash'] == daily['contentHash']
            assert data(pm.get(report_path(report)+'/facts'))['items'] == [fact]
            assert history(pm, published)['items']==[analysis]
            assert data(pm.get(dispute_path))['items'] == [saved_dispute]
            assert data(pm.get(report_path(weekly_published)))==weekly_published
            assert data(pm.get('admin/audits', params={'requestId': read_request}))['items'] == [read_audit]
            legacy_log, = data(pm.get('admin/audits', params={'requestId': legacy_request_id}))['items']
            assert legacy_log['action'] == 'auth_login' and 'sourceRefs' not in legacy_log
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
