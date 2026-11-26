"""Human gate actions use the same locked project and submission as REST/MCP."""
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import AwareDatetime, Field
from sqlalchemy.dialects.postgresql import JSONB

from . import schema_v1 as db
from .audit_store import stable_refs
from .change_intents import snapshot
from .contracts import RULE_VERSION, Result, Strict
from .evidence import lookup as evidence_lookup, lookup_for_integrity, require_verified_content
from .gates import ENTRY, REVIEW, evaluate, lock_project, submitter
from .identity import Failure
from .materials import replay
from .policy import enabled as policy_enabled, lock as lock_policy, matches as policy_matches, require, review_action, selection
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_assignment, active_member
from .requirement_exceptions import Receipt as RequirementException
from .review_responses import Response as ReinforcementResponse
from .review_scores import (ANCHOR_VERSION, Assessment, CharterDimensions, Dimension, FeasibilityDimensions,
                            Reason, Warning, assess, judgment_warnings, rubric)
from .reviews import (APPROVAL_ACTION, ReviewView, approval_hash, decision_stage, reviews,
                      snapshots, transition_project, transitions, view, withdrawal_stage)

WORKBENCH = 'human_review_workbench'
SCORE = 'human_review_score'
APPEAL = 'human_review_appeal'
CONFIRM = 'human_review_confirm'
DECIDE = 'human_review_decide'
WITHDRAW = 'human_review_withdraw'
DECISION_GET = 'pmo_decision_get'
Verdict = Literal['pass', 'conditional_pass', 'defer', 'redirect', 'reject']
ConfirmationRole = Literal['sponsor', 'initiator']
PASS = ('pass', 'conditional_pass')

appeals = sa.table('review_appeals', sa.column('review_id', sa.Uuid), sa.column('requested_by', sa.Uuid),
    sa.column('requested_at', sa.DateTime(timezone=True)), sa.column('reason'), sa.column('before_hash'),
    sa.column('request_id', sa.Uuid), sa.column('prior_scorecards', JSONB))
confirmations = sa.table('review_confirmations', sa.column('review_id', sa.Uuid), sa.column('role'),
    sa.column('score_hash'), sa.column('confirmed_by', sa.Uuid), sa.column('confirmed_at', sa.DateTime(timezone=True)),
    sa.column('request_id', sa.Uuid))
outcomes = sa.table('review_decision_snapshots', sa.column('decision_id', sa.Uuid), sa.column('review_id', sa.Uuid),
    sa.column('approval_request_id', sa.Uuid), sa.column('rule_version'), sa.column('material_hash'),
    sa.column('payload_hash'), sa.column('payload', JSONB), sa.column('request_id', sa.Uuid))
task_refs = sa.table('review_strengthening_tasks', sa.column('review_id', sa.Uuid), sa.column('task_id', sa.Uuid), sa.column('item_key'))


class ReasonCommand(Strict):
    reason: Reason


class ConfirmationCommand(Strict):
    role: ConfirmationRole


class Condition(Strict):
    title: str = Field(min_length=4, max_length=180, pattern=r'\S')
    assigneeId: UUID
    dueAt: AwareDatetime
    evidenceRequired: bool = False


class Decide(Strict):
    verdict: Verdict
    rationale: Reason
    approvalRequestId: UUID
    conditions: list[Condition] = Field(default_factory=list, max_length=20)


class Card(Strict):
    scorecardId: UUID
    reviewerId: UUID
    round: int
    dimensions: CharterDimensions | FeasibilityDimensions
    reviewedAt: datetime
    anchorVersion: str | None = None
    warnings: dict[str, list[Warning]] | None = None


class Appeal(Strict):
    reason: str
    requestedAt: datetime
    requestedBy: UUID


class Confirmation(Strict):
    role: ConfirmationRole
    confirmedAt: datetime
    confirmedBy: UUID
    current: bool


class DecisionPublic(Strict):
    decisionId: UUID
    reviewId: UUID
    subject: str
    option: str
    decidedAt: datetime
    decidedBy: UUID
    sourceRefs: list[str]


class Decision(DecisionPublic):
    rationale: str
    stage: str | None = None


class Decisions(Strict):
    items: list[DecisionPublic]
    page: int = 1
    pageSize: int = 100
    total: int
    asOf: datetime


class Task(Strict):
    taskId: UUID
    title: str
    assigneeId: UUID | None = None
    dueAt: datetime | None = None
    sourceType: str
    sourceId: UUID
    status: str
    revision: int


