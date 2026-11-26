import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import sys
from uuid import UUID

from httpx import Client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sqlalchemy as sa

from pmo import policy, policy_admin as admin, schema_v1 as db
from pmo.category_costs import required_for_project
from test_cost_reconciliation import command as reconciliation_command, evidence, reconciliation as reconciliation, sheet
from test_database_roles import serve
from test_formal_policy import ORIGINAL, TARGET, draft as policy_draft, fixture_authorities
from test_lifecycle import execution_fixture, gate, grant_closure_roles
from test_reviews import data, decide, headers, issue, planning_setup, read_gaps, score, start_charter, team as team


BUDGET = {'procurement': '50', 'travel': '20', 'cloud': '20', 'allocation': '10',
          'development': '6', 'implementation': '3', 'management': '1'}
FORECAST = {'procurement': '20', 'travel': '5', 'cloud': '8', 'allocation': '7',
            'development': '2', 'implementation': '2', 'management': '1'}


def summary(client, path, at=None):
    return data(client.get(path + '/costs', params={'asOf': at} if at else None))


def line(view, category, kind='money'):
    return next(row for row in view['categories']['items'] if (row['type'], row['category']) == (kind, category))


def approve(client, change, request=None):
    return client.post(f"changes/{change['changeId']}/approve", headers=request or headers(change['revision']),
                       json={'rationale': 'Independently reviewed allocation and aggregate estimate.'})


def grant_budget(live, pid, person):
    with live[0].begin() as c:
        c.execute(sa.text('''INSERT INTO change_approval_grants(project_id,person_id,target_type,authority_ref,expires_at)
            VALUES(:p,:u,'budget','Synthetic independent budget grant',clock_timestamp()+interval '1 hour')'''),
            {'p': pid, 'u': person})


