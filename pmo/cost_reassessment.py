"""Written LR-04 reassessment and independent, task-bound investment decision."""
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.dialects.postgresql import JSONB

from . import policy, schema_v1 as db
from .contracts import RULE_VERSION, Strict
from .cost_categories import Kind
from .costs import cost_basis
from .health_calculation import ACTIVE, _costs
from .health_schema import events
from .identity import Failure
from .projects import fingerprint
from .requirement_baselines import active_member
from .review_responses import EvidenceRef, Text, checked_evidence

ACTION = 'cost.reassess'
SUBMIT = 'human_cost_reassessment_submit'
DECIDE = 'human_cost_reassessment_decide'
OPTIONS = Literal['continue', 'adjust', 'hold', 'terminate']

records = sa.table('cost_reassessment_records', *(sa.column(name, sa.Uuid) for name in (
    'id', 'project_id', 'task_id', 'event_id', 'actor_id', 'request_id', 'submission_id', 'decision_id')),
    sa.column('task_revision', sa.BigInteger), sa.column('recorded_at', sa.DateTime(timezone=True)),
    sa.column('basis_hash'), sa.column('snapshot', JSONB))


class Submit(Strict):
    currentSituation: Text
    attribution: Literal['estimate', 'execution', 'scope']
    recommendation: Text
    evidenceRefs: list[EvidenceRef] = Field(min_length=1, max_length=12)


class Decide(Strict):
    submissionId: UUID
    option: OPTIONS
    rationale: Text
    conditions: Text | None = None


class Entry(Strict):
    recordId: UUID
    actorId: UUID
    recordedAt: datetime
    material: Submit | None = None
    decision: Decide | None = None
    decisionId: UUID | None = None


class View(Strict):
    eventId: UUID
    costType: Kind
    budget: str
    actual: str
    remaining: str
    projected: str
    variance: str
    sourceRefs: list[str]
    history: list[Entry]
    state: Literal['material_required', 'decision_required', 'decided', 'stale']
    policyStatus: Literal['POLICY_PENDING', 'POLICY_ACTIVE']
    canSubmit: bool
    canDecide: bool


def decision_basis(line):
    # A shared plan revision may change only the other unit's forecast. Keep its
    # provenance on the event and compare this unit's amount and own source rows.
    return fingerprint({**line, 'sourceRefs': [ref for ref in line['sourceRefs']
        if not ref.startswith('cost-plan:')]})


def task_event(c, row):
    if row.source_type != 'rule_event':
        return None
    e = events.c
    return c.execute(sa.select(events).where(e.id == row.source_id, e.project_id == row.project_id,
        e.rule_id == 'LR-04', e.input_snapshot['reassess'].astext == 'true')).mappings().one_or_none()


def latest_records(c, row):
    q = records.c
    return c.execute(sa.select(records).where(q.task_id == row.id, q.project_id == row.project_id,
        q.event_id == row.source_id).order_by(q.task_revision.desc())).mappings().all()


def charter_ref(c, project_id):
    t = db.charters.c
    charter = c.execute(sa.select(t.id, t.revision).where(t.project_id == project_id)).one_or_none()
    return f'charter:{charter.id}@{charter.revision}' if charter else None


def can_decide(c, a, p, submission):
    return bool(submission and a.person == p.sponsor_id and a.person not in (p.pm_id, submission.actor_id)
                and active_member(c, p, a.person, 'sponsor'))


def view(c, a, p, row, event, *, pm):
    line = event.input_snapshot
    entries = latest_records(c, row)
    latest = entries[0] if entries else None
    policy_basis = policy.selection(c, p, ACTION)
    policy_status = 'POLICY_ACTIVE' if policy_basis and 'synthetic' not in policy_basis[0] else 'POLICY_PENDING'
    active = bool(event.status == 'open' and line.get('reassessmentHash') and p.stage in ACTIVE)
    if latest and latest.decision_id:
        active = active and policy.receipt_matches(c, p, ACTION, latest.request_id, policy_basis)
    state = ('stale' if not active else 'material_required' if not latest else
             'decided' if latest.decision_id else 'decision_required')
    return View(eventId=event.id, costType=line['kind'], budget=line['budget'], actual=line['actual'],
        remaining=line['remaining'], projected=line['projected'], variance=line['variance'],
        sourceRefs=event.source_refs, state=state, policyStatus=policy_status,
        canSubmit=active and pm and state != 'decided',
        canDecide=state == 'decision_required' and can_decide(c, a, p, latest),
        history=[Entry(recordId=r.id, actorId=r.actor_id, recordedAt=r.recorded_at,
            material=r.snapshot.get('material'), decision=r.snapshot.get('decision'), decisionId=r.decision_id)
                 for r in entries])


def verify_current(c, p, row, event, now):
    line = event.input_snapshot
    current = _costs(c, cost_basis(c, p.id, now), now)[1][line['kind']]
    if (row.status in ('done', 'cancelled') or p.stage not in ACTIVE or event.status != 'open' or
            not current['reassess'] or event.rule_version != RULE_VERSION or
            line.get('reassessmentHash') != decision_basis(current)):
        raise Failure(409, 'COST_REASSESSMENT_STALE', project_id=p.id)
    return current


def save(c, a, p, row, event, command, latest, rid):
    now = c.scalar(sa.select(sa.func.clock_timestamp()))
    line = verify_current(c, p, row, event, now)
    updated = {'status': 'in_progress'}
    recorded = dict(project_id=p.id, task_id=row.id, task_revision=row.revision + 1, event_id=event.id,
        actor_id=a.person, request_id=UUID(rid), basis_hash=decision_basis(line))
    if isinstance(command, Submit):
        evidence = checked_evidence(c, a, p, command.evidenceRefs)
        recorded['snapshot'] = {'material': command.model_dump(mode='json'), 'evidence': evidence,
            'line': line, 'ruleVersion': RULE_VERSION, 'charter': charter_ref(c, p.id)}
    else:
        if latest is None or latest.id != command.submissionId or latest.decision_id is not None:
            raise Failure(409, 'COST_REASSESSMENT_SUBMISSION_CHANGED', project_id=p.id)
        if latest.basis_hash != recorded['basis_hash'] or latest.snapshot['charter'] != charter_ref(c, p.id):
            raise Failure(409, 'COST_REASSESSMENT_STALE', project_id=p.id)
        material = Submit.model_validate(latest.snapshot['material'])
        if checked_evidence(c, a, p, material.evidenceRefs, persisted=True) != latest.snapshot['evidence']:
            raise Failure(409, 'COST_REASSESSMENT_EVIDENCE_CHANGED', project_id=p.id)
        basis = policy.require(c, p, ACTION, rid)
        decision = c.scalar(db.decisions.insert().values(review_id=None, project_id=p.id, created_by=a.person,
            decided_by=a.person, decided_at=now, subject='cost-reassessment:' + str(row.id), option=command.option,
            rationale=command.rationale, exception_payload_hash=fingerprint(latest.snapshot)).returning(db.decisions.c.id))
        recorded.update(submission_id=latest.id, decision_id=decision, snapshot={
            'decision': command.model_dump(mode='json'), 'policy': basis, 'ruleVersion': RULE_VERSION})
        updated = {'status': 'done', 'completed_at': now}
    c.execute(records.insert().values(**recorded))
    t = db.tasks
    return c.execute(t.update().where(t.c.id == row.id).values(updated_at=now, revision=row.revision+1,
                     **updated).returning(t)).mappings().one(), recorded.get('decision_id')