class Workbench(Strict):
    review: ReviewView
    round: int
    approvalRequestId: UUID | None = None
    dimensions: list[Dimension]
    materialRefs: list[str]
    scores: list[Card]
    appeal: Appeal | None = None
    assessment: Assessment | None = None
    confirmations: list[Confirmation]
    decision: Decision | None = None
    requirementException: RequirementException | None = None
    withdrawalReason: str | None = None
    tasks: list[Task]
    canScore: bool
    canAppeal: bool
    canDecide: bool
    canWithdraw: bool
    canConfirmSponsor: bool
    canConfirmInitiator: bool
    policyPending: bool
    stale: bool
    scoreEvidenceStale: Literal[True] | None = None
    requiredReviewerCount: int | None = None
    submittedReviewerCount: int | None = None
    reinforcementResponses: list[ReinforcementResponse] | None = None


def one(c, table, *where):
    return c.execute(sa.select(table).where(*where)).mappings().one_or_none()


def context(c, a, review_id, *, write=False):
    v = reviews.c
    pid = c.scalar(sa.select(v.project_id).where(v.id == review_id))
    p = lock_project(c, a, pid, write=write)
    review = one(c, reviews, v.id == review_id, v.project_id == p.id)
    if review is None or (review.type != 'charter' and not p.materials_visible):
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    frozen = one(c, snapshots, snapshots.c.review_id == review_id)
    return p, review, frozen


def reviewer_assignment(c, a, p, review, frozen=None):
    if a.kind != 'human' or a.person in (p.initiator_id, p.pm_id, p.sponsor_id, review.created_by):
        return None
    grant = active_assignment(c, p, a.person, 'reviewer')
    frozen = frozen if frozen is not None else one(c, snapshots, snapshots.c.review_id == review.id)
    if grant and frozen and snapshot(grant) in frozen.materials['reviewers']:
        return grant
    return None


def approval(c, review):
    t = db.approval_requests
    return one(c, t, t.c.id == review.id, t.c.project_id == review.project_id, t.c.action == APPROVAL_ACTION)


def authorized_reviewer(c, a, p, review, frozen, intent):
    if a.person == review.created_by or (intent is not None and a.person in (intent.requested_by, intent.delegated_by)):
        raise Failure(403, 'INDEPENDENT_REVIEWER_REQUIRED', project_id=p.id)
    if not reviewer_assignment(c, a, p, review, frozen):
        raise Failure(403, 'REVIEWER_ASSIGNMENT_REQUIRED', project_id=p.id)


def current(c, a, p, review, frozen, intent):
    entry = one(c, transitions, transitions.c.review_id == review.id, transitions.c.to_stage == REVIEW[review.type])
    if (not frozen or not intent or not entry or entry.project_id != p.id or entry.actor_id != review.created_by
          or entry.from_stage != ENTRY[review.type] or entry.from_revision != intent.expected_revision
          or intent.status != 'pending' or intent.rule_version != RULE_VERSION or frozen.rule_version != RULE_VERSION
          or intent.requested_by != review.created_by or intent.delegated_by != (entry.actor_id if entry.actor_type == 'agent' else None)
          or not submitter(c, p, intent.requested_by, review.type)
          or intent.payload_hash != approval_hash(review, entry.from_revision, entry.actor_type)
          or intent.snapshot_hash != frozen.snapshot_hash or review.snapshot_hash != frozen.snapshot_hash
          or not policy_matches(c, p, review_action(review.type), review.id)
          or fingerprint(frozen.materials) != frozen.snapshot_hash):
        return False
    check, _ = evaluate(c, a, p, review.type, pending=True)
    return check.eligible and check.snapshotHash == frozen.snapshot_hash


def all_scores(c, review):
    s = db.scorecards.c
    return list(c.execute(sa.select(db.scorecards).where(s.review_id == review.id, s.project_id == review.project_id)
                          .order_by(s.round, s.reviewer_id)).mappings())


def effective(rows):
    # Reconsideration replaces only an opinion that the same reviewer actually re-files.
    latest = {r.reviewer_id: r for r in rows}
    return [latest[k] for k in sorted(latest)]


def roster_progress(review, frozen, scored):
    if review.type == 'closing' or not frozen:
        return None, None
    required = {UUID(row['person_id']) for row in frozen.materials['reviewers']}
    filed = {row.reviewer_id for row in scored}
    return len(required), len(required & filed)


def require_complete_roster(p, review, frozen, scored):
    required, submitted = roster_progress(review, frozen, scored)
    if required is not None and (not required or required != submitted):
        raise Failure(422, 'REVIEW_REQUIRED_SCORES_INCOMPLETE', project_id=p.id)


def score_hash(frozen, round_no, scores):
    return fingerprint([frozen.snapshot_hash, round_no, [snapshot(r) for r in scores]])


