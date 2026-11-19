from uuid import uuid4

import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.identity import explicit_grants


def test_automatic_scope_cannot_inherit_new_membership(live, actor):
    engine, api, _, _ = live
    secret, credential = actor()
    headers = {'Authorization': 'Bearer ' + secret, 'Idempotency-Key': uuid4().hex}
    pid = api.post('/api/v1/projects', headers=headers, json={'code': 'SCOPE', 'name': 'Scoped'}).json()['data']['projectId']
    with engine.begin() as conn:
        owner = conn.scalar(sa.select(db.projects.c.initiator_id).where(db.projects.c.id == pid))
        account = conn.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == owner))
        conn.execute(db.project_members.insert().values(project_id=pid, person_id=owner, assigned_by=owner,
                     role='pm', assignment_status='accepted', accepted_at=sa.func.now()))
        # Arrange a post-draft fixture, not an application stage-transition operation.
        conn.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='designing', pm_id=owner))
    assert api.get('/api/v1/projects', headers=headers).json()['data']['items'] == []
    assert api.get('/api/v1/projects/'+pid, headers=headers).status_code == 404
    with engine.begin() as conn:
        conn.execute(explicit_grants.insert().values(credential_id=credential, project_id=pid, granted_by=account))
    assert api.get('/api/v1/projects/'+pid, headers=headers).status_code == 200
    with engine.begin() as conn:
        conn.execute(db.project_members.update().where(db.project_members.c.project_id==pid).values(revoked_at=sa.func.now()))
    assert api.get('/api/v1/projects/'+pid, headers=headers).status_code == 404
