"""Human retrospectives and independent Lesson confirmation, not stage transitions."""
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, TypeVar
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Body, Query
from pydantic import AfterValidator, Field, StringConstraints, ValidationError, model_serializer
from sqlalchemy.dialects.postgresql import JSONB

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .cost_reconciliation import Attribution, _receipt_hash
from .costs import Kind as CostKind
from .identity import Failure, explicit_project_scope, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_member
from .review_calibration import Deviation, DimensionKey, retrospective_basis

GET = 'human_retrospective_get'
SAVE = 'human_retrospective_save'
LIST = 'human_lessons_list'
DRAFT = 'human_lessons_draft'
CONFIRM = 'human_confirm_lesson'
SEARCH = 'pmo_lessons_search'

router = APIRouter(prefix='/api/v1', tags=['Retrospectives and lessons'])
_entry = sa.table('project_stage_transitions', sa.column('project_id', sa.Uuid), sa.column('from_stage'),
                  sa.column('to_stage'), sa.column('to_revision', sa.BigInteger),
                  sa.column('transitioned_at', sa.DateTime(timezone=True)))
_retro = sa.table('project_retrospectives',
    *(sa.column(column.name, column.type) for column in db.project_retrospectives.columns),
    sa.column('calibration_deviations', JSONB))
_lessons, _projects = db.lessons, db.projects
_placeholders = frozenset({'todo', 'tbd', 'n/a', 'na', 'none', 'null', 'unknown', '待补', '待填写', '待定',
                           '未知', '无', '暂无', '不适用', '略'})
Origin = Literal['closed', 'terminated']
Kind = Literal['normal', 'terminated']
# Keep the original answer order; the short labels remain stable in model preview sources.
ANSWER_LABELS = ('目标与实际结果', '交付表现及原因', '可复用资产', '经验教训与建议')
QUESTIONS = (
    '目标对照：立项卡承诺的收益 / 成本 / 周期，实际各是多少？差异主要原因？',
    '过程表现：三线偏差各发生在哪个阶段？是估算问题、执行问题还是范围问题？',
    '可复用资产：哪些做法 / 模板 / 方案值得沉淀，下次直接用？',
    '教训与建议：如果重来一次，立项和方案阶段分别会做什么不同的事？（写入 Lesson，带分类标签）',
)
Questions = Annotated[list[str], Field(min_length=4, max_length=4)]


def _plain(value: str) -> str:
    if '\x00' in value:
        raise ValueError('Text must not contain NUL')
    value.encode('utf-8')
    return value


def _explicit_answer(value: str) -> str:
    if not any(char.isalnum() for char in value) or value.rstrip('.。!！?？').strip().casefold() in _placeholders:
        raise ValueError('An explicit retrospective answer is required')
    return _plain(value)


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(_plain)]
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20), AfterValidator(_plain)]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(_explicit_answer)]
QueryText = Annotated[str, AfterValidator(_plain)]


class Save(Strict):
    answers: list[Answer] = Field(min_length=4, max_length=4, description=(
        'In order: target vs. actual; delivery performance and causes; reusable assets; lessons and advice.'))
    calibrationDeviations: dict[DimensionKey, Deviation] = Field(default_factory=dict)


class Draft(Strict):
    category: Category
    what: Text
    rootCause: Text
    advice: Text
    sourceRetrospectiveRevision: int | None = Field(default=None, ge=1)


class Confirm(Strict):
    pass


class Retrospective(Strict):
    projectId: UUID
    stage: str
    kind: Kind | None
    fromStage: Origin | None
    retrospectiveId: UUID | None
    revision: int
    answers: list[str]
    completedAt: datetime | None
    canSave: bool


class CostDiscussion(Strict):
    type: CostKind
    attribution: Attribution
    sourceRef: str


class RetrospectiveView(Retrospective):
    questions: Questions
    costDiscussion: list[CostDiscussion] = Field(default_factory=list, max_length=2)
    calibrationDeviations: dict[DimensionKey, Deviation] | None = None
    calibrationGap: str | None = None

    @model_serializer(mode='wrap')
    def omit_unset_calibration(self, serialize):
        result = serialize(self)
        if self.calibrationDeviations is None:
            result.pop('calibrationDeviations', None)
        if self.calibrationGap is None:
            result.pop('calibrationGap', None)
        return result


