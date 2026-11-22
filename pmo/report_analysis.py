"""Human-requested analysis receipts. Suggestions never enter the Fact store here."""
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import Field, StrictBool
from sqlalchemy.dialects.postgresql import JSONB

from . import model_gateway, schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import RULE_VERSION, Result, Strict
from .fact_sources import Candidate, Quote, submitted
from .identity import Failure
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .reports import HUMAN, _context, _ref, _writable
from .requirement_baselines import active_member

ANALYZE = 'human_report_analyze'
LIST = 'human_report_analysis_list'
router = APIRouter(prefix='/api/v1', tags=['Report analysis'])
runs = sa.table('report_analysis_runs', sa.column('id', sa.Uuid), sa.column('report_id', sa.Uuid),
    sa.column('project_id', sa.Uuid), sa.column('requested_by', sa.Uuid), sa.column('requested_account_id', sa.Uuid),
    sa.column('requested_session_id', sa.Uuid), sa.column('request_id', sa.Uuid),
    sa.column('requested_at', sa.DateTime(timezone=True)), sa.column('report_revision', sa.BigInteger),
    sa.column('report_hash'), sa.column('status'), sa.column('attempts', sa.SmallInteger),
    sa.column('lease_id', sa.Uuid), sa.column('leased_until', sa.DateTime(timezone=True)),
    sa.column('completed_at', sa.DateTime(timezone=True)), sa.column('error_code'),
    sa.column('candidates', JSONB), sa.column('fallback_task_id', sa.Uuid))
r = runs.c
ACTIVE = ('queued', 'running')
TASK_SOURCE = 'report_analysis'
OPEN_TASK = ('open', 'todo', 'in_progress', 'blocked')


class Analyze(Strict):
    modelMode: Literal['process_manager'] = 'process_manager'
    forceNewRun: StrictBool = False


class Suggestion(Candidate):
    typedValue: Quote
    quotePath: str
    quoteOffset: int = Field(ge=0)


class AnalysisRun(Strict):
    runId: UUID
    reportId: UUID
    reportRevision: int
    contentHash: str
    requestedAt: datetime
    requestedBy: UUID
    completedAt: datetime | None = None
    status: Literal['queued', 'running', 'ready', 'manual', 'stale']
    attempts: int = Field(ge=0, le=2)
    errorCode: str | None = None
    fallbackTaskId: UUID | None = None
    candidates: list[Suggestion] = Field(max_length=20)


class AnalysisPage(Strict):
    items: list[AnalysisRun] = Field(max_length=10)
    page: int
    pageSize: int
    total: int
    canAnalyze: bool
    modelEnabled: bool
    reportRevision: int
    activeRunId: UUID | None = None


def permitted(c, p, report, person) -> bool:
    return ((p.pm_id == person and active_member(c, p, person, 'pm'))
            or (report.author_id == person and active_member(c, p, person, 'member')))


def view(row) -> AnalysisRun:
    return AnalysisRun(runId=row.id, reportId=row.report_id, reportRevision=row.report_revision,
        contentHash=row.report_hash, requestedAt=row.requested_at, requestedBy=row.requested_by,
        completedAt=row.completed_at, status=row.status, attempts=row.attempts, errorCode=row.error_code,
        fallbackTaskId=row.fallback_task_id, candidates=row.candidates)


def scoped(report):
    return (r.report_id == report.id, r.project_id == report.project_id)


def active(c, report):
    return c.execute(sa.select(runs).where(*scoped(report), r.status.in_(ACTIVE))).mappings().one_or_none()


def _context_for_analysis(c, a, report_id, *, write):
    p, report, _ = _context(c, a, report_id=report_id, write=write)
    if a.kind != 'human' or not permitted(c, p, report, a.person):
        raise Failure(403, 'FORBIDDEN', project_id=p.id)
    return p, report


def manual_task(c, p, receipt, at):
    t = db.tasks
    filters = (t.c.project_id == p.id, t.c.source_type == TASK_SOURCE, t.c.source_id == receipt.report_id,
               t.c.status.in_(OPEN_TASK))
    existing = c.scalar(sa.select(t.c.id).where(*filters))
    if existing:
        return existing
    assignee = p.pm_id if p.pm_id and active_member(c, p, p.pm_id, 'pm') else receipt.requested_by
    return c.scalar(t.insert().values(project_id=p.id, source_type=TASK_SOURCE, source_id=receipt.report_id,
        created_by=receipt.requested_by, assignee_id=assignee, due_at=at + timedelta(days=7), status='todo',
        title='Review report manually').returning(t.c.id))


def _event(c, receipt, event, key):
    c.execute(db.outbox_events.insert().values(dedupe_key=key, event_type=event, status='pending',
        payload={'projectId': str(receipt.project_id), 'reportId': str(receipt.report_id),
                 'analysisRunId': str(receipt.id), 'status': receipt.status}))


