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
from test_tasks import team as task_team

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
    with engine.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM reviews WHERE project_id=:p AND type='charter'"),
                        {'p': review_pid}) == 1
        assert c.scalar(sa.text("SELECT count(*) FROM requirement_transitions WHERE requirement_id=:r AND status='customer_confirmed'"),
                        {'r': req}) == 1


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


def test_browser_read_only_project_assistant_without_model(live, team, tmp_path):
    from test_reviews import member

    _, requirement, _ = execution_fixture(live, team)
    project_id, people, users, _ = team
    with live[0].begin() as c:
        change_id = c.scalar(db.change_entries.insert().values(project_id=project_id, target_type='requirement',
            target_id=requirement, created_by=people['pm'], requested_by=people['pm'], status='withdrawn',
            base_revision=1, before={'priority':'P0'}, after={'priority':'P1'},
            reason='Synthetic requirement priority history').returning(db.change_entries.c.id))
        org = c.scalar(sa.select(db.projects.c.org_id).where(db.projects.c.id == project_id))
        other_code = f'ASSISTANT-CROSS-{uuid4().hex[:8]}'
        other_name = '合成助理范围项目'
        other = c.scalar(db.projects.insert().values(org_id=org, code=other_code, name=other_name,
            stage='drafting', revision=1, created_by=people['pm'], initiator_id=people['pm'], pm_id=people['pm'])
            .returning(db.projects.c.id))
        member(c, other, people['pm'], people['initiator'], 'pm')
        duplicate_name = '同名合成助理项目'
        for _ in range(2):
            duplicate = c.scalar(db.projects.insert().values(org_id=org, code=uuid4().hex, name=duplicate_name,
                stage='drafting', revision=1, created_by=people['pm'], initiator_id=people['pm'], pm_id=people['pm'])
                .returning(db.projects.c.id))
            member(c, duplicate, people['pm'], people['initiator'], 'pm')
        hidden_code = f'ASSISTANT-HIDDEN-{uuid4().hex[:8]}'
        c.execute(db.projects.insert().values(org_id=org, code=hidden_code, name='不可读合成助理项目',
            stage='drafting', revision=1, created_by=people['outsider'], initiator_id=people['outsider']))
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'assistant', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm')),
            'PMO_TEST_ASSISTANT_HISTORY': json.dumps({'projectId': project_id, 'requirementId': requirement,
                'changeId': str(change_id), 'other': {'id': str(other), 'code': other_code, 'name': other_name},
                'duplicateName': duplicate_name, 'hiddenCode': hidden_code}),
            'PMO_TEST_ASSISTANT_SCREENSHOT': str(tmp_path / 'assistant-question.png')})


