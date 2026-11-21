"""Run explicitly after npm ci: pytest tests/browser_case.py."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
from base64 import b64encode
from io import BytesIO
from PIL import Image
import time
from uuid import uuid4
import httpx
import sqlalchemy as sa
from argon2 import PasswordHasher
from pmo import schema_v1 as db
from test_lifecycle import ANSWERS, close_materials, complete_milestones, execution_fixture, stage
from test_reviews import team as team

def test_browser_draft_workflow(live,actor):
    engine,_,api_port,_=live
    _,aid=actor(); password=secrets.token_urlsafe(32)
    with engine.begin() as c:
        uid=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==aid))
        c.execute(db.user_roles.insert().values(user_id=uid,role='system_admin'))
        c.execute(db.user_roles.insert().values(user_id=uid,role='delegator'))
        username=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==uid).values(password_hash=PasswordHasher().hash(password)).returning(db.user_accounts.c.username))
        org=c.scalar(sa.select(db.persons.c.org_id).where(db.persons.c.user_id==uid))
        accounts={}
        for role in ['issuer','nominee','baseline_pm','baseline_client','gate_reviewer']:
            name=uuid4().hex; person=uuid4(); secret=secrets.token_urlsafe(32)
            user=c.scalar(db.user_accounts.insert().values(username=name,password_hash=PasswordHasher().hash(secret),is_active=True).returning(db.user_accounts.c.id))
            c.execute(db.persons.insert().values(id=person,created_by=person,org_id=org,user_id=user,name=role,is_active=True))
            accounts.update({f'PMO_TEST_{role.upper()}':name,f'PMO_TEST_{role.upper()}_PASSWORD':secret,f'PMO_TEST_{role.upper()}_ID':str(person)})
            if role=='issuer':
                c.execute(sa.text("INSERT INTO appointment_authorities VALUES(:org,:person,'pm','SYNTHETIC E2E',now()+interval '1 hour',NULL)"),dict(person=person,org=org))
            if role=='nominee': c.execute(db.user_roles.insert().values(user_id=user,role='finance_verifier'))
        pm=accounts['PMO_TEST_BASELINE_PM_ID']; client=accounts['PMO_TEST_BASELINE_CLIENT_ID']
        review_pid=c.scalar(db.projects.insert().values(code='E2E-REVIEW',name='立项送审演练',org_id=org,stage='drafting',
            pm_id=accounts['PMO_TEST_NOMINEE_ID'],sponsor_id=accounts['PMO_TEST_ISSUER_ID'],created_by=pm,initiator_id=pm,revision=1).returning(db.projects.c.id))
        for person,role in ((accounts['PMO_TEST_GATE_REVIEWER_ID'],'reviewer'),(accounts['PMO_TEST_NOMINEE_ID'],'pm'),(accounts['PMO_TEST_ISSUER_ID'],'sponsor')):
            c.execute(db.project_members.insert().values(project_id=review_pid,person_id=person,assigned_by=pm,role=role,assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(db.charters.insert().values(project_id=review_pid,created_by=pm,value_prop='降低重复录入',target_client='试点客户',
            attractiveness='已有演练安排',expected_benefit='可追踪交付',rough_cost=100,rough_effort=10,rough_duration_days=15,
            top_risks=[{'statement':'演练资料不全','response':'对照清单复核'}]))
        pid=c.scalar(db.projects.insert().values(code='E2E-BASE',name='基线签署演练',org_id=org,stage='designing',pm_id=pm,created_by=pm,initiator_id=pm,revision=1).returning(db.projects.c.id))
        for person,role in ((pm,'pm'),(client,'client_representative')):
            c.execute(db.project_members.insert().values(project_id=pid,person_id=person,assigned_by=pm,role=role,assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(db.stakeholders.insert().values(project_id=pid,created_by=pm,name='测试客户代表',type='customer',linked_person_id=client))
        c.execute(db.requirements.insert().values(project_id=pid,created_by=pm,desc='导出归档清单',priority='P0',acceptance_criteria='导出保留全部来源编号',planned_confirm_at='2027-01-31',status='baseline',baseline_version=0))
        evidence_pid=c.scalar(db.projects.insert().values(code='E2E-FILE',name='完工凭据演练',org_id=org,stage='designing',pm_id=pm,created_by=pm,initiator_id=pm,revision=1).returning(db.projects.c.id))
        for person,role in ((pm,'pm'),(client,'reviewer'),(client,'client_representative')):
            c.execute(db.project_members.insert().values(project_id=evidence_pid,person_id=person,assigned_by=pm,role=role,assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(db.milestones.insert().values(project_id=evidence_pid,created_by=pm,name='试点验收',ordinal=1,is_key=True,planned_date='2020-03-09'))
        c.execute(db.stakeholders.insert().values(project_id=evidence_pid,created_by=pm,name='测试客户代表',type='customer',linked_person_id=client))
        req=c.scalar(db.requirements.insert().values(project_id=evidence_pid,created_by=pm,desc='完成文件交付',acceptance_criteria='文件中列明验收记录',priority='P0',planned_confirm_at='2027-01-31',status='baseline',baseline_version=0).returning(db.requirements.c.id))
        accounts['PMO_TEST_REQUIREMENT_ID']=str(req)
        c.execute(sa.text("INSERT INTO change_approval_grants VALUES(:p,:u,'requirement','browser fixture',now()+interval '1 hour',NULL)"),dict(p=evidence_pid,u=client))
        c.execute(db.change_entries.insert().values(project_id=evidence_pid,created_by=pm,requested_by=pm,target_type='requirement',target_id=req,base_revision=1,
            status='withdrawn',before={'priority':'P0'},after={'priority':'P1'},reason='验收样例：调整优先级已撤回'))
        png=BytesIO(); Image.new('RGB',(6,6),(120,90,66)).save(png,'PNG'); accounts['PMO_TEST_PNG']=b64encode(png.getvalue()).decode('ascii')
    for role,sign_as in [('BASELINE_PM','pm'),('BASELINE_CLIENT','client_representative')]:
        with httpx.Client(base_url=f'http://127.0.0.1:{api_port}/api/v1/',trust_env=False) as signer:
            signer.headers['X-CSRF-Token']=signer.post('auth/login',json={'username':accounts['PMO_TEST_'+role],'password':accounts['PMO_TEST_'+role+'_PASSWORD']}).json()['csrfToken']
            path=f'projects/{evidence_pid}/requirement-baseline'; state=signer.get(path).json()['data']
            signer.post(path+'/sign',headers={'If-Match':str(state['revision']),'Idempotency-Key':uuid4().hex},json={'contentHash':state['contentHash'],'role':sign_as,'reason':'Browser fixture baseline'}).raise_for_status()
    with engine.begin() as c: c.execute(db.projects.update().where(db.projects.c.id==evidence_pid).values(stage='executing'))
    with vite(api_port) as (root, url):
        run_browser(root, url, 'draft', {**accounts,'PMO_TEST_USER':username,'PMO_TEST_PASSWORD':password})


@contextmanager
def vite(api_port):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    root=Path(__file__).resolve().parents[1]/'web'
    url=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--port',str(port)],cwd=root,env={**os.environ,'PMO_API_PORT':str(api_port)})
    try:
        with httpx.Client(trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get(url).status_code==200: break
                except httpx.ConnectError: pass
                time.sleep(.15)
            else: raise RuntimeError('Vite startup failed')
        yield root, url
    finally: proc.terminate(); proc.wait(timeout=10)


def run_browser(root, url, case, values):
    subprocess.run(['node', f'e2e/{case}.mjs'], cwd=root, check=True, timeout=120,
                   env={**os.environ, **values, 'PMO_BROWSER_URL':url})


def browser_sessions(users, *roles):
    return {role: {'cookie': users[role].cookies['pmo_session'], 'csrf': users[role].headers['X-CSRF-Token']} for role in roles}


def test_browser_health_snapshots_and_independent_appeal(live, team):
    from datetime import datetime
    from pmo.health_calculation import TZ, week_key
    from test_health import prepare, sample
    from test_reports import body, sections
    from test_reviews import data, headers

    _, _, users, _ = team
    project, _ = prepare(live, team)
    pm = users['pm']
    snapshot = sample(pm, project)
    assert snapshot['health'] == 'green' and snapshot['publication']=='official'
    milestone = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    data(pm.post(project + '/reports',headers=headers(),json=body(period=week_key(datetime.now(TZ).date()),
        structuredFields=sections(milestone_progress=[{'milestoneId':milestone,'claim':'progress'}]))))
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'health', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm','reviewer'))})


def test_browser_delivery_through_archive(live, team):
    _, _, users, _ = team
    pm, reviewer, customer = (users[k] for k in ('pm', 'reviewer', 'client_representative'))
    project, requirement, risk = execution_fixture(live, team)
    complete_milestones(pm, reviewer, project)
    # Before any UI stage write, prepare the remaining independent materials using HTTP.
    # The customer signs only after the UI has reached the delivering stage.
    sessions = browser_sessions(users, 'pm', 'reviewer', 'outsider')
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(sessions), 'PMO_TEST_PHASE':'delivery'})
        assert stage(pm, project)['stage'] == 'delivering'
        _, risk_proof, cost_proof = close_materials(pm, reviewer, customer, project, requirement, risk)
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(sessions), 'PMO_TEST_PHASE':'closing',
            'PMO_TEST_RISK_PROOF':risk_proof['evidenceRef'], 'PMO_TEST_COST_PROOF':cost_proof['evidenceRef'],
            'PMO_TEST_ANSWERS':json.dumps(ANSWERS)})
    assert stage(pm, project)['stage'] == 'archived'


def test_browser_customer_exception_and_conditional_decision(live, team):
    from test_requirement_exceptions import deliver_with_exception_material

    _, _, users, _ = team
    project, prepare = deliver_with_exception_material(live, team)
    _, command = prepare()
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm', 'reviewer', 'client_representative')),
            'PMO_TEST_PHASE':'exception', 'PMO_TEST_REQUIREMENT_EXCEPTION':json.dumps(command)})
    assert stage(users['pm'], project)['stage'] == 'closed'


def test_browser_tasks_and_reports(live, team):
    _, _, users, _ = team
    project, _, _ = execution_fixture(live, team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm')),
            'PMO_TEST_PHASE':'tasks_reports'})
    assert stage(users['pm'], project)['stage'] == 'executing'


def test_browser_independent_termination_confirmation(live, team):
    from test_lifecycle import start_feasibility

    _, _, users, _ = team
    project, _ = start_feasibility(live, team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm', 'initiator', 'sponsor')),
            'PMO_TEST_PHASE':'termination'})
    assert stage(users['pm'], project)['stage'] == 'terminated'
