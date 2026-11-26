"""Project strengthening tasks. Updating a task never advances a project gate."""
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AfterValidator, AwareDatetime, BeforeValidator, Field, StringConstraints, model_validator
from pydantic.json_schema import SkipJsonSchema
from sqlalchemy.engine import Connection, RowMapping

from . import schema_v1 as db
from . import review_responses as written
from .access import Auth, Key, Revision
from .contracts import ProjectView, Result, Strict
from .identity import Actor, Failure, explicit_grants, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .report_analysis import OPEN_TASK, TASK_SOURCE

LIST = 'pmo_task_list'
CREATE = 'human_task_create'
UPDATE = 'pmo_task_update'
QUEUE = 'human_strengthening_queue'
RESPOND = 'human_charter_strengthening_response'

Source = Literal['review', 'rule_event', 'remediation']
Status = Literal['todo', 'in_progress', 'blocked', 'done', 'cancelled']
SOURCES = {'review': db.reviews, 'rule_event': db.rule_events, 'remediation': db.risks}
SYSTEM_SOURCES = ('fact_clarification',)
AGENT_SOURCES = (*SOURCES, *SYSTEM_SOURCES)
VISIBLE_SOURCES = (*AGENT_SOURCES, TASK_SOURCE)
t = db.tasks
router = APIRouter(prefix='/api/v1', tags=['Strengthening tasks'])


def _plain(value: str) -> str:
    if '\x00' in value:
        raise ValueError('Title must not contain NUL')
    value.encode('utf-8')
    return value


def _date_time(value: object) -> object:
    if not isinstance(value, str) or value[10:11].upper() != 'T':
        raise ValueError('Expected a date-time string with a timezone')
    return value


Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200), AfterValidator(_plain)]
DueAt = Annotated[AwareDatetime, BeforeValidator(_date_time)]


