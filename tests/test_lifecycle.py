"""Model-off product lifecycle over real HTTP, with only accounts/grants as fixtures."""
from datetime import UTC, datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.cost_reconciliation import grants
from test_evidence import binary
from test_reviews import (bench, data, decide, headers, issue, member, planning_setup, score, start_charter,
                          team as team)

ANSWERS = [
    'Compared planned costs, effort and delivery dates with the verified actuals.',
    'Checked scope, execution and estimation deviations at each milestone.',
    'The validation procedure and test data template are reusable.',
    'Capture the reconciliation source and the receiving owner during planning.',
]


def stage(client, project):
    return data(client.get(project))['project']


def propose(client, project, destination):
    return client.post(project + '/transition-requests', headers=headers(stage(client, project)['revision']),
        json={'toStage': destination, 'reason': 'Request following the documented workflow.'})


def execute(client, project, request):
    return client.post(project + '/transitions', headers=headers(stage(client, project)['revision']),
        json={'toStage': request['toStage'], 'approvalRequestId': request['requestId']})


def gate(client, project, kind):
    current = stage(client, project)
    rv = data(client.post(project + '/reviews', headers=headers(current['revision']), json={'reviewType': kind}))
    return 'reviews/' + rv['reviewId']


def upload(pm, reviewer, project, kind, resource, content=None):
    revision = data(pm.get(project + '/evidence'))['collectionRevision']
    file = ('source.json', json.dumps(content).encode(), 'application/json') if content else ('proof.png', binary('png'), 'image/png')
    proof = data(pm.post(project + '/evidence', headers=headers(revision), files={'file': file},
        data={'linkedResourceType': kind, 'linkedResourceId': resource, 'description': 'Synthetic lifecycle evidence'}))
    return data(reviewer.post(f"evidence/{proof['evidenceId']}/verify", headers=headers(proof['revision']),
        json={'reason': 'Reviewed original synthetic evidence bytes', 'sha256': proof['sha256']}))


def begin_execution(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    reviewer = users['reviewer']
    charter = start_charter(pid, users)
    score(reviewer, charter, [4] * 4)
    data(decide(reviewer, charter))
    project, requirement, risk = planning_setup(engine, pid, people, users, seed_stage=False)
    feasibility = gate(users['pm'], project, 'feasibility')
    score(reviewer, feasibility, [3] * 5)
    assert data(decide(reviewer, feasibility))['decision']['stage'] == 'executing'
    return project, requirement, risk


def execution_fixture(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    with engine.begin() as c:
        member(c, pid, people['outsider'], people['initiator'], 'member')
        c.execute(grants.insert().values(project_id=pid, person_id=people['outsider']))
        for who in ('reviewer', 'outsider'):
            account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == people[who]))
            c.execute(db.user_roles.insert().values(user_id=account, role='finance_verifier'))
    return begin_execution(live, team)


def complete_milestones(pm, reviewer, project):
    today = datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    for m in data(pm.get(project + '/milestones'))['items']:
        proof = upload(pm, reviewer, project, 'milestone', m['milestoneId'])
        data(pm.post(f"milestones/{m['milestoneId']}/complete", headers=headers(m['revision']),
            json={'actualDate': today, 'evidenceRef': proof['evidenceRef'], 'reason': 'Validated delivery output'}))


