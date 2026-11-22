"""Run explicitly after npm ci: pytest tests/browser_case.py."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from base64 import b64encode
from io import BytesIO
from PIL import Image
import time
from uuid import uuid4
import httpx
import pytest
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
        c.execute(db.change_entries.insert().values(project_id=evidence_pid,created_by=pm,requested_by=pm,target_type='requirement',target_id=req,base_revision=1,
            status='withdrawn',before={'sourceNote':'历史导入原值'},after={},reason='验收样例：存档旧记录'))
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


def test_browser_and_stdio_same_snapshot_and_private_resources(live, team):
    from hashlib import sha256
    from test_evidence import binary
    from test_health import code, prepare, sample
    from test_lifecycle import upload
    from test_reference_vectors_permissions import bearer, denied_audit, mcp_calls
    from test_reports import body
    from test_reviews import data, headers, issue

    engine, api, port, _ = live
    pid, people, users, _ = team
    pm, reviewer, outsider = (users[role] for role in ('pm', 'reviewer', 'outsider'))
    project, _ = prepare(live, team)
    report = data(pm.post(project + '/reports', headers=headers(), json=body(narrative='Restricted synthetic report.')))
    proof = upload(pm, reviewer, project, 'report', report['reportId'])
    snapshot = sample(pm, project)
    assert snapshot['publication'] == 'official' and snapshot['snapshotId']
    other = data(outsider.post('projects', headers=headers(), json={'code': 'T02-SCOPE', 'name': 'Authorized other draft'}))
    with engine.begin() as c:
        account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == people['outsider']))
        c.execute(db.user_roles.insert().values(user_id=account, role='delegator'))
    actions = ['pmo_project_get', 'pmo_health_get', 'pmo_report_get', 'pmo_project_list']
    allowed_token = issue(pm, pid, actions)
    denied_token = issue(outsider, other['projectId'], actions)
    read_report = ('pmo_report_get', {'reportId': report['reportId']})
    read_project = ('pmo_project_get', {'projectId': pid})
    read_snapshot = ('pmo_health_get', {'projectId': pid, 'asOf': snapshot['asOf']})
    details, health, visible = mcp_calls(live, allowed_token, [read_project, read_snapshot, read_report])
    assert not any(item.isError for item in (details, health, visible))
    detail_data, health_data = (item.structuredContent['data'] for item in (details, health))
    assert detail_data['project'] == stage(pm, project)
    assert health_data['historical'] and health_data['snapshotId'] == snapshot['snapshotId']
    assert health_data == {key: data_value for key, data_value in data(pm.get(project+'/health',
        params={'asOf': snapshot['asOf']})).items() if key in health_data}
    assert visible.structuredContent['data']['reportId'] == report['reportId']
    listing, *denied = mcp_calls(live, denied_token, [('pmo_project_list', {}), read_project, read_snapshot, read_report])
    assert listing.structuredContent['data']['items'] == [other]
    assert all(item.isError and item.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND' for item in denied)
    evidence_path = f"evidence/{proof['evidenceId']}?download=true"
    assert sha256(pm.get(evidence_path).raise_for_status().content).hexdigest() == proof['sha256']
    for path, status, error, operation in ((project, 404, 'RESOURCE_NOT_FOUND', 'pmo_project_get'),
            (f"reports/{report['reportId']}", 404, 'RESOURCE_NOT_FOUND', 'pmo_report_get'),
            (evidence_path, 403, 'FORBIDDEN', 'human_evidence_get')):
        refused = api.get('/api/v1/' + path, headers=bearer(denied_token))
        code(refused, status, error)
        denied_audit(engine, refused, operation)
        assert proof['sha256'] not in refused.text and report['narrative'] not in refused.text
        code(outsider.get(path), 404, 'RESOURCE_NOT_FOUND')
    with vite(port) as (root, url):
        run_browser(root, url, 'dual-entry', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm', 'outsider')),
            'PMO_TEST_PACKET': json.dumps({'project': detail_data['project'], 'health': health_data,
                'reportId': report['reportId'], 'evidenceId': proof['evidenceId'], 'other': other,
                'restricted': report['narrative']})})


def test_browser_read_only_project_assistant_without_model(live, team):
    execution_fixture(live, team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'assistant', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(team[2], 'pm'))})


def test_browser_separates_model_previews_from_official_results(live, team, tmp_path):
    from test_assistant_preview import enabled_api

    execution_fixture(live, team)
    with enabled_api(live, tmp_path, {'reviewDraft': [{'sourceId': 's1', 'lens': 'test_success'}]},
                     {'answer': [{'sourceId': 's24'}]}, {'answer': [{'sourceId': 's24'}]}) as (api, calls):
        with vite(api.base_url.port) as (root, url):
            run_browser(root, url, 'assistant-model', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(team[2], 'pm'))})
    assert [call['mode'] for call in calls] == ['charter_review_assistant', 'ledger_qa', 'ledger_qa']


def test_browser_customer_dispute_has_independent_and_persistent_history(live, team):
    _, _, users, _ = team
    execution_fixture(live, team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'requirement-disputes', {'PMO_TEST_SESSIONS': json.dumps(
            browser_sessions(users, 'pm', 'client_representative'))})


def test_browser_lr08_monthly_uses_deadline(live, team):
    from datetime import timedelta

    from test_health import close_for_lr08, scan_lr08

    _, due = close_for_lr08(live, team)
    resumed = due + timedelta(days=40, hours=10)
    scan_lr08(live[0], team[0], resumed)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lr08-monthly', {
            'PMO_TEST_SESSIONS': json.dumps(browser_sessions(team[2], 'sponsor')),
            'PMO_TEST_LR08_MONTHS': json.dumps([due.date().isoformat(), resumed.strftime('%Y-%m')]),
        })


def test_browser_weekly_health_explains_single_yellow(live, team, tmp_path):
    from test_weekly_health import yellow_sample

    _, _, saved = yellow_sample(live, team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'weekly-health', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(team[2], 'pm', 'outsider')),
            'PMO_TEST_SNAPSHOT': saved['snapshotId'], 'PMO_TEST_SCREENSHOTS': str(tmp_path)})


def test_browser_health_snapshots_and_independent_appeal(live, team):
    from datetime import datetime
    from pmo.health_calculation import TZ, week_key
    from test_health import prepare, sample
    from test_reports import body, sections
    from test_reviews import data, headers

    _, _, users, _ = team
    project, _ = prepare(live, team, effort_budget='0')
    pm = users['pm']
    pending = data(pm.get(project + '/health'))
    data(pm.post(project + '/health/zero-budget', headers=headers(pending['projectRevision']), json={
        'type': 'effort', 'baselineHash': pending['zeroBudgetHashes']['effort'],
        'reason': 'This synthetic project has an explicitly approved zero effort baseline.'}))
    snapshot = sample(pm, project)
    assert snapshot['health'] == 'green' and snapshot['publication']=='official' and snapshot['effortVarianceRate']=='UNAVAILABLE'
    milestone = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    data(pm.post(project + '/reports',headers=headers(),json=body(period=week_key(datetime.now(TZ).date()),
        structuredFields=sections(milestone_progress=[{'milestoneId':milestone,'claim':'progress'}]))))
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'health', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm','reviewer'))})


def test_browser_report_fact_conflict_resolution(live, team):
    from test_facts import submit

    _, _, users, _ = team
    project, _, _ = execution_fixture(live, team)
    submit(users['pm'], project + '/reports')
    submit(users['pm'], project + '/reports', amount='3.80', period='2025-W02')
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'facts', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm','reviewer'))})


def test_browser_organization_account_invite_and_suspend(live, team):
    _, _, users, _ = team
    me = users['pm'].get('auth/me')
    assert me.is_success
    with live[0].begin() as c:
        c.execute(db.user_roles.insert().values(user_id=me.json()['accountId'], role='system_admin'))
    name = 'web-'+uuid4().hex
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'organization', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm')),
            'PMO_TEST_USERNAME':name, 'PMO_TEST_MEMBER_PASSWORD':secrets.token_urlsafe(24)})
    account, = [row for row in users['pm'].get('admin/users').json()['data']['items'] if row['username']==name]
    assert account['revision']==2 and not account['isActive'] and not account['isSystemAdmin']


@pytest.mark.parametrize('model_on', [False, True], ids=['off', 'simulated'])
def test_browser_report_analysis(live, team, model_on, tmp_path):
    from test_database_roles import serve
    from test_facts import submit
    from test_report_analysis import answer, gateway, NARRATIVE
    from test_reports import data

    engine, _, port, env = live
    _, _, users, _ = team
    project, _, _ = execution_fixture(live, team)
    report = submit(users['pm'], project + '/reports', narrative=NARRATIVE)
    params = {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm')),
              'PMO_TEST_MODEL':str(int(model_on))}
    if model_on:
        with gateway(answer()) as (model_url, calls), serve(engine.url, tmp_path,
                extra_env={'PMO_REPORT_ANALYSIS':'1'}) as api, vite(api.base_url.port) as (root, url):
            process = subprocess.Popen([sys.executable, '-m', 'pmo.report_analysis_worker', '--watch'],
                env={**env,'PMO_REPORT_ANALYSIS':'1','PMO_REPORT_MODEL_URL':model_url,'PMO_REPORT_MODEL_TOKEN':''})
            try:
                run_browser(root, url, 'analysis', params)
            finally:
                process.terminate(); process.wait(timeout=10)
            assert len(calls)==1
    else:
        with vite(port) as (root, url):
            run_browser(root, url, 'analysis', params)
        task, = data(users['pm'].get(project + '/tasks'))['items']
        assert task['sourceType']=='report_analysis' and task['sourceId']==report['reportId']


def test_browser_archived_report_history_is_read_only(live, team):
    from test_facts import submit
    from test_report_analysis import history, request
    from test_reports import data, headers

    pid, _, users, _ = team
    pm = users['pm']
    project, _, _ = execution_fixture(live, team)

    def publish(narrative):
        report = submit(pm, project + '/reports', narrative=narrative)
        return data(pm.post(f"reports/{report['reportId']}/confirm", headers=headers(report['revision']), json={}))

    original = publish('Synthetic historical publication')
    for _ in range(4):
        assert data(request(pm, original, forceNewRun=True))['status'] == 'manual'
    publish('Synthetic current publication')
    with live[0].begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='archived'))
    read_back = history(pm, original)
    assert read_back['total'] == 4 and not read_back['canAnalyze']
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'analysis', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm')),
            'PMO_TEST_ARCHIVED_HISTORY':'1'})


def test_browser_drafting_through_archive_without_model(live, team):
    from datetime import datetime, timedelta

    from pmo.reports import TZ
    from test_lifecycle import grant_closure_roles
    from test_reviews import data

    pid, _, users, _ = team
    pm, reviewer, customer = (users[k] for k in ('pm', 'reviewer', 'client_representative'))
    project = f'projects/{pid}'
    assert live[3]['PMO_REPORT_ANALYSIS'] == '0' and stage(pm, project)['stage'] == 'drafting'
    grant_closure_roles(live, team)
    sessions = browser_sessions(users, 'pm', 'initiator', 'reviewer', 'client_representative', 'outsider')
    with vite(live[2]) as (root, url):
        today = datetime.now(TZ).date()
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(sessions), 'PMO_TEST_PHASE':'launch',
            'PMO_TEST_PLANNED_DATES':json.dumps([(today + timedelta(days=days)).isoformat() for days in (7, 8, 9)])})
        assert stage(pm, project)['stage'] == 'executing'
        requirement, = data(pm.get(project + '/requirements'))['items']
        risk, = data(pm.get(project + '/risks'))['items']
        assert requirement['baselineVersion'] == 1 and risk['isBlocking']
        # Independent evidence and actuals are prepared through the public HTTP services.
        complete_milestones(pm, reviewer, project)
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(sessions), 'PMO_TEST_PHASE':'delivery'})
        assert stage(pm, project)['stage'] == 'delivering'
        _, risk_proof, cost_proof = close_materials(pm, reviewer, customer, project, requirement['requirementId'], risk['riskId'])
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(sessions), 'PMO_TEST_PHASE':'closing',
            'PMO_TEST_RISK_PROOF':risk_proof['evidenceRef'], 'PMO_TEST_COST_PROOF':cost_proof['evidenceRef'],
            'PMO_TEST_ANSWERS':json.dumps(ANSWERS)})
    assert stage(pm, project)['stage'] == 'archived'
    with live[0].connect() as c:
        steps = c.execute(sa.text('SELECT to_stage FROM project_stage_transitions WHERE project_id=:p ORDER BY to_revision'),
                          {'p': pid}).scalars().all()
    assert steps == ['charter_review', 'designing', 'feasibility_review', 'executing', 'delivering',
                     'closing_review', 'closed', 'retrospective', 'archived']


def test_browser_closing_withdrawal_rework_and_fresh_submission(live, team):
    from test_lifecycle import gate
    from test_requirement_exceptions import deliver_with_exception_material
    from test_reviews import bench

    _, _, users, _ = team
    pm = users['pm']
    project, _ = deliver_with_exception_material(live, team, confirm=True)
    closing = gate(pm, project, 'closing')
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm')),
            'PMO_TEST_PHASE':'withdrawal', 'PMO_TEST_REVIEW_ID':closing.split('/')[-1]})
    assert stage(pm, project)['stage'] == 'closing_review'
    assert bench(pm, closing)['review']['verdict'] == 'withdrawn'


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


def test_browser_model_free_weekly_draft_and_publish(live, team):
    from test_reports import data

    _, _, users, _ = team
    project, _, _ = execution_fixture(live, team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'weekly', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users,'pm','reviewer'))})
    state, = data(users['pm'].get(project+'/reports', params={'reportType':'weekly'}))['items']
    assert state['isCurrentConfirmed'] and state['revision']==4


def test_browser_independent_requirement_addition(live, team):
    from test_lifecycle import begin_execution
    from test_requirement_additions import grant_reviewers
    from test_reports import data

    _, _, users, _ = team
    project, _, _ = begin_execution(live,team)
    grant_reviewers(live,team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'requirement-additions', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users,'pm','reviewer'))})
    quality=data(users['pm'].get(project+'/requirement-metrics'))
    assert (quality['initialCount'],quality['activeCount'],quality['approvedChangeCount'])==(1,2,1)


def test_browser_independent_termination_confirmation(live, team):
    from test_lifecycle import start_feasibility

    _, _, users, _ = team
    project, _ = start_feasibility(live, team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm', 'initiator', 'sponsor')),
            'PMO_TEST_PHASE':'termination'})
    assert stage(users['pm'], project)['stage'] == 'terminated'


def test_browser_formal_transition_authority_without_model(live, team):
    from test_lifecycle import begin_execution
    from test_reviews import member
    from test_transition_authority import grant

    pid, people, users, _ = team
    project, _, _ = begin_execution(live, team)
    with live[0].begin() as c:
        member(c, pid, people['outsider'], people['initiator'], 'member')
        refs = {dest: f'transition-grant:{grant(c, pid, people["outsider"], (origin, dest), source=source)}'
                for origin, dest, source in [('executing', 'on_hold', 'SYNTHETIC-HOLD'),
                    ('on_hold', 'executing', 'SYNTHETIC-RESUME'), ('executing', 'terminated', 'SYNTHETIC-TERMINATE')]}
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm', 'outsider', 'reviewer', 'sponsor')),
            'PMO_TEST_PHASE': 'formal-authority', 'PMO_TEST_AUTHORITY_REFS': json.dumps(refs)})
    assert stage(users['pm'], project)['stage'] == 'terminated'


def test_browser_paginates_stage_receipts_without_changing_project_version(live, team):
    from test_lifecycle import begin_execution, judgment
    from test_reviews import data, headers

    pid, _, users, _ = team
    pm, sponsor = users['pm'], users['sponsor']
    project, _, _ = begin_execution(live, team)
    revision = stage(pm, project)['revision']
    for index in range(1, 22):
        request = data(pm.post(project + '/transition-requests', headers=headers(revision),
            json={'toStage': 'on_hold', 'reason': f'Synthetic history request {index:02}'}))
        assert data(judgment(sponsor, request, decision='reject'))['status'] == 'rejected'
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm')),
            'PMO_TEST_PHASE': 'approval-pages'})
    assert stage(pm, project)['revision'] == revision
    history = data(pm.get('approvals', params={'projectId': pid}))
    assert history['total'] == 22 and history['items'][0]['status'] == 'pending'


def test_browser_project_overview_filters_and_dated_samples(live, team):
    from datetime import datetime

    from pmo.health_calculation import TZ
    from test_health import prepare, sample
    from test_reports import body, sections
    from test_reviews import data, headers

    _, _, users, _ = team
    pm, outsider = users['pm'], users['outsider']
    project, _ = prepare(live, team)
    first = sample(pm, project)
    milestone = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    data(pm.post(project + '/reports', headers=headers(), json=body(reportType='daily', period=datetime.now(TZ).date().isoformat(),
        structuredFields=sections(milestone_progress=[{'milestoneId': milestone, 'claim': 'progress'}]))))
    data(pm.post('projects', headers=headers(), json={'code': 'DASH-OWN', 'name': 'Synthetic review local'}))
    data(outsider.post('projects', headers=headers(), json={'code': 'DASH-HIDDEN', 'name': 'Synthetic review private'}))
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'project-overview', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm', 'initiator')),
            'PMO_TEST_SAMPLE': first['asOf']})
