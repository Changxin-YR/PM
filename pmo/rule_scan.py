"""Project-serialized incremental and daily rule scans. No model or HTTP required."""
import argparse
import json
from datetime import date, datetime, time, timedelta
from time import sleep
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from . import schema_v1 as db
from .contracts import RULE_VERSION
from .db import engine
from .health_calculation import ACTIVE, Observation, observe
from .health_schema import events, scan_state
from .milestone_forecasts import reconcile
from .projects import fingerprint
from .reports import TZ
from .requirement_baselines import active_member
from .retrospectives import Save, _same_org_confirmation, _valid_confirmation
from .rule_delivery import notify

SCHEDULED = (*ACTIVE, 'closed', 'retrospective')


def slot(at: datetime) -> date:
    local = at.astimezone(TZ)
    return local.date() - timedelta(days=int(local.time() < time(9)))


def _event(c, p, rule, period, condition, refs, context, recipient) -> UUID:
    digest = fingerprint([RULE_VERSION, condition])
    eid = c.scalar(insert(events).values(project_id=p.id, rule_id=rule, business_period=period,
        fingerprint=digest, rule_version=RULE_VERSION, source_refs=list(dict.fromkeys(refs)),
        input_snapshot=context, status='open').on_conflict_do_nothing(index_elements=[events.c.project_id,
            events.c.rule_id, events.c.business_period, events.c.fingerprint]).returning(events.c.id))
    if eid is None:
        row = c.execute(sa.select(events.c.id, events.c.status).where(events.c.project_id == p.id,
            events.c.rule_id == rule, events.c.business_period == period, events.c.fingerprint == digest)).one()
        eid = row.id
        if row.status == 'resolved':
            c.execute(events.update().where(events.c.id == eid).values(status='open', updated_at=sa.func.now()))
    notify(c, p.id, eid, recipient)
    return eid


def _task(c, p, eid, owner, title, at):
    if owner is None:
        return
    t = db.tasks
    if not c.scalar(sa.select(t.c.id).where(t.c.project_id == p.id, t.c.source_type == 'rule_event',
                                           t.c.source_id == eid, t.c.title == title).limit(1)):
        c.execute(t.insert().values(project_id=p.id, source_type='rule_event', source_id=eid, title=title,
            assignee_id=owner, created_by=p.created_by, due_at=at + timedelta(days=7), status='todo'))


def _resolve_inactive(c, pid, rule, opened):
    c.execute(events.update().where(events.c.project_id == pid, events.c.rule_id == rule,
        events.c.status == 'open', ~events.c.id.in_(opened)).values(status='resolved', updated_at=sa.func.now()))


def _retrospective(c, p, at, sponsor):
    if p.stage not in ('closed', 'retrospective'):
        return None
    closing = c.execute(sa.text('''SELECT s.to_revision,s.transitioned_at,d.id AS decision_id FROM project_stage_transitions s
        JOIN decisions d ON d.review_id=s.review_id AND d.project_id=s.project_id AND d.option IN ('pass','conditional_pass')
        JOIN review_decision_snapshots snap ON snap.decision_id=d.id
        WHERE s.project_id=:pid AND s.from_stage='closing_review' AND s.to_stage='closed'
        ORDER BY s.to_revision DESC LIMIT 1'''), {'pid': p.id}).mappings().one_or_none()
    if not closing:
        return None
    deadline = closing.transitioned_at.astimezone(TZ).date() + timedelta(days=14)
    if at.astimezone(TZ).date() < deadline:
        return None
    r = db.project_retrospectives
    retro = c.execute(sa.select(r).where(r.c.project_id == p.id)).mappings().one_or_none()
    complete = False
    if retro and retro.completed_at:
        try:
            Save(answers=retro.answers)
            complete = True
        except ValueError:
            pass
    l = db.lessons
    lesson = c.execute(sa.select(l.c.id, l.c.revision).where(l.c.project_id == p.id, *_valid_confirmation(),
        _same_org_confirmation(p.org_id)).order_by(l.c.confirmed_at, l.c.id).limit(1)).one_or_none()
    if complete and lesson:
        return None
    refs = [f'decision:{closing.decision_id}', f'project-transition:{p.id}@{closing.to_revision}']
    if retro:
        refs.append(f'retrospective:{retro.id}@{retro.revision}')
    if lesson:
        refs.append(f'lesson:{lesson.id}@{lesson.revision}')
    return _event(c, p, 'LR-08', deadline.isoformat(), [str(closing.decision_id)], refs, {
        'reason': '正常结项已满 14 个自然日，复盘四问或已确认教训仍缺失。', 'deadline': deadline.isoformat(),
        'retrospectiveComplete': complete, 'confirmedLessonPresent': lesson is not None}, sponsor)


