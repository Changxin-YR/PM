"""Versioned project reports. Report claims are not verified facts."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import re
from typing import Annotated, Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi import APIRouter, Body, Query
from pydantic import AfterValidator, Field, JsonValue, StrictStr, model_validator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeEngine

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .identity import Failure, explicit_grants, explicit_project_scope, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_member
from .weekly_basis import WeeklyBasis

LIST = 'pmo_report_list'
GET = 'pmo_report_get'
SUBMIT = 'pmo_report_submit'
DRAFT = 'human_report_draft_create'
EDIT = 'human_report_draft_update'
SUBMIT_EXISTING = 'human_report_submit_existing'
CONFIRM = 'human_report_confirm'
GENERATE = 'human_weekly_report_generate'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
TZ = ZoneInfo('Asia/Shanghai')
router = APIRouter(prefix='/api/v1', tags=['Reports'])

_receipts: dict[str, TypeEngine[Any]] = {'content_hash': sa.CHAR(64), 'submitted_by': sa.Uuid(),
    'confirmed_by': sa.Uuid(), 'confirmed_at': sa.DateTime(timezone=True), 'weekly_basis': JSONB(none_as_null=True)}
reports = sa.table('reports', *(sa.column(col.name, cast(TypeEngine[Any], col.type)) for col in db.reports.c if col.name not in _receipts),
                   *(sa.column(name, kind) for name, kind in _receipts.items()))
r = reports.c
projects = db.projects
_links = sa.table('evidence_links', sa.column('evidence_id', sa.Uuid), sa.column('resource_type'), sa.column('resource_id', sa.Uuid))

Kind = Literal['daily', 'weekly']
Status = Literal['draft', 'submitted', 'confirmed']
ClaimKind = Literal['progress', 'completion']
Effort = Annotated[StrictStr, Field(pattern=r'^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?\z')]


def _plain(value: str) -> str:
    if '\x00' in value:
        raise ValueError('Text must not contain NUL')
    value.encode('utf-8')
    return value


Text = Annotated[StrictStr, Field(max_length=20000), AfterValidator(_plain)]
Section = Annotated[list[dict[str, JsonValue]], Field(max_length=100)]


def _json_limits(value: JsonValue, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError('Report sections are nested too deeply')
    if isinstance(value, str):
        _plain(value)
    elif isinstance(value, list):
        for item in value:
            _json_limits(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            _plain(key)
            _json_limits(item, depth + 1)


def _uuid(value: JsonValue) -> UUID:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
        raise ValueError('Expected a resource UUID')
    return UUID(value)


def _evidence_ids(item: dict[str, JsonValue]) -> tuple[UUID, ...]:
    raw = item.get('evidenceRefs', [])
    if not isinstance(raw, list) or len(raw) > 8:
        raise ValueError('Expected up to eight evidence references')
    refs = [*raw, item['evidenceRef']] if 'evidenceRef' in item else raw
    ids = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.startswith('evidence:'):
            raise ValueError('Expected an evidence reference')
        ids.append(_uuid(ref[len('evidence:'):]))
    return tuple(dict.fromkeys(ids))


def period_bounds(kind: Kind, period: str) -> tuple[date, date]:
    """Business periods are ISO weeks or local dates, left-closed and right-open."""
    if kind == 'weekly':
        week = re.fullmatch(r'([0-9]{4})-W([0-9]{2})', period)
        if week is None:
            raise ValueError('Weekly period must be YYYY-Www')
        start = date.fromisocalendar(int(week[1]), int(week[2]), 1)
        return start, start + timedelta(days=7)
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', period):
        raise ValueError('Daily period must be YYYY-MM-DD')
    start = date.fromisoformat(period)
    return start, start + timedelta(days=1)


class DraftFields(Strict):
    milestone_progress: Section = Field(default_factory=list)
    effort_person_days: Effort | None = None
    requirement_progress: Section = Field(default_factory=list)
    risks_and_blockers: Section = Field(default_factory=list)

    @model_validator(mode='after')
    def validated(self):
        value = self.model_dump(exclude_none=True)
        _json_limits(value)
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 65536:
            raise ValueError('Report sections are too large')
        for section, id_key, _ in _sections(self):
            for item in section:
                if id_key in item:
                    _uuid(item[id_key])
                if _evidence_ids(item) and id_key not in item:
                    raise ValueError('Evidence references require a resource ID')
                if id_key == 'milestoneId' and 'claim' in item:
                    if item['claim'] not in ('progress', 'completion') or id_key not in item:
                        raise ValueError('A milestone claim needs its ID and a progress or completion kind')
        return self


def _sections(fields: DraftFields):
    return ((fields.milestone_progress, 'milestoneId', db.milestones),
            (fields.requirement_progress, 'requirementId', db.requirements),
            (fields.risks_and_blockers, 'riskId', db.risks))


class SubmitFields(DraftFields):
    milestone_progress: Section = Field(...)
    effort_person_days: Effort = Field(...)
    requirement_progress: Section = Field(...)
    risks_and_blockers: Section = Field(...)


class DraftEdit(Strict):
    structuredFields: DraftFields
    narrative: Text = ''


class DraftCreate(DraftEdit):
    reportType: Kind
    period: StrictStr = Field(min_length=4, max_length=32)

    @model_validator(mode='after')
    def valid_period(self):
        try:
            period_bounds(self.reportType, self.period)
        except OverflowError as error:
            raise ValueError('Report period is out of range') from error
        return self


class Submit(DraftCreate):
    structuredFields: SubmitFields


class Existing(Strict):
    reason: Annotated[StrictStr, Field(max_length=500), AfterValidator(_plain)] = ''


class Confirm(Strict):
    pass


class Report(Strict):
    reportId: UUID
    projectId: UUID
    type: Kind
    status: Status
    period: str
    authorId: UUID
    revision: int
    submittedAt: datetime | None = None
    structuredFields: DraftFields | None = None
    # Browser fields are omitted from the closed MCP response.
    narrative: str | None = None
    periodStart: date | None = None
    periodEnd: date | None = None
    versionNo: int | None = None
    isCurrent: bool | None = None
    isCurrentConfirmed: bool | None = None
    submittedBy: UUID | None = None
    confirmedBy: UUID | None = None
    confirmedAt: datetime | None = None
    contentHash: str | None = None
    canEdit: bool | None = None
    canSubmit: bool | None = None
    canConfirm: bool | None = None
    weeklyBasis: WeeklyBasis | None = None


class Page(Strict):
    items: list[Report] = Field(max_length=100)
    page: int
    pageSize: int
    total: int
    asOf: datetime
    canCreateDraft: bool | None = None
    weeklyGenerationRevision: int | None = None


@dataclass(frozen=True)
class MilestoneClaim:
    milestone_id: UUID
    kind: ClaimKind
    evidence_refs: tuple[str, ...]
    item_index: int


@dataclass(frozen=True)
class ReportSubmission:
    report_id: UUID
    revision: int
    version_no: int
    period: str
    period_start: date
    period_end: date
    submitted_at: datetime
    submitted_by: UUID
    content_hash: str
    milestone_claims: tuple[MilestoneClaim, ...]
    confirmed_at: datetime | None
    confirmed_revision: int | None

    @property
    def source_ref(self) -> str:
        return f'report:{self.report_id}@{self.revision}'


def _digest(pid, author, kind, period, version, fields, narrative, basis=None):
    if basis is None:
        return fingerprint(['report-v1', str(pid), str(author), kind, period, version, fields, narrative])
    return fingerprint(['weekly-report-v1', str(pid), str(author), period, version, fields, narrative, basis])


def _content(row):
    try:
        fields = SubmitFields.model_validate(row.structured_fields)
        _plain(row.narrative or '')
        bounds = period_bounds(row.type, row.period)
    except (ValueError, OverflowError):
        return None
    if (bounds != (row.period_start, row.period_end) or row.content_hash != _digest(
            row.project_id, row.author_id, row.type, row.period, row.version_no, row.structured_fields,
            row.narrative or '', row.get('weekly_basis'))):
        return None
    return fields


def _submissions(c, project_id: UUID, as_of: datetime, *period_filters) -> list[ReportSubmission]:
    """Read verifiable receipts for an already scoped project; a claim is not proof."""
    if as_of.utcoffset() is None:
        raise ValueError('Invalid report cutoff')
    rows = c.execute(sa.select(reports).where(r.project_id == project_id, *period_filters,
        r.status.in_(('submitted', 'confirmed')),
        r.submitted_at <= as_of, r.submitted_by.is_not(None), r.content_hash.is_not(None))
        .order_by(r.period_start, r.submitted_at, r.version_no, r.id)).mappings()
    submissions = []
    for row in rows:
        fields = _content(row)
        if fields is None:
            continue
        revision = row.revision
        if row.status == 'confirmed':
            if row.confirmed_by is None or row.confirmed_at is None or row.confirmed_at < row.submitted_at or revision < 2:
                continue
            revision -= 1  # Publishing increments the revision; changing the published pointer does not.
        claims = []
        for index, item in enumerate(fields.milestone_progress):
            kind = item.get('claim')
            if kind == 'progress' or kind == 'completion':
                claims.append(MilestoneClaim(_uuid(item['milestoneId']), kind,
                                             tuple(f'evidence:{eid}' for eid in _evidence_ids(item)), index))
        published = (row.status == 'confirmed' and row.is_current_confirmed and row.confirmed_at <= as_of)
        submissions.append(ReportSubmission(row.id, revision, row.version_no, row.period, row.period_start,
            row.period_end, row.submitted_at, row.submitted_by, row.content_hash, tuple(claims),
            row.confirmed_at if published else None, row.revision if published else None))
    return submissions


def weekly_submissions(c, project_id: UUID, *, as_of: datetime, start: date, end: date) -> list[ReportSubmission]:
    if end < start:
        raise ValueError('Invalid report period range')
    return _submissions(c, project_id, as_of, r.type == 'weekly', r.period_start >= start, r.period_start < end)


def daily_submissions(c, project_id: UUID, *, as_of: datetime, day: date) -> list[ReportSubmission]:
    return _submissions(c, project_id, as_of, r.type == 'daily', r.period == day.isoformat())


def _is_pm(c, a, p) -> bool:
    return p.pm_id == a.person and active_member(c, p, a.person, 'pm')


def _author_role(c, a, p, pm) -> bool:
    return pm or active_member(c, p, a.person, 'member')


def can_submit_report(c, a, p) -> bool:
    """Business capability for token issuance; invocation also checks the explicit grant."""
    return p.stage != 'archived' and _author_role(c, a, p, _is_pm(c, a, p))


def _context(c, a, *, project_id=None, report_id=None, write=False):
    scope = [visible(a)]
    if a.kind == 'agent':
        scope.append(explicit_project_scope(a))
    stmt = sa.select(projects).where(*scope)
    if report_id is None:
        stmt = stmt.where(projects.c.id == project_id)
    else:
        stmt = stmt.join(reports, r.project_id == projects.c.id).where(r.id == report_id)
    p = c.execute(stmt.with_for_update(of=projects, read=not write)).mappings().one_or_none()
    if p is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    if a.kind == 'agent':
        g = explicit_grants.c
        if not c.scalar(sa.select(g.project_id).where(g.credential_id == a.credential, g.project_id == p.id,
                g.granted_by == a.account).with_for_update(read=True)):
            raise Failure(404, 'RESOURCE_NOT_FOUND')
    pm = a.kind == 'human' and _is_pm(c, a, p)
    row = None
    if report_id is not None:
        row = c.execute(sa.select(reports).where(r.project_id == p.id, r.id == report_id)).mappings().one_or_none()
        if row is None or (row.status == 'draft' and not (a.kind == 'human' and (pm or row.author_id == a.person))):
            raise Failure(404, 'RESOURCE_NOT_FOUND')
    return p, row, pm


def _ref(row):
    return f'report:{row.id}@{row.revision}'


def _view(row, a, p, pm, author_role, *, detail=False, agent_excerpt=False):
    try:
        fields = DraftFields.model_validate(row.structured_fields) if detail else None
    except ValueError:
        raise Failure(422, 'REPORT_CONTENT_INVALID', project_id=p.id) from None
    item = Report(reportId=row.id, projectId=row.project_id, type=row.type, status=row.status, period=row.period,
                  authorId=row.author_id, revision=row.revision, submittedAt=row.submitted_at,
                  structuredFields=fields)
    if a.kind == 'human':
        item.periodStart, item.periodEnd = row.period_start, row.period_end
        item.versionNo, item.isCurrent, item.isCurrentConfirmed = row.version_no, row.is_current, row.is_current_confirmed
        item.submittedBy, item.confirmedBy, item.confirmedAt = row.submitted_by, row.confirmed_by, row.confirmed_at
        item.contentHash = row.content_hash
        item.narrative = row.narrative if detail else None
        if detail and row.weekly_basis is not None:
            try:
                item.weeklyBasis = WeeklyBasis.model_validate(row.weekly_basis)
            except ValueError:
                raise Failure(422, 'REPORT_CONTENT_INVALID', project_id=p.id) from None
        mutable = p.stage != 'archived' and row.status == 'draft'
        item.canEdit = mutable and author_role and row.author_id == a.person
        item.canSubmit = mutable and (pm or item.canEdit)
        item.canConfirm = p.stage != 'archived' and pm and row.status == 'submitted' and row.content_hash is not None
    if agent_excerpt:
        item.narrative = row.narrative or ''
    return item


def _writable(p):
    if p.stage == 'archived':
        raise Failure(422, 'PROJECT_ARCHIVED', project_id=p.id)


def _check_refs(c, p, fields):
    evidence = db.evidence_files
    for section, id_key, table in _sections(fields):
        ids = {_uuid(item[id_key]) for item in section if id_key in item}
        if ids and set(c.scalars(sa.select(table.c.id).where(table.c.project_id == p.id, table.c.id.in_(ids)))) != ids:
            raise Failure(422, 'REPORT_SOURCE_SCOPE', project_id=p.id)
        pairs = {(eid, _uuid(item[id_key])) for item in section for eid in _evidence_ids(item)}
        if pairs:
            l = _links.c
            linked = set(c.execute(sa.select(l.evidence_id, l.resource_id)
                .select_from(_links.join(evidence, evidence.c.id == l.evidence_id))
                .where(evidence.c.project_id == p.id, l.resource_type == id_key.removesuffix('Id'),
                       l.evidence_id.in_({eid for eid, _ in pairs}))).tuples())
            if not pairs.issubset(linked):
                raise Failure(422, 'REPORT_SOURCE_SCOPE', project_id=p.id)


def _replay(c, a, p, op, key, signature):
    try:
        return replay(c, a, op, key, signature, Result[Report])
    except Failure as error:
        error.project_id = p.id
        raise


def _finish(c, a, p, pm, row, op, key, rid, signature, *, previous=None, reason=None):
    item = _view(row, a, p, pm, _author_role(c, a, p, pm) if a.kind == 'human' else False, detail=True)
    info = meta(op, [_ref(row)])
    info.asOf = row.updated_at
    result = Result[Report](data=item, meta=info)
    body = result.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, row.id, p.id, signature, body, fingerprint(body), previous)
    payload = {'projectId': str(p.id), 'reportId': str(row.id), 'revision': row.revision}
    if reason:
        payload['submissionReasonHash'] = fingerprint(reason)
    c.execute(db.outbox_events.insert().values(event_type=op, dedupe_key=rid, status='pending', payload=payload))
    return result


@router.get('/projects/{projectId}/reports', operation_id=LIST, response_model_exclude_none=True)
def listing(projectId: UUID, authorization: Auth, page: int = Query(1, ge=1),
            pageSize: int = Query(20, ge=1, le=100), reportType: Kind | None = None) -> Result[Page]:
    def read(c, a, _):
        p, _, pm = _context(c, a, project_id=projectId)
        filters = [r.project_id == p.id]
        if reportType is not None:
            filters.append(r.type == reportType)
        if not pm:
            filters.append(sa.or_(r.status != 'draft', r.author_id == a.person) if a.kind == 'human' else r.status != 'draft')
        total = c.execute(sa.select(sa.func.count()).select_from(reports).where(*filters)).scalar_one()
        offset = (page - 1) * pageSize
        rows = c.execute(sa.select(reports).where(*filters).order_by(r.period_start.desc(), r.type, r.version_no.desc(), r.id)
                         .offset(offset).limit(pageSize)).mappings().all() if offset < total else []
        info = meta(LIST, [_ref(row) for row in rows], project_id=p.id)
        author_role = a.kind == 'human' and _author_role(c, a, p, pm)
        can_create = p.stage != 'archived' and author_role if a.kind == 'human' else None
        return Result[Page](data=Page(items=[_view(row, a, p, pm, author_role) for row in rows], page=page, pageSize=pageSize,
            total=total, asOf=info.asOf, canCreateDraft=can_create,
            weeklyGenerationRevision=p.revision if pm and p.stage != 'archived' else None), meta=info)
    return run(authorization, LIST, read)


@router.get('/reports/{reportId}', operation_id=GET, response_model_exclude_none=True)
def get(reportId: UUID, authorization: Auth) -> Result[Report]:
    def read(c, a, _):
        p, row, pm = _context(c, a, report_id=reportId)
        author_role = a.kind == 'human' and _author_role(c, a, p, pm)
        # Only a scoped Process Manager can see the report text to quote it in a proposal.
        excerpt = a.kind == 'agent' and 'pmo_fact_propose' in a.actions and _is_pm(c, a, p) and _content(row) is not None
        return Result[Report](data=_view(row, a, p, pm, author_role, detail=True, agent_excerpt=excerpt),
                              meta=meta(GET, [_ref(row)], project_id=p.id))
    return run(authorization, GET, read)


def _insert(c, a, p, command, now, *, submitted=False, basis=None):
    start, end = period_bounds(command.reportType, command.period)
    if start > now.astimezone(TZ).date():
        raise Failure(422, 'REPORT_PERIOD_IN_FUTURE', project_id=p.id)
    _check_refs(c, p, command.structuredFields)
    series = (r.project_id == p.id, r.type == command.reportType, r.period == command.period)
    if basis is not None and c.scalar(sa.select(r.id).where(*series, r.author_id == a.person,
            r.status.in_(('draft', 'submitted')), r.weekly_basis['sourceHash'].astext == basis['sourceHash']).limit(1)):
        raise Failure(409, 'WEEKLY_DRAFT_ALREADY_CURRENT', project_id=p.id)
    version = (c.scalar(sa.select(sa.func.max(r.version_no)).where(*series)) or 0) + 1
    c.execute(reports.update().where(*series, r.is_current).values(is_current=False))
    fields = command.structuredFields.model_dump(mode='json', exclude_none=True)
    return c.execute(reports.insert().values(project_id=p.id, created_by=a.person, author_id=a.person,
        type=command.reportType, period=command.period, period_start=start, period_end=end, version_no=version,
        is_current=True, is_current_confirmed=False, status='submitted' if submitted else 'draft',
        structured_fields=fields, narrative=command.narrative, updated_at=now, weekly_basis=basis,
        content_hash=_digest(p.id, a.person, command.reportType, command.period, version, fields, command.narrative, basis),
        submitted_at=now if submitted else None, submitted_by=a.person if submitted else None)
        .returning(reports)).mappings().one()


def _create(auth, op, pid, command, key):
    signature = fingerprint([str(pid), command.model_dump(mode='json', exclude_none=True)])

    def write(c, a, rid):
        p, _, pm = _context(c, a, project_id=pid, write=True)
        if ((op == DRAFT and a.kind != 'human') or
                not _author_role(c, a, p, pm or (a.kind == 'agent' and _is_pm(c, a, p)))):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        prior = _replay(c, a, p, op, key, signature)
        if prior is not None:
            return prior
        _writable(p)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        row = _insert(c, a, p, command, now, submitted=op == SUBMIT)
        return _finish(c, a, p, pm, row, op, key, rid, signature)
    return run(auth, op, write)


@router.post('/projects/{projectId}/reports', operation_id=SUBMIT, response_model_exclude_none=True)
def submit(projectId: UUID, command: Submit, authorization: Auth, key: Key) -> Result[Report]:
    return _create(authorization, SUBMIT, projectId, command, key)


@router.post('/projects/{projectId}/reports/drafts', operation_id=DRAFT, response_model_exclude_none=True, openapi_extra=HUMAN)
def create_draft(projectId: UUID, command: DraftCreate, authorization: Auth, key: Key) -> Result[Report]:
    return _create(authorization, DRAFT, projectId, command, key)


def _mutate(auth, op, report_id, command, key, expected):
    revision = int(expected)
    signature = fingerprint([str(report_id), revision, command.model_dump(mode='json', exclude_none=True)])

    def write(c, a, rid):
        if a.kind != 'human':
            raise Failure(403, 'FORBIDDEN')
        p, row, pm = _context(c, a, report_id=report_id, write=True)
        owner = row.author_id == a.person and _author_role(c, a, p, pm)
        permitted = owner if op == EDIT else pm or (op == SUBMIT_EXISTING and owner)
        if not permitted:
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        prior = _replay(c, a, p, op, key, signature)
        if prior is not None:
            return prior
        _writable(p)
        if row.revision != revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if row.status != ('submitted' if op == CONFIRM else 'draft'):
            raise Failure(422, 'REPORT_NOT_SUBMITTED' if op == CONFIRM else 'REPORT_NOT_DRAFT', project_id=p.id)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        values = dict(revision=revision + 1, updated_at=now)
        if op == EDIT:
            _check_refs(c, p, command.structuredFields)
            fields = command.structuredFields.model_dump(mode='json', exclude_none=True)
            values.update(structured_fields=fields, narrative=command.narrative, content_hash=_digest(
                p.id, row.author_id, row.type, row.period, row.version_no, fields, command.narrative, row.weekly_basis))
        else:
            fields = _content(row)
            if fields is None or (op == CONFIRM and (row.submitted_at is None or row.submitted_by is None or row.submitted_at > now)):
                raise Failure(422, 'REPORT_SUBMISSION_INVALID' if op == CONFIRM else 'REPORT_INCOMPLETE', project_id=p.id)
            _check_refs(c, p, fields)
            if row.weekly_basis is not None:
                from .weekly_drafts import make_draft

                _, basis = make_draft(c, p, row.period, now)
                if basis.sourceHash != row.weekly_basis['sourceHash']:
                    raise Failure(409, 'WEEKLY_DRAFT_SOURCE_CHANGED', project_id=p.id)
            if op == SUBMIT_EXISTING:
                values.update(status='submitted', submitted_at=now, submitted_by=a.person)
            else:
                current = (r.project_id == p.id, r.type == row.type, r.period == row.period, r.is_current_confirmed)
                published_version = c.scalar(sa.select(r.version_no).where(*current))
                if published_version is not None and published_version >= row.version_no:
                    raise Failure(409, 'REPORT_VERSION_SUPERSEDED', project_id=p.id)
                c.execute(reports.update().where(*current).values(is_current_confirmed=False))
                values.update(status='confirmed', confirmed_by=a.person, confirmed_at=now, is_current_confirmed=True)
        updated = c.execute(reports.update().where(r.id == row.id, r.project_id == p.id, r.revision == revision)
                            .values(**values).returning(reports)).mappings().one()
        previous = fingerprint([row.content_hash, row.revision, row.status])
        return _finish(c, a, p, pm, updated, op, key, rid, signature, previous=previous,
                       reason=command.reason if op == SUBMIT_EXISTING else None)
    return run(auth, op, write)


@router.patch('/reports/{reportId}', operation_id=EDIT, response_model_exclude_none=True, openapi_extra=HUMAN)
def edit_draft(reportId: UUID, command: DraftEdit, authorization: Auth, key: Key, expected: Revision) -> Result[Report]:
    return _mutate(authorization, EDIT, reportId, command, key, expected)


@router.post('/reports/{reportId}/submit', operation_id=SUBMIT_EXISTING, response_model_exclude_none=True, openapi_extra=HUMAN)
def submit_existing(reportId: UUID, command: Existing, authorization: Auth, key: Key, expected: Revision) -> Result[Report]:
    return _mutate(authorization, SUBMIT_EXISTING, reportId, command, key, expected)


@router.post('/reports/{reportId}/confirm', operation_id=CONFIRM, response_model_exclude_none=True, openapi_extra=HUMAN)
def confirm(reportId: UUID, authorization: Auth, key: Key, expected: Revision,
            command: Confirm = Body(default_factory=Confirm)) -> Result[Report]:
    return _mutate(authorization, CONFIRM, reportId, command, key, expected)