class Lesson(Strict):
    lessonId: UUID
    projectId: UUID
    revision: int
    status: Literal['draft', 'confirmed']
    category: str
    what: str
    rootCause: str
    advice: str
    createdBy: UUID
    createdAt: datetime
    confirmedBy: UUID | None
    confirmedAt: datetime | None
    canConfirm: bool


class LessonList(Strict):
    items: list[Lesson] = Field(max_length=100)
    total: int


class SearchHit(Strict):
    lessonId: UUID
    projectId: UUID
    category: str
    what: str
    rootCause: str
    advice: str
    sourceRefs: list[str]
    confirmedAt: datetime


class SearchPage(Strict):
    items: list[SearchHit] = Field(max_length=100)
    page: int
    pageSize: int
    total: int
    asOf: datetime


def _origin(c, p):
    if p.stage not in ('retrospective', 'archived'):
        return None
    entry = c.execute(sa.select(_entry).where(
        _entry.c.project_id == p.id, _entry.c.to_stage == 'retrospective', _entry.c.to_revision <= p.revision,
    ).order_by(_entry.c.to_revision.desc()).limit(1)).mappings().one_or_none()
    return entry if entry and entry.from_stage in ('closed', 'terminated') else None


def _kind(entry) -> Kind | None:
    if entry is None:
        return None
    return 'terminated' if entry.from_stage == 'terminated' else 'normal'


def _cost_discussion(c, p, entry) -> list[CostDiscussion]:
    if _kind(entry) != 'normal':
        return []
    row = c.execute(sa.text('''SELECT r.id,r.revision,r.signed_by,r.grant_id,r.signed_at,r.covered_through,
        r.cost_hash,r.receipt_hash,r.lines,r.request_id,r.rule_version,f.materials,f.snapshot_hash
        FROM project_stage_transitions s
        JOIN decisions d ON d.review_id=s.review_id AND d.project_id=s.project_id AND d.subject='closing'
          AND d.option IN ('pass','conditional_pass')
        JOIN review_decision_snapshots ds ON ds.decision_id=d.id AND ds.review_id=s.review_id
          AND ds.request_id=s.request_id
        JOIN review_submission_snapshots f ON f.review_id=ds.review_id AND f.snapshot_hash=ds.material_hash
        JOIN cost_reconciliations r ON r.project_id=s.project_id
          AND CAST(r.id AS text)=f.materials->'costReconciliation'->>'id'
          AND CAST(r.revision AS text)=f.materials->'costReconciliation'->>'revision'
          AND r.receipt_hash=f.materials->'costReconciliation'->>'receipt_hash'
          AND r.cost_hash=f.materials->'costReconciliation'->>'cost_hash'
        WHERE s.project_id=:pid AND s.to_revision<:revision AND s.from_stage='closing_review' AND s.to_stage='closed'
        ORDER BY s.to_revision DESC LIMIT 1'''), {'pid': p.id, 'revision': entry.to_revision}).mappings().one_or_none()
    if row is None or fingerprint(row.materials) != row.snapshot_hash:
        return []
    digest = _receipt_hash(p.id, row.revision, row.signed_by, row.grant_id, row.signed_at, row.covered_through,
                           row.cost_hash, row.lines, row.request_id, rule_version=row.rule_version)
    if digest != row.receipt_hash:
        return []
    return [CostDiscussion(type=line['type'], attribution=line['overrunReason'],
                           sourceRef=f'cost-reconciliation:{row.id}@{row.revision}')
            for line in row.lines if line.get('overrunReason') and Decimal(line['current'].get('difference') or '0') > 0]


def _entry_ref(entry):
    return f'project-transition:{entry.project_id}@{entry.to_revision}'


def _retro_ref(row):
    return f'retrospective:{row.id}@{row.revision}'


def _lesson_ref(row):
    return f'lesson:{row.id}@{row.revision}'


