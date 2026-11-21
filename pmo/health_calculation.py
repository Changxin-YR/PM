"""Read-only three-line calculation under the caller's project lock."""
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from . import evidence_storage, schema_v1 as db
from .contracts import RULE_VERSION
from .cost_math import cost_line
from .costs import CostBasis, cost_basis, zero_budget_claim
from .identity import Failure
from .projects import fingerprint
from .reports import TZ, weekly_submissions
from .scope_quality import Schedule, metrics

ACTIVE = ('executing', 'on_hold', 'delivering', 'closing_review')
WEEK = timedelta(days=7)
UNAVAILABLE = 'UNAVAILABLE'
SCALE = {'green': 0, 'yellow': 1, 'red': 2, 'untrusted': 3}


@dataclass(frozen=True)
class Observation:
    inputs: dict[str, Any]
    results: dict[str, Any]
    refs: list[str]
    milestones: list[Any]
    cost_basis: CostBasis

    @property
    def digest(self) -> str:
        return fingerprint([RULE_VERSION, self.inputs, self.results, self.refs])


def iso(value):
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value.isoformat() if value else None


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_key(day: date) -> str:
    return f'{day.isocalendar().year:04d}-W{day.isocalendar().week:02d}'


def due_weeks(entries, at: datetime) -> list[date]:
    """The last two due weeks that actually intersected execution (pauses don't count)."""
    local = at.astimezone(TZ)
    this_week = monday(local.date())
    latest = this_week - (WEEK if local >= datetime.combine(this_week, time(18), TZ) else 2 * WEEK)
    found: set[date] = set()
    end = at
    for entry in reversed(entries):
        start = entry.transitioned_at
        if entry.to_stage == 'executing' and end > start:
            # Both the stage interval and the reporting interval are left closed, right open.
            last_day = (end - timedelta(microseconds=1)).astimezone(TZ).date()
            week = min(latest, monday(last_day))
            first = monday(start.astimezone(TZ).date())
            while week >= first and len(found) < 2:
                found.add(week)
                week -= WEEK
        if len(found) == 2:
            break
        end = start
    return sorted(found)


def aggregate(lines: list[str]) -> str:
    if 'untrusted' in lines:
        return 'untrusted'
    if 'red' in lines:
        return 'red'
    return 'yellow' if lines.count('yellow') >= 2 else 'green'


def _color(colors: list[str]) -> str:
    return max(colors, key=lambda x: SCALE[x], default='green')


class MilestoneEvidence:
    """Reuse the secure storage verifier without acting as an authenticated person."""

    def __init__(self, c, pid, at):
        self.c, self.pid, self.at = c, pid, at
        self.cache: dict[tuple[UUID, str], dict[str, Any] | None] = {}

    def proof(self, mid: UUID, ref: str) -> dict[str, Any] | None:
        key = (mid, ref)
        if key in self.cache:
            return self.cache[key]
        try:
            eid = UUID(ref.removeprefix('evidence:')) if ref.startswith('evidence:') else None
        except ValueError:
            eid = None
        row = self.c.execute(sa.text('''SELECT f.id,l.revision,f.sha256,f.size_bytes,f.storage_key,f.verified_at
            FROM evidence_files f JOIN evidence_links l ON l.evidence_id=f.id
            WHERE f.id=:eid AND f.project_id=:pid AND l.resource_type='milestone' AND l.resource_id=:mid
              AND f.quarantine_status='verified' AND f.verified_by<>f.uploaded_by AND f.verified_at<=:at'''),
            dict(eid=eid, pid=self.pid, mid=mid, at=self.at)).mappings().one_or_none() if eid else None
        result = None
        if row is not None:
            try:
                evidence_storage.checked_bytes(row.storage_key, row.sha256, row.size_bytes)
            except Failure as exc:
                if exc.code != 'EVIDENCE_INTEGRITY_FAILED':
                    raise
            else:
                result = {'id': str(row.id), 'revision': row.revision, 'sha256': row.sha256,
                          'verifiedAt': iso(row.verified_at)}
        self.cache[key] = result
        return result

    def completion(self, row):
        if row.actual_date is None or row.actual_date > self.at.astimezone(TZ).date() or not row.evidence_ref:
            return None
        receipt = self.c.execute(sa.text('''SELECT evidence_id,revision,created_at FROM milestone_completions
            WHERE milestone_id=:mid AND actual_date=:day AND revision<=:rev AND created_at<=:at'''),
            dict(mid=row.id, day=row.actual_date, rev=row.revision, at=self.at)).mappings().one_or_none()
        if not receipt or f'evidence:{receipt.evidence_id}' != row.evidence_ref:
            return None
        proof = self.proof(row.id, row.evidence_ref)
        if proof:
            return {'receiptRevision': receipt.revision, 'recordedAt': iso(receipt.created_at), 'proof': proof}
        return None