def close_materials(pm, reviewer, customer, project, requirement, risk, *, confirm=True,
                    actuals=('100.00', '10.00'), remaining=('0.00', '0.00')):
    if confirm:
        req = data(pm.get(project + '/requirements'))['items'][0]
        proof = upload(pm, reviewer, project, 'requirement', requirement)
        for action, client in [('deliver', pm), ('accept', pm), ('customer-confirm', customer)]:
            req = data(client.post(f'requirements/{requirement}/{action}', headers=headers(req['revision']),
                json={'evidenceRef': proof['evidenceRef']}))
        assert req['status'] == 'customer_confirmed'
    rejected = data(pm.get(project + '/closing/check'))
    assert {'COST_RECONCILIATION_REQUIRED', 'BLOCKING_RISK_UNRESOLVED'} <= {i['code'] for i in rejected['missing']}
    risk_proof = upload(pm, reviewer, project, 'risk', risk)
    now = datetime.now(UTC)
    for kind, actual in zip(('money', 'effort'), actuals):
        posting = data(pm.post(project + '/cost-postings', headers=headers(), json={
            'type': kind, 'amount': actual, 'period': now.strftime('%Y-%m'), 'sourceRef': f'Synthetic {kind} verified source'}))
        data(reviewer.post(f"cost-postings/{posting['postingId']}/verify", headers=headers(), json={
            'reason': 'Checked underlying synthetic source', 'occurredAt': now.isoformat()}))
    for index, (kind, amount) in enumerate(zip(('money', 'effort'), remaining), start=3):
        data(pm.put(project + '/cost-forecasts', headers=headers(index), json={'type': kind,
            'forecastRemaining': amount, 'reason': 'Remaining commitments from the fixed synthetic test scenario.'}))

    # Fixed source values independent of the live cost teller; source time covers the verified subset.
    original = {'schemaVersion': 1, 'projectId': project.split('/')[-1], 'coveredThrough': datetime.now(UTC).isoformat(), 'lines': [
        {'type': 'money', 'netActual': actuals[0], 'sourceRef': 'SYNTHETIC-GL-CLOSING'},
        {'type': 'effort', 'netActual': actuals[1], 'sourceRef': 'SYNTHETIC-TIMESHEETS-CLOSING'}]}
    cost_proof = upload(pm, reviewer, project, 'project', original['projectId'], original)
    return now, risk_proof, cost_proof


def test_drafting_to_archive_without_model_or_stage_fixtures(live, team):
    pid, _, users, _ = team
    engine = live[0]
    pm, reviewer, customer, reconciler = (users[k] for k in ('pm', 'reviewer', 'client_representative', 'outsider'))
    project, requirement, risk = execution_fixture(live, team)
    assert propose(pm, project, 'closed').json()['code'] == 'TRANSITION_NOT_ALLOWED'
    assert propose(pm, project, 'delivering').json()['code'] == 'MILESTONE_COMPLETION_REQUIRED'
    complete_milestones(pm, reviewer, project)
    request = data(propose(pm, project, 'delivering'))
    assert reviewer.post(project + '/transitions', headers=headers(request['revision']),
                         json={'toStage': 'delivering', 'approvalRequestId': request['requestId']}).status_code == 403
    assert data(execute(pm, project, request))['status'] == 'applied'
    assert stage(pm, project)['stage'] == 'delivering'
    now, risk_proof, cost_proof = close_materials(pm, reviewer, customer, project, requirement, risk)
    data(reviewer.post(f'risks/{risk}/close', headers=headers(), json={'resolutionEvidenceRef': risk_proof['evidenceRef']}))
    reco = project + '/cost-reconciliations'
    state = data(reconciler.get(reco))
    assert state['canCreate']
    data(reconciler.post(reco, headers=headers(state['revision']), json={'costHash': state['costHash'], 'evidenceRefs': [cost_proof['evidenceRef']]}))
    assert data(pm.get(project + '/closing/check'))['eligible']
    closing = gate(pm, project, 'closing')
    assert data(decide(reviewer, closing))['decision']['stage'] == 'closed'

    ret = data(propose(pm, project, 'retrospective'))
    data(execute(pm, project, ret))
    retro = project + '/retrospective'
    assert data(pm.get(retro))['kind'] == 'normal'
    assert propose(pm, project, 'archived').json()['code'] == 'RETROSPECTIVE_ANSWERS_REQUIRED'
    data(pm.put(retro, headers=headers(data(pm.get(retro))['revision']), json={'answers': ANSWERS}))
    assert propose(pm, project, 'archived').json()['code'] == 'CONFIRMED_LESSON_REQUIRED'
    l = data(pm.post(project + '/lessons', headers=headers(), json={'category': 'governance',
        'what': 'Recovered trustworthy source data', 'rootCause': 'Source prepared independently', 'advice': 'Preserve evidence before review'}))
    data(reviewer.post(f"lessons/{l['lessonId']}/confirm", headers=headers(l['revision']), json={}))
    archive = data(propose(pm, project, 'archived'))
    assert pm.post(f"approvals/{archive['requestId']}/decide", headers=headers(archive['revision']),
        json={'decision': 'approve', 'rationale': 'The requester cannot approve their own submission.'}).status_code == 403
    key = headers(archive['revision'])
    command = {'decision': 'approve', 'rationale': 'Independently reviewed four answers and one confirmed lesson.'}
    saved = reviewer.post(f"approvals/{archive['requestId']}/decide", headers=key, json=command)
    assert data(saved)['status'] == 'applied'
    assert reviewer.post(f"approvals/{archive['requestId']}/decide", headers=key, json=command).json() == saved.json()
    assert stage(pm, project)['stage'] == 'archived'
    assert pm.put(retro, headers=headers(2), json={'answers': ANSWERS}).status_code == 422
    assert pm.post(project + '/cost-postings', headers=headers(), json={'type': 'money', 'amount': '1', 'period': now.strftime('%Y-%m'),
        'sourceRef': 'New posting must not be allowed'}).status_code == 422
    assert pm.put(project + '/cost-budgets', headers=headers(5), json={'type': 'money', 'budget': '102.00'}).status_code == 422
    assert decide(reviewer, closing).json()['code'] == 'REVIEW_NOT_PENDING'
    assert bench(reviewer, closing)['decision']['stage'] == 'closed'
    with engine.connect() as c:
        steps = c.execute(sa.text('SELECT to_stage FROM project_stage_transitions WHERE project_id=:pid ORDER BY to_revision'),
                          {'pid': pid}).scalars().all()
        assert steps == ['charter_review', 'designing', 'feasibility_review', 'executing', 'delivering',
                         'closing_review', 'closed', 'retrospective', 'archived']