def _project_ref(p):
    return f'project:{p.id}@{p.revision}'


def _retrospective(c, pid):
    return c.execute(sa.select(_retro).where(_retro.c.project_id == pid)).mappings().one_or_none()


def _human_project(c, a, pid, *, write=False, lesson_id=None):
    if a.kind != 'human':
        raise Failure(403, 'FORBIDDEN')
    query = sa.select(_projects).where(visible(a))
    if lesson_id is None:
        query = query.where(_projects.c.id == pid)
    else:
        query = query.join(_lessons, _lessons.c.project_id == _projects.c.id).where(_lessons.c.id == lesson_id)
    row = c.execute(query.with_for_update(of=_projects, read=not write)).mappings().one_or_none()
    if row is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return row


def _pm(c, a, p):
    return p.pm_id == a.person and active_member(c, p, a.person, 'pm')


def _reviewer(c, a, p):
    return a.person not in (p.initiator_id, p.pm_id, p.sponsor_id) and active_member(c, p, a.person, 'reviewer')


def _require_entry(c, p):
    if p.stage != 'retrospective':
        code = 'PROJECT_ARCHIVED' if p.stage == 'archived' else 'RETROSPECTIVE_STAGE_REQUIRED'
        raise Failure(422, code, project_id=p.id)
    entry = _origin(c, p)
    if entry is None:
        raise Failure(422, 'RETROSPECTIVE_ORIGIN_REQUIRED', project_id=p.id)
    return entry


def _retrospective_result(op, p, row, entry, *, can_save):
    refs = [_project_ref(p)]
    if entry is not None:
        refs.append(_entry_ref(entry))
    if row is not None:
        refs.append(_retro_ref(row))
    answers = row.answers if row is not None else []
    if not isinstance(answers, list) or any(not isinstance(answer, str) for answer in answers):
        raise Failure(422, 'RETROSPECTIVE_ANSWERS_REQUIRED', project_id=p.id)
    data = Retrospective(projectId=p.id, stage=p.stage, kind=_kind(entry), fromStage=entry.from_stage if entry else None,
                         retrospectiveId=row.id if row else None, revision=row.revision if row else 1,
                         answers=answers, completedAt=row.completed_at if row else None, canSave=can_save)
    return Result[Retrospective](data=data, meta=meta(op, refs))


def _lesson(row, *, can_confirm=False) -> Lesson:
    return Lesson(lessonId=row.id, projectId=row.project_id, revision=row.revision, status=row.status,
                  category=row.category, what=row.what, rootCause=row.root_cause, advice=row.advice,
                  createdBy=row.created_by, createdAt=row.created_at, confirmedBy=row.confirmed_by,
                  confirmedAt=row.confirmed_at, canConfirm=can_confirm)


T = TypeVar('T', bound=Strict)


def _replay(c, a, p, op, key, signature, model: type[T]) -> T | None:
    try:
        return replay(c, a, op, key, signature, model)
    except Failure as error:
        error.project_id = p.id
        raise


def _record(c, a, p, op, key, rid, signature, result, row, resource, event, *, before=None):
    body = result.model_dump(mode='json')
    record_receipt(c, a, key, op, rid, row.id, p.id, signature, body, fingerprint(body), before)
    c.execute(db.outbox_events.insert().values(dedupe_key=rid, event_type=event, status='pending', payload={
        'projectId': str(p.id), resource: str(row.id), 'revision': row.revision,
    }))
    return result


def _valid_confirmation():
    # The human confirmation write is the sole setter. Keep historical receipts valid after staff changes.
    return (_lessons.c.status == 'confirmed', _lessons.c.revision >= 2, _lessons.c.confirmed_by.is_not(None),
            _lessons.c.confirmed_by != _lessons.c.created_by, _lessons.c.confirmed_at >= _lessons.c.created_at)


def _same_org_confirmation(org_id):
    person = db.persons
    return sa.exists(sa.select(person.c.id).where(person.c.id == _lessons.c.confirmed_by, person.c.org_id == org_id))


