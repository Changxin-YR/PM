"""Section 20.5: real HTTP moves and their committed receipts on all 23 edges."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.audit_store import audit
from pmo.contracts import RULE_VERSION
from pmo.projects import fingerprint
from pmo.reviews import snapshots, transitions
from test_lifecycle import (ANSWERS, close_materials, complete_milestones, execution_fixture, grant_closure_roles, judgment,
                            propose, stage, start_feasibility)
from test_reviews import bench, data, headers, issue, outcome, planning_setup, prepare_charter, score, team as team


class StageChecks:
    def __init__(self, live, team):
        self.engine, self.api, _, _ = live
        self.pid, self.people, self.users, _ = team
        self.pm = self.users['pm']
        self.reviewer = self.users['reviewer']
        self.path = f'projects/{self.pid}'
        self.agent = issue(self.pm, self.pid, ('pmo_project_get',))

    def request(self, actor, path, key, body):
        if actor == 'agent':
            return self.api.post(f'/api/v1/{path}', headers={**key, 'Authorization': f'Bearer {self.agent}'}, json=body)
        return self.users[actor].post(path, headers=key, json=body)

    def refuse(self, actor, path, key, body, operation, *, status=403):
        before = stage(self.pm, self.path)
        response = self.request(actor, path, key, body)
        assert response.status_code == status, response.text
        rid = response.headers['X-Request-ID']
        assert response.json()['requestId'] == rid
        assert stage(self.pm, self.path) == before
        with self.engine.connect() as c:
            assert c.execute(sa.select(audit.c.action, audit.c.result, audit.c.error_code, audit.c.decision_id)
                .where(audit.c.request_id == rid)).one() == (operation, 'denied', response.json()['code'], None)
            assert c.scalar(sa.select(transitions.c.project_id).where(transitions.c.request_id == UUID(rid))) is None
            assert c.scalar(sa.select(db.outbox_events.c.id).where(db.outbox_events.c.dedupe_key == rid)) is None

    def move(self, edge, *, actor, denied, path, operation, body, review=None, revision=None, decision=False):
        origin, target = edge
        before = stage(self.pm, self.path)
        assert before['stage'] == origin
        key = headers(before['revision'] if revision is None else revision)
        self.refuse(denied, path, key, body, operation)
        if operation.startswith('human_'):
            self.refuse('agent', path, key, body, operation)
        response = self.request(actor, path, key, body)
        result = data(response)
        rid = response.headers['X-Request-ID']
        repeated = self.request(actor, path, key, body)
        assert (repeated.status_code, repeated.json()) == (response.status_code, response.json())
        after = stage(self.pm, self.path)
        assert (after['stage'], after['revision']) == (target, before['revision'] + 1)
        decision_id = (result['decision']['decisionId'] if review else result['decisionId']) if decision else None
        decision_uuid = UUID(decision_id) if decision_id else None
        who = self.people[actor]
        with self.engine.connect() as c:
            move = c.execute(sa.select(transitions).where(transitions.c.request_id == UUID(rid))).mappings().one()
            assert (str(move.project_id), move.actor_type, move.actor_id, move.from_stage, move.to_stage,
                    move.from_revision, move.to_revision) == (self.pid, 'human', who, origin, target,
                                                              before['revision'], after['revision'])
            submitted = operation == 'pmo_review_submit'
            review_id = review or result.get('reviewId')
            assert (move.review_id, move.approval_request_id) == (
                UUID(review_id) if review_id else None, None if review_id else UUID(result['requestId']))
            resource = move.review_id or move.approval_request_id
            approvals = db.approval_requests
            approval = c.execute(sa.select(approvals).where(approvals.c.id == resource)).mappings().one()
            expected = ('pending' if submitted else 'withdrawn' if operation == 'human_review_withdraw' else
                        'approved' if review and body['verdict'] == 'pass' else 'rejected' if review else 'applied')
            assert (approval.status, approval.rule_version, str(approval.project_id)) == (expected, RULE_VERSION, self.pid)
            if review_id:
                s = snapshots.c
                frozen = c.execute(sa.select(snapshots).where(s.review_id == resource)).mappings().one()
                assert frozen.snapshot_hash == fingerprint(frozen.materials) == approval.snapshot_hash
                assert frozen.source_refs
            else:
                basis = c.scalar(sa.text('SELECT basis FROM project_transition_intents WHERE approval_id=:id'), {'id': resource})
                assert approval.snapshot_hash == fingerprint(basis)

            log = c.execute(sa.select(audit).where(audit.c.request_id == rid)).mappings().one()
            assert (log.action, log.result, log.actor_type, log.project_id, log.decision_id, log.idempotency_key) == (
                operation, 'success', 'human', UUID(self.pid), decision_uuid, key['Idempotency-Key'])
            assert log.actor_id == c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == who))
            i = db.idempotency_keys.c
            receipt = c.execute(sa.select(db.idempotency_keys).where(i.actor_type == 'human', i.actor_id == log.actor_id,
                i.operation == operation, i.idempotency_key == key['Idempotency-Key'])).mappings().one()
            assert (receipt.response_status, receipt.response_body, receipt.resource_id) == (200, response.json(), resource)
            o = db.outbox_events.c
            event_type, event = c.execute(sa.select(o.event_type, o.payload).where(o.dedupe_key == rid)).one()
            assert event_type == ('review.submitted' if submitted else operation)
            assert (event['projectId'], event.get('stage', event.get('toStage')), event.get('decisionId')) == (self.pid, target, decision_id)
            rid_field = 'reviewId' if review_id else 'approvalRequestId'
            assert event[rid_field] == str(resource)
            if decision:
                d = db.decisions.c
                recorded = c.execute(sa.select(d.project_id, d.decided_by, d.rationale).where(d.id == decision_uuid)).one()
                assert recorded == (UUID(self.pid), who, body['rationale'])
                table = 'review_decision_snapshots' if review else 'project_transition_outcomes'
                payload, digest, nonce = c.execute(sa.text(f'SELECT payload,payload_hash,request_id FROM {table} WHERE decision_id=:id'),
                                                    {'id': decision_uuid}).one()
                assert (fingerprint(payload), nonce) == (digest, UUID(rid))
        return result

    def submit(self, kind, edge, *, actor='pm', denied='reviewer'):
        result = self.move(edge, actor=actor, denied=denied, path=self.path + '/reviews', operation='pmo_review_submit',
                           body={'reviewType': kind})
        return 'reviews/' + result['reviewId']

    def decide(self, path, edge, verdict='pass', *, denied='pm'):
        view = bench(self.reviewer, path)
        return self.move(edge, actor='reviewer', denied=denied, path=path + '/decisions', operation='human_review_decide',
                         body=outcome(view, verdict), review=view['review']['reviewId'], revision=view['review']['revision'], decision=True)

    def apply(self, edge, *, actor='pm', approver=None, sign=False):
        origin, target = edge
        self.refuse('reviewer', self.path + '/transition-requests', headers(stage(self.pm, self.path)['revision']),
                    {'toStage': target, 'reason': 'Change of phase requested.'}, 'pmo_transition_request')
        intent = data(propose(self.users[actor], self.path, target))
        assert intent['status'] == 'pending' and stage(self.pm, self.path)['stage'] == origin
        request_id = intent['requestId']
        if not approver:
            return self.move(edge, actor=actor, denied='sponsor', path=self.path + '/transitions', operation='human_execute_transition',
                             body={'approvalRequestId': request_id, 'toStage': target})
        if sign:
            response = judgment(self.users[approver], intent)
            assert (response.status_code, response.json()['code']) == (422, 'TERMINATION_INITIATOR_CONFIRMATION_REQUIRED')
            signoff = data(self.users['initiator'].post(f'approvals/{request_id}/confirm-termination',
                headers=headers(intent['revision']), json={'reason': 'Independent initiator accepts the termination.'}))
            assert signoff['initiatorConfirmedAt']
        body = {'decision': 'approve', 'rationale': 'Checked role and current material of the proposed edge.'}
        if target == 'on_hold':
            body['holdUntil'] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        return self.move(edge, actor=approver, denied=actor, path=f'approvals/{request_id}/decide',
                         operation='human_decide_approval', body=body, decision=True)


def closed_material(checks, requirement, risk):
    pm, reviewer = checks.pm, checks.reviewer
    _, risk_proof, cost_proof = close_materials(pm, reviewer, checks.users['client_representative'], checks.path, requirement, risk)
    data(reviewer.post(f'risks/{risk}/close', headers=headers(), json={'resolutionEvidenceRef': risk_proof['evidenceRef']}))
    reconciler = checks.users['outsider']
    reco = checks.path + '/cost-reconciliations'
    state = data(reconciler.get(reco))
    data(reconciler.post(reco, headers=headers(state['revision']), json={'costHash': state['costHash'],
                                                                       'evidenceRefs': [cost_proof['evidenceRef']]}))


def test_normal_and_rework_edges_have_role_bound_receipts(live, team):
    grant_closure_roles(live, team)
    f = StageChecks(live, team)
    prepare_charter(f.users['initiator'], f.path)
    charter = f.submit('charter', ('drafting', 'charter_review'), actor='initiator')
    score(f.reviewer, charter, [3] * 4)
    f.decide(charter, ('charter_review', 'drafting'), 'redirect', denied='initiator')
    charter = f.submit('charter', ('drafting', 'charter_review'), actor='pm', denied='sponsor')
    score(f.reviewer, charter, [4] * 4)
    f.decide(charter, ('charter_review', 'designing'))
    _, requirement, risk = planning_setup(f.engine, f.pid, f.people, f.users, seed_stage=False)
    plan = f.submit('feasibility', ('designing', 'feasibility_review'), denied='sponsor')
    score(f.reviewer, plan, [2, 3, 3, 3, 3])
    f.decide(plan, ('feasibility_review', 'designing'), 'reject')
    plan = f.submit('feasibility', ('designing', 'feasibility_review'))
    score(f.reviewer, plan, [3] * 5)
    f.decide(plan, ('feasibility_review', 'executing'))
    f.apply(('executing', 'on_hold'), approver='sponsor')
    f.apply(('on_hold', 'executing'), approver='sponsor')
    complete_milestones(f.pm, f.reviewer, f.path)
    f.apply(('executing', 'delivering'))
    f.apply(('delivering', 'executing'))
    f.apply(('executing', 'delivering'))
    closed_material(f, requirement, risk)
    closing = f.submit('closing', ('delivering', 'closing_review'), denied='client_representative')
    view = bench(f.reviewer, closing)['review']
    f.move(('closing_review', 'executing'), actor='pm', denied='reviewer', path=closing + '/withdraw',
           operation='human_review_withdraw', body={'reason': 'New delivery evidence is expected.'}, review=view['reviewId'],
           revision=view['revision'])
    f.apply(('executing', 'delivering'))
    closing = f.submit('closing', ('delivering', 'closing_review'))
    f.decide(closing, ('closing_review', 'closed'))
    f.apply(('closed', 'retrospective'))
    retro = f.path + '/retrospective'
    view = data(f.pm.get(retro))
    assert view['kind'] == 'normal'
    data(f.pm.put(retro, headers=headers(view['revision']), json={'answers': ANSWERS}))
    lesson = data(f.pm.post(f.path + '/lessons', headers=headers(), json={
        'category': 'delivery', 'what': 'Verified close sources', 'rootCause': 'Independent records permitted reconciliation',
        'advice': 'Retain source exports and original evidence'}))
    data(f.reviewer.post(f"lessons/{lesson['lessonId']}/confirm", headers=headers(lesson['revision']), json={}))
    f.apply(('retrospective', 'archived'), approver='reviewer')


def test_formal_rejection_and_one_retry_have_role_bound_receipts(live, team):
    f = StageChecks(live, team)
    prepare_charter(f.users['initiator'], f.path)
    chart = f.submit('charter', ('drafting', 'charter_review'), actor='initiator')
    score(f.reviewer, chart, [1, 5, 5, 5])
    f.decide(chart, ('charter_review', 'rejected'), 'reject', denied='initiator')
    f.move(('rejected', 'drafting'), actor='initiator', denied='reviewer', path=f.path + '/resubmit',
           operation='human_charter_resubmit', body={'reason': 'A revised proposal will address the veto.'})
    chart = f.submit('charter', ('drafting', 'charter_review'), actor='initiator')
    score(f.reviewer, chart, [2] * 4)
    f.decide(chart, ('charter_review', 'rejected'), 'reject', denied='initiator')
    f.apply(('rejected', 'terminated'), approver='sponsor')
    f.apply(('terminated', 'retrospective'))
    assert data(f.pm.get(f.path + '/retrospective'))['kind'] == 'terminated'


@pytest.mark.parametrize('origin', ('feasibility_review', 'executing', 'on_hold', 'closing_review'))
def test_other_termination_edges_have_role_bound_receipts(live, team, origin):
    f = StageChecks(live, team)
    review = None
    if origin == 'feasibility_review':
        _, review = start_feasibility(live, team)
    else:
        _, requirement, risk = execution_fixture(live, team)
        if origin == 'on_hold':
            f.apply(('executing', 'on_hold'), approver='sponsor')
        if origin == 'closing_review':
            complete_milestones(f.pm, f.reviewer, f.path)
            f.apply(('executing', 'delivering'))
            closed_material(f, requirement, risk)
            review = f.submit('closing', ('delivering', 'closing_review'))
    receipt = f.apply((origin, 'terminated'), approver='sponsor', sign=origin == 'feasibility_review')
    if review:
        stopped = bench(f.reviewer, review)
        assert stopped['review']['verdict'] == 'terminated' and stopped['decision']['decisionId'] == receipt['decisionId']
