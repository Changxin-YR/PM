"""One read-only workspace. Its modes compose existing scoped services, not authorities."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import ValidationError

from . import fact_store, gates, health, model_gateway, report_analysis, reports, retrospectives, review_outcomes, reviews
from . import schema_v1 as db
from .access import Auth
from .contracts import Result, Strict
from .costs import cost_basis, summary_line
from .fact_sources import verified_source
from .health_schema import events
from .identity import Failure
from .projects import meta, run
from .review_scores import Assessment, rubric
from .scope_quality import metrics
from .weekly_basis import WeeklyHealth
from .weekly_drafts import make_draft

CHARTER, PROCESS, CLOSING, LEDGER = ('human_assistant_charter', 'human_assistant_process',
                                    'human_assistant_closing', 'human_assistant_ledger')
router = APIRouter(prefix='/api/v1/projects/{projectId}/assistant', tags=['Project assistant'])
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
Keyword = Annotated[retrospectives.QueryText | None, Query(max_length=160)]
Category = Annotated[retrospectives.QueryText | None, Query(max_length=80)]
Topic = Literal['health', 'cost', 'requirements', 'fact', 'lesson']
HITS = 6


class Cited(Strict):
    text: str
    sourceRefs: list[str]
    observedAt: datetime | None = None


class LessonRef(Cited):
    lessonId: UUID
    projectId: UUID
    category: str


class ScoreSummary(Assessment):
    decidedAt: datetime
    sourceRefs: list[str]


class CharterMode(Strict):
    completeness: gates.Check
    scoreSummary: ScoreSummary | None
    weaknesses: list[Cited]
    lessonRefs: list[LessonRef]
    reviewDraft: list[Cited]


class ProcessAlert(Cited):
    ruleId: str
    businessPeriod: str


class WeeklyDraft(Strict):
    period: str
    structuredFields: reports.DraftFields
    sourceHash: str
    verifiedPersonDays: str | None


class ProcessMode(Strict):
    reportId: UUID | None
    analysis: report_analysis.AnalysisRun | None
    modelEnabled: bool
    factCandidates: list[report_analysis.Suggestion]
    conflicts: list[Cited]
    health: WeeklyHealth
    alerts: list[ProcessAlert]
    weeklyDraft: WeeklyDraft


class ClosingMode(Strict):
    gateGaps: list[gates.Gap]
    gateEligible: bool | None
    retrospectiveKind: retrospectives.Kind | None
    retrospectiveQuestions: list[str]
    lessonDrafts: list[Cited]


class LedgerMode(Strict):
    answer: list[Cited]
    asOf: datetime
    sourceRefs: list[str]
    unknowns: list[str]


def _project_ref(project) -> str:
    return f'project:{project.id}@{project.revision}'


def _meta(op, p, at, refs):
    info = meta(op, list(dict.fromkeys([_project_ref(p), *refs]))[:100])
    info.asOf = at
    return info


def _read(auth, op, pid, read):
    def query(c, a, _):
        p = gates.lock_project(c, a, pid)
        if not p.materials_visible and not (op == CHARTER and p.stage == 'charter_review'):
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        return read(c, a, p, c.scalar(sa.select(sa.func.clock_timestamp())))
    return run(auth, op, query)


def _lesson_references(c, a, p, at, keyword, category, *, history_only=False):
    hits, _ = retrospectives.find_lessons(c, a, keyword=keyword, category=category, page=1, page_size=HITS,
                                          exclude_project=p.id if history_only else None, at=at)
    needle = keyword.strip().casefold() if keyword else ''
    items = []
    for hit in hits:
        text = next((field for field in (hit.what, hit.rootCause, hit.advice) if needle in field.casefold()), hit.what)
        items.append(LessonRef(lessonId=hit.lessonId, projectId=hit.projectId, category=hit.category,
                               text=text[:320], observedAt=hit.confirmedAt, sourceRefs=hit.sourceRefs))
    return items


def _published_assessment(c, p, at):
    d, v, s = db.decisions.c, reviews.reviews.c, review_outcomes.outcomes.c
    row = c.execute(sa.select(d.id, d.revision, d.decided_at, s.payload, v.id.label('review_id'),
        v.revision.label('review_revision')).select_from(db.decisions.join(reviews.reviews, d.review_id == v.id)
        .join(review_outcomes.outcomes, (s.decision_id == d.id) & (s.review_id == v.id)))
        .where(d.project_id == p.id, v.project_id == p.id, v.type == 'charter', v.status == 'decided',
               d.decided_at <= at, s.payload['assessment'].astext.is_not(None))
        .order_by(d.decided_at.desc(), d.id).limit(1)).mappings().one_or_none()
    if row:
        score = Assessment.model_validate(row.payload['assessment'])
        return ScoreSummary(**score.model_dump(), decidedAt=row.decided_at, sourceRefs=[
            f'decision:{row.id}@{row.revision}', f'review:{row.review_id}@{row.review_revision}'])
    return None


@router.get('/charter', operation_id=CHARTER, openapi_extra=HUMAN)
def charter(projectId: UUID, authorization: Auth, keyword: Keyword = None, category: Category = None) -> Result[CharterMode]:
    def read(c, a, p, at):
        check, materials = gates.evaluate(c, a, p, 'charter', pending=p.stage == 'charter_review')
        score = _published_assessment(c, p, at) if p.materials_visible else None
        labels = {item.key: item.label for item in rubric('charter')}
        weak = [Cited(text=f'{labels[key]}：维度均分 {score.means[key]}，复核依据并补强。', sourceRefs=score.sourceRefs)
                for key in score.weakDimensions] if score else []
        draft = [Cited(text=gap.message, sourceRefs=gap.sourceRefs or [_project_ref(p)]) for gap in check.missing]
        if check.eligible:
            draft.append(Cited(text='材料预检通过；独立评分、必要确认与人工裁决仍需完成。',
                              sourceRefs=reviews.refs(p, materials)[:100]))
        lessons = _lesson_references(c, a, p, at, keyword, category, history_only=True) if p.materials_visible else []
        refs = [*(score.sourceRefs if score else []), *(ref for item in lessons for ref in item.sourceRefs), *materials.refs]
        return Result[CharterMode](data=CharterMode(completeness=check, scoreSummary=score, weaknesses=weak,
            lessonRefs=lessons, reviewDraft=draft), meta=_meta(CHARTER, p, at, refs))
    return _read(authorization, CHARTER, projectId, read)


def _process_sources(c, p, at):
    r, z = reports.r, report_analysis.r
    report = c.execute(sa.select(reports.reports).where(r.project_id == p.id, r.is_current,
        r.status.in_(('submitted', 'confirmed')), r.submitted_at <= at)
        .order_by(r.submitted_at.desc(), r.id).limit(1)).mappings().one_or_none()
    analysis = None
    refs = []
    if report is not None:
        refs.append(reports._ref(report))
        row = c.execute(sa.select(report_analysis.runs).where(*report_analysis.scoped(report),
            z.report_revision == report.revision, z.report_hash == report.content_hash, z.requested_at <= at)
            .order_by(z.requested_at.desc(), z.id).limit(1)).mappings().one_or_none()
        if row:
            analysis = report_analysis.view(row)
            refs.append(f'report-analysis:{row.id}')

    k, f = fact_store.k, fact_store.f
    cases = c.execute(sa.select(k.id, k.reason, k.source_ref, k.created_at, f.revision, f.id.label('fact_id'))
        .select_from(fact_store.cases.join(fact_store.facts, k.fact_id == f.id))
        .where(f.project_id == p.id, k.status == 'open', k.created_at <= at)
        .order_by(k.created_at.desc(), k.id).limit(20)).mappings()
    conflicts = [Cited(text=row.reason, observedAt=row.created_at, sourceRefs=[f'fact-clarification:{row.id}',
        f'fact:{row.fact_id}@{row.revision}', row.source_ref]) for row in cases]
    e = events.c
    rows = c.execute(sa.select(events).where(e.project_id == p.id, e.status == 'open', e.updated_at <= at)
        .order_by(e.updated_at.desc(), e.id).limit(20)).mappings()
    alerts = [ProcessAlert(ruleId=row.rule_id, text=(row.input_snapshot or {}).get('reason') or row.rule_id,
        businessPeriod=row.business_period, observedAt=row.updated_at,
        sourceRefs=[f'rule-event:{row.id}', *row.source_refs[:20]]) for row in rows]
    refs.extend(ref for item in [*conflicts, *alerts] for ref in item.sourceRefs)
    return report, analysis, conflicts, alerts, refs


@router.get('/process', operation_id=PROCESS, openapi_extra={**HUMAN, 'x-requiredRole': 'pm'})
def process(projectId: UUID, authorization: Auth) -> Result[ProcessMode]:
    def read(c, a, p, at):
        if not reports._is_pm(c, a, p):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        year, week, _ = at.astimezone(reports.TZ).isocalendar()
        period = f'{year:04d}-W{week:02d}'
        draft, basis = make_draft(c, p, period, at)
        report, analysis, conflicts, alerts, refs = _process_sources(c, p, at)
        preview = WeeklyDraft(period=period, structuredFields=draft.structuredFields, sourceHash=basis.sourceHash,
            verifiedPersonDays=basis.verifiedPeriodPersonDays if basis.verifiedPeriodPostingCount else None)
        return Result[ProcessMode](data=ProcessMode(reportId=report.id if report else None, analysis=analysis,
            modelEnabled=model_gateway.enabled(), factCandidates=analysis.candidates if analysis and analysis.status == 'ready' else [],
            conflicts=conflicts, health=basis.health, alerts=alerts, weeklyDraft=preview),
            meta=_meta(PROCESS, p, at, [*refs, *basis.sourceRefs]))
    return _read(authorization, PROCESS, projectId, read)


@router.get('/closing', operation_id=CLOSING, openapi_extra=HUMAN)
def closing(projectId: UUID, authorization: Auth) -> Result[ClosingMode]:
    def read(c, a, p, at):
        check, materials = gates.evaluate(c, a, p, 'closing', pending=p.stage == 'closing_review')
        origin = retrospectives._origin(c, p)
        kind = retrospectives._kind(origin)
        if kind is None and p.stage in ('closed', 'terminated'):
            kind = 'normal' if p.stage == 'closed' else 'terminated'
        retro = retrospectives._retrospective(c, p.id) if origin else None
        drafts = []
        if retro is not None:
            try:
                answers = retrospectives.Save(answers=retro.answers).answers
            except ValidationError as exc:
                raise Failure(422, 'RETROSPECTIVE_ANSWERS_REQUIRED', project_id=p.id) from exc
            drafts.append(Cited(text=answers[-1][:600], sourceRefs=[retrospectives._retro_ref(retro)],
                                observedAt=retro.completed_at))
        applicable = p.stage in ('delivering', 'closing_review')
        refs = [*materials.refs, *(ref for draft in drafts for ref in draft.sourceRefs)]
        if origin:
            refs.append(retrospectives._entry_ref(origin))
        return Result[ClosingMode](data=ClosingMode(gateGaps=check.missing if applicable else [],
            gateEligible=check.eligible if applicable else None, retrospectiveKind=kind,
            retrospectiveQuestions=list(retrospectives.QUESTIONS), lessonDrafts=drafts), meta=_meta(CLOSING, p, at, refs))
    return _read(authorization, CLOSING, projectId, read)


def _ledger_fact(c, p, at, keyword):
    if not keyword or not keyword.strip():
        return []
    f, r = fact_store.f, reports.r
    rows = c.execute(fact_store.query().join(reports.reports, (r.id == f.report_id) & (r.project_id == f.project_id))
        .where(f.project_id == p.id, f.as_of <= at, r.submitted_at <= at, f.validation_status == 'verified',
            f.trust.in_(('high', 'medium')), sa.or_(f.subject.icontains(keyword.strip(), autoescape=True),
                f.predicate.icontains(keyword.strip(), autoescape=True), f.quote.icontains(keyword.strip(), autoescape=True)))
        .order_by(f.as_of.desc(), f.id).limit(30)).mappings()
    items = []
    groups = set()
    for row in rows:
        if row.group_key in groups:
            continue
        report = c.execute(sa.select(reports.reports).where(r.id == row.report_id, r.project_id == p.id)).mappings().one()
        if verified_source(c, report, row):
            refs = [fact_store.fact_ref(row), row.source_ref, *([row.evidence_ref] if row.evidence_ref else [])]
            items.append(Cited(text=row.quote, observedAt=row.as_of, sourceRefs=list(dict.fromkeys(refs))))
            groups.add(row.group_key)
            if len(items) == HITS:
                break
    return items


def _ledger_health(c, a, p, at):
    current = health.current(c, a, p, at, human=False)
    if current.dataTrust != 'trusted':
        trust = '不可信' if current.dataTrust == 'untrusted' else '不可用'
        return [], [f'当前三线数据{trust}；不引用旧快照。'], current.asOf
    fields = [('时间线', current.timeLine), ('金额偏差率', current.moneyVarianceRate),
              ('人天偏差率', current.effortVarianceRate), ('需求满足率', current.requirementSatisfactionRate)]
    items, missing = [], []
    for label, value in fields:
        if value is None or value == 'UNAVAILABLE':
            missing.append(f'{label}不可用。')
        else:
            items.append(Cited(text=f'{label}：{value}', observedAt=current.asOf, sourceRefs=current.sourceRefs))
    return items, missing, current.asOf


@router.get('/ledger', operation_id=LEDGER, openapi_extra=HUMAN)
def ledger(projectId: UUID, authorization: Auth, topic: Topic = 'health', keyword: Keyword = None,
           category: Category = None) -> Result[LedgerMode]:
    def read(c, a, p, at):
        answer, unknowns = [], []
        if topic == 'fact':
            answer = _ledger_fact(c, p, at, keyword)
        elif topic == 'lesson':
            answer = [Cited(text=f'{item.category}：{item.text}', observedAt=item.observedAt, sourceRefs=item.sourceRefs)
                      for item in _lesson_references(c, a, p, at, keyword, category)]
        elif topic == 'health':
            answer, unknowns, at = _ledger_health(c, a, p, at)
        elif topic == 'requirements':
            quality, refs = metrics(c, p.id)
            if quality.satisfaction is not None:
                answer.append(Cited(text=f'加权满足率：{quality.satisfaction}；P0：{quality.confirmedP0}/{quality.activeP0}',
                                    sourceRefs=refs[:100]))
            else:
                unknowns.append('需求满足率不可用：' + '、'.join(quality.gaps))
        else:
            basis = cost_basis(c, p.id, at)
            for unit, kind in [('金额（CNY）', 'money'), ('人天', 'effort')]:
                refs = basis.refs(kind)[:100]
                if refs:
                    result = summary_line(c, basis, kind, at)
                    answer.append(Cited(text=f'{unit}：已核验净额 {result.actual}；剩余预测 {result.forecastRemaining}；'
                        f'偏差率 {result.varianceRate or "不可用"}', sourceRefs=refs))
                else:
                    unknowns.append(f'{unit}无可核验记录。')
        if not answer and not unknowns:
            unknowns.append('没有匹配的可核验来源，无法回答。')
        info = _meta(LEDGER, p, at, [ref for item in answer for ref in item.sourceRefs])
        return Result[LedgerMode](data=LedgerMode(answer=answer, asOf=at, sourceRefs=info.sourceRefs, unknowns=unknowns), meta=info)
    return _read(authorization, LEDGER, projectId, read)
