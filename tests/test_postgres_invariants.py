"""Exercise the actual constraints of a migrated PostgreSQL database."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from csv import DictReader
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.costs import plans
from pmo.reports import reports
from pmo.review_outcomes import outcomes
from test_reports import body as report_body
from test_reviews import bench, data, decide, headers, score, start_charter, team as team


MONTH = date(2020, 2, 1)
OCCURRED = datetime(2020, 2, 5, 4, 30, tzinfo=UTC)


def entry(project, creator):
    return dict(project_id=project, created_by=creator, type='money', category='posting_ledger',
                is_current_baseline=False, baseline_version=1, period_start=MONTH, period_end=date(2020, 2, 29),
                budget=Decimal('120.00'), actual=Decimal(0), forecast_remaining=Decimal('20.00'))


def posting(project, creator, verifier, cost_entry):
    return dict(project_id=project, created_by=creator, cost_entry_id=cost_entry, type='money', source_type='money',
                occurred_at=OCCURRED, period='2020-02', source_ref='Synthetic invoice INV-1', source_fingerprint='a' * 64,
                amount_signed=Decimal('42.30'), status='verified', verified_by=verifier,
                verified_at=sa.func.now(), verification_note='Independently verified synthetic invoice')


def constraint(conn, stmt, name, sqlstate):
    with pytest.raises(sa.exc.IntegrityError) as failed, conn.begin_nested():
        conn.execute(stmt)
    assert (failed.value.orig.diag.constraint_name, failed.value.orig.sqlstate) == (name, sqlstate)


def test_frozen_column_types_and_nullability_match_actual_storage(live):
    matrix = next(Path('docs/spec').glob('14_*.csv'))
    with matrix.open(encoding='utf-8', newline='') as file:
        expected = list(DictReader(file))
    assert len({(r['table'], r['field']) for r in expected}) == len(expected) == 421
    with live[0].connect() as conn:
        conn.exec_driver_sql('SET TRANSACTION READ ONLY')
        columns = {(row.table_name, row.field): (row.physical_type, row.nullable)
            for row in conn.execute(sa.text('''SELECT tbl.relname AS table_name, a.attname AS field,
                format_type(a.atttypid, a.atttypmod) AS physical_type, NOT a.attnotnull AS nullable
                FROM pg_attribute a JOIN pg_class tbl ON tbl.oid=a.attrelid
                WHERE tbl.relnamespace=current_schema()::regnamespace AND tbl.relkind='r'
                AND a.attnum>0 AND NOT a.attisdropped'''))}
    differences = set()
    for row in expected:
        key = (row['table'], row['field'])
        kind, nullable = columns[key]
        storage = kind.replace('character varying(', 'varchar(').replace('character(', 'char(')
        storage = storage.replace('timestamp with time zone', 'timestamptz')
        assert storage == row['type_and_constraint'].split()[0].rstrip('!?'), key
        if nullable != (row['nullable'] == 'YES'):
            differences.add((*key, nullable))
    # ADR 0011: a formal non-gate Decision intentionally does not fabricate a Review.
    assert differences == {('decisions', 'review_id', True)}


def test_project_members_and_milestone_positions_are_constrained(live, team):
    project, people, _, _ = team
    p, g, m = db.projects, db.project_members, db.milestones
    with live[0].begin() as c:
        current = dict(c.execute(p.select().where(p.c.id == project)).mappings().one())
        current.pop('id')
        constraint(c, p.insert().values(**current), 'projects_org_id_code_key', '23505')
        constraint(c, p.update().where(p.c.id == project).values(revision=0), 'projects_revision_check', '23514')
        membership = dict(c.execute(g.select().where(g.c.project_id == project, g.c.role == 'pm')).mappings().one())
        membership.pop('id')
        constraint(c, g.insert().values(**membership), 'project_members_project_id_person_id_role_key', '23505')
        base = dict(name='Synthetic position', project_id=project, created_by=people['pm'], is_key=True, planned_date=MONTH)
        for slot in (0, 9):
            constraint(c, m.insert().values(**base, ordinal=slot), 'milestones_ordinal_check', '23514')
        constraint(c, m.insert().values(**{**base, 'project_id': uuid4()}, ordinal=1), 'milestones_project_id_fkey', '23503')
        for slot in range(1, 9):
            c.execute(m.insert().values(**base, ordinal=slot))
        constraint(c, m.insert().values(**base, ordinal=1), 'uq_milestones_ordinal', '23505')
        cancelled = c.scalar(m.insert().values(**base, ordinal=1, is_cancelled=True).returning(m.c.id))
        constraint(c, m.update().where(m.c.id == cancelled).values(is_cancelled=False), 'uq_milestones_ordinal', '23505')
        assert c.scalar(sa.select(sa.func.count()).select_from(m).where(m.c.project_id == project, ~m.c.is_cancelled)) == 8


def test_review_versions_decision_and_audit_are_append_only(live, team):
    project, _, users, _ = team
    applicant, reviewer = users['initiator'], users['reviewer']
    path = start_charter(project, users)
    first = score(reviewer, path, [3, 4, 4, 4])
    data(applicant.post(path + '/appeals', headers=headers(first['review']['revision']),
                       json={'reason': 'Recheck the signed evidence in an independent round.'}))
    score(reviewer, path, [4] * 4)
    response = decide(reviewer, path)
    decision = data(response)['decision']
    review_id, decision_id = UUID(first['review']['reviewId']), UUID(decision['decisionId'])
    s, d, a = db.scorecards, db.decisions, db.audit_logs
    with live[0].begin() as c:
        cards = s.select().where(s.c.review_id == review_id).order_by(s.c.round)
        saved = c.execute(cards).mappings().all()
        assert [row.round for row in saved] == [1, 2]
        duplicate = dict(saved[-1]); duplicate.pop('id')
        constraint(c, s.insert().values(**duplicate), 'scorecards_review_id_reviewer_id_round_key', '23505')
        constraint(c, s.insert().values(**{**duplicate, 'review_id': uuid4()}), 'scorecards_review_id_fkey', '23503')
        constraint(c, s.insert().values(**{**duplicate, 'round': 0}), 'scorecards_round_check', '23514')
        for edit in (s.update().where(s.c.id == saved[0].id).values(dimensions={}),
                     s.delete().where(s.c.id == saved[0].id),
                     d.update().where(d.c.id == decision_id).values(rationale='Different decision'),
                     outcomes.delete().where(outcomes.c.decision_id == decision_id),
                     a.update().where(a.c.request_id == response.headers['X-Request-ID']).values(result='denied')):
            with pytest.raises(sa.exc.DBAPIError, match='append-only audit'), c.begin_nested():
                c.execute(edit)
        assert c.execute(cards).mappings().all() == saved
    history = bench(reviewer, path)
    assert history['decision'] == decision and len(history['scores']) == 2


def test_archived_project_core_records_are_read_only_in_storage(live, team):
    pid, people, users, _ = team
    reviewer = users['reviewer']
    review = start_charter(pid, users)
    score(reviewer, review, [3, 4, 4, 4])
    decision = data(decide(reviewer, review))['decision']
    p, m, s, d, e, t = db.projects, db.milestones, db.scorecards, db.decisions, db.cost_entries, db.cost_postings
    who = people['pm']
    with live[0].begin() as c:
        project = c.execute(p.select().where(p.c.id == pid)).mappings().one()
        other = c.scalar(p.insert().values(org_id=project.org_id, created_by=who, initiator_id=who,
            name='Unrelated active control', code='ARCH-'+uuid4().hex[:10], stage='designing', revision=1).returning(p.c.id))
        milestone = dict(project_id=pid, created_by=who, name='Synthetic archive milestone', ordinal=1,
            planned_date=MONTH, is_key=True)
        mid = c.scalar(m.insert().values(**milestone).returning(m.c.id))
        eid = c.scalar(e.insert().values(**entry(pid, who)).returning(e.c.id))
        invoice = posting(pid, who, people['reviewer'], eid)
        receipt = c.scalar(t.insert().values(**invoice).returning(t.c.id))
        card = dict(c.execute(s.select().where(s.c.project_id == pid)).mappings().one())
        card.pop('id')
        card['round'] += 1
        action = dict(c.execute(d.select().where(d.c.id == decision['decisionId'])).mappings().one())
        action.pop('id')
        action.update(review_id=None, subject='Synthetic additional decision', option='approve')
        # Only seed the final stage here. The product lifecycle tests perform and audit the transition.
        c.execute(p.update().where(p.c.id == pid).values(stage='archived'))
        for stmt in (
            m.update().where(m.c.id == mid).values(name='After archive'),
            m.update().where(m.c.id == mid).values(project_id=other),
            m.insert().values(**{**milestone, 'ordinal': 2}), m.delete().where(m.c.id == mid),
            s.insert().values(**card), d.insert().values(**action),
            t.insert().values(**{**invoice, 'source_fingerprint': 'e'*64}),
            e.update().where(e.c.id == eid).values(budget=Decimal('145.00')),
            p.update().where(p.c.id == pid).values(stage='executing'),
        ):
            constraint(c, stmt, 'ck_archived_project_read_only', '23514')
        constraint(c, s.insert().values(**{**card, 'project_id': other}), 'ck_review_record_project_scope', '23514')
        assert c.scalar(sa.select(m.c.name).where(m.c.id == mid)) == milestone['name']
        assert c.scalar(sa.select(t.c.amount_signed).where(t.c.id == receipt)) == invoice['amount_signed']
        assert c.scalar(sa.select(e.c.budget).where(e.c.id == eid)) == Decimal('120.00')
        c.execute(m.insert().values(**{**milestone, 'project_id': other}))
        assert c.scalar(sa.select(sa.func.count()).select_from(s).where(s.c.project_id == pid)) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(d).where(d.c.project_id == pid)) == 1


def test_archive_serializes_storage_writes_against_the_project_lock(live, team):
    pid, people, _, _ = team
    eng = live[0]
    backend_ids = Queue()

    def try_milestone():
        with eng.begin() as conn:
            conn.exec_driver_sql("SET LOCAL statement_timeout='10s'")
            backend_ids.put(conn.scalar(sa.text('SELECT pg_backend_pid()')))
            constraint(conn, db.milestones.insert().values(project_id=pid, created_by=people['pm'],
                name='Concurrent storage write', ordinal=1, planned_date=MONTH, is_key=True), 'ck_archived_project_read_only', '23514')

    with ThreadPoolExecutor(max_workers=1) as pool, eng.connect() as probe:
        with eng.begin() as archive:
            blocker = archive.scalar(sa.text('SELECT pg_backend_pid()'))
            archive.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='archived'))
            write = pool.submit(try_milestone)
            waiter = backend_ids.get(timeout=5)
            deadline = monotonic() + 5
            while not probe.scalar(sa.text('SELECT :blocker = ANY(pg_blocking_pids(:waiter))'),
                                   {'blocker': blocker, 'waiter': waiter}):
                assert not write.done() and monotonic() < deadline, 'Write did not serialize against archive'
                sleep(.025)
        write.result(timeout=10)
        assert probe.scalar(sa.select(sa.func.count()).select_from(db.milestones).where(db.milestones.c.project_id == pid)) == 0


def test_current_confirmed_report_uniqueness_preserves_prior_receipt(live, team):
    project, people, users, _ = team
    pm = users['pm']
    listing = f'projects/{project}/reports'
    version1 = data(pm.post(listing, headers=headers(), json=report_body(narrative='Original synthetic PM report.')))
    old_path = f"reports/{version1['reportId']}"
    first = data(pm.post(old_path + '/confirm', headers=headers(version1['revision']), json={}))
    version2 = data(pm.post(listing, headers=headers(), json=report_body(narrative='More recent verified context.')))
    new_id = UUID(version2['reportId'])
    with live[0].begin() as c:
        r = reports.c
        invalid = reports.update().where(r.id == new_id).values(
            status='confirmed', confirmed_by=people['pm'], confirmed_at=sa.func.now(), is_current_confirmed=True)
        constraint(c, invalid, 'uq_reports_period', '23505')
        with pytest.raises(sa.exc.IntegrityError, match='Submitted report history is immutable'), c.begin_nested():
            c.execute(reports.update().where(r.id == UUID(version1['reportId'])).values(narrative='Rewritten history'))
    replacement = data(pm.post(f'reports/{new_id}/confirm', headers=headers(version2['revision']), json={}))
    previous = data(pm.get(old_path))
    assert replacement['isCurrentConfirmed'] and not previous['isCurrentConfirmed']
    assert previous['status'] == 'confirmed' and previous['narrative'] == first['narrative']
    assert previous['contentHash'] == first['contentHash'] and previous['confirmedAt'] == first['confirmedAt']


def test_verified_posting_storage_checks_linkage_and_partial_uniqueness(live, team):
    project, people, _, _ = team
    pm, verifier = people['pm'], people['reviewer']
    e, t, p = db.cost_entries, db.cost_postings, db.projects
    with live[0].begin() as c:
        entry_id = c.scalar(e.insert().values(**entry(project, pm)).returning(e.c.id))
        values = posting(project, pm, verifier, entry_id)
        org = c.scalar(sa.select(p.c.org_id).where(p.c.id == project))
        foreign = c.scalar(p.insert().values(org_id=org, code='LEDGER-OTHER', name='Other synthetic project',
            stage='drafting', revision=1, initiator_id=pm, created_by=pm).returning(p.c.id))
        for bad in ({'project_id': foreign}, {'type': 'effort', 'source_type': 'effort'}, {'source_type': 'effort'},
                    {'period': '2020-03'}, {'occurred_at': datetime(2020, 3, 1, tzinfo=UTC), 'period': '2020-03'}):
            with pytest.raises(sa.exc.DBAPIError, match='Inconsistent cost period or entry'), c.begin_nested():
                c.execute(t.insert().values(**{**values, **bad}))
        with pytest.raises(sa.exc.DBAPIError, match='Independent verification required'), c.begin_nested():
            c.execute(t.insert().values(**{**values, 'verified_by': pm}))

        original = c.scalar(t.insert().values(**values).returning(t.c.id))
        for edit in (t.update().where(t.c.id == original).values(amount_signed=Decimal('41.20')),
                     t.delete().where(t.c.id == original)):
            with pytest.raises(sa.exc.DBAPIError, match='Immutable verified posting'), c.begin_nested():
                c.execute(edit)
        candidate = {**values, 'status': 'pending', 'verified_at': None, 'verified_by': None, 'verification_note': None}
        duplicate = c.scalar(t.insert().values(**candidate).returning(t.c.id))
        verified = dict(status='verified', verified_by=verifier, verified_at=sa.func.now(), verification_note='Checked original')
        constraint(c, t.update().where(t.c.id == duplicate).values(**verified), 'uq_cost_postings_source_fingerprint', '23505')
        reversal = {**candidate, 'posting_of_id': original, 'amount_signed': -values['amount_signed'],
                    'reversal_reason': 'Corrected invoice from source', 'source_fingerprint': 'b' * 64}
        with pytest.raises(sa.exc.DBAPIError, match='Invalid full reversal'), c.begin_nested():
            c.execute(t.insert().values(**{**reversal, 'amount_signed': Decimal('-10.00')}))
        first = c.scalar(t.insert().values(**reversal).returning(t.c.id))
        second = c.scalar(t.insert().values(**{**reversal, 'source_fingerprint': 'c' * 64}).returning(t.c.id))
        c.execute(t.update().where(t.c.id == first).values(**verified))
        constraint(c, t.update().where(t.c.id == second).values(**verified), 'uq_cost_postings_posting_of_id', '23505')
        assert c.scalar(sa.select(sa.func.sum(t.c.amount_signed)).where(t.c.project_id == project, t.c.status == 'verified')) == Decimal('0.00')


def test_postgres_rejects_nonfinite_cost_inputs(live, team):
    project, people, _, _ = team
    pm, verifier = people['pm'], people['reviewer']
    e, t = db.cost_entries, db.cost_postings
    nan = Decimal('NaN')
    with live[0].begin() as c:
        values = entry(project, pm)
        for field in ('budget', 'actual', 'forecast_remaining'):
            constraint(c, e.insert().values(**{**values, field: nan}), 'ck_cost_entries_finite', '23514')
        entry_id = c.scalar(e.insert().values(**values).returning(e.c.id))
        constraint(c, t.insert().values(**{**posting(project, pm, verifier, entry_id), 'amount_signed': nan}),
                   'ck_cost_postings_finite', '23514')
        for field in ('money_budget', 'effort_budget', 'money_remaining', 'effort_remaining'):
            constraint(c, plans.insert().values(project_id=project, revision=2, changed_field=field,
                reason='Verify stored input', created_by=pm, **{field: nan}), 'ck_cost_plan_versions_finite', '23514')
        history = sa.text('''INSERT INTO cost_baseline_history(entry_id, project_id, type, budget, forecast_remaining, is_current)
            VALUES (:entry_id, :project_id, 'money', :budget, :remaining, true)''')
        for budget, remaining in ((nan, Decimal(0)), (Decimal(0), nan)):
            constraint(c, history.bindparams(entry_id=entry_id, project_id=UUID(project), budget=budget, remaining=remaining),
                       'ck_cost_baseline_history_finite', '23514')
        saved = c.scalar(e.select().with_only_columns(e.c.budget).where(e.c.id == entry_id))
        assert saved == Decimal('120.00')


def test_finite_cost_upgrade_rolls_back_on_invalid_history():
    admin = sa.create_engine(os.environ['PMO_TEST_DATABASE_URL'], hide_parameters=True)
    assert admin.url.host in ('127.0.0.1', '::1'), 'Use a local test database'
    schema = 'pmo_cost_upgrade_' + uuid4().hex
    url = admin.url.update_query_dict({'options': f'-csearch_path={schema}'})
    engine = sa.create_engine(url, hide_parameters=True)
    env = {**os.environ, 'DATABASE_URL': url.render_as_string(hide_password=False)}
    with admin.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA {schema}')
    try:
        cli = [sys.executable, '-m', 'alembic', 'upgrade']
        subprocess.run([*cli, '0030'], check=True, env=env)
        with engine.begin() as c:
            org = c.scalar(db.organizations.insert().values(name='Synthetic invalid cost history', status='active')
                           .returning(db.organizations.c.id))
            creator = uuid4()
            c.execute(db.persons.insert().values(id=creator, created_by=creator, org_id=org, name='Fixture', is_active=True))
            p, e = db.projects, db.cost_entries
            project = c.scalar(p.insert().values(org_id=org, code='OLD-COST', name='Legacy synthetic project',
                               stage='drafting', revision=1, initiator_id=creator, created_by=creator).returning(p.c.id))
            entry_id = c.scalar(e.insert().values(**entry(project, creator)).returning(e.c.id))
            seq = c.scalar(sa.text('''INSERT INTO cost_baseline_history(entry_id, project_id, type, budget, forecast_remaining, is_current)
                VALUES (:entry, :project, 'money', 'NaN'::numeric, 0, true) RETURNING seq'''),
                {'entry': entry_id, 'project': project})

        failed = subprocess.run([*cli, '0031'], env=env, capture_output=True, text=True, timeout=30)
        assert failed.returncode != 0 and 'ck_cost_baseline_history_finite' in failed.stderr
        with engine.begin() as c:
            assert c.scalar(sa.text('SELECT version_num FROM alembic_version')) == '0030'
            assert c.scalar(sa.text('''SELECT count(*) FROM pg_constraint
                WHERE connamespace = current_schema()::regnamespace AND conname IN (
                'ck_cost_entries_finite', 'ck_cost_postings_finite', 'ck_cost_plan_versions_finite',
                'ck_cost_baseline_history_finite')''')) == 0
            old = c.scalar(sa.text('SELECT budget FROM cost_baseline_history WHERE seq=:seq'), {'seq': seq})
            assert old.is_nan()
            assert c.scalar(sa.select(e.c.budget).where(e.c.id == entry_id)) == Decimal('120.00')
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        admin.dispose()