def proofs(c, a, p, review, frozen, dimensions, *, persisted=False):
    scoped = set(frozen.source_refs)
    targets = {ref.split('@', 1)[0] for ref in scoped} | {f'review:{review.id}'}
    verified = []
    for ref in sorted({ref for value in dimensions.values() for ref in value['evidenceRefs']}):
        if not ref.startswith('evidence:'):
            if ref not in scoped:
                raise Failure(422, 'REVIEW_SOURCE_NOT_FROZEN', project_id=p.id)
            continue
        try:
            evidence_id = UUID(ref.removeprefix('evidence:'))
            # A confirmation does not grant file access. Recheck only attachments already bound to effective cards.
            proof = (lookup_for_integrity(c, p.id, evidence_id) if persisted
                     else evidence_lookup(c, a, evidence_id, True, shared=True))
            target = proof.resource_type.replace('_', '-')
            if proof.project_id != p.id or f'{target}:{proof.resource_id}' not in targets:
                raise Failure(422, 'EVIDENCE_TARGET_MISMATCH')
            require_verified_content(proof)
        except (ValueError, Failure):
            raise Failure(422, 'REVIEW_EVIDENCE_INVALID', project_id=p.id) from None
        verified.append({'ref': ref, 'sha256': proof.sha256, 'revision': proof.revision,
                         'verifiedBy': str(proof.verified_by), 'verifiedAt': proof.verified_at.isoformat()})
    return verified


def check_score_evidence(c, a, p, review, frozen, scores):
    for row in scores:
        try:
            valid = row.rule_version == RULE_VERSION and row.type == review.type and row.rationale['evidence'] == proofs(
                c, a, p, review, frozen, row.dimensions, persisted=True)
        except Failure:
            valid = False
        if not valid:
            raise Failure(409, 'REVIEW_SNAPSHOT_STALE', project_id=p.id)


def confirmation_eligible(c, a, p, role):
    return a.person == p[f'{role}_id'] and active_member(c, p, a.person, role)


def structured_rationale(p, dims, evidence, *, required):
    if not required and not any('judgments' in rating for rating in dims.values()):
        return {'evidence': evidence}
    warnings = {}
    for key, rating in dims.items():
        try:
            found = judgment_warnings(key, rating)
        except ValueError:
            raise Failure(422, 'REVIEW_JUDGMENT_INVALID', project_id=p.id) from None
        if found:
            if not rating.get('retainedReason'):
                raise Failure(422, 'REVIEW_JUDGMENT_DISPOSITION_REQUIRED', project_id=p.id)
            warnings[key] = [item.model_dump(mode='json', exclude_none=True) for item in found]
    return {'evidence': evidence, 'anchorVersion': ANCHOR_VERSION, 'warnings': warnings}


def confirmation_rows(c, review):
    t = confirmations
    return list(c.execute(sa.select(t).where(t.c.review_id == review.id).order_by(t.c.confirmed_at, t.c.role)).mappings())


def appeal_view(row):
    return Appeal(reason=row.reason, requestedAt=row.requested_at, requestedBy=row.requested_by)


def task_view(row):
    return Task(taskId=row.id, assigneeId=row.assignee_id, title=row.title, dueAt=row.due_at,
                sourceType=row.source_type, sourceId=row.source_id, status=row.status, revision=row.revision)


def decision_public(record, refs):
    return DecisionPublic(decisionId=record.id, reviewId=record.review_id, subject=record.subject, option=record.option,
                          decidedAt=record.decided_at, decidedBy=record.decided_by, sourceRefs=refs)


def decision_lookup(c, p, review):
    d = db.decisions
    return one(c, d, d.c.review_id == review.id, d.c.project_id == p.id)


def decision_snapshot(c, review, record):
    return one(c, outcomes, outcomes.c.decision_id == record.id, outcomes.c.review_id == review.id,
               outcomes.c.approval_request_id == review.id)


def decision_history(c, p, review, record, stored):
    refs = [f'decision:{record.id}@{record.revision}', f'review:{review.id}@{review.revision}']
    t = transitions.c
    stage, rule, transition = None, None, None
    if stored:
        stage, rule = stored.payload['stage'], stored.rule_version
        refs.extend((f'review-snapshot:{review.id}', f'review-decision-snapshot:{record.id}', f'approval:{stored.approval_request_id}'))
        transition = one(c, transitions, t.project_id == p.id, t.review_id == review.id,
                         t.request_id == stored.request_id, t.from_stage == REVIEW[review.type], t.to_stage == stage)
    elif review.verdict == 'terminated':
        # Review termination is an independent stage approval, not a gate verdict.
        from .project_lifecycle import ACTION, results
        q, x = db.approval_requests.c, results.c
        transition = c.execute(sa.select(t.to_stage, t.to_revision, q.id.label('approval_id'), q.rule_version)
            .select_from(results.join(db.approval_requests, q.id == x.approval_id).join(transitions,
                sa.and_(t.approval_request_id == q.id, t.request_id == x.request_id)))
            .where(x.decision_id == record.id, q.project_id == p.id, q.action == ACTION, q.status == 'applied',
                   t.project_id == p.id, t.from_stage == REVIEW[review.type], t.to_stage == 'terminated')).mappings().one_or_none()
        if transition:
            stage, rule = transition.to_stage, transition.rule_version
            refs.append(f'approval:{transition.approval_id}')
    if transition:
        refs.append(f'project-transition:{p.id}@{transition.to_revision}')
    return refs, stage, rule


