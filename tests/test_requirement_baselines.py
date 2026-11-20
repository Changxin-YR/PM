from contextlib import ExitStack
from secrets import token_urlsafe
from uuid import uuid4

import httpx
import sqlalchemy as sa
from argon2 import PasswordHasher
from pmo import schema_v1 as db


def test_two_current_people_sign_the_same_requirement_snapshot(live,actor):
    engine,api,port,_=live
    secret,cred=actor(extra_actions=('pmo_requirement_create','human_requirement_baseline_sign','human_requirement_baseline_get'))
    def h(n=1): return {'Idempotency-Key':uuid4().hex,'If-Match':str(n)}
    bearer={'Authorization':'Bearer '+secret}
    p=api.post('/api/v1/projects',headers={**bearer,**h()},json={'code':'BASE','name':'Joint confirmation'}).json()['data']; pid=p['projectId']
    base=f'projects/{pid}'; path=base+'/requirement-baseline'; body={'desc':'Export evidence list','acceptanceCriteria':'File has original fields','priority':'P0'}
    req=api.post('/api/v1/'+base+'/requirements',headers={**bearer,**h()},json=body).json()['data']['requirementId']
    assert api.get('/api/v1/'+path,headers=bearer).status_code==403
    password=token_urlsafe(); digest=PasswordHasher().hash(password)
    with engine.begin() as c:
        uid=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==cred))
        user=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==uid).values(password_hash=digest).returning(db.user_accounts.c.username))
        pm=c.scalar(sa.select(db.persons.c.id).where(db.persons.c.user_id==uid)); customer=uuid4(); username=uuid4().hex
        cid=c.scalar(db.user_accounts.insert().values(username=username,password_hash=digest,is_active=True).returning(db.user_accounts.c.id))
        c.execute(db.persons.insert().values(id=customer,created_by=pm,name='Customer',user_id=cid,org_id=p['orgId'],is_active=True))
        for person,role in ((pm,'pm'),(customer,'client_representative')):
            c.execute(db.project_members.insert().values(project_id=pid,person_id=person,assigned_by=pm,role=role,assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(db.projects.update().where(db.projects.c.id==pid).values(stage='designing',pm_id=pm))
    with ExitStack() as stack:
        def login(n):
            client=stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/',trust_env=False))
            client.headers['X-CSRF-Token']=client.post('auth/login',json={'username':n,'password':password}).json()['csrfToken']; return client
        manager=login(user); customer_session=login(username)
        def read(who): return who.get(path).json()['data']
        def sign(who,st,role,headers=None): return who.post(path+'/sign',headers=headers or h(st['revision']),json={'contentHash':st['contentHash'],'role':role,'reason':'Reviewed full list'})
        initial=read(manager); assert initial['canSignAs']=='pm' and 'canSignAs' not in read(customer_session)
        k=h(); result=sign(manager,initial,'pm',k); assert result.status_code==200,result.text
        assert sign(manager,initial,'pm',k).json()==result.json()
        old=read(customer_session); assert old['status']=='awaiting_customer' and 'canSignAs' not in old
        assert sign(customer_session,old,'client_representative').status_code==403
        with engine.begin() as c:
            c.execute(db.stakeholders.insert().values(project_id=pid,type='customer',created_by=pm,name='Authenticated client',linked_person_id=customer))
        assert read(customer_session)['canSignAs']=='client_representative'
        assert manager.patch('requirements/'+req,headers=h(),json={'fieldsRequirement':{'acceptanceCriteria':'Export includes source IDs'}}).status_code==200
        assert sign(customer_session,old,'client_representative').json()['code']=='BASELINE_CHANGED'
        new=read(manager); assert new['status']=='draft' and new['contentHash']!=initial['contentHash']
        assert sign(manager,new,'pm').status_code==200
        with engine.begin() as c: c.execute(db.project_members.update().where(db.project_members.c.project_id==pid,db.project_members.c.role=='pm').values(revoked_at=sa.func.now()))
        assert 'canSignAs' not in read(customer_session)
        with engine.begin() as c: c.execute(db.project_members.update().where(db.project_members.c.project_id==pid,db.project_members.c.role=='pm').values(revoked_at=None))
        ready=read(customer_session); k=h(ready['revision']); final=sign(customer_session,ready,'client_representative',k)
        assert final.status_code==200,final.text
        assert sign(customer_session,ready,'client_representative',k).json()==final.json()
        frozen=read(manager); assert frozen['status']=='frozen' and frozen['pmSignerId']==str(pm) and frozen['customerSignerId']==str(customer)
        assert frozen['contentHash']==new['contentHash'] and frozen['items'][0]['acceptanceCriteria']=='Export includes source IDs'
        row=manager.get(base+'/requirements').json()['data']['items'][0]; assert row['baselineVersion']==1 and row['status']=='baseline'
        assert manager.patch('requirements/'+req,headers=h(row['revision']),json={'fieldsRequirement':{'priority':'P1'}}).json()['code']=='BASELINE_FROZEN'
        assert manager.post(base+'/requirements',headers=h(),json=body).json()['code']=='BASELINE_FROZEN'
    with engine.connect() as c: assert c.scalar(sa.text('SELECT count(*) FROM requirement_baseline_signatures WHERE project_id=:pid'),{'pid':pid})==3