def finalize(c, project, row, *, status, code=None, candidates=()):
    at = c.scalar(sa.select(sa.func.clock_timestamp()))
    task_id = manual_task(c, project, row, at) if status == 'manual' else None
    updated = c.execute(runs.update().where(r.id == row.id).values(status=status, completed_at=at,
        lease_id=None, leased_until=None, error_code=code, fallback_task_id=task_id,
        candidates=[item.model_dump(mode='json') for item in candidates]).returning(runs)).mappings().one()
    event = f'report.analysis.{status}'
    # The requester is audit provenance, not an assertion that a person verified the output.
    c.execute(db.audit_logs.insert().values(action=event, actor_type='human', actor_id=row.requested_account_id,
        org_id=project.org_id, project_id=project.id, request_id=str(row.request_id), operation_id=row.id,
        result='denied' if status == 'stale' else 'success', error_code=code, rule_version=RULE_VERSION,
        before_hash=row.report_hash, after_hash=fingerprint(view(updated).model_dump(mode='json'))))
    _event(c, updated, event, f'analysis:{row.id}:terminal')
    return updated


@router.post('/reports/{reportId}/analyze', operation_id=ANALYZE, response_model_exclude_none=True,
             openapi_extra={**HUMAN, 'x-requiredRole': 'report_author_or_pm'})
def analyze(reportId: UUID, command: Analyze, authorization: Auth, key: Key, expected: Revision) -> Result[AnalysisRun]:
    rev = int(expected)
    signature = fingerprint([str(reportId), rev, command.model_dump(mode='json')])

    def write(c, a, rid):
        p, report = _context_for_analysis(c, a, reportId, write=True)
        try:
            if prior := replay(c, a, ANALYZE, key, signature, Result[AnalysisRun]):
                return prior
        except Failure as exc:
            exc.project_id = p.id
            raise
        _writable(p)
        if report.revision != rev:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if not submitted(report):
            raise Failure(422, 'FACT_REPORT_NOT_SUBMITTED', project_id=p.id)

        row = active(c, report)
        if row is not None and (row.report_revision != rev or row.report_hash != report.content_hash):
            raise Failure(409, 'REPORT_ANALYSIS_IN_PROGRESS', project_id=p.id)
        if row is None and not command.forceNewRun:
            row = c.execute(sa.select(runs).where(*scoped(report), r.report_revision == rev,
                r.report_hash == report.content_hash, r.status.in_(('ready', 'manual')))
                .order_by(r.requested_at.desc(), r.id.desc()).limit(1)).mappings().one_or_none()
        if row is None:
            row = c.execute(runs.insert().values(report_id=report.id, project_id=p.id, requested_by=a.person,
                requested_account_id=a.account, requested_session_id=a.session_id, request_id=UUID(rid),
                report_revision=rev, report_hash=report.content_hash, status='queued').returning(runs)).mappings().one()
            if not model_gateway.enabled():
                row = finalize(c, p, row, status='manual', code='MODEL_DISABLED')
            elif not model_gateway.in_project_scope('REPORT', p.id):
                row = finalize(c, p, row, status='manual', code='MODEL_SCOPE_BLOCKED')
            else:
                _event(c, row, 'report.analysis.queued', rid)
        result = Result[AnalysisRun](data=view(row), meta=meta(ANALYZE, [_ref(report), f'analysis:{row.id}']))
        body = result.model_dump(mode='json', exclude_none=True)
        record_receipt(c, a, key, ANALYZE, rid, row.id, p.id, signature, body, fingerprint(body))
        return result

    return run(authorization, ANALYZE, write)


@router.get('/reports/{reportId}/analysis-runs', operation_id=LIST, response_model_exclude_none=True, openapi_extra=HUMAN)
def listing(reportId: UUID, authorization: Auth, page: int = Query(1, ge=1, le=10000),
            pageSize: int = Query(3, ge=1, le=10)) -> Result[AnalysisPage]:
    def read(c, a, _):
        p, report = _context_for_analysis(c, a, reportId, write=False)
        total = c.scalar(sa.select(sa.func.count()).select_from(runs).where(*scoped(report)))
        rows = c.execute(sa.select(runs).where(*scoped(report)).order_by(r.requested_at.desc(), r.id.desc())
            .offset((page-1)*pageSize).limit(pageSize)).mappings()
        items = [view(row) for row in rows]
        busy = active(c, report)
        payload = AnalysisPage(items=items, total=total, page=page, pageSize=pageSize, reportRevision=report.revision,
            activeRunId=busy.id if busy else None, canAnalyze=p.stage != 'archived' and submitted(report),
            modelEnabled=model_gateway.enabled() and model_gateway.in_project_scope('REPORT', p.id))
        return Result[AnalysisPage](data=payload, meta=meta(LIST, [_ref(report), *(f'analysis:{item.runId}' for item in items)]))

    return run(authorization, LIST, read)
