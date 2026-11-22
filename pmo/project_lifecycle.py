"""Versioned non-gate stage requests; gate decisions stay in the review service."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AwareDatetime, Field
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import RowMapping

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .change_intents import snapshot
from .contracts import RULE_VERSION, Result, Strict
from .evidence import verified_link
from .gates import lock_project
from .identity import Failure, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_assignment, active_member
from .requirement_delivery import synthetic_confirmation

router = APIRouter(prefix='/api/v1', tags=['Project lifecycle'])
REQUEST = 'pmo_transition_request'
APPLY = 'human_execute_transition'
INBOX = 'human_approval_inbox'
DECIDE = 'human_decide_approval'
RESUBMIT = 'human_charter_resubmit'
TERMINATION_INBOX = 'human_termination_confirmation_inbox'
TERMINATION_CONFIRM = 'human_termination_confirm'
ACTION = 'project_stage_transition'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}

DIRECT = {('executing', 'delivering'), ('delivering', 'executing'),
          ('closed', 'retrospective'), ('terminated', 'retrospective')}
REVIEW_TERMINATIONS = {'feasibility_review': 'feasibility', 'closing_review': 'closing'}
APPROVED = {('executing', 'on_hold'), ('on_hold', 'executing'), ('rejected', 'terminated'),
            ('executing', 'terminated'), ('on_hold', 'terminated'), ('retrospective', 'archived')} | {
                (stage, 'terminated') for stage in REVIEW_TERMINATIONS}

intents = sa.table('project_transition_intents', sa.column('approval_id', sa.Uuid), sa.column('from_stage'),
    sa.column('to_stage'), sa.column('basis', JSONB), sa.column('source_refs', JSONB), sa.column('reason'),
    sa.column('actor_type'), sa.column('requested_at', sa.DateTime(timezone=True)))
results = sa.table('project_transition_outcomes', sa.column('approval_id', sa.Uuid), sa.column('decision_id', sa.Uuid),
    sa.column('payload', JSONB), sa.column('payload_hash'), sa.column('request_id', sa.Uuid))
termination_signoffs = sa.table('project_termination_confirmations', sa.column('approval_id', sa.Uuid),
    sa.column('signer_id', sa.Uuid), sa.column('assignment_id', sa.Uuid), sa.column('payload_hash'), sa.column('reason'),
    sa.column('signed_at', sa.DateTime(timezone=True)), sa.column('request_id', sa.Uuid))
transition_grants = sa.table('project_transition_grants',
    *(sa.column(k, sa.Uuid) for k in ('id', 'org_id', 'project_id', 'person_id')),
    *(sa.column(k, sa.String) for k in ('from_stage', 'to_stage', 'source_ref', 'revocation_ref')),
    *(sa.column(k, sa.DateTime(timezone=True)) for k in ('valid_from', 'expires_at', 'recorded_at', 'revoked_at')))
approval = db.approval_requests


@dataclass(frozen=True)
class DecisionAuthority:
    ref: str
    grant: RowMapping | None = None


class Propose(Strict):
    toStage: Literal['charter_review', 'designing', 'feasibility_review', 'executing', 'on_hold', 'delivering',
                     'closing_review', 'closed', 'retrospective', 'terminated', 'archived']
    reason: str = Field(default='', max_length=2000)


class Apply(Strict):
    toStage: str = Field(max_length=32)
    approvalRequestId: UUID


class Judge(Strict):
    decision: Literal['approve', 'reject']
    rationale: str = Field(min_length=4, max_length=2000, pattern=r'\S')
    holdUntil: AwareDatetime | None = None


class Resubmit(Strict):
    reason: str = Field(min_length=4, max_length=2000, pattern=r'\S')


class TransitionRequest(Strict):
    requestId: UUID
    projectId: UUID
    fromStage: str
    toStage: str
    status: str
    revision: int


class Entry(TransitionRequest):
    requestedBy: UUID
    requestedAt: datetime
    reason: str
    canExecute: bool
    canDecide: bool
    stale: bool
    decisionId: UUID | None = None
    decisionReason: str | None = None
    authorityRef: str | None = None
    authoritySourceRef: str | None = None
    holdUntil: datetime | None = None
    awaitsInitiator: bool = False
    initiatorConfirmedAt: datetime | None = None


class TerminationEntry(Entry):
    projectName: str


class TerminationPage(Strict):
    items: list[TerminationEntry]


class Page(Strict):
    items: list[Entry]
    projectId: UUID
    projectRevision: int
    stage: str
    requestable: list[str]
    canResubmit: bool
    policyPending: bool


def one(c, table, *criteria):
    return c.execute(sa.select(table).where(*criteria)).mappings().one_or_none()


def role(c, p, person, key):
    return p[key + '_id'] == person and active_member(c, p, person, key)


def direct_actor(c, a, p, edge):
    return a.kind == 'human' and edge in DIRECT and role(c, p, a.person, 'pm')


def can_request(c, p, person, edge):
    return (edge in DIRECT | APPROVED and role(c, p, person, 'pm')
            or edge in APPROVED and edge[1] == 'terminated' and role(c, p, person, 'sponsor'))


def authority(c, a, p, edge, req) -> DecisionAuthority | None:
    if a.kind != 'human' or edge not in APPROVED or a.person in (req.requested_by, req.delegated_by):
        return None
    if edge[1] == 'archived':
        if a.person in (p.initiator_id, p.pm_id, p.sponsor_id):
            return None
        assignment = active_assignment(c, p, a.person, 'reviewer')
        return DecisionAuthority(f'project-member:{assignment.id}') if assignment else None
    sponsor = active_assignment(c, p, a.person, 'sponsor') if p.sponsor_id == a.person else None
    if sponsor:
        return DecisionAuthority(f'project-member:{sponsor.id}')
    if a.org != p.org_id:
        return None
    g, m, n = transition_grants, db.project_members, db.persons
    row = c.execute(sa.select(g, m.c.id.label('membership_id')).select_from(
        g.join(m, sa.and_(m.c.project_id == g.c.project_id, m.c.person_id == g.c.person_id))
         .join(n, n.c.id == g.c.person_id)).where(
        g.c.org_id == a.org, g.c.project_id == p.id, g.c.person_id == a.person, g.c.from_stage == edge[0],
        g.c.to_stage == edge[1], g.c.revoked_at.is_(None), g.c.valid_from <= sa.func.clock_timestamp(),
        g.c.expires_at > sa.func.clock_timestamp(), n.c.org_id == a.org, n.c.user_id == a.account, n.c.is_active,
        m.c.role != 'initiator', m.c.assignment_status == 'accepted', m.c.accepted_at.is_not(None), m.c.revoked_at.is_(None))
        .order_by(g.c.expires_at.desc(), g.c.id, m.c.id).limit(1).with_for_update(read=True, of=(g, m, n))).mappings().one_or_none()
    # A grant can expire while SELECT FOR SHARE waits for a concurrent writer.
    if row is None or not row.valid_from <= c.scalar(sa.select(sa.func.clock_timestamp())) < row.expires_at:
        return None
    return DecisionAuthority(f'transition-grant:{row.id}', row)


def milestone_basis(c, a, p):
    m = db.milestones
    rows = list(c.execute(sa.select(m).where(m.c.project_id == p.id, ~m.c.is_cancelled)
                          .order_by(m.c.id)).mappings())
    if not rows:
        raise Failure(422, 'MILESTONE_COMPLETION_REQUIRED', project_id=p.id)
    inputs, refs = [], []
    for row in rows:
        if not row.actual_date or not row.evidence_ref:
            raise Failure(422, 'MILESTONE_COMPLETION_REQUIRED', project_id=p.id)
        proof = verified_link(c, a, row.evidence_ref, p.id, 'milestone', row.id, shared=True)
        receipt = c.execute(sa.text('''SELECT * FROM milestone_completions WHERE milestone_id=:id
            AND evidence_id=:e AND actual_date=:at AND revision=:revision'''),
            dict(id=row.id, e=proof.id, at=row.actual_date, revision=row.revision)).mappings().one_or_none()
        if not receipt:
            raise Failure(422, 'MILESTONE_COMPLETION_RECEIPT_REQUIRED', project_id=p.id)
        inputs.append([str(row.id), row.revision, snapshot(receipt), proof.revision, proof.sha256])
        refs.extend([f'milestone:{row.id}@{row.revision}', f'evidence:{proof.id}'])
    return {'milestoneCompletions': inputs}, refs


def rejected_basis(c, p):
    row = c.execute(sa.text('''SELECT d.id, d.decided_at, d.rationale FROM decisions d
        JOIN reviews r ON r.id=d.review_id JOIN review_decision_snapshots s ON s.decision_id=d.id
        WHERE d.project_id=:id AND r.type='charter' AND d.option='reject'
        ORDER BY d.decided_at DESC, d.id LIMIT 1'''), {'id': p.id}).mappings().one_or_none()
    if not row:
        raise Failure(422, 'CHARTER_REJECTION_REQUIRED', project_id=p.id)
    used = c.scalar(sa.text('''SELECT count(*) FROM project_stage_transitions WHERE
        project_id=:id AND from_stage='rejected' AND to_stage='drafting' '''), {'id': p.id})
    now = c.scalar(sa.select(sa.func.clock_timestamp()))
    deadline = row.decided_at + timedelta(days=30)
    return row, used, deadline, now


def terminal_basis(c, p):
    row = c.execute(sa.text('''SELECT x.decision_id, x.payload_hash FROM project_transition_outcomes x
        JOIN project_stage_transitions s ON s.approval_request_id=x.approval_id
        WHERE s.project_id=:pid AND s.to_stage='terminated' ORDER BY s.to_revision DESC LIMIT 1'''),
        {'pid': p.id}).mappings().one_or_none()
    if not row:
        raise Failure(422, 'TERMINATION_DECISION_REQUIRED', project_id=p.id)
    return {'terminationDecision': snapshot(row)}, [f'decision:{row.decision_id}']


def reviewed_termination_basis(c, p, gate):
    from .reviews import APPROVAL_ACTION, reviews, snapshots
    r, s, q = reviews.c, snapshots.c, approval.c
    row = c.execute(sa.select(r.id, r.revision, r.snapshot_hash, q.payload_hash.label('request_hash'))
        .select_from(reviews.join(snapshots, s.review_id == r.id).join(approval, q.id == r.id))
        .where(r.project_id == p.id, r.type == gate, r.status == 'pending', q.project_id == p.id,
               q.action == APPROVAL_ACTION, q.status == 'pending', r.snapshot_hash == s.snapshot_hash,
               q.snapshot_hash == s.snapshot_hash).with_for_update(read=True, of=(reviews, approval))).mappings().one_or_none()
    if row is None:
        raise Failure(422, 'PENDING_REVIEW_REQUIRED', project_id=p.id)
    contents, refs = {'review': snapshot(row)}, [f'review:{row.id}@{row.revision}', f'review-snapshot:{row.id}']
    if gate == 'feasibility':
        assignment = active_assignment(c, p, p.initiator_id, 'initiator')
        if not assignment:
            raise Failure(422, 'INITIATOR_ASSIGNMENT_REQUIRED', project_id=p.id)
        contents['initiatorAssignment'] = snapshot(assignment)
        refs.append(f'project-member:{assignment.id}')
    return contents, refs


def valid_termination_confirmation(c, req, intent):
    t = termination_signoffs.c
    assignment = intent.basis.get('initiatorAssignment')
    if not assignment:
        return None
    return one(c, termination_signoffs, t.approval_id == req.id, t.payload_hash == req.payload_hash,
               t.assignment_id == UUID(assignment['id']), t.signer_id == UUID(assignment['person_id']))


def hold_basis(c, p):
    row = c.execute(sa.text('''SELECT x.decision_id,x.payload,x.payload_hash FROM project_transition_outcomes x
        JOIN project_stage_transitions s ON s.approval_request_id=x.approval_id
        WHERE s.project_id=:pid AND s.to_stage='on_hold' ORDER BY s.to_revision DESC LIMIT 1'''),
        {'pid': p.id}).mappings().one_or_none()
    if not row or fingerprint(row.payload) != row.payload_hash or not row.payload.get('holdUntil'):
        raise Failure(422, 'HOLD_DECISION_REQUIRED', project_id=p.id)
    return {'holdDecision': str(row.decision_id), 'holdDecisionHash': row.payload_hash, 'holdUntil': row.payload['holdUntil']}, [f'decision:{row.decision_id}']


def basis(c, a, p, edge):
    if p.stage != edge[0]:
        raise Failure(409, 'TRANSITION_STALE', project_id=p.id)
    if edge == ('executing', 'delivering'):
        return milestone_basis(c, a, p)
    if edge == ('retrospective', 'archived'):
        from .retrospectives import archive_basis
        return archive_basis(c, a, p)
    if edge == ('terminated', 'retrospective'):
        return terminal_basis(c, p)
    if edge[1] == 'terminated' and edge[0] in REVIEW_TERMINATIONS:
        return reviewed_termination_basis(c, p, REVIEW_TERMINATIONS[edge[0]])
    if edge[0] == 'on_hold':
        return hold_basis(c, p)
    if edge[0] == 'rejected':
        row, used, deadline, now = rejected_basis(c, p)
        if edge[1] == 'drafting' and (now > deadline or used):
            raise Failure(422, 'CHARTER_RESUBMISSION_LIMIT', project_id=p.id)
        if edge[1] == 'terminated' and now <= deadline and not used:
            raise Failure(422, 'CHARTER_RESUBMISSION_WINDOW_OPEN', project_id=p.id)
        return {'rejection': snapshot(row), 'resubmissions': used}, [f'decision:{row.id}']
    if edge == ('closed', 'retrospective'):
        row = c.execute(sa.text('''SELECT d.id, d.decided_at, s.payload_hash FROM decisions d JOIN reviews r ON r.id=d.review_id
            JOIN review_decision_snapshots s ON s.decision_id=d.id WHERE d.project_id=:pid AND r.type='closing'
            AND d.option IN ('pass','conditional_pass') ORDER BY d.decided_at DESC LIMIT 1'''),
            {'pid': p.id}).mappings().one_or_none()
        if not row:
            raise Failure(422, 'CLOSING_DECISION_REQUIRED', project_id=p.id)
        return {'closingDecision': snapshot(row), 'retrospectiveDueAt': (row.decided_at + timedelta(days=14)).isoformat()}, [f'decision:{row.id}']
    return {}, []


def payload(p, edge, person, kind, why, contents):
    return [str(p.id), p.revision, *edge, str(person), kind, why, contents, RULE_VERSION]


def lookup(c, pid, approval_id):
    i = one(c, intents, intents.c.approval_id == approval_id)
    req = one(c, approval, approval.c.id == approval_id, approval.c.project_id == pid, approval.c.action == ACTION)
    if not i or not req:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return req, i


def current(c, a, p, req, intent):
    edge = (intent.from_stage, intent.to_stage)
    if (req.expected_revision != p.revision or req.rule_version != RULE_VERSION or p.stage != intent.from_stage
        or req.delegated_by != (req.requested_by if intent.actor_type == 'agent' else None)
        or not can_request(c, p, req.requested_by, edge)
        or fingerprint(payload(p, edge, req.requested_by, intent.actor_type, intent.reason, intent.basis)) != req.payload_hash):
        return False
    try:
        contents, _ = basis(c, a, p, edge)
        return req.snapshot_hash == fingerprint(contents) == fingerprint(intent.basis)
    except Failure:
        return False


def persist(c, a, p, edge, why, contents, refs):
    req = c.execute(approval.insert().values(action=ACTION, project_id=p.id, status='pending', expected_revision=p.revision,
        requested_by=a.person, delegated_by=a.person if a.kind == 'agent' else None, reason=why, rule_version=RULE_VERSION,
        payload_hash=fingerprint(payload(p, edge, a.person, a.kind, why, contents)), snapshot_hash=fingerprint(contents))
        .returning(approval)).mappings().one()
    i = c.execute(intents.insert().values(approval_id=req.id, from_stage=edge[0], to_stage=edge[1], basis=contents,
        source_refs=refs, reason=why, actor_type=a.kind).returning(intents)).mappings().one()
    return req, i


def entry(c, a, p, req, intent):
    edge = (intent.from_stage, intent.to_stage)
    stale = req.status == 'pending' and not current(c, a, p, req, intent)
    outcome = one(c, results, results.c.approval_id == req.id)
    evidence = outcome.payload if outcome else {}
    confirmation = valid_termination_confirmation(c, req, intent)
    return Entry(requestId=req.id, projectId=p.id, fromStage=edge[0], toStage=edge[1], status=req.status,
        revision=req.expected_revision, requestedBy=req.requested_by, requestedAt=intent.requested_at, reason=intent.reason,
        canExecute=req.status == 'pending' and not stale and direct_actor(c, a, p, edge), stale=stale,
        canDecide=req.status == 'pending' and not stale and bool(authority(c, a, p, edge, req)) and synthetic_confirmation(c),
        decisionId=outcome.decision_id if outcome else None, decisionReason=evidence.get('rationale'),
        authorityRef=evidence.get('authorityRef'), authoritySourceRef=evidence.get('authorityGrant', {}).get('source_ref'),
        holdUntil=evidence.get('holdUntil') or intent.basis.get('holdUntil'),
        awaitsInitiator=req.status == 'pending' and bool(intent.basis.get('initiatorAssignment')) and confirmation is None,
        initiatorConfirmedAt=confirmation.signed_at if confirmation else None)


def finish(c, a, p, req, op, rid, key, signature, item, refs):
    sources = [f'approval:{req.id}', *refs]
    event = {'projectId': str(p.id), 'approvalRequestId': str(req.id), 'status': item.status, 'toStage': item.toStage}
    decision_id = None
    if op == DECIDE:
        assert isinstance(item, Entry) and item.decisionId is not None and item.authorityRef is not None
        decision_id = item.decisionId
        sources.extend((f'decision:{decision_id}@1', item.authorityRef))
        event['decisionId'] = str(decision_id)
        event['authorityRef'] = item.authorityRef
    result = Result[type(item)](data=item, meta=meta(op, sources))
    response = result.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, req.id, p.id, signature, response, fingerprint(response), req.snapshot_hash,
                   decision_id=decision_id)
    c.execute(db.outbox_events.insert().values(event_type=op, dedupe_key=rid, status='pending', payload=event))
    return result


@router.post('/projects/{projectId}/transition-requests', operation_id=REQUEST, response_model_exclude_none=True)
def propose(projectId: UUID, command: Propose, authorization: Auth, key: Key, expected: Revision) -> Result[TransitionRequest]:
    sig = fingerprint([str(projectId), expected, command.model_dump(mode='json')])
    def write(c, a, rid):
        p = lock_project(c, a, projectId, write=True)
        if not (role(c, p, a.person, 'pm') or command.toStage == 'terminated' and role(c, p, a.person, 'sponsor')):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        old = replay(c, a, REQUEST, key, sig, Result[TransitionRequest])
        if old:
            return old
        if p.revision != int(expected):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        edge = (p.stage, command.toStage)
        if not can_request(c, p, a.person, edge):
            raise Failure(422, 'TRANSITION_NOT_ALLOWED', project_id=p.id)
        why = command.reason.strip()
        if len(why) < 3:
            raise Failure(422, 'TRANSITION_REASON_REQUIRED', project_id=p.id)
        contents, refs = basis(c, a, p, edge)
        duplicate = sa.select(approval.c.id).join(intents, intents.c.approval_id == approval.c.id).where(
            approval.c.project_id == p.id, approval.c.action == ACTION, approval.c.status == 'pending',
            approval.c.expected_revision == p.revision, approval.c.snapshot_hash == fingerprint(contents),
            intents.c.from_stage == edge[0], intents.c.to_stage == edge[1])
        if edge[1] == 'terminated' and a.person == p.pm_id and p.sponsor_id not in (None, a.person):
            # The Sponsor cannot approve their own intent. Preserve it without blocking the PM's independent request.
            duplicate = duplicate.where(approval.c.requested_by != p.sponsor_id)
        if c.scalar(duplicate.limit(1)):
            raise Failure(409, 'TRANSITION_REQUEST_PENDING', project_id=p.id)
        req, i = persist(c, a, p, edge, why, contents, refs)
        item = TransitionRequest(requestId=req.id, projectId=p.id, fromStage=i.from_stage, toStage=i.to_stage,
                                 status=req.status, revision=req.expected_revision)
        return finish(c, a, p, req, REQUEST, rid, key, sig, item, refs)
    return run(authorization, REQUEST, write)


def record_transition(c, a, p, req, intent, rid, *, judge=None, grant=None):
    from .reviews import APPROVAL_ACTION, reviews, transition_project
    at = c.scalar(sa.select(sa.func.clock_timestamp()))
    review = intent.basis.get('review')
    approval_of_review_termination = judge and judge.decision == 'approve' and review is not None
    review_id = UUID(review['id']) if approval_of_review_termination else None
    if judge:
        assert grant is not None
        if grant.grant is not None and not grant.grant.valid_from <= at < grant.grant.expires_at:
            raise Failure(403, 'INDEPENDENT_APPROVER_REQUIRED', project_id=p.id)
        why = judge.rationale.strip()
        if len(why) < 4:
            raise Failure(422, 'INVALID_REASON', project_id=p.id)
        requires_until = intent.to_stage == 'on_hold' and judge.decision == 'approve'
        if (judge.holdUntil is not None) != requires_until or judge.holdUntil is not None and judge.holdUntil <= at:
            raise Failure(422, 'INVALID_HOLD_UNTIL', project_id=p.id)
        confirmation = valid_termination_confirmation(c, req, intent)
        if approval_of_review_termination and intent.from_stage == 'feasibility_review' and not confirmation:
            raise Failure(422, 'TERMINATION_INITIATOR_CONFIRMATION_REQUIRED', project_id=p.id)
        decision = c.scalar(db.decisions.insert().values(review_id=review_id, project_id=p.id, created_by=a.person, decided_by=a.person,
            decided_at=at, subject=f'project-transition:{intent.from_stage}:{intent.to_stage}', option=judge.decision, rationale=why)
            .returning(db.decisions.c.id))
        evidence = {'approvalId': str(req.id), 'proposedBy': str(req.requested_by), 'actorType': intent.actor_type,
            'fromStage': intent.from_stage, 'toStage': intent.to_stage, 'basis': intent.basis, 'ruleVersion': RULE_VERSION,
            'decidedBy': str(a.person), 'authorityRef': grant.ref, 'decision': judge.decision, 'rationale': why,
            'holdUntil': judge.holdUntil.isoformat() if judge.holdUntil else None}
        if grant.grant is not None:
            evidence['authorityGrant'] = snapshot(grant.grant)
        if confirmation:
            evidence['initiatorConfirmation'] = snapshot(confirmation)
        c.execute(results.insert().values(approval_id=req.id, decision_id=decision, request_id=UUID(rid), payload=evidence,
                                         payload_hash=fingerprint(evidence)))
    else:
        why = intent.reason
    status = 'rejected' if judge and judge.decision == 'reject' else 'applied'
    if status == 'applied':
        transition_project(c, a, p, None, intent.to_stage, rid, intent_id=req.id)
        if review_id is not None:
            c.execute(reviews.update().where(reviews.c.id == review_id, reviews.c.project_id == p.id, reviews.c.status == 'pending')
                .values(status='decided', verdict='terminated', minutes=why, updated_at=at, revision=reviews.c.revision + 1).returning(reviews.c.id)).scalar_one()
            c.execute(approval.update().where(approval.c.id == review_id, approval.c.action == APPROVAL_ACTION, approval.c.status == 'pending')
                .values(status='withdrawn', decided_by=a.person, reason=why, decided_at=at, updated_at=at).returning(approval.c.id)).scalar_one()
    return c.execute(approval.update().where(approval.c.id == req.id).values(status=status,
        decided_by=a.person, decided_at=at, updated_at=at, reason=why).returning(approval)).mappings().one()


def permit_transition(c, a, p, destination, intent_id):
    req, i = lookup(c, p.id, intent_id)
    edge = (p.stage, destination)
    if (i.from_stage, i.to_stage) != edge or req.status != 'pending':
        return False
    if edge == ('rejected', 'drafting'):
        contents, _ = basis(c, a, p, edge)
        return bool(a.kind == 'human' and a.person == req.requested_by and req.expected_revision == p.revision and
                    fingerprint(contents) == req.snapshot_hash and
                    (role(c, p, a.person, 'initiator') or role(c, p, a.person, 'pm')))
    if not current(c, a, p, req, i):
        return False
    if direct_actor(c, a, p, edge):
        return True
    decided = one(c, results, results.c.approval_id == req.id)
    grant = authority(c, a, p, edge, req)
    return bool(synthetic_confirmation(c) and grant and decided and decided.payload['decision'] == 'approve'
                and decided.payload['decidedBy'] == str(a.person) and decided.payload['authorityRef'] == grant.ref
                and (edge != ('feasibility_review', 'terminated') or valid_termination_confirmation(c, req, i)))


def execute(auth, project_id, request_id, op, command, key, expected):
    sig = fingerprint([str(project_id), str(request_id), expected, command.model_dump(mode='json')])
    def write(c, a, rid):
        pid = project_id or c.scalar(sa.select(approval.c.project_id).where(approval.c.id == request_id,
                                                                            approval.c.action == ACTION))
        p = lock_project(c, a, pid, write=True)
        req, i = lookup(c, p.id, request_id)
        edge = (i.from_stage, i.to_stage)
        judge = command if op == DECIDE else None
        grant = authority(c, a, p, edge, req)
        if (judge is not None and not grant) or (judge is None and not direct_actor(c, a, p, edge)):
            raise Failure(403, 'INDEPENDENT_APPROVER_REQUIRED' if judge is not None else 'TRANSITION_NOT_AUTHORIZED', project_id=p.id)
        if judge and not synthetic_confirmation(c):
            raise Failure(403, 'POLICY_PENDING', project_id=p.id)
        old = replay(c, a, op, key, sig, Result[Entry])
        if old:
            return old
        if int(expected) != p.revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if not judge and command.toStage != i.to_stage:
            raise Failure(409, 'TRANSITION_REQUEST_MISMATCH', project_id=p.id)
        if req.status != 'pending':
            raise Failure(409, 'TRANSITION_NOT_PENDING', project_id=p.id)
        if not current(c, a, p, req, i):
            raise Failure(409, 'TRANSITION_STALE', project_id=p.id)
        saved = record_transition(c, a, p, req, i, rid, judge=judge, grant=grant)
        return finish(c, a, p, saved, op, rid, key, sig, entry(c, a, p, saved, i), i.source_refs)
    return run(auth, op, write)


@router.post('/projects/{projectId}/transitions', operation_id=APPLY, response_model_exclude_none=True)
def apply(projectId: UUID, command: Apply, authorization: Auth, key: Key, expected: Revision) -> Result[Entry]:
    return execute(authorization, projectId, command.approvalRequestId, APPLY, command, key, expected)


@router.post('/approvals/{approvalId}/decide', operation_id=DECIDE, response_model_exclude_none=True)
def judge(approvalId: UUID, command: Judge, authorization: Auth, key: Key, expected: Revision) -> Result[Entry]:
    return execute(authorization, None, approvalId, DECIDE, command, key, expected)


@router.post('/approvals/{approvalId}/confirm-termination', operation_id=TERMINATION_CONFIRM,
             response_model_exclude_none=True, openapi_extra=HUMAN)
def confirm_termination(approvalId: UUID, command: Resubmit, authorization: Auth, key: Key, expected: Revision) -> Result[Entry]:
    sig = fingerprint([str(approvalId), expected, command.model_dump(mode='json')])

    def write(c, a, rid):
        if a.kind != 'human':
            raise Failure(403, 'FORBIDDEN')
        pid = c.scalar(sa.select(approval.c.project_id).where(approval.c.id == approvalId, approval.c.action == ACTION))
        p = lock_project(c, a, pid, write=True)
        req, intent = lookup(c, p.id, approvalId)
        assignment = active_assignment(c, p, a.person, 'initiator') if p.initiator_id == a.person else None
        if (intent.from_stage, intent.to_stage) != ('feasibility_review', 'terminated') or not assignment:
            raise Failure(403, 'TERMINATION_INITIATOR_REQUIRED', project_id=p.id)
        old = replay(c, a, TERMINATION_CONFIRM, key, sig, Result[Entry])
        if old:
            return old
        if p.revision != int(expected):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if req.status != 'pending' or not current(c, a, p, req, intent):
            raise Failure(409, 'TRANSITION_STALE', project_id=p.id)
        if valid_termination_confirmation(c, req, intent):
            raise Failure(409, 'TERMINATION_ALREADY_CONFIRMED', project_id=p.id)
        if len(why := command.reason.strip()) < 4:
            raise Failure(422, 'INVALID_REASON', project_id=p.id)
        c.execute(termination_signoffs.insert().values(approval_id=req.id, signer_id=a.person, assignment_id=assignment.id,
            payload_hash=req.payload_hash, reason=why, request_id=UUID(rid)))
        return finish(c, a, p, req, TERMINATION_CONFIRM, rid, key, sig, entry(c, a, p, req, intent),
                      [*intent.source_refs, f'termination-confirmation:{req.id}'])

    return run(authorization, TERMINATION_CONFIRM, write)


@router.get('/me/termination-confirmations', operation_id=TERMINATION_INBOX, response_model_exclude_none=True, openapi_extra=HUMAN)
def termination_inbox(authorization: Auth) -> Result[TerminationPage]:
    def read(c, a, _):
        if a.kind != 'human':
            raise Failure(403, 'FORBIDDEN')
        p, q, i = db.projects, approval, intents
        rows = c.execute(sa.select(q.c.id, p.c.id.label('pid')).select_from(
            q.join(i, i.c.approval_id == q.c.id).join(p, p.c.id == q.c.project_id)).where(
            q.c.status == 'pending', q.c.action == ACTION, i.c.from_stage == 'feasibility_review', i.c.to_stage == 'terminated',
            p.c.org_id == a.org, p.c.initiator_id == a.person,
            p.c.stage == i.c.from_stage, q.c.expected_revision == p.c.revision, visible(a, include_initiator=True))
            .order_by(q.c.created_at.desc(), q.c.id).limit(100)).all()
        items = []
        for row in rows:
            project = lock_project(c, a, row.pid)
            if not active_member(c, project, a.person, 'initiator'):
                continue
            item = entry(c, a, project, *lookup(c, project.id, row.id))
            if item.awaitsInitiator and not item.stale:
                items.append(TerminationEntry(**item.model_dump(), projectName=project.name))
        return Result[TerminationPage](data=TerminationPage(items=items), meta=meta(TERMINATION_INBOX, [f'approval:{i.requestId}' for i in items]))

    return run(authorization, TERMINATION_INBOX, read)


@router.get('/approvals', operation_id=INBOX, response_model_exclude_none=True)
def inbox(authorization: Auth, projectId: UUID = Query(...)) -> Result[Page]:
    def read(c, a, _):
        p = lock_project(c, a, projectId)
        rows = c.execute(sa.select(approval.c.id).join(intents, intents.c.approval_id == approval.c.id).where(
            approval.c.project_id == p.id, approval.c.action == ACTION).order_by(approval.c.created_at.desc(), approval.c.id).limit(100)).scalars()
        items = [entry(c, a, p, *lookup(c, p.id, rid)) for rid in rows]
        can_resubmit = False
        if p.stage == 'rejected' and (role(c, p, a.person, 'pm') or role(c, p, a.person, 'initiator')):
            try:
                basis(c, a, p, ('rejected', 'drafting'))
                can_resubmit = True
            except Failure:
                pass
        choices = sorted(dest for origin, dest in DIRECT | APPROVED if origin == p.stage and can_request(c, p, a.person, (origin,dest)))
        return Result[Page](data=Page(items=items, projectId=p.id, projectRevision=p.revision, stage=p.stage, requestable=choices,
                                   canResubmit=can_resubmit, policyPending=not synthetic_confirmation(c)),
                            meta=meta(INBOX, [f'project:{p.id}@{p.revision}']))
    return run(authorization, INBOX, read)


@router.post('/projects/{projectId}/resubmit', operation_id=RESUBMIT, response_model_exclude_none=True)
def resubmit(projectId: UUID, command: Resubmit, authorization: Auth, key: Key, expected: Revision) -> Result[Entry]:
    sig = fingerprint([str(projectId), expected, command.model_dump(mode='json')])
    def write(c, a, rid):
        p = lock_project(c, a, projectId, write=True)
        if not (role(c, p, a.person, 'initiator') or role(c, p, a.person, 'pm')):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        old = replay(c, a, RESUBMIT, key, sig, Result[Entry])
        if old:
            return old
        if p.revision != int(expected):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        edge = ('rejected', 'drafting')
        why = command.reason.strip()
        if len(why) < 4:
            raise Failure(422, 'INVALID_REASON', project_id=p.id)
        contents, refs = basis(c, a, p, edge)
        req, i = persist(c, a, p, edge, why, contents, refs)
        saved = record_transition(c, a, p, req, i, rid)
        return finish(c, a, p, saved, RESUBMIT, rid, key, sig, entry(c, a, p, saved, i), refs)
    return run(authorization, RESUBMIT, write)
