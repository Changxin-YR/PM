"""Compare actual outcomes against the expectations and scores frozen at charter."""
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import Field, StringConstraints, model_validator
from sqlalchemy.dialects.postgresql import JSONB

from . import schema_v1 as db
from .change_intents import snapshot
from .contracts import Result, Strict
from .identity import Failure
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_member
from .review_responses import EvidenceRef, Text, checked_evidence
from .review_scores import ANCHOR_VERSION, ANCHORS, WEIGHTS, Anchor, Dimension, rubric

GET = 'human_review_calibration_get'
PLAN = 'human_review_calibration_plan'
OUTCOME = 'human_review_calibration_result'
POLICY = 'review.calibrate'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
DimensionKey = Literal['strategicAlignment', 'customerAttractiveness', 'financialResourceReturn', 'riskControllability']
Score = Annotated[str, StringConstraints(pattern=r'^(?:[1-4](?:\.\d{1,2})?|5(?:\.0{1,2})?)$')]
plans = sa.table('review_calibration_plans', sa.column('id', sa.Uuid), sa.column('project_id', sa.Uuid),
    sa.column('revision', sa.BigInteger), sa.column('submitted_by', sa.Uuid),
    sa.column('submitted_at', sa.DateTime(timezone=True)), sa.column('dimensions', JSONB))
results = sa.table('review_calibration_results', sa.column('id', sa.Uuid), sa.column('review_id', sa.Uuid),
    sa.column('dimension'), sa.column('revision', sa.BigInteger), sa.column('submitted_by', sa.Uuid),
    sa.column('submitted_at', sa.DateTime(timezone=True)), sa.column('evaluation', JSONB))


class Expectation(Strict):
    expected: Text
    objective: Text | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=60, pattern=r'\S')
    observeAfter: date
    reviewerId: UUID
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list, max_length=12, json_schema_extra={'uniqueItems': True})

    @model_validator(mode='after')
    def target_unit(self):
        if self.unit is not None and self.objective is None:
            raise ValueError('Provide a target for the specified unit')
        return self


class Plan(Strict):
    dimensions: dict[DimensionKey, Expectation]

    @model_validator(mode='after')
    def complete(self):
        if self.dimensions.keys() != WEIGHTS.keys():
            raise ValueError('All four charter dimensions require an expectation')
        return self


class Deviation(Strict):
    resultRevision: int = Field(ge=1)
    explanation: Text
    improvement: Text


class Evaluation(Strict):
    status: Literal['evaluated', 'pending', 'incomparable']
    reasoning: Text
    score: Score | None = None
    actualAnchorCode: Anchor | None = None
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list, max_length=12, json_schema_extra={'uniqueItems': True})
    followUpBy: date | None = None
    evidencePlan: Text | None = None
    followUpDeviation: Deviation | None = None

    @model_validator(mode='after')
    def required_inputs(self):
        if self.status == 'evaluated':
            if self.score is None or not self.evidenceRefs or self.followUpBy or self.evidencePlan:
                raise ValueError('A result score requires verified evidence and no observation plan')
            if self.actualAnchorCode is not None and Decimal(self.score) != ANCHORS.index(self.actualAnchorCode) + 1:
                raise ValueError('The selected outcome anchor must match its score')
        elif self.score is not None or self.actualAnchorCode is not None or self.followUpDeviation is not None:
            raise ValueError('A result score requires comparable evidence')
        if self.status == 'pending' and (self.followUpBy is None or self.evidencePlan is None):
            raise ValueError('A pending observation requires a deadline and an evidence plan')
        if self.status == 'incomparable' and (self.followUpBy or self.evidencePlan):
            raise ValueError('Use pending for a result with a scheduled follow-up')
        return self


class ResultView(Evaluation):
    resultId: UUID
    revision: int
    reviewerId: UUID
    evaluatedAt: datetime
    closingDecisionRef: str | None = None