def test_browser_separates_model_previews_from_official_results(live, team, tmp_path):
    from test_assistant_preview import enabled_api

    execution_fixture(live, team)
    with enabled_api(live, tmp_path, team[0], {'reviewDraft': [{'sourceId': 's1', 'lens': 'test_success'}]},
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
    from test_reports import data, headers

    engine, _, port, env = live
    _, _, users, _ = team
    project, _, _ = execution_fixture(live, team)
    report = submit(users['pm'], project + '/reports', narrative=NARRATIVE)
    params = {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm')),
              'PMO_TEST_MODEL':str(int(model_on))}
    if model_on:
        scope = {'PMO_REPORT_ANALYSIS': '1', 'PMO_REPORT_MODEL_PROJECTS': project.removeprefix('projects/')}
        with gateway(answer(typedValue='2.50 days')) as (model_url, calls), serve(engine.url, tmp_path,
                 extra_env=scope) as api, vite(api.base_url.port) as (root, url):
            process = subprocess.Popen([sys.executable, '-m', 'pmo.report_analysis_worker', '--watch'],
                 env={**env, **scope, 'PMO_REPORT_MODEL_URL':model_url,'PMO_REPORT_MODEL_TOKEN':''})
            try:
                run_browser(root, url, 'analysis', params)
            finally:
                process.terminate(); process.wait(timeout=10)
            assert len(calls)==1
            reviewer = users['reviewer']
            fact, = data(reviewer.get(f"reports/{report['reportId']}/facts"))['items']
            result = data(reviewer.post(f"facts/{fact['factId']}/verify", headers=headers(fact['revision']),
                        json={'trust':'medium','evidenceRef':fact['sourceRef']}))
            assert result['validationStatus']=='verified' and result['trust']=='medium'
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


@pytest.mark.parametrize('revoke', [False, True], ids=['complete', 'withdrawn-while-open'])
def test_browser_combined_closure_and_stale_forms(live, team, revoke):
    from secrets import token_urlsafe
    from uuid import uuid4

    import httpx
    from argon2 import PasswordHasher

    from pmo.cost_reconciliation import receipts as cost_receipts
    from pmo.requirement_exceptions import events as exception_events
    from pmo.risk_resolution import acceptances, resolutions
    from test_requirement_exceptions import conditional_material
    from test_reviews import data_login, result_counts

    pid, people, users, _ = team
    project, risk, proof, cost, prepare = conditional_material(live, team)
    _, command = prepare()
    receiver, username, password = uuid4(), uuid4().hex, token_urlsafe(24)
    with live[0].begin() as c:
        org = c.scalar(sa.select(db.projects.c.org_id).where(db.projects.c.id == pid))
        account = c.scalar(db.user_accounts.insert().values(username=username, is_active=True,
            password_hash=PasswordHasher().hash(password)).returning(db.user_accounts.c.id))
        c.execute(db.persons.insert().values(id=receiver, user_id=account, org_id=org, created_by=people['initiator'],
                                           name='Synthetic independent receiver', is_active=True))
    with httpx.Client(base_url=users['pm'].base_url, trust_env=False) as client:
        client.headers['X-CSRF-Token'] = data_login(client, username, password)
        sessions = browser_sessions({**users, 'receiver': client}, 'pm', 'reviewer', 'client_representative', 'outsider', 'receiver')
        with vite(live[2]) as (root, url):
            run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS': json.dumps(sessions), 'PMO_TEST_PHASE': 'exception',
                'PMO_TEST_REQUIREMENT_EXCEPTION': json.dumps(command), 'PMO_TEST_CLOSEOUT': json.dumps(dict(
                    project=project, risk=risk, riskProof=proof['evidenceRef'], costProof=cost['evidenceRef'],
                    receiver=str(receiver), revoke=revoke))})
    assert stage(users['pm'], project)['stage'] == ('closing_review' if revoke else 'closed')
    with live[0].connect() as c:
        assert result_counts(c, pid)[1:3] == ((2, 2) if revoke else (3, 3))
        assert c.scalar(sa.select(sa.func.count()).select_from(cost_receipts)
                        .where(cost_receipts.c.project_id == pid)) == 1
        exception_kinds = c.execute(sa.select(exception_events.c.kind).where(exception_events.c.project_id == pid)
                                    .order_by(exception_events.c.revision)).scalars().all()
        assert exception_kinds == (['signed', 'revoked'] if revoke else ['signed'])
        requested, accepted = c.execute(sa.select(acceptances).where(acceptances.c.risk_id == risk)
            .order_by(acceptances.c.created_at)).mappings().all()
        assert (requested.kind, accepted.kind, accepted.request_receipt_id) == ('requested', 'receiver_confirmed', requested.id)
        assert c.scalar(sa.select(resolutions.c.acceptance_id).where(resolutions.c.risk_id == risk)) == accepted.id


def test_browser_tasks_and_reports(live, team):
    _, _, users, _ = team
    project, _, _ = execution_fixture(live, team)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'lifecycle', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm')),
            'PMO_TEST_PHASE':'tasks_reports'})
    assert stage(users['pm'], project)['stage'] == 'executing'