def test_category_plan_freezes_both_units_and_transfers_at_one_instant(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    grant_closure_roles(live, team)
    grant_budget(live, pid, people['reviewer'])
    charter = start_charter(pid, users)
    score(reviewer, charter, [4] * 4)
    data(decide(reviewer, charter))
    project, _, _ = planning_setup(engine, pid, people, users, seed_stage=False)
    draft = project + '/cost-categories/budget-draft'
    forecast = project + '/cost-categories/forecast'
    assert pm.put(draft, headers=headers(), json={'amounts': {'procurement': '10'}}).json()['code'] == 'CATEGORY_ALLOCATION_INCOMPLETE'
    assert pm.put(draft, headers=headers(), json={'amounts': {**BUDGET, 'cloud': '0.001'}}).status_code == 422
    key = headers()
    saved = pm.put(draft, headers=key, json={'amounts': BUDGET})
    assert data(saved)['revision'] == 2
    assert pm.put(draft, headers=key, json={'amounts': BUDGET}).json() == saved.json()
    assert 'CATEGORY_FORECAST_REQUIRED' in read_gaps(pm, project, 'feasibility')
    assert data(pm.put(forecast, headers=headers(), json={'amounts': FORECAST, 'reason': 'Bottom-up estimate.'}))['revision'] == 2
    review = gate(pm, project, 'feasibility')
    score(reviewer, review, [3] * 5)
    data(decide(reviewer, review))
    before = summary(pm, project)
    assert before['categories']['mode'] == 'classified'
    assert (before['money']['budget'], before['effort']['budget']) == ('100.00', '10.00')
    assert (before['money']['forecastRemaining'], before['effort']['forecastRemaining']) == ('40.00', '5.00')
    assert line(before, 'travel')['initialBudget'] == '20.00'

    path = project + '/changes/category-budget'
    rev = before['categories']['budgetRevision']
    choice = {'amounts': {**BUDGET, 'travel': '15', 'cloud': '25'}, 'reason': 'Shift allocation to cloud resources.'}
    change_key = headers(rev)
    request = pm.post(path, headers=change_key, json=choice)
    pending = data(request)
    assert 'legacySnapshot' not in pending and pending['status'] == 'pending'
    assert pm.post(path, headers=change_key, json=choice).json() == request.json()
    assert pm.post(path, headers=change_key, json={**choice, 'reason': 'Different reason'}).json()['code'] == 'IDEMPOTENCY_CONFLICT'
    assert line(summary(pm, project), 'travel')['budget'] == '20.00'
    competing = data(pm.post(path, headers=headers(rev), json={**choice, 'amounts': {**BUDGET, 'travel': '16', 'cloud': '24'}}))
    assert approve(pm, pending).status_code == 403
    first_key, second_key = headers(pending['revision']), headers(competing['revision'])
    with ThreadPoolExecutor() as pool:
        first = pool.submit(approve, reviewer, pending, first_key)
        second = pool.submit(approve, reviewer, competing, second_key)
        results = [(first.result(), pending, first_key), (second.result(), competing, second_key)]
    assert sorted(result.status_code for result, _, _ in results) == [200, 409]
    result, applied, applied_key = next(item for item in results if item[0].status_code == 200)
    receipt = data(result)
    assert approve(reviewer, applied, applied_key).json() == result.json()
    stamp = datetime.fromisoformat(receipt['effectiveAt'])
    old = summary(pm, project, (stamp - timedelta(microseconds=1)).isoformat())
    new = summary(pm, project, stamp.isoformat())
    assert old['money'] == new['money']
    assert (line(old, 'travel')['budget'], line(old, 'cloud')['budget']) == ('20.00', '20.00')
    assert (line(new, 'travel')['budget'], line(new, 'cloud')['budget']) == (
        receipt['after']['categoryBudgets']['travel'], receipt['after']['categoryBudgets']['cloud'])
    assert line(new, 'travel')['forecastRemaining'] == '5.00'
    assert new['categories']['forecastNeedsReview'] is True
    rev = new['categories']['budgetRevision']
    increase = {'amounts': {**BUDGET, 'procurement': '80', 'development': '8'}, 'reason': 'Added approved delivery work.'}
    assert pm.post(path, headers=headers(rev), json=increase).json()['code'] == 'BUDGET_REESTIMATE_REQUIRED'
    updated = data(pm.post(path, headers=headers(rev), json={**increase,
        'reestimate': 'Increase estimate due to supplier scope and development resourcing.'}))
    at = data(approve(reviewer, updated))['effectiveAt']
    revised = summary(pm, project, at)
    assert (revised['money']['budget'], revised['effort']['budget']) == ('130.00', '12.00')
    assert (line(revised, 'procurement')['initialBudget'], line(revised, 'procurement')['budget']) == ('50.00', '80.00')
    assert pm.put(project + '/cost-forecasts', headers=headers(), json={
        'type': 'money', 'forecastRemaining': '30', 'reason': 'Legacy aggregate update'}).json()['code'] == 'CATEGORY_ALLOCATION_REQUIRED'


def test_legacy_classification_keeps_history_totals_and_mcp_scope(live, team):
    engine, api, port, _ = live
    pid, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    project, _, _ = execution_fixture(live, team)
    grant_budget(live, pid, people['reviewer'])
    now = datetime.now(UTC)
    posting = data(pm.post(project + '/cost-postings', headers=headers(), json={'type': 'money', 'amount': '30.25',
        'period': now.strftime('%Y-%m'), 'sourceRef': 'Synthetic verified legacy entry.'}))
    posting_id = posting['postingId']
    data(reviewer.post(f'cost-postings/{posting_id}/verify', headers=headers(),
                       json={'reason': 'Compared receipt to the original', 'occurredAt': now.isoformat()}))
    old = summary(pm, project)
    assert line(old, 'historical_uncategorized')['actual'] == '30.25'
    assert 'forecastRemaining' not in line(old, 'procurement')
    assert pm.post(project + '/changes/category-budget', headers=headers(), json={
        'amounts': {**BUDGET, 'cloud': '30'}, 'reason': 'Invalid historical change'}).json()['code'] == 'CATEGORY_ACTIVATION_MUST_PRESERVE_TOTALS'
    activation = data(pm.post(project + '/changes/category-budget', headers=headers(),
                              json={'amounts': BUDGET, 'reason': 'Distribute already approved budget.'}))
    data(approve(reviewer, activation))
    reclass = project + f'/cost-postings/{posting_id}/classification'
    body = {'category': 'procurement', 'reason': 'Source is a third-party procurement receipt.'}
    assert pm.post(reclass, headers=headers(2), json={**body, 'category': 'development'}).json()['code'] == 'INVALID_CATEGORY_UNIT'
    request = data(pm.post(reclass, headers=headers(2), json=body))
    posted = data(reviewer.get(project + '/cost-postings'))['items'][0]
    assert posted['pendingClassificationId'] == request['requestId']
    key = headers(2)
    url = reclass + '/' + request['requestId'] + '/verify'
    proof = {'reason': 'Verified against original source document.'}
    assert pm.post(url, headers=key, json=proof).status_code == 403
    approved = reviewer.post(url, headers=key, json=proof)
    approval = data(approved)
    assert reviewer.post(url, headers=key, json=proof).json() == approved.json()
    view = summary(pm, project, approval['effectiveAt'])
    assert view['money']['actual'] == old['money']['actual']
    assert line(view, 'procurement')['actual'] == '30.25'
    assert line(view, 'historical_uncategorized')['actual'] == '0.00'
    assert line(summary(pm, project, old['asOf']), 'historical_uncategorized')['actual'] == '30.25'
    assert data(pm.get(project + '/cost-postings', params={'category': 'procurement'}))['items'][0]['postingId'] == posting_id
    assert approval['classificationId'] in ' '.join(line(view, 'procurement')['sourceRefs'])

    token = issue(pm, pid, ('pmo_cost_summary',))
    params = StdioServerParameters(command=sys.executable, args=['-m', 'pmo.mcp_server'],
        env={'PMO_AGENT_TOKEN': token, 'PMO_API_PORT': str(port), 'PYTHONUTF8': '1'})

    async def read():
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as cli:
            await cli.initialize()
            result = await cli.call_tool('pmo_cost_summary', {'projectId': pid, 'asOf': approval['effectiveAt']})
            assert not result.isError, result
            signed = result.structuredContent['data']
            assert 'categoryEditor' not in signed and 'plan' not in signed
            assert line(signed, 'procurement')['actual'] == '30.25'
            assert 'procurement receipt' not in str(signed)

    asyncio.run(read())
    denied = api.post('/api/v1/' + reclass, headers={**headers(2), 'Authorization': f'Bearer {token}'}, json=body)
    assert denied.status_code == 403
    with engine.begin() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(db.audit_logs).where(db.audit_logs.c.project_id == pid,
            db.audit_logs.c.action == 'pmo_cost_summary', db.audit_logs.c.actor_type == 'agent')) == 1
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='archived'))
    assert pm.post(reclass, headers=headers(2), json=body).json()['code'] == 'LEDGER_LOCKED'


