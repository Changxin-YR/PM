"""Build a PM-editable weekly report from scoped, deterministic sources."""
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field, StrictStr, field_validator

from . import fact_store as facts, schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import RULE_VERSION, Result, Strict
from .fact_sources import verified_source
from .health_calculation import ACTIVE, observe
from .identity import Failure
from .projects import fingerprint, run
from .report_fields import DraftFields, MilestoneEntry, RequirementEntry, RiskEntry
from .reports import (GENERATE, HUMAN, TZ, DraftCreate, Report, _context, _finish, _insert, _replay,
                      _writable, period_bounds, reports)
from .weekly_basis import WeeklyBasis, WeeklyFact, WeeklyHealth

router = APIRouter(prefix='/api/v1', tags=['Reports'])
SECTION_LIMIT, FACT_LIMIT = 40, 20


class Generate(Strict):
    period: StrictStr = Field(pattern=r'^[0-9]{4}-W[0-9]{2}\z')

    @field_validator('period')
    @classmethod
    def check_period(cls, period: str) -> str:
        try:
            period_bounds('weekly', period)
        except OverflowError as error:
            raise ValueError('Report period is out of range') from error
        return period


def _trusted_facts(c, p, start, end, at):
    f, r = facts.f, reports.c
    rows = c.execute(facts.query().join(reports, (r.id == f.report_id) & (r.project_id == f.project_id))
        .where(f.project_id == p.id, f.as_of >= start, f.as_of < end, f.as_of <= at, r.submitted_at <= at,
            f.validation_status == 'verified', f.trust.in_(('high', 'medium')))
        .order_by(f.as_of.desc(), sa.case((f.trust == 'high', 0), else_=1), f.id)).mappings().all()
    seen: set[str] = set()
    trusted = []
    sources = {}
    for row in rows:
        if row.group_key in seen:
            continue
        if row.report_id not in sources:
            sources[row.report_id] = c.execute(sa.select(reports).where(r.id == row.report_id,
                r.project_id == p.id)).mappings().one()
        report = sources[row.report_id]
        if not verified_source(c, report, row):
            continue
        trusted.append(WeeklyFact(sourceRef=facts.fact_ref(row), reportRef=row.source_ref, reportHash=row.content_hash,
            quotePath=row.quote_path, quoteOffset=row.quote_offset, quote=row.quote, subject=row.subject,
            predicate=row.predicate, value=row.value, trust=row.trust, asOf=row.as_of,
            evidenceRef=row.evidence_ref if row.trust == 'high' else None, evidenceHash=row.evidence_hash))
        seen.add(row.group_key)
    return trusted


def _milestones(details, start, end) -> list[MilestoneEntry]:
    rows = []
    for item in details:
        proof = item['completion']
        entry = dict(milestoneId=item['source'].split(':', 1)[1].split('@', 1)[0],
            summary=f"{item['name']}：计划 {item['plannedDate']}；预测 {item['forecastDate'] or '未填'}")
        if proof:
            entry['summary'] += f"；已核验完成 {item['actualDate']}"
            if start <= date.fromisoformat(item['actualDate']) < end:
                entry.update(claim='completion', evidenceRef=f"evidence:{proof['proof']['id']}")
        elif item['actualDate']:
            entry['summary'] += '；完成无有效佐证'
        rows.append(MilestoneEntry.model_validate(entry))
    return rows