def _evidence_ref(proof):
    return f'evidence:{proof["id"]}@{proof["revision"]}'


def _timing(rows, today, evidence):
    details, refs, gaps = [], [], set()
    completions = {}
    for row in rows:
        if row.is_cancelled:
            continue
        ref = f'milestone:{row.id}@{row.revision}'
        refs.append(ref)
        delay = max(0, ((row.forecast_date or row.planned_date) - row.planned_date).days)
        color = 'red' if row.planned_date < today or delay > 10 else 'yellow' if delay >= 4 else 'green'
        completion = evidence.completion(row) if row.actual_date else None
        if completion:
            completions[row.id] = completion['proof']
            refs.append(_evidence_ref(completion['proof']))
        elif row.actual_date is not None and row.is_key:
            gaps.add('MILESTONE_COMPLETION_EVIDENCE_INVALID')
        details.append({'source': ref, 'name': row.name, 'key': row.is_key, 'plannedDate': iso(row.planned_date),
            'forecastDate': iso(row.forecast_date), 'forecastMissing': row.forecast_date is None,
            'delayDays': delay, 'actualDate': iso(row.actual_date), 'completion': completion,
            'actualDelayDays': max(0, (row.actual_date - row.planned_date).days) if completion else None,
            'color': color if row.is_key and not completion else None})
    return _color([item['color'] for item in details if item['color']]), details, completions, refs, gaps


