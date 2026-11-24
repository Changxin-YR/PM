"""One read-only workspace. Its modes compose existing scoped services, not authorities."""
from datetime import datetime
import re
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import Field, ValidationError

from . import assistant_model as model, assistant_preview as preview
from . import fact_store, gates, health, model_gateway, report_analysis, reports, retrospectives, review_outcomes, reviews
from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .costs import cost_basis, summary_line
from .dto import UNAVAILABLE
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
PreviewTopic = Literal['health', 'cost', 'requirements', 'fact', 'lesson']
Topic = Literal[PreviewTopic, 'forecast']
HITS = 6
LESSON_TERMS = re.compile(r'[A-Za-z][A-Za-z0-9_-]{3,}|[\u3400-\u9fff]{2,}')
IGNORED_LESSON_TERMS = frozenset({'project', 'system', 'with', 'from', 'that', 'this', 'only', '项目', '系统'})


Cited = model.Excerpt


class LessonRef(Cited):
    lessonId: UUID
    projectId: UUID
    category: str
    matchedTerm: str | None = None


class ScoreSummary(Assessment):
    decidedAt: datetime
    sourceRefs: list[str]


class PendingScore(Strict):
    total: str | None
    means: dict[str, str]
    observedAt: datetime
    sourceRefs: list[str]


class CharterMode(Strict):
    completeness: gates.Check
    scoreSummary: ScoreSummary | None
    pendingScore: PendingScore | None
    pendingWeaknesses: list[Cited]
    charterFacts: list[Cited]
    weaknesses: list[Cited]
    lessonRefs: list[LessonRef]
    reviewDraft: list[Cited]
    previewEnabled: bool


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
    retrospectiveQuestions: retrospectives.Questions
    lessonDrafts: list[Cited]
    canStartLessonDraft: bool
    previewEnabled: bool


class LedgerMode(Strict):
    answer: list[Cited]
    asOf: datetime
    sourceRefs: list[str]
    unknowns: list[str]
    previewEnabled: bool


def _previews_enabled(project_id: UUID) -> bool:
    return model_gateway.previews_enabled() and model_gateway.in_project_scope('ASSISTANT', project_id)


def _project_ref(project) -> str:
    return f'project:{project.id}@{project.revision}'


def _meta(op, p, at, refs):
    info = meta(op, list(dict.fromkeys([_project_ref(p), *refs]))[:100])
    info.asOf = at
    return info


def _project(c, a, pid, mode):
    p = gates.lock_project(c, a, pid)
    if not p.materials_visible and not (mode == 'charter' and p.stage == 'charter_review'):
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return p


def _read(auth, op, pid, read):
    def query(c, a, _):
        p = _project(c, a, pid, 'charter' if op == CHARTER else None)
        return read(c, a, p, c.scalar(sa.select(sa.func.clock_timestamp())))
    return run(auth, op, query)


def _lesson_references(c, a, p, at, keyword, category, *, history_only=False, for_preview=False):
    charter_ref = None
    terms = [keyword]
    if history_only and not (keyword or category):
        ch = db.charters.c
        charter = c.execute(sa.select(ch.id, ch.revision, ch.target_client, ch.value_prop, ch.attractiveness)
                            .where(ch.project_id == p.id)).mappings().one_or_none()
        if charter is None:
            return []
        charter_ref = f'charter:{charter.id}@{charter.revision}'
        terms = _charter_terms(charter.target_client, charter.value_prop, charter.attractiveness)
    items = []
    seen = set()
    for term in terms:
        hits, _ = retrospectives.find_lessons(c, a, keyword=term, category=category, page=1, page_size=HITS,
                                              exclude_project=p.id if history_only else None, at=at)
        needle = term.strip().casefold() if term else ''
        for hit in hits:
            if hit.lessonId in seen or (for_preview and not model_gateway.in_project_scope('ASSISTANT', hit.projectId)):
                continue
            text = next((field for field in (hit.what, hit.rootCause, hit.advice) if needle in field.casefold()), hit.what)
            items.append(LessonRef(lessonId=hit.lessonId, projectId=hit.projectId, category=hit.category,
                text=text[:320], observedAt=hit.confirmedAt,
                sourceRefs=[*hit.sourceRefs, *([charter_ref] if charter_ref else [])], matchedTerm=term if charter_ref else None))
            seen.add(hit.lessonId)
            if len(items) == HITS:
                return items
    return items


