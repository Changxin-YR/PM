from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.requirement_disputes import OPEN, WITHDRAW, t
from test_health import code, prepare, sample
from test_lifecycle import close_materials, complete_milestones, execute, execution_fixture, gate, propose, stage, upload
from test_reviews import bench, data, decide, headers, issue, team as team


def test_current_customer_objection_changes_quality_and_replays_once(live, team, actor):
    engine, api, _, _ = live
    pid, people, users, _ = team
    pm, customer, reviewer = (users[who] for who in ('pm', 'client_representative', 'reviewer'))
    project, _ = prepare(live, team, priority='P1')
    r, = data(pm.get(project + '/requirements'))['items']
    req = r['requirementId']
    path = f'requirements/{req}/disputes'
    proof = upload(pm, reviewer, project, 'requirement', req)
    for action in ('deliver', 'accept'):
        r = data(pm.post(f'requirements/{req}/{action}', headers=headers(r['revision']),
                         json={'evidenceRef': proof['evidenceRef']}))
    before = sample(pm, project)
    assert before['qualityLine'] == 'green' and before['requirementSatisfactionRate'] == '0.000000'
    command = {'reason': 'The identified requirement does not include the required export columns.'}
    code(pm.post(path, headers=headers(r['revision']), json=command), 403, 'FORBIDDEN')
    code(customer.post(path, headers=headers(r['revision'] - 1), json=command), 409, 'VERSION_CONFLICT')
    token = issue(pm, pid, actions=('pmo_requirement_list',))
    agent = {'Authorization': f'Bearer {token}', **headers(r['revision'])}
    code(api.post('/api/v1/' + path, headers=agent, json=command), 403, 'FORBIDDEN')
    code(api.get('/api/v1/' + path, headers=agent), 403, 'FORBIDDEN')
    code(users['outsider'].get(path), 404, 'RESOURCE_NOT_FOUND')
    foreign, _ = actor()
    foreign_id = data(api.post('/api/v1/projects', headers={'Authorization': f'Bearer {foreign}', **headers()},
                                   json={'name': 'External organization fixture', 'code': 'FOREIGN'}))['projectId']
    with engine.begin() as c:
        outside_req = c.scalar(db.requirements.insert().values(project_id=foreign_id,
            created_by=people['pm'], desc='Foreign requirement', priority='P1', acceptance_criteria='Foreign criteria',
            status='baseline', baseline_version=1).returning(db.requirements.c.id))
    code(customer.get(f'requirements/{outside_req}/disputes'), 404, 'RESOURCE_NOT_FOUND')
    code(customer.post(f'requirements/{outside_req}/disputes', headers=headers(), json=command), 404, 'RESOURCE_NOT_FOUND')

    attempt = headers(r['revision'])
    with ThreadPoolExecutor(max_workers=2) as threads:
        responses = list(threads.map(lambda _: customer.post(path, headers=attempt, json=command), range(2)))
    receipt = data(responses[0]); assert responses[0].json() == responses[1].json()
    assert receipt['customerId'] == str(people['client_representative']) and receipt['requirementRevision'] == r['revision']
    code(customer.post(path, headers=headers(r['revision']), json=command), 409, 'CUSTOMER_DISPUTE_OPEN')
    code(customer.post(path, headers=attempt, json={'reason': 'Different statement.'}), 409, 'IDEMPOTENCY_CONFLICT')
    after = sample(pm, project)
    assert after['qualityLine'] == 'red' and after['health'] == 'red'
    assert after['inputs']['quality']['schedule'][0]['disputed']
    source = f"requirement-dispute:{receipt['disputeId']}@1"
    assert source in after['sourceRefs']
    code(customer.post(f'requirements/{req}/customer-confirm', headers=headers(r['revision']),
                       json={'evidenceRef': proof['evidenceRef']}), 422, 'CUSTOMER_DISPUTE_OPEN')

    withdrawal = path + f"/{receipt['disputeId']}/withdraw"
    reason = {'reason': 'Customer verified the missing columns and explicitly retracts the objection.'}
    code(reviewer.post(withdrawal, headers=headers(), json=reason), 403, 'FORBIDDEN')
    code(customer.post(withdrawal, headers=headers(2), json=reason), 409, 'VERSION_CONFLICT')
    key = headers()
    saved = customer.post(withdrawal, headers=key, json=reason)
    assert data(saved)['withdrawalReason'] == reason['reason']
    assert customer.post(withdrawal, headers=key, json=reason).json() == saved.json()
    code(customer.post(withdrawal, headers=headers(2), json=reason), 422, 'DISPUTE_ALREADY_WITHDRAWN')
    assert data(pm.get(path))['items'] == [data(saved)]
    assert sample(pm, project)['qualityLine'] == 'green'
    assert data(customer.post(f'requirements/{req}/customer-confirm', headers=headers(r['revision']),
                              json={'evidenceRef': proof['evidenceRef']}))['status'] == 'customer_confirmed'
    with engine.connect() as c:
        assert counts(c, pid) == (1, 1, 1, 1, 1)


