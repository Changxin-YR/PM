from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

import sqlalchemy as sa
from pmo import schema_v1 as db
from pmo.identity import explicit_grants


def test_milestone_limit_replay_and_freeze(live, actor):
    engine, api, _, _ = live
    token, cid = actor()
    def headers(secret=token):
        return {'Authorization':'Bearer '+secret,'Idempotency-Key':uuid4().hex}
    pid=api.post('/api/v1/projects',headers=headers(),json={'code':'MS-01','name':'验收'}).json()['data']['projectId']
    path=f'/api/v1/projects/{pid}/milestones'
    command={'name':'阶段成果','plannedDate':'2027-01-01','isKey':True}
    key=headers()
    first=api.post(path,headers=key,json=command)
    assert first.status_code==200 and api.post(path,headers=key,json=command).json()==first.json()
    assert api.post(path,headers=key,json={**command,'name':'异参'}).status_code==409
    assert api.post(path,headers=headers(),json={**command,'ordinal':1}).status_code==409
    item='/api/v1/milestones/'+first.json()['data']['milestoneId']
    fields={'fieldsMilestone':{'forecastDate':'2027-01-04'}}
    foreign,_=actor()
    assert api.get(path,headers=headers(foreign)).status_code==404
    assert api.patch(item,headers={**headers(foreign),'If-Match':'1'},json=fields).status_code==404
    assert api.patch(item,headers={**headers(),'If-Match':'2'},json=fields).status_code==409
    assert api.patch(item,headers={**headers(),'If-Match':'1'},json={'fieldsMilestone':{'actualDate':'2026-12-01'}}).status_code==422
    for slot in range(2,8):
        assert api.post(path,headers=headers(),json={**command,'ordinal':slot}).status_code==200
    second=token_urlsafe(32); digest=sha256(second.encode()).hexdigest()
    with engine.begin() as c:
        src=dict(c.execute(sa.select(db.agent_credentials).where(db.agent_credentials.c.id==cid)).mappings().one())
        src.update(id=uuid4(),token_hash=digest,token_fingerprint=digest[:12]); c.execute(db.agent_credentials.insert().values(**src))
    with ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda t:api.post(path,headers=headers(t),json=command),[token,second]))
    assert sorted(r.status_code for r in responses)==[200,422]
    assert next(r for r in responses if r.status_code==422).json()['code']=='MILESTONE_LIMIT'
    assert api.get(path,headers=headers()).json()['data']['total']==8
    with engine.begin() as c:
        owner=c.scalar(sa.select(db.projects.c.initiator_id).where(db.projects.c.id==pid))
        c.execute(db.project_members.insert().values(project_id=pid,person_id=owner,assigned_by=owner,role='pm',assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(explicit_grants.insert().values(credential_id=cid,project_id=pid,granted_by=src['delegated_by']))
        c.execute(db.projects.update().where(db.projects.c.id==pid).values(stage='executing',pm_id=owner))
    frozen=api.patch(item,headers={**headers(),'If-Match':'1'},json=fields)
    assert frozen.status_code==422 and frozen.json()['code']=='BASELINE_FROZEN'
    assert api.get(path,headers=headers()).json()['data']['items'][0]['revision']==1