class OriginalRating(Strict):
    score: int
    round: int
    anchor: Anchor
    scorecardRef: str
    evidenceRefs: list[str]


class DimensionView(Strict):
    originalMean: str
    current: ResultView | None = None
    history: list[ResultView] = Field(default_factory=list)
    difference: str | None = None
    requiresRetrospective: bool = False
    canEvaluate: bool = False
    anchors: dict[Anchor, str] | None = None
    originalScores: list[OriginalRating] | None = None


class State(Strict):
    projectId: UUID
    planRevision: int
    canPlan: bool
    plan: Plan | None = None
    status: Literal['historical_undefined', 'unplanned', 'planned', 'open', 'complete']
    anchorVersion: str | None = None
    charterReviewId: UUID | None = None
    dimensions: dict[str, DimensionView]
    overestimated: list[str]


def is_reviewer(c, p, person):
    return person not in (p.initiator_id, p.pm_id, p.sponsor_id) and active_member(c, p, person, 'reviewer')


def can_plan(c, a, p):
    from .gates import submitter
    return a.kind == 'human' and p.stage == 'drafting' and submitter(c, p, a.person, 'charter')


def current_plan(c, pid):
    return c.execute(sa.select(plans).where(plans.c.project_id == pid)
                     .order_by(plans.c.revision.desc()).limit(1)).mappings().one_or_none()


def plan_ref(row):
    return f'calibration-plan:{row.id}@{row.revision}'


def result_ref(row):
    return f'calibration-result:{row.id}@{row.revision}'


def approved_charter(c, pid):
    from .review_outcomes import outcomes
    from .reviews import snapshots, transitions
    t, r, s = transitions.c, db.reviews.c, snapshots.c
    return c.execute(sa.select(outcomes.c.review_id, outcomes.c.payload, s.materials, outcomes.c.decision_id)
        .join(db.reviews, r.id == outcomes.c.review_id)
        .join(snapshots, s.review_id == r.id)
        .join(transitions, sa.and_(t.review_id == r.id, t.request_id == outcomes.c.request_id))
        .where(t.project_id == pid, r.project_id == pid, r.type == 'charter', t.to_stage == 'designing',
               r.status == 'decided', r.verdict.in_(('pass', 'conditional_pass')))
        .order_by(t.to_revision.desc()).limit(1)).mappings().one_or_none()


def reviewed_days(c):
    from .reports import TZ
    return c.scalar(sa.select(sa.func.clock_timestamp())).astimezone(TZ).date()


def recorded_plan(row):
    return Plan(dimensions={key: raw['expectation'] for key, raw in row.dimensions.items()})


def freeze(c, a, p, charter, out):
    from .policy import review_action, selection
    policy = selection(c, p, review_action('charter'))
    required = bool(policy and 'synthetic' not in policy[0])
    row = current_plan(c, p.id)
    if row is None:
        if required:
            out.reject('REVIEW_CALIBRATION_PLAN_REQUIRED', '请先登记立项四维预期、依据、观察时间及责任人。')
        return
    ref = plan_ref(row)
    out.refs.append(ref)
    charter_ref = f'charter:{charter.id}@{charter.revision}' if charter else None
    for key, expected in recorded_plan(row).dimensions.items():
        grouped = row.dimensions[key]
        try:
            valid = grouped['charterRef'] == charter_ref and is_reviewer(c, p, expected.reviewerId) and (
                checked_evidence(c, a, p, expected.evidenceRefs, persisted=True) == grouped['evidence'])
        except Failure:
            valid = False
        if not valid:
            out.reject('REVIEW_CALIBRATION_PLAN_STALE', f'以下立项预期的材料或评价责任人已变化：{key}。', [ref])
        out.refs.extend(expected.evidenceRefs)
    out.content['calibrationPlan'] = {'anchorVersion': ANCHOR_VERSION, 'id': str(row.id), 'revision': row.revision,
        'submittedBy': str(row.submitted_by), 'submittedAt': row.submitted_at.isoformat(), 'dimensions': row.dimensions}


