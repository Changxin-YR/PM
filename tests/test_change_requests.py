from datetime import date
from uuid import uuid4
import sqlalchemy as sa
from pmo import schema_v1 as db
from pmo.identity import explicit_grants


def test_proposals_bind_current_target_and_never_apply(live,actor):
    engine,api,_,_=live; token,credential=actor(extra_actions=('pmo_change_propose',))
    def h(v=1): return {'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex,'If-Match':str(v)}
    pid=api.post('/api/v1/projects',headers=h(),json={'code':'CHANGE','name':'Changes'}).json()['data']['projectId']; path=f'/api/v1/projects/{pid}/changes'
    with engine.begin() as c:
        pm=c.scalar(sa.select(db.projects.c.initiator_id).where(db.projects.c.id==pid)); user=c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id==pm))
        c.execute(db.project_members.insert().values(project_id=pid,person_id=pm,assigned_by=pm,role='pm',assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(db.projects.update().where(db.projects.c.id==pid).values(pm_id=pm,stage='executing'))
        c.execute(explicit_grants.insert().values(credential_id=credential,granted_by=user,project_id=pid))
        mid=c.scalar(db.milestones.insert().values(project_id=pid,name='Checkpoint',created_by=pm,ordinal=1,is_key=True,planned_date=date(2026,11,9)).returning(db.milestones.c.id))
        bid=c.scalar(db.cost_entries.insert().values(project_id=pid,created_by=pm,type='money',category='fixture',period_start=date(2020,11,1),period_end=date(2020,11,30),budget=100,actual=0,forecast_remaining=0,baseline_version=1).returning(db.cost_entries.c.id))
        cid=c.scalar(db.charters.insert().values(project_id=pid,created_by=pm,is_frozen=True,rough_duration_days=5).returning(db.charters.c.id))
    for kind,tid,before,after in [('milestone',mid,{'plannedDate':'2026-11-09'},{'plannedDate':'2026-12-05'}),
            ('budget',bid,{'budget':'100.00'},{'budget':'120.00'}),('charter',cid,{'roughDuration':5},{'roughDuration':8})]:
        body={'targetType':kind,'targetId':str(tid),'before':before,'after':after,'reason':'Verified baseline difference'}
        assert api.post(path,headers=h(),json={**body,'after':{'approvedBy':str(pm)}}).status_code==422
        assert api.post(path,headers=h(),json={**body,'before':{'other':0}}).json()['code']=='BASELINE_CHANGED'
        assert api.post(path,headers=h(2),json=body).json()['code']=='VERSION_CONFLICT'
        key=h(); response=api.post(path,headers=key,json=body); assert response.status_code==200,response.text
        assert response.json()['data']['status']=='pending' and response.json()==api.post(path,headers=key,json=body).json()
        assert api.post(path,headers=h(),json=body).json()['code']=='CHANGE_ALREADY_PENDING'
    with engine.connect() as c:
        assert c.scalar(sa.select(db.milestones.c.planned_date).where(db.milestones.c.id==mid))==date(2026,11,9)
        assert c.scalar(sa.select(db.cost_entries.c.budget).where(db.cost_entries.c.id==bid))==100
        assert c.scalar(sa.select(db.charters.c.rough_duration_days).where(db.charters.c.id==cid))==5