def _quality(c, pid, today):
    schedule: list[Schedule] = []
    result, refs = metrics(c, pid, schedule=schedule)
    colors, details = [], []
    for row in schedule:
        lag = max(0, (monday(today) - monday(row.planned)).days // 7) if row.planned else 0
        color = ('green' if row.confirmed else 'red' if row.priority == 'P0' or lag >= 2
                 else 'yellow' if lag == 1 else 'green')
        colors.append(color)
        details.append({**asdict(row), 'planned': iso(row.planned), 'lagWeeks': lag, 'color': color})
    gaps = set(result.gaps)
    if any(row.planned is None for row in schedule):
        gaps.add('REQUIREMENT_PLAN_DATE_MISSING')
    return _color(colors), result, details, refs, gaps


def _costs(c, source, at):
    lines, all_refs, gaps = {}, [], set()
    for kind in ('money', 'effort'):
        budget, actual, remaining = source.amounts(kind)
        refs = source.refs(kind)
        zero = zero_budget_claim(c, source, kind, at) if budget == 0 else None
        if zero:
            refs = [*refs, f'zero-budget:{zero.id}']
        result = cost_line(budget, actual, remaining, zero_declared=zero is not None)
        if result.color == 'untrusted':
            gaps.add(f'{kind.upper()}_BUDGET_MISSING' if budget is None else f'{kind.upper()}_ZERO_BUDGET_UNDECLARED')
        lines[kind] = {'budget': str(budget) if budget is not None else None, 'actual': str(actual),
            'remaining': str(remaining), 'projected': str(result.projected), 'color': result.color,
            'variance': format(result.variance, '.4f') if result.variance is not None else UNAVAILABLE,
            'overspend': result.overspend_alert, 'reassess': result.reassess, 'sourceRefs': refs,
            'budgetRefs': source.budget_refs(kind)}
        all_refs.extend(refs)
    return _color([x['color'] for x in lines.values()]), lines, all_refs, gaps


def _weekly(c, p, at, stage_entries, milestones, evidence, completions):
    due = due_weeks(stage_entries, at)
    current = monday(at.astimezone(TZ).date())
    # The latest due week remains operational until the next due week takes over.
    overdue_cutoff = current - WEEK
    bounds = [*due, overdue_cutoff, current]
    submissions = weekly_submissions(c, p.id, as_of=at, start=min(bounds), end=current + WEEK)
    by_author = {}
    eligible = {current, overdue_cutoff, *due[-1:]}
    for row in submissions:
        if row.period_start in eligible:
            by_author[(row.period_start, row.submitted_by)] = row
    # Only the latest receipt of each submitting identity supplies active claims.
    keys = {r.id: r for r in milestones if r.is_key and not r.is_cancelled}
    all_claims, missing, evidence_refs = [], [], []
    for report in by_author.values():
        for claim in report.milestone_claims:
            if claim.milestone_id not in keys:
                continue
            proofs = [x for ref in claim.evidence_refs if (x := evidence.proof(claim.milestone_id, ref))]
            if not proofs and claim.kind == 'completion' and claim.milestone_id in completions:
                milestone = keys[claim.milestone_id]
                if report.period_start <= milestone.actual_date < report.period_end:
                    proofs = [completions[claim.milestone_id]]
            receipt = {'report': report.source_ref, 'period': report.period,
                'milestone': f'milestone:{claim.milestone_id}@{keys[claim.milestone_id].revision}',
                'kind': claim.kind, 'item': claim.item_index, 'evidence': proofs}
            all_claims.append(receipt)
            evidence_refs.extend(_evidence_ref(x) for x in proofs)
            if not proofs:
                missing.append(receipt)
    present = {report.period_start for report in submissions}
    missing_periods = [week_key(day) for day in due if day not in present]
    two_missed = p.stage == 'executing' and len(missing_periods) == 2
    confirmed = not due or any(r.period_start == due[-1] and r.confirmed_at for r in submissions)
    receipts = [{'source': r.source_ref, 'hash': r.content_hash, 'period': r.period,
                 'submittedAt': iso(r.submitted_at), 'confirmedAt': iso(r.confirmed_at),
                 'confirmedRevision': r.confirmed_revision} for r in submissions if r.period_start in bounds]
    refs = [*(i['source'] for i in receipts), *evidence_refs]
    refs.extend(f'report:{r.report_id}@{r.confirmed_revision}' for r in submissions
                if r.confirmed_revision and r.period_start in bounds)
    return {'due': [week_key(day) for day in due], 'missingPeriods': missing_periods, 'twoMissed': two_missed,
        'confirmed': confirmed, 'receipts': receipts, 'claims': all_claims, 'missingEvidence': missing}, refs


def observe(c, p, at: datetime) -> Observation:
    """No historical reconstruction from mutable live rows. Use persisted observations for asOf reads."""
    today = at.astimezone(TZ).date()
    m = db.milestones
    rows = list(c.execute(sa.select(m).where(m.c.project_id == p.id).order_by(m.c.id)).mappings())
    transitions = list(c.execute(sa.text('''SELECT to_stage,to_revision,transitioned_at FROM project_stage_transitions
        WHERE project_id=:id AND transitioned_at<=:at ORDER BY to_revision'''), dict(id=p.id, at=at)).mappings())
    proof = MilestoneEvidence(c, p.id, at)
    timeline, time_inputs, completions, time_refs, time_gaps = _timing(rows, today, proof)
    source = cost_basis(c, p.id, at)
    costs, cost_inputs, cost_refs, cost_gaps = _costs(c, source, at)
    quality, quality_data, quality_inputs, quality_refs, quality_gaps = _quality(c, p.id, today)
    weekly, weekly_refs = _weekly(c, p, at, transitions, rows, proof, completions)
    gaps = time_gaps | cost_gaps | quality_gaps
    if not time_inputs:
        gaps.add('NO_ACTIVE_MILESTONES')
    if p.stage == 'executing' and not any(i.to_stage == 'executing' for i in transitions):
        gaps.add('EXECUTION_RECEIPT_MISSING')
    if weekly['twoMissed']:
        gaps.add('LR06_TWO_WEEKS_MISSED')
    if weekly['missingEvidence']:
        gaps.add('LR06_CLAIM_WITHOUT_EVIDENCE')
    trusted = not gaps
    results: dict[str, Any] = {'health': aggregate([timeline, costs, quality]) if trusted else 'untrusted',
        'dataTrust': 'trusted' if trusted else 'untrusted',
        'publication': 'official' if trusted and weekly['confirmed'] else 'preview' if trusted else 'withheld',
        'gaps': sorted(gaps | ({'WEEKLY_PM_CONFIRMATION_PENDING'} if trusted and not weekly['confirmed'] else set()))}
    if not trusted:
        # Show unavailable or untrusted explicitly; never attach a stale color or rate.
        results.update(requirementSatisfactionRate=UNAVAILABLE if quality_data.satisfaction is None else None)
    else:
        results.update(timeLine=timeline, costLine=costs, qualityLine=quality,
                       moneyVarianceRate=cost_inputs['money']['variance'], effortVarianceRate=cost_inputs['effort']['variance'],
                       requirementSatisfactionRate=quality_data.satisfaction)
    refs = list(dict.fromkeys([f'project:{p.id}@{p.revision}',
        *(f'project-transition:{p.id}@{i.to_revision}' for i in transitions), *time_refs, *cost_refs, *quality_refs, *weekly_refs]))
    inputs = {'day': today.isoformat(), 'stage': p.stage, 'time': time_inputs, 'cost': cost_inputs,
              'quality': {'metrics': quality_data.model_dump(mode='json'), 'schedule': quality_inputs}, 'weekly': weekly}
    return Observation(inputs, results, refs, rows, source)