def decision_list(auth, review_id):
    def read(c, a, _):
        p, review, _frozen = context(c, a, review_id)
        record = decision_lookup(c, p, review)
        rows, rule = [], None
        refs = [f'review:{review.id}@{review.revision}']
        if record:
            refs, _, rule = decision_history(c, p, review, record, decision_snapshot(c, review, record))
            rows.append(decision_public(record, refs))
        metadata = meta(DECISION_GET, refs, project_id=p.id)
        if rule is not None:
            metadata.ruleVersion = rule
        return Result[Decisions](data=Decisions(items=rows, total=len(rows), asOf=metadata.asOf), meta=metadata)
    return run(auth, DECISION_GET, read)


def workbench(c, a, p, review, frozen):
    intent = approval(c, review)
    reconsideration = one(c, appeals, appeals.c.review_id == review.id)
    round_no = 2 if reconsideration else 1
    pending = review.status == 'pending' and p.stage == REVIEW[review.type]
    stale = pending and not current(c, a, p, review, frozen, intent)
    reviewer = bool(reviewer_assignment(c, a, p, review, frozen))
    owner = submitter(c, p, a.person, review.type)
    sponsor = confirmation_eligible(c, a, p, 'sponsor')
    initiator = confirmation_eligible(c, a, p, 'initiator')
    rows = all_scores(c, review)
    latest = effective(rows)
    calculated = assess(review.type, latest)
    digest = score_hash(frozen, round_no, latest) if frozen else None
    can_read_scores = not pending or owner or sponsor or initiator or any(row.reviewer_id == a.person for row in latest)
    record = decision_lookup(c, p, review)
    stored = decision_snapshot(c, review, record) if record else None
    if stored:
        calculated = Assessment.model_validate(stored.payload['assessment']) if stored.payload['assessment'] else None
        digest = stored.payload['scoreHash']
    confirmations_found = [Confirmation(role=row.role, confirmedBy=row.confirmed_by, confirmedAt=row.confirmed_at,
        current=row.score_hash == digest) for row in confirmation_rows(c, review)]
    confirmed = {row.role for row in confirmations_found if row.current}
    required, submitted = roster_progress(review, frozen, latest)
    complete = required is None or (required > 0 and required == submitted)
    valid = pending and not stale
    fast = bool(calculated and calculated.classification == 'fast_track' and valid and complete)
    no_policy = not policy_enabled(c, p, review_action(review.type))
    can_decide = valid and reviewer and not no_policy and complete and (calculated is not None or review.type == 'closing')
    can_confirm_sponsor = fast and not no_policy and sponsor and 'sponsor' not in confirmed
    can_confirm_initiator = fast and not no_policy and initiator and 'initiator' not in confirmed
    score_evidence_stale: Literal[True] | None = None
    if can_decide or can_confirm_sponsor or can_confirm_initiator:
        try:
            check_score_evidence(c, a, p, review, frozen, latest)
        except Failure as failure:
            if failure.code != 'REVIEW_SNAPSHOT_STALE':
                raise
            score_evidence_stale = True
            can_decide = can_confirm_sponsor = can_confirm_initiator = False
    t = db.tasks
    task_rows = c.execute(sa.select(t).join(task_refs, task_refs.c.task_id == t.c.id).where(
        task_refs.c.review_id == review.id, t.c.project_id == p.id).order_by(t.c.created_at, t.c.id)).mappings()
    show_full_material = bool(p.materials_visible or owner or initiator)
    exception = (stored.payload.get('requirementException') if stored else
                 frozen.materials.get('requirementException') if frozen and pending else None)
    decision = None
    if record:
        refs, stage, _ = decision_history(c, p, review, record, stored)
        decision = Decision(**decision_public(record, refs).model_dump(), rationale=record.rationale, stage=stage)
    reinforcement = frozen.materials.get('reinforcementResponses') if frozen and show_full_material else None
    return Workbench(review=view(review), round=round_no, approvalRequestId=intent.id if intent else None,
        dimensions=rubric(review.type), materialRefs=frozen.source_refs if frozen and show_full_material else [],
        scores=[Card(scorecardId=row.id, reviewerId=row.reviewer_id, round=row.round, dimensions=row.dimensions,
                     reviewedAt=row.reviewed_at, anchorVersion=row.rationale.get('anchorVersion'),
                     warnings=row.rationale.get('warnings')) for row in rows if not pending or row.reviewer_id == a.person],
        appeal=appeal_view(reconsideration) if reconsideration else None, assessment=calculated if can_read_scores else None,
        confirmations=confirmations_found if can_read_scores else [], decision=decision,
        requirementException=RequirementException.model_validate(exception) if exception and show_full_material else None,
        tasks=[task_view(row) for row in task_rows], stale=stale, policyPending=no_policy, scoreEvidenceStale=score_evidence_stale,
        requiredReviewerCount=required, submittedReviewerCount=submitted,
        reinforcementResponses=[ReinforcementResponse.model_validate({k: v for k, v in row.items() if k != 'evidence'})
                                for row in reinforcement] if reinforcement else None,
        withdrawalReason=review.minutes if review.verdict == 'withdrawn' else None,
        canScore=valid and reviewer and bool(rubric(review.type)) and not any(r.reviewer_id == a.person and r.round == round_no for r in rows),
        canAppeal=valid and owner and bool(rows) and reconsideration is None,
        canDecide=can_decide, canWithdraw=pending and owner, canConfirmSponsor=can_confirm_sponsor,
        canConfirmInitiator=can_confirm_initiator)