def test_post_activation_new_projects_require_classification_without_changing_old_projects(live, team, monkeypatch, tmp_path):
    engine, _, _, env = live
    pid, people, users, _ = team
    action = 'cost.category'
    monkeypatch.delenv('PMO_SYNTHETIC_POLICY_SCHEMA', raising=False)
    monkeypatch.setenv('PMO_POLICY_ENVIRONMENT', TARGET)
    with engine.begin() as c:
        old = c.execute(sa.select(db.projects).where(db.projects.c.id == UUID(pid))).mappings().one()
    fixture_authorities(engine, old, people, 'DEC-26')
    packet = policy_draft(old, people, [action], 'DEC-26', projectId=None)
    with engine.begin() as c:
        identifier = admin.register(c, packet, people['pm'], ORIGINAL)
        admin.change_state(c, old.org_id, identifier, people['reviewer'], 'active', 'isolated trial', ORIGINAL)
        assert policy.enabled(c, old, action) and not required_for_project(c, old)

    with serve(engine.url, tmp_path, evidence_dir=env['PMO_EVIDENCE_DIR'],
               extra_env={'PMO_SYNTHETIC_POLICY_SCHEMA': '', 'PMO_POLICY_ENVIRONMENT': TARGET}) as restricted:
        with Client(base_url=restricted.base_url, trust_env=False, cookies=users['pm'].cookies,
                    headers={'X-CSRF-Token': users['pm'].headers['X-CSRF-Token']}) as pm:
            new = data(pm.post('projects', headers=headers(), json={'code': 'CATEGORY-NEW', 'name': 'New category budget'}))
            project = f"projects/{new['projectId']}"
            with engine.begin() as c:
                created = c.execute(sa.select(db.projects).where(db.projects.c.id == new['projectId'])).mappings().one()
                assert policy.enabled(c, created, action) and required_for_project(c, created)
            assert pm.put(project + '/cost-budgets', headers=headers(),
                          json={'type': 'money', 'budget': '100'}).json()['code'] == 'CATEGORY_ALLOCATION_REQUIRED'
            assert data(pm.put(project + '/cost-categories/budget-draft', headers=headers(), json={'amounts': BUDGET}))['revision'] == 2
            with engine.begin() as c:
                admin.change_state(c, old.org_id, identifier, people['reviewer'], 'revoked', 'isolated policy withdrawal')
                assert required_for_project(c, created) and not policy.enabled(c, created, action)
                assert not required_for_project(c, old)
                scoped = policy_draft(old, people, [action], 'DEC-26')
                scoped_id = admin.register(c, scoped, people['pm'], ORIGINAL)
                admin.change_state(c, old.org_id, scoped_id, people['reviewer'], 'active', 'isolated project-only activation', ORIGINAL)
                assert required_for_project(c, old) and policy.enabled(c, old, action)