class Create(Strict):
    title: Title
    sourceType: Source
    sourceId: UUID
    assigneeId: UUID | SkipJsonSchema[None] = None
    dueAt: DueAt | SkipJsonSchema[None] = None

    @model_validator(mode='after')
    def optional_values(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError('Omit optional fields rather than passing null')
        return self


class Fields(Strict):
    status: Status | SkipJsonSchema[None] = None
    dueAt: DueAt | SkipJsonSchema[None] = None

    @model_validator(mode='after')
    def required_change(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError('Expected one or more non-null task fields')
        return self


class Update(Strict):
    fieldsTask: Fields


class Task(Strict):
    taskId: UUID
    projectId: UUID
    title: str
    status: Status
    revision: int
    assigneeId: UUID | None = None
    dueAt: datetime | None = None
    # Only browser sessions receive provenance, completion time and UI permissions.
    sourceType: Source | Literal['fact_clarification', 'report_analysis'] | None = None
    sourceId: UUID | None = None
    completedAt: datetime | None = None
    canUpdate: bool | None = None
    canRespond: bool | None = None
    responseEvidenceRequired: bool | None = None
    writtenResponse: written.Response | None = None


class TaskPage(Strict):
    items: list[Task] = Field(max_length=100)
    page: int
    pageSize: int
    total: int
    canCreate: bool | None = None
    projectRevision: int | None = None


class QueueEntry(Strict):
    project: ProjectView
    task: Task


class QueuePage(Strict):
    items: list[QueueEntry] = Field(max_length=100)
    page: int
    pageSize: int
    total: int


def _project(c: Connection, a: Actor, project_id: UUID | None = None, *, task_id: UUID | None = None,
             write: bool = False) -> RowMapping:
    p = db.projects
    stmt = sa.select(p).where(visible(a))
    if task_id is None:
        stmt = stmt.where(p.c.id == project_id)
    else:
        stmt = stmt.join(t, t.c.project_id == p.c.id).where(t.c.id == task_id,
            t.c.source_type.in_(VISIBLE_SOURCES if a.kind == 'human' else AGENT_SOURCES))
    row = c.execute(stmt.with_for_update(of=p, read=not write)).mappings().one_or_none()
    if row is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    if a.kind == 'agent':
        # A newly created draft in allowed_project_ids is not an affirmative task grant.
        g = explicit_grants.c
        grant = c.scalar(sa.select(g.project_id).where(g.credential_id == a.credential, g.granted_by == a.account,
                                                       g.project_id == row.id).with_for_update(read=True))
        if grant is None:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
    return row


def _roles(c: Connection, project: RowMapping, person: UUID) -> set[str]:
    m, n, u = db.project_members, db.persons, db.user_accounts
    stmt = sa.select(m.c.role).join(n, n.c.id == m.c.person_id).join(u, u.c.id == n.c.user_id).where(
        m.c.project_id == project.id, m.c.person_id == person, m.c.assignment_status == 'accepted',
        m.c.accepted_at.is_not(None), m.c.revoked_at.is_(None), n.c.org_id == project.org_id,
        n.c.is_active, u.c.is_active, sa.or_(u.c.locked_until.is_(None), u.c.locked_until <= sa.func.now()),
    )
    if project.initiator_id != person or project.stage != 'drafting':
        stmt = stmt.where(m.c.role != 'initiator')
    return set(c.scalars(stmt.with_for_update(read=True, of=(m, n))))


def _pm(project: RowMapping, a: Actor, roles: set[str]) -> bool:
    return a.person == project.pm_id and 'pm' in roles


def can_grant_task_update(c: Connection, a: Actor, project: RowMapping) -> bool:
    """Issuer preflight for each explicit project; a call still checks the actual task."""
    if a.kind != 'human' or a.org != project.org_id:
        return False
    p = _project(c, a, project.id)
    if p.stage == 'archived':
        return False
    roles = _roles(c, p, a.person)
    return _pm(p, a, roles) or bool(roles and c.scalar(sa.select(t.c.id).where(
        t.c.project_id == p.id, t.c.assignee_id == a.person, t.c.source_type.in_(SOURCES)).limit(1)))


def _task(row: RowMapping, *, can_update: bool | None = None) -> Task:
    item = Task(taskId=row.id, projectId=row.project_id, title=row.title, revision=row.revision,
                status='todo' if row.status == 'open' else row.status, assigneeId=row.assignee_id, dueAt=row.due_at)
    if can_update is not None:
        item.sourceType, item.sourceId, item.completedAt, item.canUpdate = (
            row.source_type, row.source_id, row.completed_at, can_update)
    return item


def _charter_binding(c, row):
    if row.source_type != 'review':
        return None
    b, r = written.bindings.c, db.reviews.c
    return c.execute(sa.select(written.bindings).join(db.reviews, r.id == b.review_id).where(b.task_id == row.id,
        b.review_id == row.source_id, r.project_id == row.project_id, r.type == 'charter',
        r.status == 'decided', r.verdict.in_(('pass', 'conditional_pass')))).mappings().one_or_none()


def _response_state(c, p, row, item, *, pm):
    bound = _charter_binding(c, row)
    if bound is None:
        return
    previous = written.latest(c, row.id)
    item.canRespond = pm and p.stage in ('designing', 'feasibility_review')
    item.responseEvidenceRequired = bool(bound.evidence_required)
    if previous is not None:
        item.writtenResponse = written.view(previous, bound.review_id, bound.item_key)


@router.get('/projects/{projectId}/tasks', operation_id=LIST, response_model_exclude_none=True)
def listing(projectId: UUID, authorization: Auth, page: int = Query(1, ge=1),
            pageSize: int = Query(20, ge=1, le=100), taskId: UUID | None = Query(None)) -> Result[TaskPage]:
    def query(c, a, _):
        p = _project(c, a, projectId)
        roles = _roles(c, p, a.person)
        if not roles:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        pm = _pm(p, a, roles)
        project_wide = pm or bool(roles & {'reviewer', 'client_representative'}) or (
            'sponsor' in roles and p.sponsor_id == a.person)
        cond = [t.c.project_id == p.id, t.c.source_type.in_(VISIBLE_SOURCES if a.kind == 'human' else AGENT_SOURCES)]
        if not project_wide:
            cond.append(t.c.assignee_id == a.person)
        if taskId is not None:
            cond.append(t.c.id == taskId)
        total = c.execute(sa.select(sa.func.count()).select_from(t).where(*cond)).scalar_one()
        offset = (page - 1) * pageSize
        rows = c.execute(sa.select(t).where(*cond).order_by(t.c.created_at.desc(), t.c.id)
                         .limit(pageSize).offset(offset)).mappings().all() if offset < total else []
        human, writable = a.kind == 'human', p.stage != 'archived'
        items = [_task(r, can_update=writable and r.source_type not in SYSTEM_SOURCES
            and (pm or r.assignee_id == a.person) if human else None) for r in rows]
        if human:
            for row, item in zip(rows, items, strict=True):
                _response_state(c, p, row, item, pm=pm)
        result = TaskPage(items=items, page=page, pageSize=pageSize, total=total,
                          canCreate=writable and pm if human else None, projectRevision=p.revision if human else None)
        return Result[TaskPage](data=result, meta=meta(LIST, [f'task:{i.taskId}@{i.revision}' for i in items], project_id=p.id))
    return run(authorization, LIST, query)


@router.get('/me/strengthening-tasks', operation_id=QUEUE, response_model_exclude_none=True)
def personal_queue(authorization: Auth, page: int = Query(1, ge=1),
                   pageSize: int = Query(20, ge=1, le=100)) -> Result[QueuePage]:
    def read(c, a, _):
        p = db.projects
        authorized = sa.select(t, p.c.name, p.c.code, p.c.stage, p.c.org_id, p.c.revision.label('project_revision'),
            p.c.created_at.label('project_created_at')).join(p, p.c.id == t.c.project_id).where(
            t.c.assignee_id == a.person, t.c.source_type.in_(VISIBLE_SOURCES), t.c.status.in_(OPEN_TASK),
            p.c.stage != 'archived', visible(a)).cte('assigned_tasks')
        counts = sa.select(sa.func.count().label('total')).select_from(authorized).subquery()
        order = (authorized.c.due_at.asc().nulls_last(), authorized.c.created_at.desc(), authorized.c.id)
        selected = sa.select(authorized).order_by(*order).limit(pageSize).offset(
            min((page - 1) * pageSize, 2**63 - 1)).subquery('assigned_task_page')
        # An empty page still returns its scoped count and database read time.
        rows = c.execute(sa.select(selected, counts.c.total, sa.func.statement_timestamp().label('read_at'))
                        .select_from(counts.outerjoin(selected, sa.true())).order_by(
            selected.c.due_at.asc().nulls_last(), selected.c.created_at.desc(), selected.c.id)).mappings().all()
        items = [QueueEntry(project=ProjectView(projectId=r.project_id, code=r.code, name=r.name, stage=r.stage,
                    orgId=r.org_id, revision=r.project_revision, createdAt=r.project_created_at),
                    task=_task(r, can_update=r.source_type not in SYSTEM_SOURCES)) for r in rows if r.id is not None]
        info = meta(QUEUE, [f'task:{item.task.taskId}@{item.task.revision}' for item in items])
        info.asOf = rows[0].read_at
        return Result[QueuePage](data=QueuePage(items=items, page=page, pageSize=pageSize, total=rows[0].total), meta=info)
    return run(authorization, QUEUE, read)


def _open_project(p: RowMapping) -> None:
    if p.stage == 'archived':
        raise Failure(422, 'PROJECT_ARCHIVED', project_id=p.id)


def _prior(c, a, p, op, key, sig):
    try:
        return replay(c, a, op, key, sig, Result[Task])
    except Failure as failure:
        failure.project_id = p.id
        raise


def _finish(c, a, p, op, rid, key, sig, row, *, before=None, bound=None, response_row=None) -> Result[Task]:
    item = _task(row, can_update=True if a.kind == 'human' else None)
    refs = [f'task:{item.taskId}@{item.revision}']
    if response_row is not None:
        item.canRespond = True
        item.responseEvidenceRequired = bool(bound.evidence_required)
        item.writtenResponse = written.view(response_row, bound.review_id, bound.item_key)
        refs.extend([written.source_ref(response_row), *item.writtenResponse.evidenceRefs])
    response = Result[Task](data=item, meta=meta(op, refs))
    body = response.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, row.id, p.id, sig, body, fingerprint(body), before)
    event = {CREATE: 'task.created', RESPOND: 'task.response_submitted'}.get(op, 'task.updated')
    c.execute(db.outbox_events.insert().values(dedupe_key=rid, event_type=event,
        status='pending', payload={'projectId': str(p.id), 'taskId': str(row.id), 'revision': row.revision, 'status': item.status}))
    return response


@router.post('/projects/{projectId}/tasks', operation_id=CREATE, response_model_exclude_none=True,
             openapi_extra={'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True})
def create(projectId: UUID, command: Create, authorization: Auth, key: Key, expected: Revision) -> Result[Task]:
    revision = int(expected)
    sig = fingerprint([str(projectId), revision, command.model_dump(mode='json', exclude_none=True)])

    def write(c, a, rid):
        if a.kind != 'human':
            raise Failure(403, 'FORBIDDEN')
        p = _project(c, a, projectId, write=True)
        if not _pm(p, a, _roles(c, p, a.person)):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        old = _prior(c, a, p, CREATE, key, sig)
        if old is not None:
            return old
        _open_project(p)
        if p.revision != revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        source = SOURCES[command.sourceType]
        if c.scalar(sa.select(source.c.id).where(source.c.id == command.sourceId, source.c.project_id == p.id)) is None:
            raise Failure(422, 'INVALID_TASK_SOURCE', project_id=p.id)
        if command.assigneeId is not None and not _roles(c, p, command.assigneeId):
            raise Failure(422, 'INVALID_TASK_ASSIGNEE', project_id=p.id)
        row = c.execute(t.insert().values(project_id=p.id, title=command.title, created_by=a.person, status='todo',
            assignee_id=command.assigneeId, due_at=command.dueAt, source_type=command.sourceType,
            source_id=command.sourceId).returning(t)).mappings().one()
        return _finish(c, a, p, CREATE, rid, key, sig, row)
    return run(authorization, CREATE, write)


@router.patch('/tasks/{taskId}', operation_id=UPDATE, response_model_exclude_none=True)
def update(taskId: UUID, command: Update, authorization: Auth, key: Key, expected: Revision) -> Result[Task]:
    revision = int(expected)
    sig = fingerprint([str(taskId), revision, command.model_dump(mode='json', exclude_unset=True)])

    def write(c, a, rid):
        p = _project(c, a, task_id=taskId, write=True)
        row = c.execute(sa.select(t).where(t.c.id == taskId, t.c.project_id == p.id,
                        t.c.source_type.in_(VISIBLE_SOURCES)).with_for_update()).mappings().one_or_none()
        if row is None:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        roles = _roles(c, p, a.person)
        if not (_pm(p, a, roles) or (roles and row.assignee_id == a.person)):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        if row.source_type in SYSTEM_SOURCES:
            raise Failure(422, 'TASK_SOURCE_CONTROLLED', project_id=p.id)
        old = _prior(c, a, p, UPDATE, key, sig)
        if old is not None:
            return old
        _open_project(p)
        if row.revision != revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        fields = command.fieldsTask
        bound = _charter_binding(c, row)
        if fields.status == 'done' and row.status != 'done' and bound is not None and bound.evidence_required is not None:
            raise Failure(422, 'CHARTER_STRENGTHENING_RESPONSE_REQUIRED', project_id=p.id)
        if row.source_type == TASK_SOURCE and fields.status in OPEN_TASK and row.status not in OPEN_TASK and c.scalar(
                sa.select(t.c.id).where(t.c.project_id == p.id, t.c.source_type == TASK_SOURCE,
                t.c.source_id == row.source_id, t.c.id != row.id, t.c.status.in_(OPEN_TASK)).limit(1)):
            raise Failure(409, 'MANUAL_TASK_ALREADY_OPEN', project_id=p.id)
        values: dict[str, Any] = dict(revision=revision + 1, updated_at=sa.func.clock_timestamp())
        if fields.status is not None:
            values['status'] = fields.status
            values['completed_at'] = (row.completed_at or sa.func.clock_timestamp()) if fields.status == 'done' else None
        if fields.dueAt is not None:
            values['due_at'] = fields.dueAt
        saved = c.execute(t.update().where(t.c.id == row.id, t.c.revision == revision)
                          .values(**values).returning(t)).mappings().one()
        before = fingerprint(_task(row, can_update=True).model_dump(mode='json', exclude={'canUpdate'}, exclude_none=True))
        return _finish(c, a, p, UPDATE, rid, key, sig, saved, before=before)
    return run(authorization, UPDATE, write)


@router.post('/tasks/{taskId}/written-response', operation_id=RESPOND, response_model_exclude_none=True,
             openapi_extra={'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True})
def respond(taskId: UUID, command: written.Command, authorization: Auth, key: Key, expected: Revision) -> Result[Task]:
    revision = int(expected)
    signature = fingerprint([str(taskId), revision, command.model_dump(mode='json')])

    def write(c, a, rid):
        p = _project(c, a, task_id=taskId, write=True)
        if a.kind != 'human' or not _pm(p, a, _roles(c, p, a.person)):
            raise Failure(403, 'PM_ASSIGNMENT_REQUIRED', project_id=p.id)
        old = _prior(c, a, p, RESPOND, key, signature)
        if old is not None:
            return old
        _open_project(p)
        if p.stage not in ('designing', 'feasibility_review'):
            raise Failure(422, 'REVIEW_RESPONSE_STAGE_REQUIRED', project_id=p.id)
        row = c.execute(sa.select(t).where(t.c.id == taskId, t.c.project_id == p.id).with_for_update()).mappings().one()
        bound = _charter_binding(c, row)
        if bound is None:
            raise Failure(422, 'CHARTER_STRENGTHENING_TASK_REQUIRED', project_id=p.id)
        if row.revision != revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if bound.evidence_required and not command.evidenceRefs:
            raise Failure(422, 'REVIEW_RESPONSE_EVIDENCE_REQUIRED', project_id=p.id)
        evidence = written.checked_evidence(c, a, p, command.evidenceRefs)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        submitted = c.execute(written.responses.insert().values(task_id=taskId, revision=revision + 1,
            submitted_by=a.person, submitted_at=now, response_text=command.responseText, evidence=evidence)
            .returning(written.responses)).mappings().one()
        saved = c.execute(t.update().where(t.c.id == row.id).values(status='done', completed_at=now, updated_at=now,
            revision=revision + 1).returning(t)).mappings().one()
        before = fingerprint(_task(row, can_update=True).model_dump(mode='json', exclude={'canUpdate'}, exclude_none=True))
        return _finish(c, a, p, RESPOND, rid, key, signature, saved, before=before, bound=bound, response_row=submitted)

    return run(authorization, RESPOND, write)