def read_workbench(auth, review_id):
    def read(c, a, _):
        p, review, frozen = context(c, a, review_id)
        state = workbench(c, a, p, review, frozen)
        refs = state.decision.sourceRefs if state.decision else [f'review:{review_id}@{review.revision}']
        return Result[Workbench](data=state, meta=meta(WORKBENCH, refs))
    return run(auth, WORKBENCH, read)


def bump(c, review, **changes):
    return c.execute(reviews.update().where(reviews.c.id == review.id).values(**changes, revision=review.revision + 1,
        updated_at=sa.func.now()).returning(reviews)).mappings().one()


def event(c, rid, kind, p, review, **data):
    c.execute(db.outbox_events.insert().values(dedupe_key=rid, event_type=kind, status='pending',
        payload={'projectId': str(p.id), 'reviewId': str(review.id), **data}))


def strengthen(c, a, p, review, specs, *, response_evidence=None):
    from .review_responses import bindings

    created = []
    for item_key, title, who, due in specs:
        if one(c, task_refs, task_refs.c.review_id == review.id, task_refs.c.item_key == item_key):
            continue
        tid = c.scalar(db.tasks.insert().values(project_id=p.id, created_by=a.person, title=title, due_at=due,
            assignee_id=who, source_type='review', source_id=review.id, status='todo').returning(db.tasks.c.id))
        c.execute(bindings.insert().values(review_id=review.id, task_id=tid, item_key=item_key,
            evidence_required=response_evidence[item_key] if response_evidence is not None else None))
        created.append(str(tid))
    return created


def valid_assignee(c, p, who):
    m = db.project_members.c
    roles = c.scalars(sa.select(m.role).where(m.project_id == p.id, m.person_id == who)).all()
    return any(active_member(c, p, who, role) for role in roles)


def make_tasks(c, a, p, review, verdict, conditions, calculated, now):
    if (verdict == 'conditional_pass' and review.type != 'closing') != bool(conditions):
        raise Failure(422, 'CONDITIONS_REQUIRED_OR_NOT_ALLOWED', project_id=p.id)
    specs = {}
    for term in conditions:
        if term.evidenceRequired and review.type != 'charter':
            raise Failure(422, 'CHARTER_STRENGTHENING_TASK_REQUIRED', project_id=p.id)
        title = ' '.join(term.title.split())
        if len(title) < 4 or term.dueAt <= now or not valid_assignee(c, p, term.assigneeId):
            raise Failure(422, 'INVALID_STRENGTHENING_TASK', project_id=p.id)
        key = f'condition:{fingerprint([title.casefold(), str(term.assigneeId)])}'
        if key in specs and specs[key][2:] != (term.dueAt, term.evidenceRequired):
            raise Failure(422, 'DUPLICATE_CONDITION', project_id=p.id)
        specs[key] = (title, term.assigneeId, term.dueAt, term.evidenceRequired)
    if calculated and (review.type == 'charter' and verdict in PASS or calculated.classification == 'shortfall'):
        labels = {d.key: d.label for d in rubric(review.type)}
        for dimension in calculated.weakDimensions:
            specs[f'dimension:{dimension}:{p.pm_id}'] = (f'评审补强：{labels[dimension]}', p.pm_id, None, False)
    response_evidence = {key: spec[3] for key, spec in specs.items()} if review.type == 'charter' else None
    strengthen(c, a, p, review, [(key, *spec[:3]) for key, spec in specs.items()], response_evidence=response_evidence)