def test_termination_and_rejected_resubmission_use_separate_histories(live, team):
    engine, api, _, _ = live
    pid, _, users, _ = team
    project = f'projects/{pid}'
    pm, reviewer, initiator, sponsor = (users[r] for r in ('pm', 'reviewer', 'initiator', 'sponsor'))
    item = start_charter(pid, users)
    score(reviewer, item, [1, 5, 5, 5])
    a = bench(initiator, item)
    data(initiator.post(item + '/appeals', headers=headers(a['review']['revision']), json={'reason': 'Review the proposed evidence again'}))
    first = data(decide(reviewer, item, verdict='reject'))
    assert first['decision']['stage'] == 'rejected'
    assert propose(pm, project, 'terminated').json()['code'] == 'CHARTER_RESUBMISSION_WINDOW_OPEN'
    h = headers(stage(pm, project)['revision'])
    changed = {'reason': 'Reconsidered the commercial positioning and dependencies.'}
    decision = initiator.post(project + '/resubmit', headers=h, json=changed)
    assert data(decision)['toStage'] == 'drafting'
    assert initiator.post(project + '/resubmit', headers=h, json=changed).json() == decision.json()
    item = start_charter(pid, users)
    score(reviewer, item, [2, 2, 2, 2])
    assert data(decide(reviewer, item, verdict='reject'))['decision']['stage'] == 'rejected'
    assert initiator.post(project + '/resubmit', headers=headers(stage(pm, project)['revision']), json=changed).json()['code'] == 'CHARTER_RESUBMISSION_LIMIT'
    token = issue(pm, pid, actions=('pmo_transition_request',))
    terminate = data(api.post('/api/v1/' + project + '/transition-requests', headers={**headers(stage(pm, project)['revision']),
        'Authorization': 'Bearer ' + token}, json={'toStage': 'terminated', 'reason': 'Second formal rejection; no remaining resubmission.'}))
    why = {'decision': 'approve', 'rationale': 'Examined the second formal rejection and termination reason.'}
    path = f"approvals/{terminate['requestId']}/decide"
    assert pm.post(path, headers=headers(terminate['revision']), json=why).status_code == 403
    assert api.post('/api/v1/' + path, headers={**headers(terminate['revision']), 'Authorization': 'Bearer ' + token}, json=why).status_code == 403
    approved = data(sponsor.post(path, headers=headers(terminate['revision']), json=why))
    assert approved['decisionId'] and stage(pm, project)['stage'] == 'terminated'
    rq = data(propose(pm, project, 'retrospective'))
    data(execute(pm, project, rq))
    assert data(pm.get(project + '/retrospective'))['kind'] == 'terminated'
    with engine.connect() as c:
        assert c.scalar(sa.text('''SELECT count(*) FROM project_stage_transitions WHERE project_id=:pid
            AND from_stage='rejected' AND to_stage='drafting' '''), {'pid': pid}) == 1
        assert c.scalar(sa.select(db.decisions.c.review_id).where(db.decisions.c.id == UUID(approved['decisionId']))) is None