def _charter_terms(*fields):
    terms = []
    for field in fields:
        for word in LESSON_TERMS.findall((field or '')[:160].casefold()):
            # Longer unspaced CJK text is searched by overlapping short phrases.
            if len(word) > 6 and '\u3400' <= word[0] <= '\u9fff':
                terms.extend(word[start:start + 3] for start in range(len(word) - 2))
            else:
                terms.append(word)
    return list(dict.fromkeys(term for term in terms if term not in IGNORED_LESSON_TERMS))[:HITS]


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


def _pending_assessment(c, a, p, at):
    if p.stage != 'charter_review':
        return None, []
    v = reviews.reviews.c
    review = c.execute(sa.select(reviews.reviews).where(v.project_id == p.id, v.type == 'charter',
        v.status == 'pending').order_by(v.created_at.desc(), v.id).limit(1)).mappings().one_or_none()
    if review is None:
        return None, []
    frozen = review_outcomes.one(c, reviews.snapshots, reviews.snapshots.c.review_id == review.id)
    bench = review_outcomes.workbench(c, a, p, review, frozen)
    assessment = bench.assessment
    if assessment is None or bench.stale or bench.scoreEvidenceStale:
        return None, []
    source = [f'review:{review.id}@{review.revision}']
    labels = {item.key: item.label for item in rubric('charter')}
    weak = [Cited(text=f'{labels[dimension]}：维度均分 {assessment.means[dimension]}，复核依据并补强。',
                  sourceRefs=source, observedAt=at) for dimension in assessment.weakDimensions]
    return PendingScore(total=assessment.total, means=assessment.means, sourceRefs=source, observedAt=at), weak


def _charter_facts(c, p):
    if not p.materials_visible:
        return []
    row = c.execute(sa.select(db.charters).where(db.charters.c.project_id == p.id)).mappings().one_or_none()
    if row is None:
        return []
    fields = [('价值主张', row.value_prop), ('目标客户', row.target_client), ('吸引力', row.attractiveness),
              ('预期收益', row.expected_benefit), ('成功标准', row.success_criteria), ('估算金额（元）', row.rough_cost),
              ('估算人天', row.rough_effort), ('估算工期（天）', row.rough_duration_days)]
    reference = [f'charter:{row.id}@{row.revision}']
    return [Cited(text=f'{label}：{value}', sourceRefs=reference) for label, value in fields if value not in (None, '')]