def records(c, review_id):
    found = c.execute(sa.select(results).where(results.c.review_id == review_id)
        .order_by(results.c.dimension, results.c.revision)).mappings().all()
    grouped = {key: [] for key in WEIGHTS}
    for row in found:
        grouped[row.dimension].append(row)
    return grouped


def comparison(charter, key, score=None):
    original = [row['dimensions'][key]['score'] for row in charter.payload['scorecards']]
    mean = Fraction(sum(original), len(original))
    difference = None if score is None else Fraction(Decimal(score)) - mean
    return mean, difference


def display(value):
    return f'{Decimal(value.numerator) / Decimal(value.denominator):.2f}' if value is not None else None


def result_view(row):
    return ResultView.model_validate({**row.evaluation['command'], 'resultId': row.id, 'revision': row.revision,
                                     'reviewerId': row.submitted_by, 'evaluatedAt': row.submitted_at,
                                     'closingDecisionRef': row.evaluation.get('closingDecisionRef')})


def closing_decision_ref(c, pid):
    from .review_outcomes import outcomes
    from .reviews import transitions
    d, x, t = db.decisions.c, outcomes.c, transitions.c
    row = c.execute(sa.select(d.id, d.revision).join(outcomes, x.decision_id == d.id)
        .join(transitions, sa.and_(t.review_id == d.review_id, t.request_id == x.request_id))
        .where(d.project_id == pid, t.project_id == pid, t.from_stage == 'closing_review', t.to_stage == 'closed')
        .order_by(t.to_revision.desc()).limit(1)).mappings().one_or_none()
    return f'decision:{row.id}@{row.revision}' if row else None


def original_ratings(approved, key):
    return [OriginalRating(score=row['dimensions'][key]['score'], round=row['round'],
        anchor=row['dimensions'][key]['selectedAnchorCode'], scorecardRef=f"scorecard:{row['id']}",
        evidenceRefs=row['dimensions'][key]['evidenceRefs']) for row in approved.payload['scorecards']]


def state(c, a, p, op):
    approved = approved_charter(c, p.id)
    frozen = approved.materials.get('calibrationPlan') if approved else None
    draft = current_plan(c, p.id)
    refs = [f'project:{p.id}@{p.revision}']
    values = State(projectId=p.id, planRevision=draft.revision if draft else 1, canPlan=can_plan(c, a, p),
        status='historical_undefined' if approved else 'planned' if draft else 'unplanned',
        plan=recorded_plan(draft) if draft and not approved else None, dimensions={}, overestimated=[])
    if frozen:
        values.charterReviewId = approved.review_id
        values.anchorVersion = frozen['anchorVersion']
        values.plan = Plan(dimensions={key: raw['expectation'] for key, raw in frozen['dimensions'].items()})
        refs.extend([f"calibration-plan:{frozen['id']}@{frozen['revision']}", f'review-snapshot:{approved.review_id}'])
        original_rubric = {item['key']: Dimension.model_validate(item) for item in approved.payload.get('ratingRubric', [])}
        if not original_rubric and frozen['anchorVersion'] == ANCHOR_VERSION:
            original_rubric = {item.key: item for item in rubric('charter')}
        for key, rows in records(c, approved.review_id).items():
            current = result_view(rows[-1]) if rows else None
            mean, difference = comparison(approved, key, current.score if current else None)
            can_evaluate = p.stage in ('closed', 'retrospective', 'archived') and (
                p.stage != 'archived' or current is not None and current.status == 'pending')
            assignee = values.plan.dimensions[key].reviewerId
            sufficiently_negative = difference is not None and difference <= -1
            if sufficiently_negative:
                values.overestimated.append(key)
            values.dimensions[key] = DimensionView(originalMean=display(mean) or '', current=current,
                history=[result_view(row) for row in rows], difference=display(difference),
                requiresRetrospective=difference is not None and abs(difference) >= 1,
                canEvaluate=can_evaluate and assignee == a.person and is_reviewer(c, p, a.person),
                anchors=original_rubric[key].anchors if key in original_rubric else None,
                originalScores=original_ratings(approved, key))
            if rows:
                refs.append(result_ref(rows[-1]))
            if current and current.closingDecisionRef:
                refs.append(current.closingDecisionRef)
        values.status = 'complete' if all(item.current is not None and item.current.status == 'evaluated'
                                          for item in values.dimensions.values()) else 'open'
    elif draft and not approved:
        refs.append(plan_ref(draft))
    return Result[State](data=values, meta=meta(op, refs, project_id=p.id))