def _governance_events(c, p, at, health: Observation | None):
    opened: dict[str, list[UUID]] = {'LR-04': [], 'LR-06': [], 'LR-08': []}
    pm = p.pm_id if p.pm_id and active_member(c, p, p.pm_id, 'pm') else None
    sponsor = p.sponsor_id if p.sponsor_id and active_member(c, p, p.sponsor_id, 'sponsor') else None
    visible_ids: list[UUID] = []
    if health is not None:
        for milestone in health.milestones:
            eid = reconcile(c, p, milestone)
            if eid is not None:
                visible_ids.append(eid)
        for kind, line in health.inputs['cost'].items():
            if not line['overspend']:
                continue
            # One ongoing alert per distinct cost basis/threshold level, not one per day.
            period = at.astimezone(TZ).strftime('%Y-%m')
            refs = line['sourceRefs'] or [f'project:{p.id}@{p.revision}']
            level = 'zero' if line['budget'] == '0.00' else 'reassess' if line['reassess'] else 'red'
            eid = _event(c, p, 'LR-04', period, [kind, line['budgetRefs'], level], refs,
                {**line, 'kind': kind, 'reason': '金额超支' if kind == 'money' else '人天超支'}, sponsor)
            opened['LR-04'].append(eid)
            if line['reassess']:
                _task(c, p, eid, sponsor, f'LR-04 复评继续投入（{"金额" if kind == "money" else "人天"}）', at)
        weekly = health.inputs['weekly']
        if weekly['twoMissed']:
            periods = weekly['missingPeriods']
            eid = _event(c, p, 'LR-06', periods[-1], ['missing-weeks', periods], health.refs,
                {'reason': '连续两个应交周期缺少有效周报。', 'missingPeriods': periods}, pm)
            opened['LR-06'].append(eid)
            _task(c, p, eid, pm, 'LR-06 澄清缺失周报', at)
        for claim in weekly['missingEvidence']:
            eid = _event(c, p, 'LR-06', claim['period'], ['missing-evidence', claim['milestone'].split('@')[0], claim['kind']],
                [claim['report'], claim['milestone']], {**claim, 'reason': '关键里程碑申报缺少可核验佐证。'}, pm)
            opened['LR-06'].append(eid)
            _task(c, p, eid, pm, 'LR-06 补核关键里程碑佐证', at)
    else:
        # Resolve delay episodes after leaving the operational lifecycle.
        for row in c.execute(sa.select(db.milestones).where(db.milestones.c.project_id == p.id)).mappings():
            reconcile(c, p, row)
    reminder = _retrospective(c, p, at, sponsor)
    if reminder:
        opened['LR-08'].append(reminder)
    for rule, eids in opened.items():
        _resolve_inactive(c, p.id, rule, eids)
        visible_ids.extend(eids)
    return sorted(set(visible_ids))


def latest(c, pid, cutoff=None):
    s = db.health_snapshots
    query = sa.select(s).where(s.c.project_id == pid, s.c.results.has_key('publication'))  # noqa: W601
    if cutoff is not None:
        query = query.where(s.c.as_of <= cutoff)
    return c.execute(query.order_by(s.c.as_of.desc(), s.c.id.desc()).limit(1)).mappings().one_or_none()


def scan_locked(c, p, at: datetime, *, daily: date | None = None):
    """Caller holds FOR UPDATE on the project until snapshots/tasks/outbox commit."""
    health = observe(c, p, at) if p.stage in ACTIVE else None
    ids = _governance_events(c, p, at, health)
    row = None
    if health is not None:
        s = db.health_snapshots
        row = latest(c, p.id)
        if row is None or row.input_hash != health.digest or row.rule_event_ids != [str(eid) for eid in ids]:
            row = c.execute(s.insert().values(project_id=p.id, as_of=at, calculated_at=sa.func.clock_timestamp(),
                data_trust=health.results['dataTrust'], rule_version=RULE_VERSION, input_hash=health.digest,
                input_revisions=health.inputs, results=health.results, source_refs=health.refs,
                rule_event_ids=[str(eid) for eid in ids]).returning(s)).mappings().one()
    values = {'dirty': False, 'last_scanned_at': at}
    if daily is not None:
        values['last_daily_slot'] = daily
    c.execute(insert(scan_state).values(project_id=p.id, **values).on_conflict_do_update(
        index_elements=[scan_state.c.project_id], set_=values))
    return row


def scan_batch(database, limit=50):
    """A missed daily slot is caught up against actual current inputs, never backdated."""
    p, org, s = db.projects, db.organizations, scan_state.c
    processed = 0
    for _ in range(limit):
        with database.begin() as c:
            at = c.scalar(sa.select(sa.func.clock_timestamp()))
            day = slot(at)
            project = c.execute(sa.select(p).select_from(p.join(org, p.c.org_id == org.c.id)
                .outerjoin(scan_state, s.project_id == p.c.id)).where(org.c.status == 'active', sa.or_(s.dirty,
                    sa.and_(p.c.stage.in_(SCHEDULED), sa.or_(s.last_daily_slot.is_(None), s.last_daily_slot < day))))
                .order_by(s.last_scanned_at.asc().nulls_first(), p.c.id).limit(1)
                .with_for_update(of=p, skip_locked=True)).mappings().one_or_none()
            if project is None:
                break
            scan_locked(c, project, at, daily=day)
            processed += 1
    return processed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, choices=range(1, 501), default=50, metavar='1..500')
    parser.add_argument('--watch', action='store_true', help='Run the independent scheduler every 30 seconds')
    args = parser.parse_args()
    database = engine()
    while True:
        count = scan_batch(database, args.limit)
        print(json.dumps({'scanned': count}), flush=True)
        if not args.watch:
            return
        sleep(30 if count < args.limit else 1)


if __name__ == '__main__':
    main()