@router.get('/charter', operation_id=CHARTER, openapi_extra=HUMAN)
def charter(projectId: UUID, authorization: Auth, keyword: Keyword = None, category: Category = None) -> Result[CharterMode]:
    def read(c, a, p, at):
        check, materials = gates.evaluate(c, a, p, 'charter', pending=p.stage == 'charter_review')
        score = _published_assessment(c, p, at) if p.materials_visible else None
        pending, pending_weak = _pending_assessment(c, a, p, at)
        facts = _charter_facts(c, p)
        labels = {item.key: item.label for item in rubric('charter')}
        weak = [Cited(text=f'{labels[key]}：维度均分 {score.means[key]}，复核依据并补强。', sourceRefs=score.sourceRefs)
                for key in score.weakDimensions] if score else []
        draft = [Cited(text=gap.message, sourceRefs=gap.sourceRefs or [_project_ref(p)]) for gap in check.missing]
        if check.eligible:
            draft.append(Cited(text='材料预检通过；独立评分、必要确认与人工裁决仍需完成。',
                              sourceRefs=reviews.refs(p, materials)[:100]))
        lessons = _lesson_references(c, a, p, at, keyword, category, history_only=True) if p.materials_visible else []
        refs = [*(score.sourceRefs if score else []), *(pending.sourceRefs if pending else []),
                *(ref for item in [*facts, *lessons] for ref in item.sourceRefs), *materials.refs]
        return Result[CharterMode](data=CharterMode(completeness=check, scoreSummary=score, weaknesses=weak,
            pendingScore=pending, pendingWeaknesses=pending_weak, charterFacts=facts, lessonRefs=lessons, reviewDraft=draft,
            previewEnabled=_previews_enabled(p.id)), meta=_meta(CHARTER, p, at, refs))
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
            modelEnabled=model_gateway.enabled() and model_gateway.in_project_scope('REPORT', p.id),
            factCandidates=analysis.candidates if analysis and analysis.status == 'ready' else [],
            conflicts=conflicts, health=basis.health, alerts=alerts, weeklyDraft=preview),
            meta=_meta(PROCESS, p, at, [*refs, *basis.sourceRefs]))
    return _read(authorization, PROCESS, projectId, read)


@router.get('/closing', operation_id=CLOSING, openapi_extra=HUMAN)
def closing(projectId: UUID, authorization: Auth) -> Result[ClosingMode]:
    def read(c, a, p, at):
        check, materials, origin, answers = _closing_sources(c, a, p)
        kind = retrospectives._kind(origin)
        if kind is None and p.stage in ('closed', 'terminated'):
            kind = 'normal' if p.stage == 'closed' else 'terminated'
        drafts = [answers[-1].model_copy(update={'text': answers[-1].text[:600]})] if answers else []
        applicable = p.stage in ('delivering', 'closing_review')
        refs = [*materials.refs, *(ref for draft in drafts for ref in draft.sourceRefs)]
        if origin:
            refs.append(retrospectives._entry_ref(origin))
        return Result[ClosingMode](data=ClosingMode(gateGaps=check.missing if applicable else [],
            gateEligible=check.eligible if applicable else None, retrospectiveKind=kind,
            retrospectiveQuestions=list(retrospectives.QUESTIONS), lessonDrafts=drafts,
            canStartLessonDraft=bool(answers and p.stage == 'retrospective' and retrospectives._pm(c, a, p)),
            previewEnabled=_previews_enabled(p.id)), meta=_meta(CLOSING, p, at, refs))
    return _read(authorization, CLOSING, projectId, read)


def _closing_sources(c, a, p):
    check, materials = gates.evaluate(c, a, p, 'closing', pending=p.stage == 'closing_review')
    origin = retrospectives._origin(c, p)
    retro = retrospectives._retrospective(c, p.id) if origin else None
    if retro is None:
        return check, materials, origin, []
    try:
        answers = retrospectives.Save(answers=retro.answers).answers
    except ValidationError as exc:
        raise Failure(422, 'RETROSPECTIVE_ANSWERS_REQUIRED', project_id=p.id) from exc
    refs = [retrospectives._retro_ref(retro)]
    return check, materials, origin, [Cited(text=answer, sourceRefs=refs, observedAt=retro.completed_at) for answer in answers]


def _ledger_fact(c, a, p, at, keyword):
    if not keyword or not keyword.strip():
        return []
    f = fact_store.f
    readable = reports.readable_reports(a, p.id)
    authorized = readable.subquery()
    rows = c.execute(fact_store.query().join(authorized, authorized.c.id == f.report_id)
        .where(f.project_id == p.id, f.as_of <= at, authorized.c.submitted_at <= at, f.validation_status == 'verified',
            f.trust.in_(('high', 'medium')), sa.or_(f.subject.icontains(keyword.strip(), autoescape=True),
                f.predicate.icontains(keyword.strip(), autoescape=True), f.quote.icontains(keyword.strip(), autoescape=True)))
        .order_by(f.as_of.desc(), f.id).limit(30)).mappings()
    items = []
    groups = set()
    for row in rows:
        if row.group_key in groups:
            continue
        report = c.execute(readable.where(reports.r.id == row.report_id)).mappings().one_or_none()
        if report is not None and verified_source(c, report, row):
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
        if value is None or value == UNAVAILABLE:
            missing.append(f'{label}不可用。')
        else:
            items.append(Cited(text=f'{label}：{value}', observedAt=current.asOf, sourceRefs=current.sourceRefs))
    return items, missing, current.asOf


