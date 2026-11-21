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
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .identity import Actor, Failure, explicit_grants, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run

LIST = 'pmo_task_list'
CREATE = 'human_task_create'
UPDATE = 'pmo_task_update'

Source = Literal['review', 'rule_event', 'remediation']
Status = Literal['todo', 'in_progress', 'blocked', 'done', 'cancelled']
SOURCES = {'review': db.reviews, 'rule_event': db.rule_events, 'remediation': db.risks}
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
    sourceType: Source | None = None
    sourceId: UUID | None = None
    completedAt: datetime | None = None
    canUpdate: bool | None = None


class TaskPage(Strict):
    items: list[Task] = Field(max_length=100)
    page: int
    pageSize: int
    total: int
    canCreate: bool | None = None
    projectRevision: int | None = None


def _project(c: Connection, a: Actor, project_id: UUID | None = None, *, task_id: UUID | None = None,
             write: bool = False) -> RowMapping:
    p = db.projects
    stmt = sa.select(p).where(visible(a))
    if task_id is None:
        stmt = stmt.where(p.c.id == project_id)
    else:
        stmt = stmt.join(t, t.c.project_id == p.c.id).where(t.c.id == task_id, t.c.source_type.in_(SOURCES))
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


@router.get('/projects/{projectId}/tasks', operation_id=LIST, response_model_exclude_none=True)
def listing(projectId: UUID, authorization: Auth, page: int = Query(1, ge=1),
            pageSize: int = Query(20, ge=1, le=100)) -> Result[TaskPage]:
    def query(c, a, _):
        p = _project(c, a, projectId)
        roles = _roles(c, p, a.person)
        if not roles:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        pm = _pm(p, a, roles)
        project_wide = pm or bool(roles & {'reviewer', 'client_representative'}) or (
            'sponsor' in roles and p.sponsor_id == a.person)
        cond = [t.c.project_id == p.id, t.c.source_type.in_(SOURCES)]
        if not project_wide:
            cond.append(t.c.assignee_id == a.person)
        total = c.execute(sa.select(sa.func.count()).select_from(t).where(*cond)).scalar_one()
        rows = c.execute(sa.select(t).where(*cond).order_by(t.c.created_at.desc(), t.c.id)
                         .limit(pageSize).offset((page - 1) * pageSize)).mappings()
        human, writable = a.kind == 'human', p.stage != 'archived'
        items = [_task(r, can_update=writable and (pm or r.assignee_id == a.person) if human else None) for r in rows]
        result = TaskPage(items=items, page=page, pageSize=pageSize, total=total,
                          canCreate=writable and pm if human else None, projectRevision=p.revision if human else None)
        return Result[TaskPage](data=result, meta=meta(LIST, [f'task:{i.taskId}@{i.revision}' for i in items]))
    return run(authorization, LIST, query)


def _open_project(p: RowMapping) -> None:
    if p.stage == 'archived':
        raise Failure(422, 'PROJECT_ARCHIVED', project_id=p.id)


def _prior(c, a, p, op, key, sig):
    try:
        return replay(c, a, op, key, sig, Result[Task])
    except Failure as failure:
        failure.project_id = p.id
        raise


def _finish(c, a, p, op, rid, key, sig, row, *, before=None) -> Result[Task]:
    item = _task(row, can_update=True if a.kind == 'human' else None)
    response = Result[Task](data=item, meta=meta(op, [f'task:{item.taskId}@{item.revision}']))
    body = response.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, row.id, p.id, sig, body, fingerprint(body), before)
    c.execute(db.outbox_events.insert().values(dedupe_key=rid, event_type='task.created' if op == CREATE else 'task.updated',
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
                        t.c.source_type.in_(SOURCES)).with_for_update()).mappings().one_or_none()
        if row is None:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        roles = _roles(c, p, a.person)
        if not (_pm(p, a, roles) or (roles and row.assignee_id == a.person)):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        old = _prior(c, a, p, UPDATE, key, sig)
        if old is not None:
            return old
        _open_project(p)
        if row.revision != revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        fields = command.fieldsTask
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
