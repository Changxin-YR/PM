"""Deterministic health, alerts and immutable receipts against the running services."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.health_calculation import TZ, WEEK, aggregate, due_weeks, monday, week_key
from pmo.outbox import dispatch
from pmo.rule_scan import scan_batch, scan_locked, slot
from test_lifecycle import (ANSWERS, close_materials, complete_milestones, execute, execution_fixture, gate, propose,
                            stage, upload)
from test_reports import body, sections
from test_reviews import data, decide, headers, issue, member, planning_setup, score, start_charter, team as team


def prepare(live, team, *, priority='P1', effort_budget='10'):
    engine, _, _, _ = live
    pid, people, users, _ = team
    charter = start_charter(pid, users)
    score(users['reviewer'], charter, [4] * 4)
    data(decide(users['reviewer'], charter))
    first = datetime.now(TZ).date() + timedelta(days=7)
    project, requirement, risk = planning_setup(engine, pid, people, users, seed_stage=False, priority=priority,
                                               first_day=first, effort_budget=effort_budget)
    feasibility = gate(users['pm'], project, 'feasibility')
    score(users['reviewer'], feasibility, [3] * 5)
    assert data(decide(users['reviewer'], feasibility))['decision']['stage'] == 'executing'
    return project, first


def sample(pm, project):
    return data(pm.post(project + '/health/refresh', headers=headers(stage(pm, project)['revision'])))


def code(response, status, error):
    assert (response.status_code, response.json()['code']) == (status, error), response.text


def test_business_time_cutoffs_and_composition():
    local = lambda y, m, d, h=0: datetime(y, m, d, h, tzinfo=TZ)
    def edge(at, name='executing'):
        return SimpleNamespace(to_stage=name, transitioned_at=at)
    # Exactly Monday 18:00 China time, not UTC. The project only owes weeks it executed in.
    entries = [edge(local(2026, 10, 26)), edge(local(2026, 11, 2), 'on_hold'), edge(local(2026, 11, 9))]
    assert due_weeks(entries, local(2026, 11, 16, 17)) == [date(2026, 10, 26)]
    assert due_weeks(entries, local(2026, 11, 16, 18)) == [date(2026, 10, 26), date(2026, 11, 9)]
    assert slot(local(2026, 11, 16, 8)) == date(2026, 11, 15)
    assert slot(local(2026, 11, 16, 9)) == date(2026, 11, 16)
    for lines, result in [(['yellow','green','green'],'green'), (['yellow','yellow','green'],'yellow'),
                          (['yellow','yellow','red'],'red'), (['untrusted','red','red'],'untrusted')]:
        assert aggregate(lines) == result


def test_live_snapshot_appeal_and_stdio_scope(live, team):
    eng, api, port, _ = live
    pid, people, users, _ = team
    pm, reviewer, outsider = (users[i] for i in ('pm','reviewer','outsider'))
    project, first = prepare(live, team)
    milestone = data(pm.get(project + '/milestones'))['items'][0]
    data(pm.patch(f"milestones/{milestone['milestoneId']}/forecast", headers=headers(milestone['revision']),
        json={'newDate': (first + timedelta(days=4)).isoformat(), 'reason': 'Observed dependency moved four business days.'}))
    preview = data(pm.get(project + '/health'))
    assert (preview['timeLine'], preview['costLine'], preview['qualityLine'], preview['health']) == ('yellow','green','green','green')
    assert preview['publication'] == 'preview' and 'snapshotId' not in preview
    assert any(ref.startswith('cost-entry:') for ref in preview['sourceRefs'])
    snapshot = sample(pm, project)
    assert snapshot['publication'] == 'official' and snapshot['requirementSatisfactionRate'] == '0.000000'
    assert snapshot['timeLine'] == 'yellow' and snapshot['canAppeal'] is True
    assert sample(pm, project)['snapshotId'] == snapshot['snapshotId']
    assert data(pm.get(project + '/health/history'))['total'] == 1
    assert data(pm.get(project + '/health', params={'asOf': snapshot['asOf']}))['historical']
    code(outsider.get(project + '/health'), 404, 'RESOURCE_NOT_FOUND')

    token = issue(pm, pid, ['pmo_health_get'])
    bearer = {'Authorization': 'Bearer ' + token}
    agent_health = data(api.get('/api/v1/' + project + '/health', headers=bearer))
    for field, val in agent_health.items():
        assert snapshot[field] == val
    assert 'inputs' not in agent_health and 'canAppeal' not in agent_health
    params = StdioServerParameters(command=sys.executable, args=['-m','pmo.mcp_server'],
                                   env={'PMO_AGENT_TOKEN':token,'PMO_API_PORT':str(port),'PYTHONUTF8':'1'})

    async def stdio():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert 'pmo_health_get' in {tool.name for tool in tools.tools}
                response = await client.call_tool('pmo_health_get', {'projectId': pid})
                assert not response.isError and response.structuredContent['data'] == agent_health
                denied = await client.call_tool('pmo_health_get', {'projectId': pid, 'role':'pm'})
                assert denied.isError and denied.structuredContent['error']['code'] == 'VALIDATION_FAILED'
    asyncio.run(stdio())

    sid = snapshot['snapshotId']
    path = f'health-snapshots/{sid}/appeals'
    request = {'reason':'The dated delivery evidence was received after this snapshot.'}
    code(api.post('/api/v1/' + path, headers={**headers(),**bearer},json=request), 403, 'FORBIDDEN')
    code(outsider.post(path,headers=headers(),json=request), 404, 'RESOURCE_NOT_FOUND')
    code(reviewer.post(path,headers=headers(),json=request), 403, 'FORBIDDEN')
    code(pm.post(path,headers=headers(2),json=request), 409, 'VERSION_CONFLICT')
    repeat = headers()
    with ThreadPoolExecutor(2) as pool:
        writes = list(pool.map(lambda _: pm.post(path,headers=repeat,json=request), range(2)))
    receipt = data(writes[0])
    assert writes[0].json() == writes[1].json()
    code(pm.post(path,headers=repeat,json={'reason':'A different explanation'}), 409, 'IDEMPOTENCY_CONFLICT')
    code(pm.post(path,headers=headers(),json=request), 409, 'HEALTH_APPEAL_LIMIT')
    verify = f"health-appeals/{receipt['appealId']}/review"
    decision = {'verdict':'upheld','rationale':'The four-day slip was accurate at the frozen cutoff.'}
    with eng.begin() as c:
        member(c,pid,people['pm'],people['sponsor'],'reviewer')
    code(pm.post(verify,headers=headers(),json=decision), 403, 'INDEPENDENT_REVIEWER_REQUIRED')
    signed = data(reviewer.post(verify,headers=headers(),json=decision))
    assert signed['revision'] == 2 and signed['reviewerId'] == str(people['reviewer'])
    old = data(pm.get(project + '/health/history'))['items'][0]
    assert old['historical'] and old['inputHash'] == snapshot['inputHash'] and old['timeLine'] == 'yellow'
    assert old['appeal']['rationale'] == decision['rationale'] and not old['canAppeal']
    with eng.connect() as c:
        writes = c.execute(sa.text("SELECT count(*) FROM health_appeals WHERE snapshot_id=:sid"), {'sid':sid}).scalar_one()
        assert writes == 1


def test_incremental_scan_lr04_lr03_dedupe_and_transaction(live, team):
    eng, _, _, _ = live
    pid, people, users, _ = team
    project, _ = prepare(live, team)
    pm = users['pm']
    def forecast(amount, kind='money'):
        rev = data(pm.get(project + '/costs'))['plan']['revision']
        data(pm.put(project + '/cost-forecasts', headers=headers(rev), json={'type':kind,'forecastRemaining':amount,
             'reason':'Change to isolated cost forecast.'}))

    def stats():
        with eng.connect() as c:
            return c.execute(sa.text('''SELECT
                (SELECT count(*) FROM rule_events WHERE project_id=:p AND rule_id='LR-04' AND status='open'),
                (SELECT count(*) FROM tasks WHERE project_id=:p AND title LIKE 'LR-04%'),
                (SELECT count(*) FROM health_snapshots WHERE project_id=:p),
                (SELECT count(*) FROM outbox_events WHERE payload->>'projectId'=CAST(:p AS text) AND event_type='rule.notify')'''),
                {'p':pid}).one()
    forecast('115.00')
    assert scan_batch(eng, 200) > 0
    assert stats() == (0, 0, 1, 0)
    forecast('120.00')
    assert scan_batch(eng, 200) > 0
    assert stats() == (1, 0, 2, 1)
    forecast('120.01')
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TRIGGER health_test_abort BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()")
    try:
        res = pm.post(project + '/health/refresh',headers=headers(stage(pm, project)['revision']))
        code(res, 500, 'DATABASE_FAILURE')
        assert stats() == (1,0,2,1)
        with eng.connect() as c:
            assert c.scalar(sa.text('SELECT dirty FROM project_rule_scan_state WHERE project_id=:p'),{'p':pid})
            assert not c.scalar(sa.text("SELECT count(*) FROM audit_logs WHERE project_id=:p AND action='human_health_refresh' AND result='success'"),{'p':pid})
    finally:
        with eng.begin() as c:
            c.exec_driver_sql('DROP TRIGGER health_test_abort ON outbox_events')
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(lambda _: scan_batch(eng, 200), range(2))) >= 1
    assert stats() == (1,1,3,2)
    first = sample(pm, project)
    assert first['moneyVarianceRate'] == '0.2001'
    assert sample(pm, project)['snapshotId'] == first['snapshotId']
    assert stats() == (1,1,3,2)
    forecast('12.01','effort')
    assert scan_batch(eng, 200) > 0
    open_ids = [r['alertId'] for r in data(pm.get(project + '/alerts'))['items'] if r['ruleId'] == 'LR-04' and r['status']=='open']
    assert len(open_ids) == 2
    dispatch(eng, 500)
    dispatch(eng, 500)
    with eng.connect() as c:
        notifications = c.execute(sa.select(db.notifications.c.recipient_person_id).where(
            db.notifications.c.rule_event_id.in_(open_ids))).scalars().all()
        assert notifications == [people['sponsor']] * 2
        assert c.execute(sa.select(db.tasks.c.assignee_id).where(db.tasks.c.source_id.in_(open_ids))).scalars().all() == [people['sponsor']] * 2

    # Catch up a legacy late forecast without fabricating a versioned forecast receipt.
    m = data(pm.get(project + '/milestones'))['items'][0]
    with eng.begin() as c:
        c.execute(db.milestones.update().where(db.milestones.c.id == m['milestoneId']).values(
            forecast_date=date.fromisoformat(m['plannedDate']) + timedelta(days=12), revision=m['revision']+1))
        c.execute(sa.text('UPDATE project_rule_scan_state SET last_daily_slot=last_daily_slot-3 WHERE project_id=:p'), {'p':pid})
    assert scan_batch(eng, 200) > 0
    latest = data(pm.get(project + '/health'))
    assert latest['timeLine'] == 'red' and latest['publication'] == 'official'
    lr03 = [r for r in data(pm.get(project + '/alerts'))['items'] if r['ruleId']=='LR-03']
    assert len(lr03) == 1 and lr03[0]['severity'] == 'yellow' and lr03[0]['forecastDate']
    assert scan_batch(eng, 200) == 0


@pytest.mark.parametrize('claim', ['progress', 'completion'])
def test_lr06_claims_need_private_bytes_and_never_become_stale_green(live, team, claim):
    eng, _, _, env = live
    pid, _, users, _ = team
    project, _ = prepare(live, team)
    pm, reviewer = users['pm'], users['reviewer']
    before = sample(pm, project)
    milestone = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    wk = week_key(datetime.now(TZ).date())
    payload = body(period=wk, structuredFields=sections(milestone_progress=[{'milestoneId':milestone,'claim':claim}]))
    data(pm.post(project + '/reports',headers=headers(),json=payload))
    current = data(pm.get(project + '/health'))
    assert current['health'] == 'untrusted' and current['publication'] == 'withheld' and 'timeLine' not in current
    assert current['gaps'] == ['LR06_CLAIM_WITHOUT_EVIDENCE'] and 'snapshotId' not in current
    out = sample(pm, project)
    assert out['snapshotId'] != before['snapshotId']
    assert sample(pm, project)['snapshotId'] == out['snapshotId']
    assert data(pm.get(project + '/health',params={'asOf':before['asOf']}))['health'] == 'green'
    refs = [r for r in data(pm.get(project + '/alerts'))['items'] if r['ruleId']=='LR-06']
    assert len(refs) == 1 and refs[0]['status']=='open'
    proof = upload(pm, reviewer, project, 'milestone', milestone)
    payload['structuredFields']['milestone_progress'][0]['evidenceRefs'] = [proof['evidenceRef']]
    data(pm.post(project + '/reports',headers=headers(),json=payload))
    assert sample(pm, project)['publication']=='official'
    assert [r['status'] for r in data(pm.get(project + '/alerts'))['items'] if r['ruleId']=='LR-06'] == ['resolved']
    assert data(pm.get(project + '/milestones'))['items'][0].get('actualDate') is None
    with eng.connect() as c:
        stored = c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id==proof['evidenceId']))
    file = Path(env['PMO_EVIDENCE_DIR'])/stored
    original = file.read_bytes()
    file.write_bytes(b'X'+original[1:])
    try:
        assert data(pm.get(project + '/health'))['health']=='untrusted'
    finally:
        file.write_bytes(original)


def test_weekly_presence_and_formal_publication_are_independent(live, team):
    eng, _, _, _ = live
    pid, _, users, _ = team
    project, _ = prepare(live, team)
    pm = users['pm']
    week = monday(datetime.now(TZ).date())
    due = datetime.combine(week + 2*WEEK, time(18), TZ)
    with eng.begin() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id==pid).with_for_update()).mappings().one()
        a = scan_locked(c,p,due-timedelta(seconds=1))
        b = scan_locked(c,p,due)
        assert a.results['publication']=='preview' and a.results['dataTrust']=='trusted'
        assert b.results['health']=='untrusted' and len(b.input_revisions['weekly']['missingPeriods'])==2
    data(pm.post(project + '/reports/drafts',headers=headers(),json=body(period=week_key(week))))
    with eng.begin() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id==pid).with_for_update()).mappings().one()
        assert scan_locked(c,p,due).input_revisions['weekly']['twoMissed']
    report = data(pm.post(project + '/reports',headers=headers(),json=body(period=week_key(week))))
    with eng.begin() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id==pid).with_for_update()).mappings().one()
        obs = scan_locked(c,p,due)
        assert obs.input_revisions['weekly']['twoMissed'] is False
        assert obs.results['publication']=='preview'
    data(pm.post(f"reports/{report['reportId']}/confirm",headers=headers(report['revision']),json={}))
    with eng.begin() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id==pid).with_for_update()).mappings().one()
        obs = scan_locked(c,p,due)
        # The confirmed older week satisfies presence, but not publication of the newest due week.
        assert obs.results['publication']=='preview' and obs.input_revisions['weekly']['missingPeriods']==[week_key(week+WEEK)]
        assert c.scalar(sa.text("SELECT count(*) FROM rule_events WHERE project_id=:p AND rule_id='LR-06'"),{'p':pid}) == 1


def test_zero_budget_is_explicit_and_not_absent(live, team):
    _, _, users, _ = team
    project, _ = prepare(live, team, effort_budget='0')
    pm = users['pm']
    raw = data(pm.get(project + '/health'))
    assert raw['health']=='untrusted' and raw['gaps']==['EFFORT_ZERO_BUDGET_UNDECLARED']
    url = project + '/health/zero-budget'
    command = {'type':'effort','reason':'Authorized as a non-labor pilot budget.', 'baselineHash':raw['zeroBudgetHashes']['effort']}
    v = raw['projectRevision']
    code(pm.post(url,headers=headers(v),json={**command,'baselineHash':'a'*64}),409,'ZERO_BUDGET_BASIS_STALE')
    code(users['sponsor'].post(url,headers=headers(v),json=command),403,'FORBIDDEN')
    key = headers(v)
    saved = pm.post(url,headers=key,json=command)
    assert data(saved)['declaredBy']
    assert pm.post(url,headers=key,json=command).json()==saved.json()
    obs = sample(pm, project)
    assert obs['health']=='green' and obs['effortVarianceRate']=='UNAVAILABLE'
    assert any(ref.startswith('zero-budget:') for ref in obs['sourceRefs'])
    plan = data(pm.get(project + '/costs'))['plan']['revision']
    data(pm.put(project+'/cost-forecasts',headers=headers(plan),json={'type':'effort','forecastRemaining':'0.01','reason':'New labor needed'}))
    obs = sample(pm,project)
    assert obs['health']=='red' and obs['effortVarianceRate']=='UNAVAILABLE'


def test_lr08_needs_both_review_and_lesson_and_uses_calendar_day(live, team):
    eng, _, _, _ = live
    pid, people, users, _ = team
    pm, reviewer, customer, reconciler = (users[k] for k in ('pm','reviewer','client_representative','outsider'))
    project, requirement, risk = execution_fixture(live, team)
    complete_milestones(pm, reviewer, project)
    data(execute(pm, project, data(propose(pm, project, 'delivering'))))
    _, risk_proof, cost_proof = close_materials(pm, reviewer, customer, project, requirement, risk)
    data(reviewer.post(f'risks/{risk}/close',headers=headers(),json={'resolutionEvidenceRef':risk_proof['evidenceRef']}))
    reco = project + '/cost-reconciliations'
    state = data(reconciler.get(reco))
    data(reconciler.post(reco,headers=headers(state['revision']),json={'costHash':state['costHash'],'evidenceRefs':[cost_proof['evidenceRef']]}))
    assert data(decide(reviewer, gate(pm, project, 'closing')))['decision']['stage']=='closed'
    with eng.connect() as c:
        closed = c.scalar(sa.text("SELECT transitioned_at FROM project_stage_transitions WHERE project_id=:p AND to_stage='closed'"),{'p':pid})
    cut = datetime.combine(closed.astimezone(TZ).date()+timedelta(days=14), time(), TZ)
    def scan(at):
        with eng.begin() as c:
            p = c.execute(sa.select(db.projects).where(db.projects.c.id==pid).with_for_update()).mappings().one()
            scan_locked(c,p,at)
    scan(cut-timedelta(seconds=1))
    assert not [r for r in data(pm.get(project+'/alerts'))['items'] if r['ruleId']=='LR-08']
    scan(cut)
    data(execute(pm,project,data(propose(pm,project,'retrospective'))))
    retro = project + '/retrospective'
    data(pm.put(retro,headers=headers(data(pm.get(retro))['revision']),json={'answers':ANSWERS}))
    scan(cut+timedelta(days=1))
    assert [r['status'] for r in data(pm.get(project+'/alerts'))['items'] if r['ruleId']=='LR-08']==['open']
    l = data(pm.post(project+'/lessons',headers=headers(),json={'category':'delivery','what':'Aligned delivery with evidence',
         'rootCause':'Missing source record','advice':'Retain the original'}))
    data(reviewer.post(f"lessons/{l['lessonId']}/confirm",headers=headers(l['revision']),json={}))
    scan(cut+timedelta(days=1))
    assert [r['status'] for r in data(pm.get(project+'/alerts'))['items'] if r['ruleId']=='LR-08']==['resolved']
    dispatch(eng,500)
    with eng.connect() as c:
        assert c.execute(sa.text('''SELECT n.recipient_person_id FROM notifications n JOIN rule_events e ON e.id=n.rule_event_id
            WHERE e.project_id=:p AND e.rule_id='LR-08' '''),{'p':pid}).scalars().all()==[people['sponsor']]