def project(c, a, pid, *, write=False, include_charter_owner=False):
    from .gates import lock_project
    if a.kind != 'human':
        raise Failure(403, 'FORBIDDEN')
    p = lock_project(c, a, pid, write=write)
    if not p.materials_visible and not (include_charter_owner and p.initiator_id == a.person) and not can_plan(c, a, p):
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return p


def save(c, a, p, op, rid, key, signature, row, previous):
    result = state(c, a, p, op)
    body = result.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, row.id, p.id, signature, body, fingerprint(body),
                   fingerprint(snapshot(previous)) if previous else None)
    return result


def read(auth, pid):
    return run(auth, GET, lambda c, a, _: state(c, a, project(c, a, pid), GET))


def write_plan(auth, pid, command, key, expected):
    signature = fingerprint([str(pid), expected, command.model_dump(mode='json')])

    def write(c, a, rid):
        p = project(c, a, pid, write=True, include_charter_owner=True)
        from .gates import submitter
        if not submitter(c, p, a.person, 'charter'):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        old = replay(c, a, PLAN, key, signature, Result[State])
        if old is not None:
            return old
        if not can_plan(c, a, p):
            raise Failure(422, 'REVIEW_CALIBRATION_PLAN_FROZEN', project_id=p.id)
        previous = current_plan(c, p.id)
        if expected != (previous.revision if previous else 1):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        t = db.charters
        charter = c.execute(sa.select(t.c.id, t.c.revision).where(t.c.project_id == p.id)).one_or_none()
        if charter is None or not charter.revision:
            raise Failure(422, 'CHARTER_MISSING', project_id=p.id)
        today = reviewed_days(c)
        dimensions = {}
        for dimension, expectation in command.dimensions.items():
            if not is_reviewer(c, p, expectation.reviewerId) or expectation.observeAfter < today:
                raise Failure(422, 'REVIEW_CALIBRATION_EXPECTATION_INVALID', project_id=p.id)
            proofs = checked_evidence(c, a, p, expectation.evidenceRefs)
            dimensions[dimension] = {'expectation': expectation.model_dump(mode='json', exclude_none=True),
                'evidence': proofs, 'charterRef': f'charter:{charter.id}@{charter.revision}'}
        row = c.execute(plans.insert().values(project_id=p.id, revision=expected + 1, submitted_by=a.person,
                         dimensions=dimensions).returning(plans)).mappings().one()
        return save(c, a, p, PLAN, rid, key, signature, row, previous)

    return run(auth, PLAN, write)