def archive_basis(c, a, p) -> tuple[dict[str, Any], list[str]]:
    """Read gate evidence under the caller's project write lock; authorize and perform the transition elsewhere."""
    if a.org != p.org_id:
        raise Failure(422, 'RETROSPECTIVE_BASIS_REQUIRED')
    entry = _require_entry(c, p)
    row = _retrospective(c, p.id)
    if row is None or row.completed_at is None:
        raise Failure(422, 'RETROSPECTIVE_ANSWERS_REQUIRED', project_id=p.id)
    try:
        answers = Save.model_validate({'answers': row.answers}).answers
    except ValidationError as error:
        raise Failure(422, 'RETROSPECTIVE_ANSWERS_REQUIRED', project_id=p.id) from error

    # One stable witness is enough; later confirmations need not invalidate an existing archive intent.
    witness = c.execute(sa.select(_lessons).where(_lessons.c.project_id == p.id, *_valid_confirmation(),
        _same_org_confirmation(p.org_id)).order_by(_lessons.c.confirmed_at, _lessons.c.id).limit(1)).mappings().one_or_none()
    if witness is None:
        raise Failure(422, 'CONFIRMED_LESSON_REQUIRED', project_id=p.id)
    proof = _lesson(witness).model_dump(mode='json', exclude={'canConfirm', 'createdAt', 'confirmedAt'})
    proof['createdAt'] = witness.created_at.astimezone(UTC).isoformat()
    proof['confirmedAt'] = witness.confirmed_at.astimezone(UTC).isoformat()
    content = {
        'projectId': str(p.id), 'kind': _kind(entry), 'fromStage': entry.from_stage,
        'entryRevision': entry.to_revision, 'enteredAt': entry.transitioned_at.astimezone(UTC).isoformat(),
        'retrospective': {'retrospectiveId': str(row.id), 'revision': row.revision, 'answers': answers,
                          'completedAt': row.completed_at.astimezone(UTC).isoformat()},
        'lessons': [proof],
    }
    refs = [_entry_ref(entry), _retro_ref(row), _lesson_ref(witness)]
    if _kind(entry) == 'normal':
        deviations = {k: Deviation.model_validate(v) for k, v in row.calibration_deviations.items()}
        details, calibration_refs = retrospective_basis(c, a, p, deviations, archive=True)
        if details:
            content['calibration'] = details
            refs.extend(calibration_refs)
    return content, refs


@router.get('/projects/{projectId}/retrospective', operation_id=GET)
def get_retrospective(projectId: UUID, authorization: Auth) -> Result[RetrospectiveView]:
    def query(c, a, _):
        p = _human_project(c, a, projectId)
        entry = _origin(c, p)
        row = _retrospective(c, p.id)
        result = _retrospective_result(GET, p, row, entry,
                                       can_save=entry is not None and p.stage == 'retrospective' and _pm(c, a, p))
        discussions = _cost_discussion(c, p, entry)
        result.meta.sourceRefs = list(dict.fromkeys([*result.meta.sourceRefs, *(i.sourceRef for i in discussions)]))
        gap = None
        if row and p.stage == 'retrospective' and _kind(entry) == 'normal':
            try:
                deviations = {k: Deviation.model_validate(v) for k, v in row.calibration_deviations.items()}
                _details, refs = retrospective_basis(c, a, p, deviations)
                result.meta.sourceRefs = list(dict.fromkeys([*result.meta.sourceRefs, *refs]))
            except Failure as failure:
                gap = failure.code
        return Result[RetrospectiveView](data=RetrospectiveView(**result.data.model_dump(), questions=list(QUESTIONS),
            costDiscussion=discussions, calibrationDeviations=(row.calibration_deviations or None) if row else None,
            calibrationGap=gap),
                                         meta=result.meta)
    return run(authorization, GET, query)


