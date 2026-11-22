"""Q7: scoped query of recorded health observations, not a live health recomputation."""
from datetime import date, datetime, time, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from . import schema_v1 as db
from .access import Auth
from .contracts import Result, Strict
from .health import HUMAN
from .health_calculation import TZ, WEEK, monday
from .health_schema import events
from .identity import Failure, visible
from .projects import meta, run
from .requirement_disputes import t as disputes

router = APIRouter(prefix='/api/v1/analytics', tags=['Analytics'])
OP = 'human_weekly_health_list'
SIGNALS = ('yellow', 'red')
Color = Literal['green', 'yellow', 'red']
s, p = db.health_snapshots, db.projects


class Cause(Strict):
    line: Literal['time', 'money', 'effort', 'quality']
    color: Literal['yellow', 'red']
    code: str
    name: str | None = None
    plannedDate: date | None = None
    forecastDate: date | None = None
    delayDays: int | None = None
    varianceRate: str | None = None
    sourceRefs: list[str]


class RuleSource(Strict):
    alertId: UUID
    ruleId: str
    sourceRefs: list[str]


class Sample(Strict):
    snapshotId: UUID
    asOf: datetime
    ruleVersion: str
    health: Color | Literal['untrusted']
    publication: Literal['official', 'preview', 'withheld']
    timeLine: Color | None = None
    costLine: Color | None = None
    qualityLine: Color | None = None
    causes: list[Cause]
    omittedCauses: int
    gaps: list[str]
    ruleEvents: list[RuleSource]
    sourceRefs: list[str]


class Item(Strict):
    projectId: UUID
    projectCode: str
    projectName: str
    latest: Sample
    earlierSignal: Sample | None = None


class WeekPage(Strict):
    weekStart: date
    weekEnd: date
    asOf: datetime
    sampledProjects: int
    items: list[Item]
    page: int
    pageSize: int
    total: int


def _dispute_sources(c, snapshots) -> dict[tuple[UUID, str], list[str]]:
    # Older observations predate the disputed flag; they cannot carry a recorded objection.
    ids = [row.id for row in snapshots if row.results['dataTrust'] == 'trusted'
           and any(item.get('disputed') is True for item in row.input_revisions['quality']['schedule'])]
    if not ids:
        return {}
    d = disputes.c
    # Match the opening receipt saved at observation time, regardless of a later withdrawal.
    opening = sa.literal('requirement-dispute:') + d.id.cast(sa.Text) + '@1'
    match = sa.and_(d.project_id == s.c.project_id, s.c.source_refs.contains(sa.func.jsonb_build_array(opening)))
    query = (sa.select(s.c.id, d.requirement_id, opening).select_from(s.join(disputes, match))
             .where(s.c.id.in_(ids)).order_by(d.id))
    records = c.execute(query)
    refs: dict[tuple[UUID, str], list[str]] = {}
    for snapshot_id, requirement_id, source in records:
        refs.setdefault((snapshot_id, str(requirement_id)), []).append(source)
    return refs


def _sample(row, rule_sources, dispute_sources) -> Sample:
    values, inputs = row.results, row.input_revisions
    trusted = values['dataTrust'] == 'trusted'
    causes = []
    if trusted:
        for milestone in inputs['time']:
            if milestone['color'] in SIGNALS:
                code = 'MILESTONE_OVERDUE' if milestone['plannedDate'] < inputs['day'] else 'MILESTONE_DRIFT'
                causes.append(Cause(line='time', color=milestone['color'], code=code, name=milestone['name'],
                    plannedDate=milestone['plannedDate'], forecastDate=milestone['forecastDate'],
                    delayDays=milestone['delayDays'], sourceRefs=[milestone['source']]))
        for kind, cost in inputs['cost'].items():
            if cost['color'] in SIGNALS:
                causes.append(Cause(line=kind, color=cost['color'], code='COST_VARIANCE', varianceRate=cost['variance'],
                    sourceRefs=cost['sourceRefs'][:100]))
        for requirement in inputs['quality']['schedule']:
            if requirement['color'] in SIGNALS:
                disputed = requirement.get('disputed') is True
                code = ('CUSTOMER_DISPUTE' if disputed else
                        'P0_UNCONFIRMED' if requirement['priority'] == 'P0' else 'REQUIREMENT_LATE')
                ref = f"requirement:{requirement['requirement_id']}@{requirement['revision']}"
                receipt_refs = dispute_sources.get((row.id, requirement['requirement_id']), ()) if disputed else ()
                causes.append(Cause(line='quality', color=requirement['color'], code=code, plannedDate=requirement['planned'],
                    sourceRefs=[ref, *receipt_refs]))
    refs = list(dict.fromkeys([f'health-snapshot:{row.id}', *row.source_refs]))[:100]
    keys = [(row.project_id, UUID(eid)) for eid in row.rule_event_ids[:100]]
    return Sample(snapshotId=row.id, asOf=row.as_of, ruleVersion=row.rule_version, health=values['health'],
        publication=values['publication'], timeLine=values.get('timeLine') if trusted else None,
        costLine=values.get('costLine') if trusted else None, qualityLine=values.get('qualityLine') if trusted else None,
        causes=causes[:24], omittedCauses=max(0, len(causes)-24), gaps=values.get('gaps', []), sourceRefs=refs,
        ruleEvents=[rule_sources[key] for key in keys if key in rule_sources])