def write_result(auth, pid, dimension, command, key, expected):
    signature = fingerprint([str(pid), dimension, expected, command.model_dump(mode='json')])

    def write(c, a, rid):
        p = project(c, a, pid, write=True)
        charter = approved_charter(c, p.id)
        plan = charter.materials.get('calibrationPlan') if charter else None
        if not plan:
            raise Failure(422, 'REVIEW_CALIBRATION_PLAN_REQUIRED', project_id=p.id)
        candidate = Expectation.model_validate(plan['dimensions'][dimension]['expectation'])
        if candidate.reviewerId != a.person or not is_reviewer(c, p, a.person):
            raise Failure(403, 'INDEPENDENT_REVIEWER_REQUIRED', project_id=p.id)
        old = replay(c, a, OUTCOME, key, signature, Result[State])
        if old is not None:
            return old
        history = records(c, charter.review_id)[dimension]
        previous = history[-1] if history else None
        if p.stage not in ('closed', 'retrospective', 'archived') or (
                p.stage == 'archived' and (not previous or previous.evaluation['command']['status'] != 'pending')):
            raise Failure(422, 'REVIEW_CALIBRATION_STAGE_REQUIRED', project_id=p.id)
        if plan['anchorVersion'] != ANCHOR_VERSION:
            raise Failure(409, 'REVIEW_CALIBRATION_ANCHOR_MISMATCH', project_id=p.id)
        if expected != (previous.revision if previous else 1):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        today = reviewed_days(c)
        if (command.score is not None and today < candidate.observeAfter) or (
                command.followUpBy is not None and (command.followUpBy <= today or command.followUpBy < candidate.observeAfter)):
            raise Failure(422, 'REVIEW_CALIBRATION_OBSERVATION_NOT_DUE', project_id=p.id)
        _mean, delta = comparison(charter, dimension, command.score)
        revised = expected + 1
        if p.stage == 'archived' and delta is not None and abs(delta) >= 1:
            if not command.followUpDeviation or command.followUpDeviation.resultRevision != revised:
                raise Failure(422, 'CALIBRATION_DEVIATION_REQUIRED', project_id=p.id)
        elif command.followUpDeviation is not None:
            raise Failure(422, 'CALIBRATION_DEVIATION_NOT_ALLOWED', project_id=p.id)
        from .policy import require
        require(c, p, POLICY, rid)
        evidence = checked_evidence(c, a, p, command.evidenceRefs)
        serialized = command.model_dump(mode='json', exclude_none=True)
        result_data = {'command': serialized, 'evidence': evidence, 'anchorVersion': plan['anchorVersion']}
        closing_ref = closing_decision_ref(c, p.id)
        if closing_ref:
            result_data['closingDecisionRef'] = closing_ref
        row = c.execute(results.insert().values(review_id=charter.review_id, dimension=dimension, revision=revised,
            submitted_by=a.person, evaluation=result_data).returning(results)).mappings().one()
        return save(c, a, p, OUTCOME, rid, key, signature, row, previous)

    return run(auth, OUTCOME, write)


def retrospective_basis(c, a, p, deviations, *, archive=False):
    charter = approved_charter(c, p.id)
    if not charter or 'calibrationPlan' not in charter.materials:
        return [], []
    details, refs = [], []
    today = reviewed_days(c)
    for key, rows in records(c, charter.review_id).items():
        current = rows[-1] if rows else None
        if current is None:
            raise Failure(422, 'REVIEW_CALIBRATION_RESULT_REQUIRED', project_id=p.id)
        result = result_view(current)
        try:
            valid = checked_evidence(c, a, p, result.evidenceRefs, persisted=True) == current.evaluation['evidence']
        except Failure:
            valid = False
        if not valid or (archive and result.followUpBy is not None and (
                result.followUpBy <= today or not is_reviewer(c, p, result.reviewerId))):
            raise Failure(422, 'REVIEW_CALIBRATION_RESULT_STALE', project_id=p.id)
        _original, difference = comparison(charter, key, result.score)
        recorded = deviations.get(key)
        if difference is not None and abs(difference) >= 1 and (
                recorded is None or recorded.resultRevision != current.revision):
            raise Failure(422, 'CALIBRATION_DEVIATION_REQUIRED', project_id=p.id)
        details.append({'dimension': key, 'result': snapshot(current),
                        'deviation': recorded.model_dump(mode='json') if recorded else None})
        refs.extend([result_ref(current), *result.evidenceRefs])
    return details, refs