def freeze_approved_baselines(c, a, p, review, decision_id, rid):
    if review.type == 'charter':
        ch = db.charters
        row = c.execute(ch.update().where(ch.c.project_id == p.id, ~ch.c.is_frozen).values(
            is_frozen=True, revision=ch.c.revision + 1, updated_at=sa.func.now()).returning(ch.c.id, ch.c.revision)).one_or_none()
        if row is None:
            raise Failure(409, 'CHARTER_BASELINE_ALREADY_ACTIVE', project_id=p.id)
        return [f'charter:{row.id}@{row.revision}']
    if review.type != 'feasibility':
        return []
    from .category_costs import allocation, freeze, freeze_material
    from .costs import latest_plan
    from .policy import matches as policy_matches, require as require_policy
    t, m = db.cost_entries, db.milestones
    if c.scalar(sa.select(t.c.id).where(t.c.project_id == p.id, t.c.is_current_baseline).limit(1)):
        raise Failure(409, 'COST_BASELINE_ALREADY_ACTIVE', project_id=p.id)
    plan = c.execute(latest_plan(p.id)).mappings().one()
    draft, category_forecast = freeze_material(c, p)
    if draft is not None:
        require_policy(c, p, 'cost.category', rid)
        if not policy_matches(c, p, 'cost.category', review.id):
            raise Failure(409, 'POLICY_SNAPSHOT_STALE', project_id=p.id)
        _, totals = allocation(draft.amounts)
        if (category_forecast is None or category_forecast.budget_revision != draft.revision
                or any(plan[kind + '_budget'] != total for kind, total in totals.items())):
            raise Failure(409, 'CATEGORY_PLAN_STALE', project_id=p.id)
    first, last = c.execute(sa.select(sa.func.min(m.c.planned_date), sa.func.max(m.c.planned_date)).where(
        m.c.project_id == p.id, ~m.c.is_cancelled)).one()
    rows = []
    for kind in ('money', 'effort'):
        budget, forecast = plan[kind + '_budget'], plan[kind + '_remaining']
        rows.append(dict(project_id=p.id, created_by=a.person, category='project_baseline', type=kind,
            period_start=first, period_end=last, budget=budget, actual=0,
            forecast_remaining=budget if forecast is None else forecast, baseline_version=1,
            source_ref=f'decision:{decision_id}'))
    saved = c.execute(t.insert().values(rows).returning(
        t.c.id, t.c.revision, sa.func.statement_timestamp().label('effective_at'))).all()
    refs = [f'cost-entry:{row.id}@{row.revision}' for row in saved]
    if draft is not None:
        refs.append(freeze(c, p, a, draft, decision_id, saved[0].effective_at))
    return refs


