"""Database guards reject mismatches independently of the normal application checks."""
from contextlib import ExitStack
import os
from secrets import token_urlsafe
import subprocess
import sys
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from conftest import isolated_live_stack
import httpx
import psycopg
import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.bootstrap import bootstrap
from pmo.database_roles import apply_roles
from pmo.identity import explicit_grants
from pmo.recovery import connection_args
from pmo.risk_resolution import acceptances, resolutions
from test_database_roles import serve, split_roles as split_roles
from test_evidence import binary
from test_risk_resolution import SCOPE, headers, ok, rejected, scenario as scenario


@pytest.fixture(scope='module')
def live_stack(tmp_path_factory):
    # This module exercises a legacy downgrade. Newer tests may have written
    # onboarding history that 0038 intentionally prevents from being dropped.
    yield from isolated_live_stack(tmp_path_factory)


def rejected_insert(connection, statement, constraint):
    with pytest.raises(sa.exc.IntegrityError) as invalid, connection.begin_nested():
        connection.execute(statement)
    assert invalid.value.orig.diag.constraint_name == constraint


def test_agent_grant_cannot_cross_credential_organization(scenario):
    s = scenario
    with s.engine.begin() as c:
        agent = db.agent_credentials.c
        account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == s.people['reviewer']))
        credential, = c.scalars(sa.select(agent.id).where(agent.delegated_by == account)).all()
        foreign = s.people['foreign']
        foreign_org = c.scalar(sa.select(db.persons.c.org_id).where(db.persons.c.id == foreign))
        project = c.scalar(db.projects.insert().values(code=uuid4().hex, name='Other organization',
            created_by=foreign, initiator_id=foreign, org_id=foreign_org, revision=1, stage='drafting').returning(db.projects.c.id))
        grant = dict(credential_id=credential, granted_by=account)
        rejected_insert(c, explicit_grants.insert().values(**grant, project_id=project), 'fk_agent_grants_project_org')
        rejected_insert(c, sa.text('''INSERT INTO agent_project_grants (credential_id,granted_by,project_id,org_id)
            VALUES (:credential_id,:granted_by,:project_id,:org_id)''').bindparams(
                **grant, project_id=project, org_id=foreign_org), 'fk_agent_grants_credential_org')
        reference = c.execute(sa.text('SELECT project_id,org_id FROM agent_project_grants WHERE credential_id=:credential'),
                              {'credential': credential}).all()
        assert reference == [(s.project, s.org)]


def test_risk_receipt_bindings_and_upgrade_preserve_history(live, scenario):
    s = scenario
    risk = s.risk()
    other_risk = s.risk(s.other)
    proof = s.upload(risk)
    wrong_proof = s.upload(other_risk)
    reviewer, receiver = s.clients['reviewer'], s.clients['receiver']
    path = f'risks/{risk.id}/handoff'
    binding = {'receiverPersonId': str(s.people['receiver']), 'acceptanceEvidenceRef': proof['evidenceRef']}
    request = {'kind': 'request', **binding, 'acceptanceScope': SCOPE}
    rejected(reviewer.post(path + '/acceptance', headers=headers(), json={**request,
        'acceptanceEvidenceRef': wrong_proof['evidenceRef']}), 404, 'RESOURCE_NOT_FOUND')
    asked = ok(reviewer.post(path + '/acceptance', headers=headers(), json=request))
    asked_id = UUID(asked['acceptanceId'])
    with s.engine.begin() as c:
        row = c.execute(sa.select(acceptances).where(acceptances.c.id == asked_id)).mappings().one()
        new = dict(row, id=uuid4(), request_id=uuid4(), receiver_id=s.people['outsider'])
        rejected_insert(c, acceptances.insert().values(**dict(new, project_id=s.other)),
                        'fk_risk_acceptances_risk_project')
        rejected_insert(c, acceptances.insert().values(**dict(new, risk_id=other_risk.id)), 'ck_risk_receipt_source')
        rejected_insert(c, acceptances.insert().values(**dict(new, risk_revision=2)), 'ck_risk_receipt_version')
        assignment = c.scalar(sa.select(db.project_members.c.id).where(db.project_members.c.project_id == s.other,
                              db.project_members.c.person_id == s.people['reviewer']))
        rejected_insert(c, acceptances.insert().values(**dict(new, reviewer_assignment_id=assignment)),
                        'fk_risk_acceptances_reviewer')
        confirmation = dict(new, kind='receiver_confirmed', actor_id=s.people['receiver'],
                            request_receipt_id=asked_id, reviewer_assignment_id=None,
                            receiver_id=s.people['receiver'], risk_revision=2)
        with c.begin_nested() as advance:
            c.execute(db.risks.update().where(db.risks.c.id == risk.id).values(revision=2))
            rejected_insert(c, acceptances.insert().values(**confirmation), 'fk_risk_acceptances_request')
            advance.rollback()
    accepted = ok(receiver.post(path + '/acceptance', headers=headers(), json={**request,
        'kind': 'confirm', 'decision': 'accept'}))
    receipt = ok(reviewer.post(path, headers=headers(), json=binding))
    accepted_id, resolution_id = UUID(accepted['acceptanceId']), UUID(receipt['resolutionId'])
    with s.engine.begin() as c:
        stored = c.execute(sa.select(resolutions).where(resolutions.c.id == resolution_id)).mappings().one()
        assert stored.acceptance_id == accepted_id and stored.revision == stored.prior_revision + 1
        new = dict(stored, id=uuid4(), request_id=uuid4(), prior_revision=2, revision=3)
        rejected_insert(c, resolutions.insert().values(**dict(new, acceptance_id=asked_id)), 'ck_risk_receipt_source')
        rejected_insert(c, resolutions.insert().values(**new), 'ck_risk_receipt_version')
        with c.begin_nested() as advance:
            c.execute(db.risks.update().where(db.risks.c.id == risk.id).values(revision=3))
            rejected_insert(c, resolutions.insert().values(**new), 'fk_risk_resolutions_acceptance')
            advance.rollback()

    # The old accepted revision remains historically valid after the risk advances.
    for action, target in (('downgrade', '0036'), ('upgrade', '0037')):
        subprocess.run([sys.executable, '-m', 'alembic', action, target], env=live[3], check=True, timeout=45)
    with s.engine.connect() as c:
        after = c.execute(sa.select(resolutions).where(resolutions.c.id == resolution_id)).mappings().one()
        assert dict(after) == dict(stored)
        rows = c.execute(sa.select(acceptances.c.id, acceptances.c.risk_revision).where(
            acceptances.c.id.in_([asked_id, accepted_id]))).all()
        assert dict(rows) == {asked_id: 1, accepted_id: 1}
        assert c.scalar(sa.select(db.risks.c.revision).where(db.risks.c.id == risk.id)) == 2
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], env=live[3], check=True, timeout=45)


