from contextlib import ExitStack
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from io import BytesIO
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
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
        requirement_delivery(engine,api,port,manager,customer_session,pid,req,row['revision'],pm,uid,customer,h)
    with engine.connect() as c: assert c.scalar(sa.text('SELECT count(*) FROM requirement_baseline_signatures WHERE project_id=:pid'),{'pid':pid})==3


def requirement_delivery(engine,api,port,manager,client,pid,req,rev,pm,uid,customer,h):
    path=f'requirements/{req}'; files=f'projects/{pid}/evidence'; tokens='me/agent-tokens'
    with engine.begin() as c:
        c.execute(db.user_roles.insert().values(user_id=uid,role='delegator'))
        c.execute(db.project_members.insert().values(project_id=pid,person_id=customer,assigned_by=pm,role='reviewer',assignment_status='accepted',accepted_at=sa.func.now()))
    grant={'allowedActions':['pmo_requirement_deliver'],'allowedProjectIds':[pid],'expiresAt':(datetime.now(UTC)+timedelta(minutes=30)).isoformat()}
    assert manager.post(tokens,headers=h(),json=grant).status_code==403
    with engine.begin() as c: c.execute(db.projects.update().where(db.projects.c.id==pid).values(stage='executing'))
    buf=BytesIO(); Image.new('RGB',(3,3)).save(buf,'PNG')
    evidence=manager.post(files,headers=h(),files={'file':('record.png',buf.getvalue(),'image/png')},data={'linkedResourceType':'requirement','linkedResourceId':req}).json()['data']
    proof={'evidenceRef':evidence['evidenceRef']}
    def step(user,name,version): return user.post(path+'/'+name,headers=h(version),json=proof)
    assert step(manager,'deliver',rev).json()['code']=='VERIFIED_EVIDENCE_REQUIRED'
    assert step(manager,'accept',rev).json()['code']=='INVALID_REQUIREMENT_TRANSITION'
    assert step(client,'deliver',rev).status_code==403
    assert client.post('evidence/'+evidence['evidenceId']+'/verify',headers=h(),json={'sha256':evidence['sha256'],'reason':'Checked original'}).status_code==200
    with engine.begin() as c: change=c.scalar(db.change_entries.insert().values(project_id=pid,created_by=pm,requested_by=pm,target_id=req,target_type='requirement',
        base_revision=rev,status='pending',before={},after={},reason='Scope under review').returning(db.change_entries.c.id))
    assert step(manager,'deliver',rev).json()['code']=='PENDING_SCOPE_CHANGE'
    with engine.begin() as c: c.execute(db.change_entries.update().where(db.change_entries.c.id==change).values(status='withdrawn'))
    issued=manager.post(tokens,headers=h(manager.get(tokens).json()['data']['collectionRevision']),json=grant); assert issued.status_code==200,issued.text
    token=issued.headers['x-pmo-new-token']
    async def delegated(propose=False):
        env={**{k:os.environ[k] for k in ('SYSTEMROOT','PATH','TEMP','TMP') if k in os.environ},'PMO_API_PORT':str(port),'PMO_AGENT_TOKEN':token,'PYTHONUTF8':'1'}
        params=StdioServerParameters(command=sys.executable,args=['-m','pmo.mcp_server'],env=env)
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize(); args={**proof,'requirementId':req,'expectedRevision':rev,'idempotencyKey':uuid4().hex}
            if propose:
                args={'projectId':pid,'targetId':req,'targetType':'requirement','before':{'priority':'P0'},'after':{'priority':'P1'},
                    'expectedRevision':rev+3,'reason':'客户建议调整实施顺序','idempotencyKey':uuid4().hex}
                result=await session.call_tool('pmo_change_propose',args); assert not result.isError,result.content
                assert result.structuredContent['data']['status']=='pending' and 'approvedBy' not in result.structuredContent['data']
                assert result.structuredContent==(await session.call_tool('pmo_change_propose',args)).structuredContent
                return
            delivered=await session.call_tool('pmo_requirement_deliver',args); assert not delivered.isError,delivered.content
            assert delivered.structuredContent['data']['status']=='delivered'
            assert delivered.structuredContent==(await session.call_tool('pmo_requirement_deliver',args)).structuredContent
    asyncio.run(delegated())
    for action in ('accept','customer-confirm'):
        assert api.post('/api/v1/'+path+'/'+action,headers={**h(rev+1),'Authorization':'Bearer '+token},json=proof).status_code==403
    assert step(client,'customer-confirm',rev+1).json()['code']=='INVALID_REQUIREMENT_TRANSITION'
    assert step(manager,'accept',rev).json()['code']=='VERSION_CONFLICT'
    accepted=step(manager,'accept',rev+1); assert accepted.json()['data']['status']=='accepted' and accepted.json()['data']['acceptedAt']
    assert step(manager,'customer-confirm',rev+2).status_code==403
    with engine.begin() as c: c.execute(db.stakeholders.update().where(db.stakeholders.c.project_id==pid).values(linked_person_id=None))
    assert step(client,'customer-confirm',rev+2).status_code==403
    with engine.begin() as c: c.execute(db.stakeholders.update().where(db.stakeholders.c.project_id==pid).values(linked_person_id=customer))
    final=step(client,'customer-confirm',rev+2); assert final.json()['data']['status']=='customer_confirmed',final.text
    issued=manager.post(tokens,headers=h(manager.get(tokens).json()['data']['collectionRevision']),json={**grant,'allowedActions':['pmo_change_propose']})
    assert issued.status_code==200,issued.text
    token=issued.headers['x-pmo-new-token']; asyncio.run(delegated(propose=True))
    latest=manager.get(path+'/changes').json()['data']['items'][0]; assert latest['status']=='pending' and latest['reason']=='客户建议调整实施顺序'
    ledger=f'projects/{pid}/changes'; assert api.get('/api/v1/'+ledger,headers={'Authorization':'Bearer '+token}).status_code==403
    receipt=manager.get(ledger,params={'pageSize':1}).json()['data']; assert receipt['canPropose'] and receipt['total']==2 and receipt['items']==[latest]
    assert not client.get(ledger).json()['data']['canPropose']
    unchanged=manager.get(f'projects/{pid}/requirements').json()['data']['items'][0]; assert unchanged['priority']=='P0' and unchanged['revision']==rev+3
    with engine.connect() as c:
        records=c.execute(sa.text('SELECT status,actor_type,actor_id,evidence_id FROM requirement_transitions WHERE requirement_id=:r ORDER BY revision'),{'r':req}).all()
        assert [(s,k,a) for s,k,a,_ in records]==[('delivered','agent',pm),('accepted','human',pm),('customer_confirmed','human',customer)]
        assert all(str(e)==evidence['evidenceId'] for *_,e in records)
    decision=f"changes/{latest['changeId']}/approve"; verdict={'rationale':'独立核对需求版本和差异'}
    assert client.post(decision,headers=h(),json=verdict).status_code==403
    assert api.post('/api/v1/'+decision,headers={**h(),'Authorization':'Bearer '+token},json=verdict).status_code==403
    with engine.begin() as c:
        c.execute(db.project_members.insert().values(project_id=pid,person_id=pm,assigned_by=pm,role='reviewer',assignment_status='accepted',accepted_at=sa.func.now()))
        for person in (customer,pm): c.execute(sa.text("INSERT INTO change_approval_grants VALUES(:p,:u,'requirement','isolated fixture',now()+interval '1 hour',NULL)"),dict(p=pid,u=person))
    assert manager.post(decision,headers=h(),json=verdict).json()['code']=='INDEPENDENT_APPROVER_REQUIRED'
    key=h(); applied=client.post(decision,headers=key,json=verdict); assert applied.status_code==200,applied.text
    assert applied.json()==client.post(decision,headers=key,json=verdict).json() and applied.json()['data']['approvedBy']==str(customer)
    changed=manager.get(f'projects/{pid}/requirements').json()['data']['items'][0]; assert (changed['priority'],changed['status'],changed['baselineVersion'])==('P1','baseline',2)
    assert step(manager,'deliver',changed['revision']).json()['code']=='CURRENT_BASELINE_EVIDENCE_REQUIRED'
    assert manager.get(files,params={'requirementId':req,'verifiedOnly':'true','currentBaselineOnly':'true'}).json()['data']['total']==0
    newproof=manager.post(files,headers=h(2),files={'file':('v2.png',buf.getvalue(),'image/png')},data={'linkedResourceType':'requirement','linkedResourceId':req}).json()['data']
    assert client.post('evidence/'+newproof['evidenceId']+'/verify',headers=h(),json={'sha256':newproof['sha256'],'reason':'Checked revised scope'}).status_code==200
    proof={'evidenceRef':newproof['evidenceRef']}; assert step(manager,'deliver',changed['revision']).json()['data']['status']=='delivered'
    assert client.post(decision,headers=h(3),json=verdict).json()['code']=='CHANGE_NOT_PENDING'
    with engine.connect() as c:
        snap=c.execute(sa.text('SELECT old_state,new_state,rationale FROM change_decision_records WHERE change_id=:id'),{'id':latest['changeId']}).mappings().one()
        assert snap.old_state['status']=='customer_confirmed' and snap.new_state['accepted_by'] is None and snap.rationale==verdict['rationale']