def test_browser_personal_strengthening_queue(live, task_team):
    from test_tasks import command, headers, ok, project, PROJECT_REVISION

    people, users = task_team
    first, second = (project(live[0], people) for _ in range(2))
    pm, owner = users['pm'], users['owner']
    assignee = {'assigneeId': str(people['owner'].id)}
    task = ok(pm.post(first.path, headers=headers(PROJECT_REVISION), json=command(first, 'rule_event', **assignee,
        title='Target: reconcile supporting figures', dueAt='2027-01-10T16:00:00+08:00')))
    for i in range(20):
        ok(pm.post(first.path, headers=headers(PROJECT_REVISION), json=command(first, 'remediation', **assignee,
            title=f'Synthetic follow-up {i:02}', dueAt='2027-01-12T16:00:00+08:00')))
    ok(pm.post(second.path, headers=headers(PROJECT_REVISION), json=command(second, 'review', **assignee,
        title='Second project follow-up')))
    ok(pm.post(second.path, headers=headers(PROJECT_REVISION), json=command(second, 'review',
        assigneeId=str(people['peer'].id), title='Only for the other assignee')))
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'personal-tasks', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'owner')),
            'PMO_TEST_TASK': json.dumps(task)})
    saved, = ok(owner.get(first.path, params={'taskId': task['taskId']}))['items']
    assert saved['status'] == 'done' and saved['completedAt'] and saved['revision'] == task['revision'] + 1
    assert ok(owner.get('me/strengthening-tasks'))['total'] == 23


def test_browser_personal_review_and_stage_approvals(live, team):
    from test_approval_queue import another_project
    from test_lifecycle import begin_execution, propose
    from test_reviews import data, score, start_charter

    _, people, users, _ = team
    project, _, _ = begin_execution(live, team)
    pause = data(propose(users['pm'], project, 'on_hold'))
    review = start_charter(another_project(live[0], people, users), users)
    score(users['reviewer'], review, [4] * 4)
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'personal-approvals', {'PMO_TEST_SESSIONS':json.dumps(browser_sessions(users, 'pm', 'reviewer', 'sponsor')),
            'PMO_TEST_APPROVALS': json.dumps({'reviewId': review.split('/')[-1], 'transition': pause})})
    assert stage(users['pm'], project)['stage'] == 'on_hold'
    assert data(users['reviewer'].get(review + '/workbench'))['decision']['stage'] == 'designing'


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
    from pmo.outbox import dispatch
    from test_health import prepare, sample
    from test_reports import body, sections
    from test_reviews import data, headers

    pid, people, users, _ = team
    pm, outsider = users['pm'], users['outsider']
    project, _ = prepare(live, team)
    plan = data(pm.get(project + '/costs'))['plan']
    data(pm.put(project + '/cost-forecasts', headers=headers(plan['revision']), json={
        'type':'money', 'forecastRemaining':'116.00', 'reason':'Synthetic personal cost reminder.'}))
    first = sample(pm, project)
    with live[0].connect() as c:
        queued = c.scalar(sa.select(sa.func.count()).select_from(db.outbox_events).where(
            db.outbox_events.c.status.in_(('pending', 'processing'))))
    # Full Edge runs share a test outbox. Drain the fixture's queue through the normal worker.
    dispatch(live[0], queued)
    with live[0].connect() as c:
        assert c.scalar(sa.text('''SELECT EXISTS (SELECT 1 FROM notifications n
            JOIN rule_events e ON n.rule_event_id = e.id AND e.project_id = n.project_id
            WHERE n.project_id = :pid AND n.recipient_person_id = :person
            AND e.rule_id = 'LR-04' AND n.delivery_status = 'delivered')'''),
            {'pid': pid, 'person': people['initiator']})
    milestone = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    data(pm.post(project + '/reports', headers=headers(), json=body(reportType='daily', period=datetime.now(TZ).date().isoformat(),
        structuredFields=sections(milestone_progress=[{'milestoneId': milestone, 'claim': 'progress'}]))))
    data(pm.post('projects', headers=headers(), json={'code': 'DASH-OWN', 'name': 'Synthetic review local'}))
    data(outsider.post('projects', headers=headers(), json={'code': 'DASH-HIDDEN', 'name': 'Synthetic review private'}))
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'project-overview', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm', 'initiator')),
            'PMO_TEST_SAMPLE': first['asOf']})


def test_browser_local_workbench_latency(live, team):
    from test_reviews import data, headers

    _, _, users, _ = team
    client = users['outsider']
    for i in range(1, 16):
        project = data(client.post('projects', headers=headers(), json={'code': f'RC-SMOKE-{i:02}', 'name': f'RC project {i:02}'}))
        milestone = data(client.post(f"projects/{project['projectId']}/milestones", headers=headers(),
            json={'name': f'Acceptance milestone {i:02}', 'plannedDate': '2027-01-10'}))
        assert milestone['projectId'] == project['projectId']
    session = browser_sessions(users, 'outsider')['outsider']
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'deployment-performance', {'PMO_SYNTHETIC_SESSION': json.dumps(session)})


