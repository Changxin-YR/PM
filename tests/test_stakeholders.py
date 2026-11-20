from uuid import uuid4

import sqlalchemy as sa

from pmo import schema_v1 as db


def test_contacts_are_not_identities_and_are_scoped(live, actor):
    engine, api, _, _ = live
    actions = ('pmo_stakeholder_create', 'human_stakeholder_list')
    token, _ = actor(extra_actions=actions)
    other, _ = actor(extra_actions=actions)
    def headers(secret=token):
        return {'Authorization': 'Bearer '+secret, 'Idempotency-Key': uuid4().hex}

    pid = api.post('/api/v1/projects', headers=headers(), json={'code':'CONTACT','name':'Contact register'}).json()['data']['projectId']
    path = f'/api/v1/projects/{pid}/stakeholders'
    body = {'name':'Reviewer', 'stakeholderType':'customer', 'influence':'high', 'expectation':'private'}
    h = headers()
    for field, value in [('linkedPersonId',str(uuid4())), ('contactRole','client_representative'), ('isVerified',True), ('stakeholderType','admin'), ('influence',None)]:
        assert api.post(path, headers=h, json={**body, field:value}).status_code == 422
    assert api.get(path, headers=h).status_code == 403
    assert api.post(path, headers=headers(other), json=body).status_code == 404
    t = db.project_members
    with engine.connect() as c:
        members = c.execute(sa.select(t).where(t.c.project_id==pid)).all()
    first = api.post(path, headers=h, json=body)
    assert first.is_success and api.post(path, headers=h, json=body).json() == first.json()
    assert api.post(path, headers=h, json={**body, 'name':'Different'}).status_code == 409
    contact = first.json()['data']
    assert set(contact) == {'stakeholderId','projectId','name','type','revision'}
    with engine.begin() as c:
        row = c.execute(sa.select(db.stakeholders).where(db.stakeholders.c.project_id==pid)).mappings().one()
        assert row.linked_person_id is None and row.contact_role is None and row.expectation == 'private'
        assert c.execute(sa.select(t).where(t.c.project_id==pid)).all() == members
        assert c.scalar(sa.select(sa.func.count()).select_from(db.audit_logs).where(db.audit_logs.c.project_id==pid,
                     db.audit_logs.c.action=='pmo_stakeholder_create',db.audit_logs.c.result=='success')) == 1
        c.execute(db.stakeholders.insert(), [dict(project_id=pid, created_by=row.created_by,
                  type='internal', name=f'Person {i}') for i in range(100)])
    detail = api.get(f'/api/v1/projects/{pid}', headers=h).json()
    assert len(detail['data']['stakeholders']) == 100
    assert all(item['projectId']==pid and set(item)==set(contact) for item in detail['data']['stakeholders'])
