import asyncio
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import jsonschema
import pytest
import sqlalchemy as sa
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pmo import schema_v1 as db
from pmo.gates import ENTRY, REVIEW
from pmo.mcp_server import tools
from pmo.project_lifecycle import APPROVED, DIRECT
from pmo.reviews import decision_stage, withdrawal_stage
from pmo.tool_routes import ROUTES
from scripts.schema_erd import render as render_erd


def test_runtime_has_all_baseline_methods_paths_and_tool_mappings(live):
    _, api, _, _ = live
    catalog = yaml.safe_load(next(Path('docs/spec').glob('12_OpenAPI*')).read_text(encoding='utf-8'))

    def operations(paths):
        return {spec['operationId']:(method,path) for path,item in paths.items() for method,spec in item.items()
                if isinstance(spec,dict) and 'operationId' in spec}

    expected = operations(catalog['paths'])
    actual = operations(api.get('/openapi.json').json()['paths'])
    assert len(expected)==95 and {op:actual.get(op) for op in expected}==expected
    assert len(tools)==40 and set(tools)==set(ROUTES) and set(tools)<=actual.keys()

def test_postgres_erd_matches_migrated_relationships(live):
    with live[0].connect() as conn:
        conn.exec_driver_sql('SET TRANSACTION READ ONLY')
        assert render_erd(conn) == Path('docs/erd.md').read_text(encoding='utf-8')


def test_runtime_stage_catalog_matches_frozen_graph(live):
    rules = yaml.safe_load(next(Path('docs/spec').glob('02_*.yaml')).read_text(encoding='utf-8'))
    states = rules['states']
    expected = {(origin, target) for origin, targets in rules['allowed_transitions'].items() for target in targets}
    assert len(states) == 13 and len(expected) == 23

    gates = {(start, REVIEW[gate]) for gate, start in ENTRY.items()}
    gates |= {(REVIEW[gate], decision_stage(gate, verdict)) for gate in ENTRY for verdict in rules['review_verdicts']}
    gates |= {(REVIEW[gate], withdrawal_stage(gate)) for gate in ENTRY}
    lifecycle = DIRECT | APPROVED | {('rejected', 'drafting')}
    assert gates.isdisjoint(lifecycle) and gates | lifecycle == expected

    # Copy only the real PostgreSQL column/check constraints, not application rows, FKs or audit triggers.
    # Exercise all known state pairs for each receipt path on this disposable relation.
    with live[0].begin() as conn:
        conn.exec_driver_sql('CREATE TEMP TABLE pmo_edge_probe (LIKE project_stage_transitions INCLUDING CONSTRAINTS) ON COMMIT DROP')
        insert = sa.text('''INSERT INTO pmo_edge_probe
            (project_id, from_revision, to_revision, from_stage, to_stage, review_id, approval_request_id, actor_id,
             actor_type, request_id, transitioned_at)
            VALUES (:id, 1, 2, :origin, :target, :review, :approval, :id, 'human', :id, now())''')
        ref = uuid4()
        for review, approval, permitted in ((ref, None, gates), (None, ref, lifecycle)):
            observed = set()
            for origin in states:
                for target in states:
                    with conn.begin_nested() as savepoint:
                        try:
                            conn.execute(insert, dict(id=ref, origin=origin, target=target, review=review, approval=approval))
                            observed.add((origin, target))
                        except sa.exc.IntegrityError as e:
                            assert e.orig.sqlstate == '23514'  # CHECK violation, not an unrelated failure.
                        finally:
                            savepoint.rollback()
            assert observed == permitted
        for review, approval in ((None, None), (ref, ref)):
            with pytest.raises(sa.exc.IntegrityError, match='ck_project_stage_edges'), conn.begin_nested():
                conn.execute(insert, dict(id=ref, origin='drafting', target='charter_review', review=review, approval=approval))