@router.put('/projects/{projectId}/retrospective', operation_id=SAVE)
def save_retrospective(projectId: UUID, command: Save, authorization: Auth, key: Key, expected: Revision) -> Result[Retrospective]:
    revision = int(expected)
    signature = fingerprint([str(projectId), revision, command.answers] + (
        [command.model_dump(mode='json')['calibrationDeviations']] if command.calibrationDeviations else []))

    def write(c, a, rid):
        p = _human_project(c, a, projectId, write=True)
        if not _pm(c, a, p):
            raise Failure(403, 'PM_ASSIGNMENT_REQUIRED', project_id=p.id)
        prior = _replay(c, a, p, SAVE, key, signature, Result[Retrospective])
        if prior is not None:
            return prior
        entry = _require_entry(c, p)
        old = _retrospective(c, p.id)
        if revision != (old.revision if old else 1):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if _kind(entry) == 'normal':
            retrospective_basis(c, a, p, command.calibrationDeviations)
        values = {'answers': command.answers, 'revision': revision + 1, 'completed_at': sa.func.now(),
                  'updated_at': sa.func.now(),
                  'calibration_deviations': command.model_dump(mode='json')['calibrationDeviations']}
        stmt = _retro.update().where(_retro.c.id == old.id) if old else _retro.insert().values(project_id=p.id)
        row = c.execute(stmt.values(**values).returning(_retro)).mappings().one()
        result = _retrospective_result(SAVE, p, row, entry, can_save=True)
        before = fingerprint([old.revision, old.answers]) if old else None
        return _record(c, a, p, SAVE, key, rid, signature, result, row, 'retrospectiveId', 'retrospective.saved', before=before)
    return run(authorization, SAVE, write)


@router.get('/projects/{projectId}/lessons', operation_id=LIST)
def list_lessons(projectId: UUID, authorization: Auth) -> Result[LessonList]:
    def query(c, a, _):
        p = _human_project(c, a, projectId)
        reviewing = p.stage == 'retrospective' and _origin(c, p) is not None and _reviewer(c, a, p)
        subject = _lessons.c.project_id == p.id
        total = c.scalar(sa.select(sa.func.count()).select_from(_lessons).where(subject))
        rows = list(c.execute(sa.select(_lessons).where(subject).order_by(
            _lessons.c.created_at.desc(), _lessons.c.id).limit(100)).mappings())
        items = [_lesson(row, can_confirm=reviewing and row.status == 'draft' and row.created_by != a.person) for row in rows]
        return Result[LessonList](data=LessonList(items=items, total=total), meta=meta(LIST, [_project_ref(p), *map(_lesson_ref, rows)]))
    return run(authorization, LIST, query)


@router.post('/projects/{projectId}/lessons', operation_id=DRAFT)
def draft_lesson(projectId: UUID, command: Draft, authorization: Auth, key: Key) -> Result[Lesson]:
    signature = fingerprint([str(projectId), command.model_dump(mode='json', exclude_none=True)])

    def write(c, a, rid):
        p = _human_project(c, a, projectId, write=True)
        if not _pm(c, a, p):
            raise Failure(403, 'PM_ASSIGNMENT_REQUIRED', project_id=p.id)
        prior = _replay(c, a, p, DRAFT, key, signature, Result[Lesson])
        if prior is not None:
            return prior
        entry = _require_entry(c, p)
        basis = []
        if command.sourceRetrospectiveRevision is not None:
            source = _retrospective(c, p.id)
            if source is None or source.revision != command.sourceRetrospectiveRevision:
                raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
            basis.append(_retro_ref(source))
        row = c.execute(_lessons.insert().values(project_id=p.id, created_by=a.person, status='draft', category=command.category,
            what=command.what, root_cause=command.rootCause, advice=command.advice).returning(_lessons)).mappings().one()
        result = Result[Lesson](data=_lesson(row), meta=meta(DRAFT, [_project_ref(p), _entry_ref(entry), *basis, _lesson_ref(row)]))
        return _record(c, a, p, DRAFT, key, rid, signature, result, row, 'lessonId', 'lesson.drafted')
    return run(authorization, DRAFT, write)