def execute_decision(c, a, p, review, frozen, intent, command, scored, round_no, rid):
    if command.approvalRequestId != intent.id:
        raise Failure(409, 'REVIEW_APPROVAL_MISMATCH', project_id=p.id)
    calculated = assess(review.type, scored)
    if review.type != 'closing' and calculated is None:
        raise Failure(422, 'REVIEW_SCORE_REQUIRED', project_id=p.id)
    require_complete_roster(p, review, frozen, scored)
    digest = score_hash(frozen, round_no, scored)
    signoffs = [i for i in confirmation_rows(c, review) if i.score_hash == digest]
    if command.verdict in PASS and calculated:
        reject = {'veto': 'CHARTER_SCORE_VETO', 'below_threshold': 'CHARTER_SCORE_BELOW_THRESHOLD', 'shortfall': 'FEASIBILITY_SHORTFALL'}
        if calculated.classification in reject:
            raise Failure(422, reject[calculated.classification], project_id=p.id)
        if calculated.classification == 'fast_track':
            by_role = {i.role: i.confirmed_by for i in signoffs}
            if by_role.get('sponsor') != p.sponsor_id:
                raise Failure(422, 'SPONSOR_CONFIRMATION_REQUIRED', project_id=p.id)
            if by_role.get('initiator') != p.initiator_id or not active_member(c, p, p.initiator_id, 'initiator'):
                raise Failure(422, 'INITIATOR_CONFIRMATION_REQUIRED', project_id=p.id)
    exception = frozen.materials.get('requirementException') if review.type == 'closing' else None
    if review.type == 'closing' and command.verdict in PASS:
        if command.verdict == 'conditional_pass' and not exception:
            raise Failure(422, 'CLOSING_EXCEPTION_NOT_AVAILABLE', project_id=p.id)
        if command.verdict == 'pass' and exception:
            raise Failure(422, 'CLOSING_CONDITIONAL_DECISION_REQUIRED', project_id=p.id)
    check_score_evidence(c, a, p, review, frozen, scored)
    now = c.scalar(sa.select(sa.func.clock_timestamp()))
    if exception and command.verdict == 'conditional_pass' and now >= RequirementException.model_validate(exception).expiresAt:
        raise Failure(409, 'REVIEW_SNAPSHOT_STALE', project_id=p.id)
    make_tasks(c, a, p, review, command.verdict, command.conditions, calculated, now)
    destination = decision_stage(review.type, command.verdict)
    assert destination is not None
    decision_id = c.scalar(db.decisions.insert().values(review_id=review.id, project_id=p.id, created_by=a.person,
        decided_by=a.person, decided_at=now, subject=review.type, option=command.verdict, rationale=reason(command.rationale))
        .returning(db.decisions.c.id))
    baselines = freeze_approved_baselines(c, a, p, review, decision_id, rid) if command.verdict in PASS else []
    payload = {'stage': destination, 'verdict': command.verdict, 'rationale': reason(command.rationale),
        'materialHash': frozen.snapshot_hash, 'scoreHash': digest, 'scorecards': [snapshot(row) for row in scored],
        'round': round_no, 'assessment': calculated.model_dump(mode='json') if calculated else None,
        'confirmations': [snapshot(row) for row in signoffs], 'approvalRequestId': str(intent.id),
        'submittedBy': str(intent.requested_by), 'delegatedBy': str(intent.delegated_by) if intent.delegated_by else None,
        'reviewerAssignment': snapshot(reviewer_assignment(c, a, p, review, frozen)), 'sourceRefs': frozen.source_refs,
        'conditions': [term.model_dump(mode='json') for term in command.conditions], 'approvedBaselines': baselines}
    if exception and command.verdict == 'conditional_pass':
        payload['requirementException'] = exception
    c.execute(outcomes.insert().values(decision_id=decision_id, review_id=review.id, approval_request_id=intent.id,
        rule_version=RULE_VERSION, material_hash=frozen.snapshot_hash, payload_hash=fingerprint(payload), payload=payload, request_id=UUID(rid)))
    transition_project(c, a, p, review, destination, rid, verdict=command.verdict)
    t = db.approval_requests
    c.execute(t.update().where(t.c.id == intent.id).values(status='approved' if command.verdict in PASS else 'rejected',
        decided_at=now, decided_by=a.person, reason=payload['rationale'], updated_at=now))
    return bump(c, review, status='decided', verdict=command.verdict), destination


def reason(text):
    if len(text.strip()) < 4:
        raise Failure(422, 'INVALID_REASON')
    return text.strip()


def shortfall_after_denial(c, actor, failure, rid, review_id, expected):
    if failure.code != 'FEASIBILITY_SHORTFALL':
        return
    p, review, frozen = context(c, actor, review_id, write=True)
    intent = approval(c, review)
    if (review.status != 'pending' or review.revision != expected or not reviewer_assignment(c, actor, p, review, frozen)
            or not current(c, actor, p, review, frozen, intent) or not policy_enabled(c, p, review_action(review.type))):
        return
    assessment = assess(review.type, effective(all_scores(c, review)))
    if not assessment or assessment.classification != 'shortfall':
        return
    labels = {d.key: d.label for d in rubric(review.type)}
    tasks = strengthen(c, actor, p, review, [(f'dimension:{key}:{p.pm_id}', f'评审补强：{labels[key]}', p.pm_id, None)
                                           for key in assessment.weakDimensions])
    if tasks:
        op = 'review.shortfall_remediation'
        c.execute(db.audit_logs.insert().values(action=op, actor_type='human', actor_id=actor.credential, org_id=actor.org,
            project_id=p.id, result='success', after_hash=fingerprint(tasks), request_id=rid, rule_version=RULE_VERSION))
        event(c, rid, op, p, review, taskIds=tasks, ruleId='LR-02')