def test_postgres_contract_and_downgrade_guard(live,actor):
    actor()
    engine,_,_,env=live
    inspector=sa.inspect(engine)
    assert len(db.metadata.tables)==33
    assert sum(len(t.c) for t in db.metadata.tables.values())==421
    extras = {'reports': {'content_hash', 'submitted_by', 'confirmed_by', 'confirmed_at', 'weekly_basis'}, 'user_accounts': {'status_revision'},
              'health_appeals': {'appealed_at'}, 'rule_events': {'input_snapshot'}, 'audit_logs': {'source_refs'}}
    for name, table in db.metadata.tables.items():
        assert {column['name'] for column in inspector.get_columns(name)} == set(table.c.keys()) | extras.get(name, set())
    result=subprocess.run([sys.executable,'-m','alembic','downgrade','base'],env=env,capture_output=True,text=True)
    assert result.returncode and any(f'RuntimeError: {reason}' in result.stderr for reason in (
        'Refusing to discard written audit reference history',
        'Refusing destructive downgrade', 'Refusing to discard written acceptance history',
        'Refusing to discard report receipts', 'Refusing to discard review termination history',
        'Refusing to discard health and rule scan history', 'Refusing to discard Fact source and verification history',
        'Refusing to discard report analysis runs', 'Refusing to discard account lifecycle history',
        'Refusing to discard weekly report source receipts', 'Refusing to discard requirement dispute history'))
    head = ScriptDirectory.from_config(Config('alembic.ini')).get_current_head()
    with engine.connect() as c: assert c.scalar(sa.text('select version_num from alembic_version'))==head

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
    cost_ops=('pmo_cost_summary','pmo_cost_budget_draft_save','pmo_cost_forecast_save','pmo_cost_posting_propose','pmo_preapproval_spending_list','pmo_milestone_forecast','pmo_alert_list','pmo_requirement_deliver','pmo_requirement_change_history','pmo_change_propose')
    engine,_,port,_=live; token,ident=actor(extra_actions=(*cost_ops,'pmo_requirement_create','pmo_requirement_list','pmo_requirement_draft_update','pmo_charter_save','pmo_role_assignment_propose','pmo_stakeholder_create','pmo_risk_create','pmo_risk_list','pmo_risk_update'))
    async def scenario():
        env={**{k:os.environ[k] for k in ('SYSTEMROOT','PATH','TEMP','TMP') if k in os.environ},'PMO_API_PORT':str(port),'PMO_AGENT_TOKEN':token,'PYTHONUTF8':'1'}
        params=StdioServerParameters(command=sys.executable,args=['-m','pmo.mcp_server'],env=env)
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize(); tools={t.name:t for t in (await session.list_tools()).tools}
            assert set(tools)=={*cost_ops,'pmo_project_create','pmo_project_list','pmo_project_get','pmo_project_draft_update','pmo_milestone_create','pmo_milestone_list','pmo_milestone_draft_update','pmo_requirement_create','pmo_requirement_list','pmo_requirement_draft_update','pmo_charter_save','pmo_role_assignment_propose','pmo_stakeholder_create','pmo_risk_create','pmo_risk_list','pmo_risk_update','pmo_gate_check','pmo_review_prepare','pmo_review_list','pmo_review_submit','pmo_decision_get','pmo_transition_request','pmo_lessons_search','pmo_task_list','pmo_task_update','pmo_report_list','pmo_report_get','pmo_report_submit','pmo_health_get','pmo_fact_propose'}
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
            assert (await call('pmo_requirement_change_history',{'requirementId':req['requirementId']}))['total']==0
            with engine.begin() as c:
                author=c.scalar(sa.select(db.projects.c.initiator_id).where(db.projects.c.id==p['projectId']))
                change=c.scalar(db.change_entries.insert().values(project_id=p['projectId'],target_id=req['requirementId'],target_type='requirement',created_by=author,
                    requested_by=author,approved_by=author,base_revision=2,status='applied',before={'priority':'P1'},after={'priority':'P0'},reason='Synthetic past approval').returning(db.change_entries.c.id))
            event=(await call('pmo_requirement_change_history',{'requirementId':req['requirementId']}))['items'][0]
            assert event['changeId']==str(change) and event['approvedBy']==str(author) and event['before']=={'priority':'P1'} and 'reason' not in event
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
