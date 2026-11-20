import asyncio
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import jsonschema
import sqlalchemy as sa
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pmo import schema_v1 as db

def test_postgres_contract_and_downgrade_guard(live,actor):
    actor()
    engine,_,_,env=live
    inspector=sa.inspect(engine)
    assert len(db.metadata.tables)==33
    assert sum(len(inspector.get_columns(t)) for t in db.metadata.tables)==421
    result=subprocess.run([sys.executable,'-m','alembic','downgrade','base'],env=env,capture_output=True,text=True)
    assert result.returncode and 'Refusing destructive downgrade' in result.stderr
    with engine.connect() as c: assert c.scalar(sa.text('select version_num from alembic_version'))=='0014'

def test_concurrent_replay_scope_and_denial(live,actor):
    engine,api,_,_=live
    token,ident=actor(); h={'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex}
    body={'code':'QA-1','name':'Acceptance'}
    def send(_): return api.post('/api/v1/projects',headers=h,json=body)
    with ThreadPoolExecutor(2) as pool: responses=list(pool.map(send,range(2)))
    assert [r.status_code for r in responses]==[200,200]
    assert responses[0].json()==responses[1].json()
    pid=responses[0].json()['data']['projectId']
    assert api.post('/api/v1/projects',headers=h,json={**body,'name':'Different'}).status_code==409
    assert api.get('/api/v1/projects',headers=h).json()['data']['total']==1
    other,_=actor(False); h2={**h,'Authorization':'Bearer '+other}
    assert api.get('/api/v1/projects',headers=h2).json()['data']['items']==[]
    assert api.get('/api/v1/projects/'+pid,headers=h2).status_code==404
    assert api.post('/api/v1/projects',headers=h2,json=body).status_code==403
    with engine.connect() as c:
        assert c.execute(sa.select(db.project_members.c.role).where(db.project_members.c.project_id==pid)).scalars().all()==['initiator']
        assert c.scalar(sa.select(sa.func.count()).select_from(db.audit_logs).where(db.audit_logs.c.project_id==pid))==1
        assert c.scalar(sa.select(sa.func.count()).select_from(db.audit_logs).where(db.audit_logs.c.actor_id==ident,db.audit_logs.c.result=='denied'))==1

def test_mcp_stdio(live,actor):
    cost_ops=('pmo_cost_summary','pmo_cost_budget_draft_save','pmo_cost_forecast_save','pmo_cost_posting_propose','pmo_preapproval_spending_list','pmo_milestone_forecast','pmo_alert_list','pmo_requirement_deliver')
    engine,_,port,_=live; token,ident=actor(extra_actions=(*cost_ops,'pmo_requirement_create','pmo_requirement_list','pmo_requirement_draft_update','pmo_charter_save','pmo_role_assignment_propose','pmo_stakeholder_create','pmo_risk_create','pmo_risk_list','pmo_risk_update'))
    async def scenario():
        env={**{k:os.environ[k] for k in ('SYSTEMROOT','PATH','TEMP','TMP') if k in os.environ},'PMO_API_PORT':str(port),'PMO_AGENT_TOKEN':token,'PYTHONUTF8':'1'}
        params=StdioServerParameters(command=sys.executable,args=['-m','pmo.mcp_server'],env=env)
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize(); tools={t.name:t for t in (await session.list_tools()).tools}
            assert set(tools)=={*cost_ops,'pmo_project_create','pmo_project_list','pmo_project_get','pmo_project_draft_update','pmo_milestone_create','pmo_milestone_list','pmo_milestone_draft_update','pmo_requirement_create','pmo_requirement_list','pmo_requirement_draft_update','pmo_charter_save','pmo_role_assignment_propose','pmo_stakeholder_create','pmo_risk_create','pmo_risk_list','pmo_risk_update'}
            async def call(op,args):
                r=await session.call_tool(op,args); assert not r.isError,r.content
                jsonschema.validate(r.structuredContent,tools[op].outputSchema); return r.structuredContent['data']
            p=await call('pmo_project_create',{'code':'MCP-1','name':'合成项目','idempotencyKey':uuid4().hex})
            assert (await call('pmo_project_get',{'projectId':p['projectId']}))['project']==p
            base={'projectId':p['projectId'],'type':'money'}
            candidate=await call('pmo_cost_posting_propose',{**base,'period':'2020-11','amount':'3.14','sourceRef':'synthetic-receipt','idempotencyKey':uuid4().hex})
            assert candidate['status']=='pending' and candidate['amountSigned']=='3.14'
            assert (await call('pmo_preapproval_spending_list',{}))['items']==[]
            plan=await call('pmo_cost_budget_draft_save',{**base,'budget':'109.70','expectedRevision':1,'idempotencyKey':uuid4().hex})
            assert plan['moneyBudget']=='109.70' and plan['revision']==2
            await call('pmo_cost_forecast_save',{**base,'forecastRemaining':'20.05','reason':'Updated quote','expectedRevision':2,'idempotencyKey':uuid4().hex})
            line=(await call('pmo_cost_summary',{'projectId':p['projectId']}))['money']
            assert line['budget']=='0.00' and line['forecastRemaining']=='20.05' and line['color']=='untrusted'
            contact=await call('pmo_stakeholder_create',{'projectId':p['projectId'],'name':'Customer','stakeholderType':'customer','expectation':'private note','idempotencyKey':uuid4().hex})
            assert 'expectation' not in contact
            assert (await call('pmo_project_get',{'projectId':p['projectId']}))['stakeholders']==[contact]
            risk=await call('pmo_risk_create',{'projectId':p['projectId'],'statement':'合成测试阻塞风险','prob':2,'impact':5,'response':'安排核查','isBlocking':True,'idempotencyKey':uuid4().hex})
            changed=await call('pmo_risk_update',{'riskId':risk['riskId'],'expectedRevision':1,'fieldsRisk':{'prob':3},'idempotencyKey':uuid4().hex})
            assert changed['revision']==2 and changed['isBlocking'] and (await call('pmo_risk_list',{'projectId':p['projectId']}))['items']==[changed]
            assert (await call('pmo_project_list',{'filters':{'keyword':'合成'}}))['items']==[p]
            args={'projectId':p['projectId'],'fields':{'name':'改名'},'expectedRevision':1,'idempotencyKey':uuid4().hex}
            updated=await call('pmo_project_draft_update',args)
            assert updated['revision']==2 and updated['name']=='改名'
            assert (await call('pmo_project_draft_update',args))==updated
            ch=await call('pmo_charter_save',{'projectId':p['projectId'],'fieldsCharter':{'valueProp':'减少重复录入','roughCost':'502.10'},'expectedRevision':updated['revision'],'idempotencyKey':uuid4().hex})
            assert ch['roughCost']=='502.10' and (await call('pmo_project_get',{'projectId':p['projectId']}))['charterSummary']==ch
            m=await call('pmo_milestone_create',{'projectId':p['projectId'],'name':'试运行','plannedDate':'2026-12-15','idempotencyKey':uuid4().hex})
            m2=await call('pmo_milestone_draft_update',{'milestoneId':m['milestoneId'],'expectedRevision':1,'fieldsMilestone':{'name':'交付验证','isKey':True},'idempotencyKey':uuid4().hex})
            assert m2['revision']==2 and (await call('pmo_milestone_list',{'projectId':p['projectId']}))['items']==[m2]
            f=await call('pmo_milestone_forecast',{'milestoneId':m['milestoneId'],'newDate':'2026-12-16','reason':'Supplier update','expectedRevision':2,'idempotencyKey':uuid4().hex})
            assert f['revision']==3 and f['plannedDate']==m['plannedDate'] and f['forecastDate']=='2026-12-16'
            assert (await call('pmo_alert_list',{'projectId':p['projectId']}))['items']==[]
            req=await call('pmo_requirement_create',{'projectId':p['projectId'],'desc':'导出审批明细清单','priority':'P1','acceptanceCriteria':'完整保留所有列名','idempotencyKey':uuid4().hex})
            revised=await call('pmo_requirement_draft_update',{'requirementId':req['requirementId'],'fieldsRequirement':{'priority':'P0'},'expectedRevision':1,'idempotencyKey':uuid4().hex})
            assert revised['priority']=='P0' and revised['revision']==2
            assert (await call('pmo_requirement_list',{'projectId':p['projectId'],'filters':{'keyword':'导出'}}))['items']==[revised]
            bad=await session.call_tool('pmo_project_list',{'pageSize':101}); assert bad.isError
            with engine.connect() as c: nominee=c.scalar(sa.select(db.projects.c.initiator_id).where(db.projects.c.id==p['projectId']))
            proposal={'projectId':p['projectId'],'personId':str(nominee),'role':'pm','expectedRevision':2,'idempotencyKey':uuid4().hex}
            invitation=await call('pmo_role_assignment_propose',proposal)
            assert invitation['status']=='proposed' and (await call('pmo_role_assignment_propose',proposal))==invitation
            with engine.begin() as c: c.execute(db.agent_credentials.update().where(db.agent_credentials.c.id==ident).values(revoked_at=sa.func.now()))
            denied=await session.call_tool('pmo_project_list',{}); assert denied.isError and denied.structuredContent['error']['code']=='UNAUTHORIZED'
    asyncio.run(scenario())

def test_failed_audit_rolls_back_project_and_grant(live,actor):
    engine,api,_,_=live; token,ident=actor(); key=uuid4().hex
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER injected_failure BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION pmo_append_only()')
    try:
        response=api.post('/api/v1/projects',headers={'Authorization':'Bearer '+token,'Idempotency-Key':key},json={'code':'FAIL-AUDIT','name':'Rollback'})
        assert response.status_code==500
        assert response.json()=={'code':'DATABASE_FAILURE','message':'DATABASE_FAILURE','requestId':response.headers['X-Request-ID']}
        with engine.connect() as c:
            assert c.scalar(sa.select(db.projects.c.id).where(db.projects.c.code=='FAIL-AUDIT')) is None
            assert c.scalar(sa.select(db.agent_credentials.c.allowed_project_ids).where(db.agent_credentials.c.id==ident))==[]
            assert c.scalar(sa.select(db.idempotency_keys.c.id).where(db.idempotency_keys.c.actor_id==ident)) is None
    finally:
        with engine.begin() as c: c.exec_driver_sql('DROP TRIGGER injected_failure ON audit_logs')

def test_two_credentials_cannot_overwrite_new_revision(live,actor):
    engine,api,_,_=live; token,ident=actor()
    headers={'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex}
    project=api.post('/api/v1/projects',headers=headers,json={'code':'LOCK-1','name':'Race'}).json()['data']
    from hashlib import sha256
    from secrets import token_urlsafe
    second=token_urlsafe(32); digest=sha256(second.encode()).hexdigest()
    with engine.begin() as c:
        src=dict(c.execute(sa.select(db.agent_credentials).where(db.agent_credentials.c.id==ident)).mappings().one())
        src.update(id=uuid4(),token_hash=digest,token_fingerprint=digest[:12]); c.execute(db.agent_credentials.insert().values(**src))
    path='/api/v1/projects/'+project['projectId']
    def update(pair):
        index,secret=pair
        return api.patch(path,headers={'Authorization':'Bearer '+secret,'Idempotency-Key':uuid4().hex,'If-Match':'1'},json={'fields':{'name':f'Winner {index}'}})
    with ThreadPoolExecutor(2) as pool: responses=list(pool.map(update,enumerate([token,second])))
    assert sorted(r.status_code for r in responses)==[200,409]
    winner=next(r.json()['data'] for r in responses if r.is_success)
    assert next(r for r in responses if not r.is_success).json()['code']=='VERSION_CONFLICT'
    assert api.get(path,headers=headers).json()['data']['project']==winner
    assert winner['revision']==2
    for fields in [{'stage':'closed'},{'name':None},{}]:
        assert api.patch(path,headers={**headers,'If-Match':'2'},json={'fields':fields}).status_code==422