def _ledger_forecasts(c, p, at):
    e = events.c
    matches = c.execute(sa.select(events).where(e.project_id == p.id, e.rule_id == 'LR-03', e.status == 'open',
        e.updated_at <= at).order_by(e.updated_at.desc(), e.id).limit(HITS + 1)).mappings().all()
    results = []
    for row in matches[:HITS]:
        history = row.input_snapshot or {}
        subject = history.get('milestoneName') or '里程碑'
        plan, forecast = history.get('plannedDate'), history.get('forecastDate')
        detail = f'{subject}：计划 {plan}，预测 {forecast}。' if plan and forecast else subject
        results.append(Cited(text=f'{detail}　{history.get("reason") or "预测延期的正式计划变更待处理。"}',
            observedAt=row.updated_at, sourceRefs=[f'rule-event:{row.id}', *row.source_refs[:20]]))
    return results, ['仅显示最近六条；其余记录可在预警中心查询。'] if len(matches) > HITS else []


@router.get('/ledger', operation_id=LEDGER, openapi_extra=HUMAN)
def ledger(projectId: UUID, authorization: Auth, topic: Topic = 'health', keyword: Keyword = None,
           category: Category = None) -> Result[LedgerMode]:
    return _read(authorization, LEDGER, projectId, lambda c, a, p, at: _ledger(c, a, p, at, topic, keyword, category))


def _ledger(c, a, p, at, topic, keyword, category, *, for_preview=False):
    answer, unknowns = [], []
    if topic == 'fact':
        answer = _ledger_fact(c, a, p, at, keyword)
    elif topic == 'lesson':
        answer = [Cited(text=f'{item.category}：{item.text}', observedAt=item.observedAt, sourceRefs=item.sourceRefs)
                  for item in _lesson_references(c, a, p, at, keyword, category, for_preview=for_preview)]
    elif topic == 'health':
        answer, unknowns, at = _ledger_health(c, a, p, at)
    elif topic == 'forecast':
        answer, unknowns = _ledger_forecasts(c, p, at)
        if not answer:
            unknowns.append('当前项目暂无未解决的 LR-03 延期预警。')
    elif topic == 'requirements':
        quality, refs = metrics(c, p.id)
        if quality.satisfaction is not None:
            answer.append(Cited(text=f'加权满足率：{quality.satisfaction}；P0：{quality.confirmedP0}/{quality.activeP0}',
                                sourceRefs=refs[:100]))
        else:
            unknowns.append('需求满足率不可用：' + '、'.join(quality.gaps))
    else:
        basis = cost_basis(c, p.id, at)
        for kind in ('money', 'effort'):
            unit = '金额（CNY）' if kind == 'money' else '人天'
            result, refs = summary_line(c, basis, kind, at)
            if refs:
                rate = result.varianceRate
                answer.append(Cited(text=f'{unit}：已核验净额 {result.actual}；剩余预测 {result.forecastRemaining}；'
                    f'偏差率 {rate if rate not in (None, UNAVAILABLE) else "不可用"}', sourceRefs=refs[:100]))
            else:
                unknowns.append(f'{unit}无可核验记录。')
    if not answer and not unknowns:
        unknowns.append('没有匹配的可核验来源，无法回答。')
    info = _meta(LEDGER, p, at, [ref for item in answer for ref in item.sourceRefs])
    return Result[LedgerMode](data=LedgerMode(answer=answer, asOf=at, sourceRefs=info.sourceRefs, unknowns=unknowns,
        previewEnabled=topic != 'forecast' and _previews_enabled(p.id)), meta=info)


