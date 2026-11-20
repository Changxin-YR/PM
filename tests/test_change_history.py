from uuid import uuid4

import sqlalchemy as sa
from pmo import schema_v1 as db


def test_history_checks_target_type_project_and_pagination(live,actor):
    engine,api,_,_=live
    token,_=actor(extra_actions=('pmo_requirement_create','pmo_requirement_change_history'))
    outsider,_=actor(extra_actions=('pmo_requirement_change_history',))
    def h(): return {'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex}
    def project(code): return api.post('/api/v1/projects',headers=h(),json={'code':code,'name':code}).json()['data']['projectId']
    pid=project('HISTORY-A'); other=project('HISTORY-B')
    req=api.post(f'/api/v1/projects/{pid}/requirements',headers=h(),json={'desc':'Initial scope','priority':'P0','acceptanceCriteria':'All tests pass'}).json()['data']['requirementId']
    path=f'/api/v1/requirements/{req}/changes'
    with engine.begin() as c:
        pm=c.scalar(sa.select(db.projects.c.initiator_id).where(db.projects.c.id==pid))
        for project_id,kind,status in [(pid,'requirement','withdrawn'),(pid,'requirement','applied'),(other,'requirement','pending'),(pid,'milestone','draft')]:
            c.execute(db.change_entries.insert().values(project_id=project_id,target_type=kind,target_id=req,created_by=pm,requested_by=pm,status=status,base_revision=2,
                before={'priority':'P0'},after={'priority':'P1'},reason='Reassessed priority',approved_by=pm if status=='applied' else None))
    data=api.get(path,headers=h()).json(); assert data['data']['total']==2 and len(data['meta']['sourceRefs'])==2
    rows=data['data']['items']; applied=next(r for r in rows if r['status']=='applied'); assert applied['approvedBy']==str(pm) and applied['reason']=='Reassessed priority' and applied['before']=={'priority':'P0'}
    for n in (1,2,3):
        page=api.get(path,headers=h(),params={'page':n,'pageSize':1}).json()['data']; assert page['total']==2 and page['items']==rows[n-1:n]
    with engine.begin() as c:
        # Same organization alone is insufficient; replace the outsider's org with a separate human who lacks membership.
        peer=uuid4(); user=c.scalar(db.user_accounts.insert().values(username=uuid4().hex,password_hash='!disabled!',is_active=True).returning(db.user_accounts.c.id))
        org=c.scalar(sa.select(db.projects.c.org_id).where(db.projects.c.id==pid)); c.execute(db.persons.insert().values(id=peer,user_id=user,org_id=org,name='Other',created_by=peer,is_active=True))
        import hashlib
        c.execute(db.agent_credentials.update().where(db.agent_credentials.c.token_hash==hashlib.sha256(outsider.encode()).hexdigest()).values(org_id=org,delegated_by=user,allowed_project_ids=[pid]))
    assert api.get(path,headers={'Authorization':'Bearer '+outsider}).status_code==404