def test_browser_formal_baseline_changes(live, team):
    from datetime import timedelta

    from test_health import code, prepare
    from test_reviews import data, headers, issue

    pid, people, users, _ = team
    pm, reviewer, outsider = (users[role] for role in ('pm', 'reviewer', 'outsider'))
    project, first = prepare(live, team)
    targets = {}
    for kind in ('milestone', 'budget', 'charter'):
        endpoint = project + '/changes/targets'
        targets[kind] = data(pm.get(endpoint, params={'kind': kind}))['items'][0]
        code(reviewer.get(endpoint, params={'kind': kind}), 403, 'FORBIDDEN')
        code(outsider.get(endpoint, params={'kind': kind}), 404, 'RESOURCE_NOT_FOUND')
    token = issue(pm, pid, ['pmo_change_propose'])
    refused = live[1].get('/api/v1/' + project + '/changes/targets?kind=budget', headers={'Authorization': 'Bearer ' + token})
    code(refused, 403, 'FORBIDDEN')

    with live[0].begin() as c:
        for kind in targets:
            c.execute(sa.text('''INSERT INTO change_approval_grants
                VALUES (:p,:reviewer,:kind,'synthetic Web change',now()+interval '1 hour',NULL)'''),
                {'p': pid, 'reviewer': people['reviewer'], 'kind': kind})
    dates = [(first + timedelta(days=days)).isoformat() for days in (2, 4)]
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'formal-changes', {'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm', 'reviewer')),
            'PMO_TEST_FORMAL_CHANGES': json.dumps({'project': stage(pm, project), 'targets': targets,
                'forecastDate': dates[0], 'plannedDate': dates[1]})})

    records = {item['targetType']: item for item in data(pm.get(project + '/changes'))['items']}
    assert len(records) == len(data(pm.get(project + '/changes'))['items']) == 3
    assert all(item['status'] == 'pending' for item in records.values())
    assert records['milestone']['before'] == {'plannedDate': targets['milestone']['plannedDate']}
    assert records['milestone']['after'] == {'plannedDate': dates[1]}
    assert records['budget']['before'] == {'budget': targets['budget']['budget']}
    assert records['budget']['after'] == {'budget': '137.50'}
    assert records['charter']['after'] == {'roughDuration': targets['charter']['charter']['roughDurationDays'] + 1}
    assert data(pm.get(project + '/changes/targets?kind=budget'))['items'][0] == targets['budget']
    for item in records.values():
        approved = data(reviewer.post(f"changes/{item['changeId']}/approve", headers=headers(item['revision']),
            json={'rationale': 'Reviewed the existing baseline and proposed difference separately.'}))
        assert approved['status'] == 'applied'
    approved_milestone = next(item for item in data(pm.get(project + '/milestones'))['items']
        if item['milestoneId'] == targets['milestone']['targetId'])
    assert approved_milestone['plannedDate'] == dates[1] and approved_milestone['forecastDate'] == dates[0]


def test_browser_weekly_portfolio_and_manual_fact_handoff(live, team, tmp_path):
    from test_facts import medium, post, proposal
    from test_reports import data
    from test_weekly_report_workflow import publish, submitted

    _, _, users, _ = team
    project, _, _ = execution_fixture(live, team)
    pm, reviewer = users['pm'], users['reviewer']
    for period, amount, instant in [('2024-W52', '3.00', '2024-12-23T08:00:00+08:00'),
                                      ('2025-W01', '4.00', '2024-12-30T08:00:00+08:00')]:
        report = publish(pm, submitted(pm, f'{project}/reports', amount=amount, period=period))
        proposed = data(post(pm, report, proposal(report, amount, asOf=instant)))
        assert data(medium(reviewer, proposed))['validationStatus'] == 'verified'
    with vite(live[2]) as (root, url):
        run_browser(root, url, 'weekly-portfolio', {
            'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm', 'reviewer', 'outsider', 'initiator')),
            'PMO_TEST_SCREENSHOTS': str(tmp_path),
        })
