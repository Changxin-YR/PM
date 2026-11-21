"""Assert the persisted ledger invariants on the migrated PostgreSQL database."""
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.costs import plans
from test_reviews import team as team


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