@router.get('/weekly-health', operation_id=OP, response_model_exclude_none=True, openapi_extra=HUMAN)
def weekly_health(authorization: Auth, weekStart: date | None = None, asOf: AwareDatetime | None = None,
                  page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=50)) -> Result[WeekPage]:
    def read(c, a, _):
        if a.kind != 'human':
            raise Failure(403, 'FORBIDDEN')
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        at = asOf or now
        if at > now:
            raise Failure(422, 'FUTURE_AS_OF')
        start = weekStart or monday(at.astimezone(TZ).date())
        if start.weekday() != 0 or start > at.astimezone(TZ).date():
            raise Failure(422, 'INVALID_HEALTH_WEEK')
        end = start + WEEK
        window = (sa.select(s.c.id, s.c.project_id, s.c.as_of, s.c.results, p.c.code, p.c.name)
            .select_from(p.join(s, s.c.project_id == p.c.id)).where(visible(a), s.c.results.has_key('publication'),
                s.c.as_of >= datetime.combine(start, time(), TZ), s.c.as_of < datetime.combine(end, time(), TZ), s.c.as_of <= at)
            .cte('week_samples'))
        sort = (window.c.project_id, window.c.as_of.desc(), window.c.id.desc())
        last = sa.select(window).distinct(window.c.project_id).order_by(*sort).cte('latest_samples')
        lines = ('timeLine', 'costLine', 'qualityLine')
        signal = (sa.select(window.c.project_id, window.c.id).where(sa.or_(
            *(window.c.results[line].astext.in_(SIGNALS) for line in lines)))
            .distinct(window.c.project_id).order_by(*sort).cte('last_signals'))
        matched = (sa.select(last.c.id, last.c.project_id, last.c.code, last.c.name, last.c.as_of,
                    signal.c.id.label('signal_id')).select_from(last.outerjoin(signal, signal.c.project_id == last.c.project_id))
                   .where(sa.or_(signal.c.id.is_not(None), last.c.results['health'].astext == 'untrusted')).cte('attention'))
        counts = sa.select(sa.select(sa.func.count()).select_from(last).scalar_subquery().label('sampled'),
            sa.select(sa.func.count()).select_from(matched).scalar_subquery().label('total')).subquery()
        selected = sa.select(matched).order_by(matched.c.as_of.desc(), matched.c.project_id).limit(pageSize).offset((page-1)*pageSize).subquery()
        rows = c.execute(sa.select(counts, selected).select_from(counts.outerjoin(selected, sa.true()))
                         .order_by(selected.c.as_of.desc(), selected.c.project_id)).mappings().all()
        found = [row for row in rows if row.id is not None]
        ids = {eid for row in found for eid in (row.id, row.signal_id) if eid is not None}
        snapshots = {row.id: row for row in c.execute(sa.select(s).where(s.c.id.in_(ids))).mappings()} if ids else {}
        event_keys = {(row.project_id, UUID(eid)) for row in snapshots.values() for eid in row.rule_event_ids[:100]}
        e = events.c
        sources = {(row.project_id, row.id): RuleSource(alertId=row.id, ruleId=row.rule_id, sourceRefs=row.source_refs[:100])
            for row in c.execute(sa.select(e.project_id, e.id, e.rule_id, e.source_refs)
                                 .where(sa.tuple_(e.project_id, e.id).in_(event_keys))).mappings()} if event_keys else {}
        dispute_sources = _dispute_sources(c, snapshots.values())
        items = [Item(projectId=row.project_id, projectCode=row.code, projectName=row.name,
                      latest=_sample(snapshots[row.id], sources, dispute_sources),
                      earlierSignal=_sample(snapshots[row.signal_id], sources, dispute_sources)
                      if row.signal_id and row.id != row.signal_id else None)
                 for row in found]
        info = meta(OP, [f'health-snapshot:{eid}' for eid in sorted(ids)])
        info.asOf = at
        return Result[WeekPage](data=WeekPage(weekStart=start, weekEnd=end - timedelta(days=1), asOf=at, sampledProjects=rows[0].sampled,
            items=items, page=page, pageSize=pageSize, total=rows[0].total), meta=info)
    return run(authorization, OP, read)
