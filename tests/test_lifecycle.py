"""Model-off product lifecycle over real HTTP, with only accounts/grants as fixtures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
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


def begin_execution(live, team, *, effort_budget='10', extra_priorities=(), first_day=None):
    engine, _, _, _ = live
    pid, people, users, _ = team
    reviewer = users['reviewer']
    charter = start_charter(pid, users)
    score(reviewer, charter, [4] * 4)
    data(decide(reviewer, charter))
    project, requirement, risk = planning_setup(engine, pid, people, users, seed_stage=False, effort_budget=effort_budget,
                                               extra_priorities=extra_priorities, first_day=first_day)
    feasibility = gate(users['pm'], project, 'feasibility')
    score(reviewer, feasibility, [3] * 5)
    assert data(decide(reviewer, feasibility))['decision']['stage'] == 'executing'
    return project, requirement, risk


def grant_closure_roles(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    with engine.begin() as c:
        member(c, pid, people['outsider'], people['initiator'], 'member')
        c.execute(grants.insert().values(project_id=pid, person_id=people['outsider']))
        for who in ('reviewer', 'outsider'):
            account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == people[who]))
            c.execute(db.user_roles.insert().values(user_id=account, role='finance_verifier'))


def execution_fixture(live, team, *, effort_budget='10', extra_priorities=()):
    grant_closure_roles(live, team)
    return begin_execution(live, team, effort_budget=effort_budget, extra_priorities=extra_priorities)


def complete_milestones(pm, reviewer, project):
    today = datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    for m in data(pm.get(project + '/milestones'))['items']:
        proof = upload(pm, reviewer, project, 'milestone', m['milestoneId'])
        data(pm.post(f"milestones/{m['milestoneId']}/complete", headers=headers(m['revision']),
            json={'actualDate': today, 'evidenceRef': proof['evidenceRef'], 'reason': 'Validated delivery output'}))


def confirm_requirement(pm, reviewer, customer, project, req):
    requirement = req['requirementId']
    proof = upload(pm, reviewer, project, 'requirement', requirement)
    for action, client in [('deliver', pm), ('accept', pm), ('customer-confirm', customer)]:
        req = data(client.post(f'requirements/{requirement}/{action}', headers=headers(req['revision']),
            json={'evidenceRef': proof['evidenceRef']}))
    assert req['status'] == 'customer_confirmed'


def close_materials(pm, reviewer, customer, project, requirement, risk, *, confirm=True,
                    actuals=('100.00', '10.00'), remaining=('0.00', '0.00')):
    if confirm:
        req = next(req for req in data(pm.get(project + '/requirements'))['items'] if req['requirementId'] == requirement)
        confirm_requirement(pm, reviewer, customer, project, req)
    rejected = data(pm.get(project + '/closing/check'))
    assert {'COST_RECONCILIATION_REQUIRED', 'BLOCKING_RISK_UNRESOLVED'} <= {i['code'] for i in rejected['missing']}
    risk_proof = upload(pm, reviewer, project, 'risk', risk)
    now = datetime.now(UTC)
    for kind, actual in zip(('money', 'effort'), actuals):
        if Decimal(actual) == 0:
            continue
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
    checked = data(pm.get(project + '/closing/check'))
    assisted = data(pm.get(project + '/assistant/closing'))
    assert assisted['gateEligible'] is False and assisted['gateGaps'] == checked['missing']
    data(reviewer.post(f'risks/{risk}/close', headers=headers(), json={'resolutionEvidenceRef': risk_proof['evidenceRef']}))
    reco = project + '/cost-reconciliations'
    state = data(reconciler.get(reco))
    assert state['canCreate']
    data(reconciler.post(reco, headers=headers(state['revision']), json={'costHash': state['costHash'], 'evidenceRefs': [cost_proof['evidenceRef']]}))
    assert data(pm.get(project + '/closing/check'))['eligible']
    assert data(pm.get(project + '/assistant/closing'))['gateEligible'] is True
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
    # Even a credential delegated by the very reviewer who can approve does not gain approval authority.
    account = reviewer.get('auth/me').raise_for_status().json()['accountId']
    with engine.begin() as c:
        c.execute(db.user_roles.insert().values(user_id=account, role='delegator'))
    token = issue(reviewer, pid, ['pmo_project_get'])
    _, api, _, _ = live
    agent = {'Authorization': 'Bearer ' + token}
    assert data(api.get('/api/v1/' + project, headers=agent))['project']['stage'] == 'retrospective'
    for uri, body in ((f"approvals/{archive['requestId']}/decide", command),
                     (project + '/transitions', {'toStage': 'archived', 'approvalRequestId': archive['requestId']})):
        refused = api.post('/api/v1/' + uri, headers={**key, **agent}, json=body)
        assert (refused.status_code, refused.json()['code']) == (403, 'FORBIDDEN')
    assert stage(pm, project)['stage'] == 'retrospective'
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


def judgment(client, request, *, key=None, **details):
    return client.post(f"approvals/{request['requestId']}/decide", headers=key or headers(request['revision']),
        json={'decision': 'approve', 'rationale': 'Checked authority and the recorded reason.', **details})


def start_feasibility(live, team):
    pid, people, users, _ = team
    charter = start_charter(pid, users)
    score(users['reviewer'], charter, [4] * 4)
    data(decide(users['reviewer'], charter))
    project, _, _ = planning_setup(live[0], pid, people, users, seed_stage=False)
    return project, gate(users['pm'], project, 'feasibility')


def test_feasibility_termination_confirmation_is_scoped_and_atomic(live, team):
    engine, api, _, _ = live
    pid, _, users, _ = team
    pm, sponsor, owner, reviewer = (users[r] for r in ('pm', 'sponsor', 'initiator', 'reviewer'))
    project, review = start_feasibility(live, team)
    req = data(propose(pm, project, 'terminated'))
    why = {'reason': 'As original initiator I confirm stopping this proposal.'}
    path = f"approvals/{req['requestId']}/confirm-termination"
    assert [row['requestId'] for row in data(owner.get('me/termination-confirmations'))['items']] == [req['requestId']]
    assert data(reviewer.get('me/termination-confirmations'))['items'] == []
    assert judgment(sponsor, req).json()['code'] == 'TERMINATION_INITIATOR_CONFIRMATION_REQUIRED'
    assert judgment(pm, req).status_code == 403
    assert pm.post(path, headers=headers(req['revision']), json=why).status_code == 403
    score(reviewer, review, [3] * 5)
    assert owner.post(path, headers=headers(req['revision']), json=why).json()['code'] == 'TRANSITION_STALE'
    assert data(owner.get('me/termination-confirmations'))['items'] == []

    grant = issue(pm, pid, actions=('pmo_transition_request',))
    req = data(propose(pm, project, 'terminated'))
    path = f"approvals/{req['requestId']}/confirm-termination"
    auth = {'Authorization': f'Bearer {grant}', **headers(req['revision'])}
    assert api.post('/api/v1/' + path, headers=auth, json=why).status_code == 403
    assert api.post('/api/v1/' + path.replace('confirm-termination', 'decide'), headers=auth,
        json={'decision': 'approve', 'rationale': why['reason']}).status_code == 403
    sign_key = headers(req['revision'])
    sign = data(owner.post(path, headers=sign_key, json=why))
    assert sign['initiatorConfirmedAt'] and not sign['awaitsInitiator']
    assert data(owner.post(path, headers=sign_key, json=why)) == sign
    assert data(owner.get('me/termination-confirmations'))['items'] == []

    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER test_terminate_outbox_failure BEFORE INSERT ON outbox_events '
                          'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        assert judgment(sponsor, req).status_code >= 500
        assert stage(pm, project)['stage'] == 'feasibility_review'
        assert bench(reviewer, review)['review']['status'] == 'pending'
        with engine.connect() as c:
            assert not c.scalar(sa.text("SELECT count(*) FROM decisions WHERE project_id=:id AND subject LIKE 'project-transition:%'"), {'id': pid})
            assert not c.scalar(sa.text("""SELECT count(*) FROM audit_logs WHERE project_id=:id
                AND action='human_decide_approval' AND result='success'"""), {'id': pid})
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER test_terminate_outbox_failure ON outbox_events')
    decide_key = headers(req['revision'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, replayed = pool.map(lambda _: judgment(sponsor, req, key=decide_key), range(2))
    assert first.json() == replayed.json()
    decision = data(first)
    assert decision['status'] == 'applied' and stage(pm, project)['stage'] == 'terminated'
    stopped = bench(reviewer, review)
    assert stopped['review']['verdict'] == 'terminated' and stopped['decision']['decisionId'] == decision['decisionId']
    assert decide(reviewer, review).json()['code'] == 'REVIEW_NOT_PENDING'
    assert data(execute(pm, project, data(propose(pm, project, 'retrospective'))))['status'] == 'applied'
    assert bench(reviewer, review)['decision']['stage'] == 'terminated'
    assert data(pm.get(project + '/retrospective'))['kind'] == 'terminated'


def test_closing_rework_and_then_authorized_termination(live, team):
    _, _, users, _ = team
    pm, sponsor, reviewer, customer, accountant = (users[r] for r in ('pm', 'sponsor', 'reviewer', 'client_representative', 'outsider'))
    project, req, risk = execution_fixture(live, team)
    complete_milestones(pm, reviewer, project)
    data(execute(pm, project, data(propose(pm, project, 'delivering'))))
    _, risk_proof, cost_proof = close_materials(pm, reviewer, customer, project, req, risk)
    data(reviewer.post(f'risks/{risk}/close', headers=headers(), json={'resolutionEvidenceRef': risk_proof['evidenceRef']}))
    path = project + '/cost-reconciliations'
    reco = data(accountant.get(path))
    data(accountant.post(path, headers=headers(reco['revision']), json={'costHash': reco['costHash'], 'evidenceRefs': [cost_proof['evidenceRef']]}))
    review = gate(pm, project, 'closing')
    assert data(decide(reviewer, review, verdict='reject'))['decision']['stage'] == 'executing'
    data(execute(pm, project, data(propose(pm, project, 'delivering'))))
    review = gate(pm, project, 'closing')
    req = data(propose(pm, project, 'terminated'))
    assert judgment(reviewer, req).status_code == 403
    assert data(judgment(sponsor, req))['status'] == 'applied'
    assert bench(reviewer, review)['review']['verdict'] == 'terminated'
    assert stage(pm, project)['stage'] == 'terminated'


def test_pause_resume_checks_sponsor_deadline_and_stale_revision(live, team):
    _, _, users, _ = team
    pm, sponsor, reviewer = (users[r] for r in ('pm', 'sponsor', 'reviewer'))
    project, _, _ = execution_fixture(live, team)
    pause = data(propose(pm, project, 'on_hold'))
    expiry = datetime.now(UTC) + timedelta(hours=1)
    assert judgment(pm, pause, holdUntil=expiry.isoformat()).status_code == 403
    assert judgment(reviewer, pause, holdUntil=expiry.isoformat()).status_code == 403
    assert judgment(sponsor, pause).json()['code'] == 'INVALID_HOLD_UNTIL'
    assert judgment(sponsor, pause, holdUntil=(expiry - timedelta(hours=2)).isoformat()).json()['code'] == 'INVALID_HOLD_UNTIL'
    held = data(judgment(sponsor, pause, holdUntil=expiry.isoformat()))
    assert datetime.fromisoformat(held['holdUntil']) == expiry and stage(pm, project)['stage'] == 'on_hold'
    assert judgment(sponsor, pause, holdUntil=expiry.isoformat()).json()['code'] == 'VERSION_CONFLICT'
    resume = data(propose(pm, project, 'executing'))
    resumed = data(judgment(sponsor, resume))
    assert datetime.fromisoformat(resumed['holdUntil']) == expiry and stage(pm, project)['stage'] == 'executing'