@router.post('/lessons/{lessonId}/confirm', operation_id=CONFIRM)
def confirm_lesson(lessonId: UUID, authorization: Auth, key: Key, expected: Revision,
                   command: Confirm = Body(default_factory=Confirm)) -> Result[Lesson]:
    revision = int(expected)
    signature = fingerprint([str(lessonId), revision])

    def write(c, a, rid):
        p = _human_project(c, a, None, write=True, lesson_id=lessonId)
        old = c.execute(sa.select(_lessons).where(_lessons.c.id == lessonId, _lessons.c.project_id == p.id)).mappings().one()
        if old.created_by == a.person or not _reviewer(c, a, p):
            raise Failure(403, 'INDEPENDENT_REVIEWER_REQUIRED', project_id=p.id)
        prior = _replay(c, a, p, CONFIRM, key, signature, Result[Lesson])
        if prior is not None:
            return prior
        entry = _require_entry(c, p)
        if old.revision != revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if old.status != 'draft' or old.confirmed_at is not None or old.confirmed_by is not None:
            raise Failure(409, 'LESSON_NOT_DRAFT', project_id=p.id)
        row = c.execute(_lessons.update().where(_lessons.c.id == old.id).values(status='confirmed',
            confirmed_by=a.person, confirmed_at=sa.func.now(), revision=revision + 1,
            updated_at=sa.func.now()).returning(_lessons)).mappings().one()
        result = Result[Lesson](data=_lesson(row), meta=meta(CONFIRM, [_project_ref(p), _entry_ref(entry), _lesson_ref(row)]))
        before = fingerprint(_lesson(old).model_dump(mode='json'))
        return _record(c, a, p, CONFIRM, key, rid, signature, result, row, 'lessonId', 'lesson.confirmed', before=before)
    return run(authorization, CONFIRM, write)


def find_lessons(c, a, *, keyword, category, page, page_size, exclude_project=None, at=None):
    source = _lessons.join(_projects, _lessons.c.project_id == _projects.c.id)
    filters = [visible(a), *_valid_confirmation(), _same_org_confirmation(_projects.c.org_id),
               _projects.c.stage.in_(('retrospective', 'archived'))]
    if a.kind == 'agent':
        filters.append(explicit_project_scope(a))
    if exclude_project is not None:
        filters.append(_projects.c.id != exclude_project)
    if at is not None:
        filters.append(_lessons.c.confirmed_at <= at)
    if keyword and keyword.strip():
        filters.append(sa.or_(*[col.icontains(keyword.strip(), autoescape=True) for col in
                               (_lessons.c.what, _lessons.c.root_cause, _lessons.c.advice)]))
    if category and category.strip():
        filters.append(sa.func.lower(_lessons.c.category) == category.strip().lower())
    total = c.scalar(sa.select(sa.func.count()).select_from(source).where(*filters))
    items = []
    offset = (page - 1) * page_size
    if offset < total:
        rows = c.execute(sa.select(_lessons, _projects.c.revision.label('project_revision')).select_from(source)
                         .where(*filters).order_by(_lessons.c.confirmed_at.desc(), _lessons.c.id)
                         .limit(page_size).offset(offset)).mappings()
        for row in rows:
            hits = [_lesson_ref(row), f'project:{row.project_id}@{row.project_revision}']
            items.append(SearchHit(lessonId=row.id, projectId=row.project_id, category=row.category, what=row.what,
                                   rootCause=row.root_cause, advice=row.advice, sourceRefs=hits, confirmedAt=row.confirmed_at))
    return items, total


@router.get('/lessons/search', operation_id=SEARCH)
def search_lessons(authorization: Auth, keyword: Annotated[QueryText | None, Query(max_length=160)] = None,
                   category: Annotated[QueryText | None, Query(max_length=80)] = None,
                   page: Annotated[int, Query(ge=1)] = 1, pageSize: Annotated[int, Query(ge=1, le=100)] = 20) -> Result[SearchPage]:
    def query(c, a, _):
        items, total = find_lessons(c, a, keyword=keyword, category=category, page=page, page_size=pageSize)
        refs = [ref for hit in items for ref in hit.sourceRefs]
        metadata = meta(SEARCH, list(dict.fromkeys(refs)))
        data = SearchPage(items=items, page=page, pageSize=pageSize, total=total, asOf=metadata.asOf)
        return Result[SearchPage](data=data, meta=metadata)
    return run(authorization, SEARCH, query)