def counts(c, pid):
    return tuple(c.execute(sa.text('''SELECT
        (SELECT count(*) FROM requirement_disputes WHERE project_id=:p),
        (SELECT count(*) FROM audit_logs WHERE project_id=:p AND action=:o AND result='success'),
        (SELECT count(*) FROM audit_logs WHERE project_id=:p AND action=:w AND result='success'),
        (SELECT count(*) FROM outbox_events WHERE payload->>'projectId'=CAST(:p AS text) AND event_type=:o),
        (SELECT count(*) FROM outbox_events WHERE payload->>'projectId'=CAST(:p AS text) AND event_type=:w)'''),
        {'p': pid, 'o': OPEN, 'w': WITHDRAW}).one())


def test_dispute_changes_frozen_closing_basis_and_is_atomic(live, team):
    engine, _, _, _ = live
    pid, _, users, _ = team
    pm, reviewer, customer, reconciler = (users[k] for k in ('pm', 'reviewer', 'client_representative', 'outsider'))
    project, req, risk = execution_fixture(live, team)
    complete_milestones(pm, reviewer, project)
    data(execute(pm, project, data(propose(pm, project, 'delivering'))))
    _, risk_proof, cost_proof = close_materials(pm, reviewer, customer, project, req, risk)
    data(reviewer.post(f'risks/{risk}/close', headers=headers(), json={'resolutionEvidenceRef': risk_proof['evidenceRef']}))
    reco = project + '/cost-reconciliations'
    state = data(reconciler.get(reco))
    data(reconciler.post(reco, headers=headers(state['revision']),
                         json={'costHash': state['costHash'], 'evidenceRefs': [cost_proof['evidenceRef']]}))
    closing = gate(pm, project, 'closing')
    path = f'requirements/{req}/disputes'
    current = data(customer.get(path))
    key = headers(current['requirementRevision'])
    command = {'reason': 'Customer found an omitted result after submission for closing review.'}
    with engine.begin() as c:
        c.exec_driver_sql('''CREATE FUNCTION test_reject_dispute_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic outbox failure'; END $$''')
        c.exec_driver_sql(f'''CREATE TRIGGER test_reject_dispute BEFORE INSERT ON outbox_events
            FOR EACH ROW WHEN (NEW.event_type = '{OPEN}') EXECUTE FUNCTION test_reject_dispute_outbox()''')
    try:
        code(customer.post(path, headers=key, json=command), 500, 'DATABASE_FAILURE')
        with engine.connect() as c:
            assert counts(c, pid) == (0, 0, 0, 0, 0)
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER test_reject_dispute ON outbox_events')
            c.exec_driver_sql('DROP FUNCTION test_reject_dispute_outbox()')
    opened = data(customer.post(path, headers=key, json=command))
    assert data(pm.get(project + '/requirement-metrics'))['confirmedWeight'] == 0
    code(decide(reviewer, closing), 409, 'REVIEW_SNAPSHOT_STALE')
    data(pm.post(closing + '/withdraw', headers=headers(bench(pm, closing)['review']['revision']),
                 json={'reason': 'Customer raised a formal objection to the submitted material.'}))
    assert any(i['code'] == 'CUSTOMER_DISPUTE_OPEN' for i in data(pm.get(project + '/closing/check'))['missing'])
    assert stage(pm, project)['stage'] == 'delivering'
    withdrawn = data(customer.post(path + f"/{opened['disputeId']}/withdraw", headers=headers(),
                     json={'reason': 'Customer has rechecked the source and explicitly withdrew the objection.'}))
    assert withdrawn['revision'] == 2
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
        c.execute(t.update().where(t.c.id == UUID(opened['disputeId'])).values(reason='Rewrite old statement'))
    new_review = gate(pm, project, 'closing')
    assert new_review != closing
    assert data(decide(reviewer, new_review))['decision']['stage'] == 'closed'
    code(customer.post(path, headers=headers(current['requirementRevision']), json=command), 422, 'REQUIREMENT_DISPUTE_LOCKED')