def test_category_reconciliation_expires_without_invalidating_aggregate_receipt(reconciliation):
    engine, pid, _, users, _, through, _ = reconciliation
    pm, signer, verifier = (users[name] for name in ('pm', 'reviewer', 'sponsor'))
    project = f'projects/{pid}'
    path = project + '/cost-reconciliations'
    base = sheet(pid, through)
    keys, totals = reconciliation_command(signer, path, [evidence(users, pid, base)], reasons={'effort': 'execution'})
    total_receipt = data(signer.post(path, headers=keys, json=totals))
    legacy_money = {k: '0.00' for k in ('procurement', 'travel', 'cloud', 'allocation')}
    legacy_effort = {k: '0.00' for k in ('development', 'implementation', 'management')}
    base['schemaVersion'] = 2
    base['lines'][0]['categoryActuals'] = {**legacy_money, 'historical_uncategorized': '80.00'}
    base['lines'][1]['categoryActuals'] = {**legacy_effort, 'historical_uncategorized': '12.00'}
    categorized = data(signer.get(path, params={'scope': 'categories'}))
    category_receipt = data(signer.post(path, headers=headers(categorized['revision']), json={
        'costHash': categorized['costHash'], 'evidenceRefs': [evidence(users, pid, base)],
        'overrunReasons': {'effort': 'execution'}, 'scope': 'categories'}))
    assert category_receipt['lines'][0]['categoryActuals']['historical_uncategorized'] == '80.00'
    assert data(pm.get(path, params={'scope': 'categories'}))['current'] == category_receipt

    with engine.begin() as c:
        p = db.cost_postings.c
        posting_id = c.scalar(sa.select(p.id).where(p.project_id == pid, p.amount_signed == 80))
    classification = project + f'/cost-postings/{posting_id}/classification'
    request = data(pm.post(classification, headers=headers(2), json={
        'category': 'procurement', 'reason': 'The original was a procurement voucher.'}))
    data(verifier.post(classification + f"/{request['requestId']}/verify", headers=headers(2),
                       json={'reason': 'Independently confirmed category.'}))
    assert data(pm.get(path, params={'scope': 'categories'}))['current'] is None
    assert data(pm.get(path))['current'] == total_receipt