def make_draft(c, p, period: str, at: datetime) -> tuple[DraftCreate, WeeklyBasis]:
    start, end = period_bounds('weekly', period)
    if start > at.astimezone(TZ).date():
        raise Failure(422, 'REPORT_PERIOD_IN_FUTURE', project_id=p.id)
    lower, upper = datetime.combine(start, time.min, TZ), datetime.combine(end, time.min, TZ)
    obs = observe(c, p, at)
    postings = [row for row in obs.cost_basis.verified if row.type == 'effort' and lower <= row.occurred_at < upper]
    net = sum((row.amount_signed for row in postings), Decimal(0))
    posting_refs = [f'cost-posting:{row.id}' for row in postings]
    notes = _trusted_facts(c, p, lower, upper, at)

    risk = db.risks.c
    open_risks = c.execute(sa.select(risk.id, risk.revision, risk.statement, risk.is_blocking)
        .where(risk.project_id == p.id, risk.status.in_(('open', 'mitigated')))
        .order_by(risk.is_blocking.desc(), risk.id)).mappings().all()
    risk_refs = [f'risk:{row.id}@{row.revision}' for row in open_risks]
    risk_entries = [
        RiskEntry(riskId=str(row.id), summary=f"{'阻塞' if row.is_blocking else '风险'}：{row.statement[:120]}")
        for row in open_risks[:SECTION_LIMIT]]
    requirement = db.requirements.c
    quality = obs.inputs['quality']
    schedule = sorted(quality['schedule'], key=lambda row: (row['priority'], row['requirement_id']))
    candidates = [item for item in schedule if item['revision'] is not None][:SECTION_LIMIT]
    labels = dict(c.execute(sa.select(requirement.id.cast(sa.Text), requirement['desc']).where(requirement.project_id == p.id,
        requirement.id.in_([UUID(item['requirement_id']) for item in candidates]))).tuples().all())
    requirements = [RequirementEntry(requirementId=item['requirement_id'], summary=
        f"{item['priority']} · {labels[item['requirement_id']][:120]}：" + (
            '变更冻结' if item['frozen'] else '客户已确认' if item['confirmed'] else '未获有效客户确认'))
        for item in candidates if item['requirement_id'] in labels]

    # Clock-driven lamps and report receipts (including this draft's own submission) are not material edits.
    time_inputs = [{key: value for key, value in item.items() if key != 'color'} for item in obs.inputs['time']]
    quality_inputs = [{key: value for key, value in item.items() if key not in ('lagWeeks', 'color')} for item in schedule]
    note_values = [item.model_dump(mode='json', exclude_none=True) for item in notes]
    material_refs = [ref for ref in obs.refs if ref.startswith(('requirement:', 'requirement-baseline:', 'change:'))]
    digest = fingerprint(['weekly-basis-v1', RULE_VERSION, str(p.id), p.stage, period, time_inputs,
        obs.inputs['cost'], posting_refs, str(net), quality['metrics'], quality_inputs, material_refs, risk_refs, note_values])
    refs = list(dict.fromkeys([*obs.refs, *risk_refs, *(s for item in notes[:FACT_LIMIT]
        for s in (item.sourceRef, item.reportRef, item.evidenceRef) if s)]))
    health = (WeeklyHealth.model_validate({key: value for key, value in obs.results.items() if key in WeeklyHealth.model_fields})
              if p.stage in ACTIVE else WeeklyHealth(health='unavailable', dataTrust='unavailable', gaps=['HEALTH_NOT_ACTIVE']))
    basis = WeeklyBasis(asOf=at, ruleVersion=RULE_VERSION, projectRevision=p.revision, sourceHash=digest, sourceRefs=refs,
        verifiedPeriodPersonDays=format(net, '.2f'), verifiedPeriodPostingCount=len(postings),
        verifiedPeriodPostingRefs=posting_refs, health=health, verifiedFacts=notes[:FACT_LIMIT],
        omittedVerifiedFacts=max(0, len(notes)-FACT_LIMIT), omittedRequirements=len(schedule)-len(requirements),
        omittedRisks=len(open_risks)-len(risk_entries))
    fields = DraftFields(milestone_progress=_milestones(obs.inputs['time'], start, end),
        effort_person_days=basis.verifiedPeriodPersonDays if postings and 0 <= net < 10**10 else None,
        requirement_progress=requirements, risks_and_blockers=risk_entries)
    return DraftCreate(reportType='weekly', period=period, structuredFields=fields), basis


@router.post('/projects/{projectId}/reports/weekly-drafts', operation_id=GENERATE,
             response_model_exclude_none=True, openapi_extra=HUMAN)
def generate(projectId: UUID, command: Generate, authorization: Auth, key: Key, expected: Revision) -> Result[Report]:
    revision = int(expected)
    signature = fingerprint([str(projectId), revision, command.period])

    def write(c, a, rid):
        p, _, pm = _context(c, a, project_id=projectId, write=True)
        if not pm:
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        if prior := _replay(c, a, p, GENERATE, key, signature):
            return prior
        _writable(p)
        if p.revision != revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        at = c.scalar(sa.select(sa.func.clock_timestamp()))
        draft, basis = make_draft(c, p, command.period, at)
        row = _insert(c, a, p, draft, at, basis=basis.model_dump(mode='json', exclude_none=True))
        return _finish(c, a, p, pm, row, GENERATE, key, rid, signature)
    return run(authorization, GENERATE, write)