def test_risk_handoff_runs_as_restricted_api_without_routine_grants(split_roles, tmp_path):
    urls = split_roles
    env = {**os.environ, 'DATABASE_URL': urls['owner'].render_as_string(hide_password=False)}
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], env=env, check=True, timeout=45)
    password = token_urlsafe(24)
    password_hash = PasswordHasher().hash(password)
    with ExitStack() as cleanup:
        engine = sa.create_engine(urls['owner'], hide_parameters=True)
        cleanup.callback(engine.dispose)
        seed = cleanup.enter_context(engine.begin())
        bootstrap(seed, 'Restricted synthetic risk', 'risk-owner', 'Owner', password)
        owner, org = seed.execute(sa.select(db.persons.c.id, db.persons.c.org_id)).one()
        people = {}
        for role in ('reviewer', 'receiver'):
            account = seed.scalar(db.user_accounts.insert().values(username=f'risk-{role}', password_hash=password_hash,
                is_active=True).returning(db.user_accounts.c.id))
            people[role] = seed.scalar(db.persons.insert().values(user_id=account, org_id=org, created_by=owner,
                name=role, is_active=True).returning(db.persons.c.id))

    with psycopg.connect(**connection_args(urls['owner']), autocommit=True) as migration:
        apply_roles(migration, urls['api'].username, urls['worker'].username)
        with psycopg.connect(**connection_args(urls['api']), autocommit=True) as runtime:
            assert runtime.execute("SELECT has_function_privilege('pmo_bind_risk_receipt()', 'EXECUTE')").fetchone() == (False,)
        with serve(urls['api'], tmp_path) as api, ExitStack() as sessions:
            clients = {'owner': api}
            for role in people:
                clients[role] = sessions.enter_context(httpx.Client(base_url=api.base_url, trust_env=False))
            for role, client in clients.items():
                login = client.post('auth/login', json={'username': f'risk-{role}', 'password': password}).raise_for_status()
                client.headers['X-CSRF-Token'] = login.json()['csrfToken']
            reviewer, receiver = clients['reviewer'], clients['receiver']
            project = ok(api.post('projects', headers=headers(), json={'code': 'RISK-ACL', 'name': 'Restricted synthetic risk'}))
            pid = project['projectId']
            with migration.transaction():
                migration.execute("UPDATE projects SET pm_id=%s, stage='executing' WHERE id=%s", (owner, pid))
                for person, role in ((owner, 'pm'), (people['reviewer'], 'reviewer')):
                    migration.execute('''INSERT INTO project_members
                        (project_id,person_id,assigned_by,role,assignment_status,accepted_at)
                        VALUES (%s,%s,%s,%s,'accepted',now())''', (pid, person, owner, role))
            path = f'projects/{pid}'
            risk = ok(api.post(path + '/risks', headers=headers(), json=dict(
                statement='Synthetic risk for least privileges', response='Transfer to nominated receiver', prob=2, impact=3)))
            proof = ok(api.post(path + '/evidence', headers=headers(), files={'file': ('risk.png', binary('png'), 'image/png')},
                data={'linkedResourceType': 'risk', 'linkedResourceId': risk['riskId']}))
            ok(reviewer.post(f"evidence/{proof['evidenceId']}/verify", headers=headers(proof['revision']),
                json={'reason': 'Compared synthetic original contents', 'sha256': proof['sha256']}))
            handoff = f"risks/{risk['riskId']}/handoff"
            binding = {'receiverPersonId': str(people['receiver']), 'acceptanceEvidenceRef': proof['evidenceRef']}
            request = ok(reviewer.post(handoff + '/acceptance', headers=headers(),
                json={**binding, 'kind': 'request', 'acceptanceScope': SCOPE}))
            confirmation = ok(receiver.post(handoff + '/acceptance', headers=headers(),
                json={**binding, 'kind': 'confirm', 'acceptanceScope': SCOPE, 'decision': 'accept'}))
            result = ok(reviewer.post(handoff, headers=headers(), json=binding))
            assert request['acceptanceId'] != confirmation['acceptanceId']
            assert (result['status'], result['acceptanceId']) == ('transferred', confirmation['acceptanceId'])