class CharterPreview(Strict):
    mode: Literal['charter']
    keyword: retrospectives.QueryText | None = Field(default=None, max_length=160)
    category: retrospectives.QueryText | None = Field(default=None, max_length=80)


class ClosingPreview(Strict):
    mode: Literal['closing']


class LedgerPreview(Strict):
    mode: Literal['ledger']
    topic: PreviewTopic = 'health'
    keyword: retrospectives.QueryText | None = Field(default=None, max_length=160)
    category: retrospectives.QueryText | None = Field(default=None, max_length=80)


PreviewCommand = Annotated[CharterPreview | ClosingPreview | LedgerPreview, Field(discriminator='mode')]


def _gaps(p, rows):
    return [model.Part(kind='gap', text=gap.message, sourceRefs=gap.sourceRefs or [_project_ref(p)]) for gap in rows]


def _charter_parts(c, a, p, at, keyword, category):
    check, _ = gates.evaluate(c, a, p, 'charter', pending=p.stage == 'charter_review')
    parts = _gaps(p, [gap for gap in check.missing if gap.code != 'GATE_STAGE_MISMATCH'])
    if p.materials_visible:
        table = db.charters
        row = c.execute(sa.select(table).where(table.c.project_id == p.id)).mappings().one_or_none()
        if row is not None:
            refs = [f'charter:{row.id}@{row.revision}']
            fields = [('价值主张', row.value_prop), ('吸引力', row.attractiveness),
                      ('预期收益', row.expected_benefit), ('验收标准', row.success_criteria)]
            parts.extend(model.Part(kind='charter', text=f'{label}：{value}', sourceRefs=refs)
                         for label, value in fields if value)
        score = _published_assessment(c, p, at)
        if score is not None:
            parts.extend(model.Part(kind='score', text=f'{item.label}：{score.means[item.key]}',
                         sourceRefs=score.sourceRefs, observedAt=score.decidedAt)
                         for item in rubric('charter') if item.key in score.means)
        parts.extend(model.Part(kind='history', text=item.text, sourceRefs=item.sourceRefs, observedAt=item.observedAt)
            for item in _lesson_references(c, a, p, at, keyword, category, history_only=True, for_preview=True))
    return check.snapshotHash, parts


def _preview_basis(c, a, p, at, command):
    if command.mode == 'charter':
        scope, parts = _charter_parts(c, a, p, at, command.keyword, command.category)
    elif command.mode == 'closing':
        check, _, origin, answers = _closing_sources(c, a, p)
        scope = [check.snapshotHash, retrospectives._kind(origin)]
        parts = _gaps(p, check.missing) if p.stage in ('delivering', 'closing_review') else []
        parts.extend(model.Part(kind='retro', text=f'{label}：{answer.text}', sourceRefs=answer.sourceRefs,
                     observedAt=answer.observedAt) for label, answer in zip(retrospectives.ANSWER_LABELS, answers))
    else:
        data = _ledger(c, a, p, at, command.topic, command.keyword, command.category, for_preview=True).data
        scope = [command.topic, data.sourceRefs, data.unknowns]
        parts = [model.Part(kind='ledger', **item.model_dump()) for item in data.answer]
    return preview.Basis.from_parts(parts, p.id, p.revision, [p.materials_visible, scope], at)


@router.post('/suggestions', operation_id=preview.OP, openapi_extra=HUMAN, response_model_exclude_none=True)
def suggestions(projectId: UUID, command: PreviewCommand, authorization: Auth, key: Key, expected: Revision) -> Result[preview.Preview]:
    return preview.execute(authorization, projectId, key, int(expected), command, _project, _preview_basis)