def write_action(auth, op, review_id, command, key, expected):
    content = command.model_dump(mode='json', exclude_none=True)
    if op == DECIDE:
        for term in content['conditions']:
            if not term['evidenceRequired']:
                term.pop('evidenceRequired')
    signature = fingerprint([str(review_id), expected, content])

    def write(c, a, rid):
        p, review, frozen = context(c, a, review_id, write=True)
        intent = approval(c, review)
        if op in (SCORE, DECIDE):
            authorized_reviewer(c, a, p, review, frozen, intent)
        elif op == CONFIRM:
            if not confirmation_eligible(c, a, p, command.role):
                raise Failure(403, 'CONFIRMATION_ROLE_REQUIRED', project_id=p.id)
        elif not submitter(c, p, a.person, review.type):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        prior = replay(c, a, op, key, signature, Result[Workbench])
        if prior:
            return prior
        if review.revision != expected:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if review.status != 'pending' or p.stage != REVIEW[review.type]:
            raise Failure(409, 'REVIEW_NOT_PENDING', project_id=p.id)
        destination = p.stage
        if op == WITHDRAW:
            why = reason(command.reason)
            destination = withdrawal_stage(review.type)
            transition_project(c, a, p, review, destination, rid, verdict='withdrawn')
            if intent:
                t = db.approval_requests
                c.execute(t.update().where(t.c.id == intent.id).values(status='withdrawn', reason=why, decided_at=sa.func.now(),
                    decided_by=a.person, updated_at=sa.func.now()))
            saved = bump(c, review, status='decided', verdict='withdrawn', minutes=why)
        else:
            action = review_action(review.type)
            lock_policy(c, p.org_id)
            if op in (DECIDE, CONFIRM) or policy_enabled(c, p, action):
                require(c, p, action, rid)
            if not current(c, a, p, review, frozen, intent):
                raise Failure(409, 'REVIEW_SNAPSHOT_STALE', project_id=p.id)
            rows = all_scores(c, review)
            scored = effective(rows)
            round_no = 2 if one(c, appeals, appeals.c.review_id == review.id) else 1
            if op == SCORE:
                if review.type != command.reviewType:
                    raise Failure(422, 'REVIEW_TYPE_MISMATCH', project_id=p.id)
                if round_no != command.round:
                    raise Failure(409, 'REVIEW_ROUND_CHANGED', project_id=p.id)
                if any(row.round == round_no and row.reviewer_id == a.person for row in rows):
                    raise Failure(409, 'REVIEW_SCORE_ALREADY_SUBMITTED', project_id=p.id)
                policy = selection(c, p, action)
                dims = command.dimensions.model_dump(mode='json', exclude_none=True)
                evidence = proofs(c, a, p, review, frozen, dims)
                versioned = 'calibrationPlan' in frozen.materials or bool(policy and 'synthetic' not in policy[0])
                rationale = structured_rationale(p, dims, evidence, required=versioned)
                c.execute(db.scorecards.insert().values(review_id=review.id, project_id=p.id, created_by=a.person,
                    reviewer_id=a.person, round=round_no, reviewed_at=sa.func.now(), type=review.type, rule_version=RULE_VERSION,
                    dimensions=dims, rationale=rationale))
                saved = bump(c, review)
            elif op == APPEAL:
                if round_no != 1:
                    raise Failure(409, 'REVIEW_APPEAL_LIMIT', project_id=p.id)
                if not scored:
                    raise Failure(422, 'REVIEW_SCORE_REQUIRED', project_id=p.id)
                c.execute(appeals.insert().values(review_id=review.id, requested_by=a.person, reason=reason(command.reason),
                    before_hash=score_hash(frozen, round_no, scored), prior_scorecards=[snapshot(row) for row in scored], request_id=UUID(rid)))
                saved = bump(c, review)
            elif op == CONFIRM:
                calculated = assess(review.type, scored)
                if not calculated or calculated.classification != 'fast_track':
                    raise Failure(422, 'FAST_TRACK_NOT_AVAILABLE', project_id=p.id)
                require_complete_roster(p, review, frozen, scored)
                check_score_evidence(c, a, p, review, frozen, scored)
                digest = score_hash(frozen, round_no, scored)
                t = confirmations
                if one(c, t, t.c.review_id == review.id, t.c.role == command.role, t.c.score_hash == digest):
                    raise Failure(409, 'REVIEW_CONFIRMATION_EXISTS', project_id=p.id)
                c.execute(t.insert().values(review_id=review.id, role=command.role, confirmed_by=a.person,
                    score_hash=digest, request_id=UUID(rid)))
                saved = bump(c, review)
            else:
                saved, destination = execute_decision(c, a, p, review, frozen, intent, command, scored, round_no, rid)
        # Reload cannot apply a stage update of its own. The response and all writes share this transaction.
        project = lock_project(c, a, p.id, write=True) if destination != p.stage else p
        state = workbench(c, a, project, saved, frozen)
        refs = [f'review:{review_id}@{saved.revision}']
        event_data = {}
        decision_id = None
        if op == DECIDE:
            assert state.decision is not None
            decision_id = state.decision.decisionId
            refs.extend(state.decision.sourceRefs)
            event_data['decisionId'] = str(decision_id)
        result = Result[Workbench](data=state, meta=meta(op, stable_refs(refs)))
        body = result.model_dump(mode='json', exclude_none=True)
        record_receipt(c, a, key, op, rid, review.id, p.id, signature, body, fingerprint(body), fingerprint(snapshot(review)),
                       decision_id=decision_id)
        event(c, rid, op, p, saved, stage=destination, revision=saved.revision, **event_data)
        return result

    return run(auth, op, write, after_denied=lambda c, a, failure, rid: shortfall_after_denial(c, a, failure, rid, review_id, expected))
