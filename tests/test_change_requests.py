from datetime import date
from secrets import token_urlsafe
import httpx
from argon2 import PasswordHasher
from uuid import uuid4
import sqlalchemy as sa
from pmo import schema_v1 as db
from pmo.identity import explicit_grants


def test_proposals_bind_current_target_and_never_apply(live,actor):
    engine,api,port,_=live; token,credential=actor(extra_actions=('pmo_change_propose',)); ids={}
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
        ids[kind]=response.json()['data']['changeId']
        assert api.post(path,headers=h(),json=body).json()['code']=='CHANGE_ALREADY_PENDING'
    with engine.connect() as c:
        assert c.scalar(sa.select(db.milestones.c.planned_date).where(db.milestones.c.id==mid))==date(2026,11,9)
        assert c.scalar(sa.select(db.cost_entries.c.budget).where(db.cost_entries.c.id==bid))==100
        assert c.scalar(sa.select(db.charters.c.rough_duration_days).where(db.charters.c.id==cid))==5
    username=uuid4().hex; password=token_urlsafe(); reviewer=uuid4()
    with engine.begin() as c:
        org=c.scalar(sa.select(db.projects.c.org_id).where(db.projects.c.id==pid)); uid=c.scalar(db.user_accounts.insert().values(username=username,password_hash=PasswordHasher().hash(password),is_active=True).returning(db.user_accounts.c.id))
        c.execute(db.persons.insert().values(id=reviewer,user_id=uid,org_id=org,created_by=reviewer,name='Independent',is_active=True))
        c.execute(db.project_members.insert().values(project_id=pid,person_id=reviewer,assigned_by=pm,role='reviewer',assignment_status='accepted',accepted_at=sa.func.now()))
        for kind in ids: c.execute(sa.text("INSERT INTO change_approval_grants VALUES(:p,:u,:kind,'isolated fixture',now()+interval '1 hour',NULL)"),dict(p=pid,u=reviewer,kind=kind))
    with httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/',trust_env=False) as cli:
        cli.headers['X-CSRF-Token']=cli.post('auth/login',json={'username':username,'password':password}).json()['csrfToken']
        def decide(kind,action='approve'):
            return cli.post(f'changes/{ids[kind]}/{action}',headers={'If-Match':'1','Idempotency-Key':uuid4().hex},json={'rationale':'Reviewed current baseline'})
        with engine.begin() as c: c.execute(db.change_entries.update().where(db.change_entries.c.id==ids['budget']).values(after={'budget':'999.00'}))
        assert decide('budget').json()['code']=='APPROVAL_STALE'
        with engine.begin() as c:
            c.execute(db.change_entries.update().where(db.change_entries.c.id==ids['budget']).values(after={'budget':'120.00'}))
            c.execute(sa.text("UPDATE change_approval_grants SET revoked_at=now() WHERE project_id=:p AND target_type='budget'"),{'p':pid})
        assert decide('budget').json()['code']=='INDEPENDENT_APPROVER_REQUIRED'
        with engine.begin() as c: c.execute(sa.text("UPDATE change_approval_grants SET revoked_at=NULL WHERE project_id=:p AND target_type='budget'"),{'p':pid})
        for kind in ('budget','milestone'):
            result=decide(kind); assert result.status_code==200,result.text
        with engine.begin() as c: c.execute(db.charters.update().where(db.charters.c.id==cid).values(revision=2))
        assert decide('charter').json()['code']=='APPROVAL_STALE'
        assert decide('charter','reject').json()['data']['status']=='rejected'
        response=api.post(path,headers=h(2),json=body); assert response.status_code==200,response.text
        ids['charter']=response.json()['data']['changeId']; assert decide('charter').json()['data']['status']=='applied'
    with engine.connect() as c:
        assert c.scalar(sa.select(db.milestones.c.planned_date).where(db.milestones.c.id==mid))==date(2026,12,5)
        assert c.scalar(sa.select(db.charters.c.rough_duration_days).where(db.charters.c.id==cid))==8
        budgets=c.execute(sa.select(db.cost_entries.c.budget,db.cost_entries.c.is_current_baseline).where(db.cost_entries.c.project_id==pid).order_by(db.cost_entries.c.baseline_version)).all()
        assert budgets==[(100,False),(120,True)]
