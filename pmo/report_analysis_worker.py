"""Bounded, resumable report analysis; calls the model outside any transaction."""
import argparse
from dataclasses import dataclass
from datetime import timedelta
from time import sleep
from uuid import uuid4

import sqlalchemy as sa
from pydantic import Field, ValidationError
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError

from . import schema_v1 as db
from .contracts import Strict
from .db import engine
from .fact_sources import CandidateFields, Quote, _leaves, quote_location, submitted
from .identity import Failure, active_browser_session
from .model_gateway import Gateway, Model, ModelFailure, enabled
from .report_analysis import Suggestion, finalize, permitted, r, runs
from .reports import _ref, reports
from .session_schema import SESSION_ID

LEASE = timedelta(seconds=40)
RETRYABLE = ('MODEL_SCHEMA_INVALID', 'MODEL_SOURCE_INVALID')


class Extracted(CandidateFields):
    typedValue: Quote  # A value is copied as text, never parsed from a model float.


class Output(Strict):
    candidates: list[Extracted] = Field(max_length=20)


@dataclass(frozen=True)
class Claim:
    row: RowMapping
    exhausted: bool


def claim(pool: Engine) -> Claim | None:
    with pool.begin() as c:
        row = c.execute(sa.select(runs).where(sa.or_(r.status == 'queued',
            sa.and_(r.status == 'running', r.leased_until <= sa.func.clock_timestamp())))
            .order_by(r.requested_at, r.id).limit(1).with_for_update(skip_locked=True)).mappings().one_or_none()
        if row is None:
            return None
        lease = c.execute(runs.update().where(r.id == row.id).values(status='running', lease_id=uuid4(),
            leased_until=sa.func.clock_timestamp() + LEASE, attempts=min(2, row.attempts + 1))
            .returning(runs)).mappings().one()
        return Claim(lease, row.attempts >= 2)


def _check(c, job):
    # Match the API's account -> project -> run lock order. Revalidate after a network call too.
    who = active_browser_session(c, SESSION_ID == job.requested_session_id)
    p = c.execute(sa.select(db.projects).where(db.projects.c.id == job.project_id)
        .with_for_update()).mappings().one()
    row = c.execute(sa.select(runs).where(r.id == job.id, r.status == 'running', r.lease_id == job.lease_id,
        r.leased_until > sa.func.clock_timestamp()).with_for_update()).mappings().one_or_none()
    if row is None:
        return p, None, None
    if (who is None or who.id != row.requested_account_id or who.person != row.requested_by
            or who.org_id != p.org_id or p.stage == 'archived'):
        finalize(c, p, row, status='stale', code='ANALYSIS_AUTHORITY_CHANGED')
        return p, None, None
    report = c.execute(sa.select(reports).where(reports.c.id == row.report_id, reports.c.project_id == p.id)
        ).mappings().one_or_none()
    if (report is None or report.revision != row.report_revision or report.content_hash != row.report_hash
            or not submitted(report)):
        finalize(c, p, row, status='stale', code='ANALYSIS_SOURCE_CHANGED')
        return p, None, None
    if not permitted(c, p, report, who.person):
        finalize(c, p, row, status='stale', code='ANALYSIS_AUTHORITY_CHANGED')
        return p, None, None
    return p, row, report


def _notes(report):
    # Structured totals, IDs, attachment references and identities do not go to the gateway.
    return [text for path, text in _leaves(report.structured_fields, '')
            if path.rsplit('/', 1)[-1] in ('summary', 'description', 'name', 'note')]


def _validate(report, output, cutoff) -> list[Suggestion]:
    try:
        parsed = Output.model_validate(output)
    except ValidationError as exc:
        raise ModelFailure('MODEL_SCHEMA_INVALID') from exc
    available = [report.narrative or '', *_notes(report)]
    found = []
    seen = set()
    for item in parsed.candidates:
        try:
            if item.typedValue not in item.quote or not any(item.quote in text for text in available):
                raise ModelFailure('MODEL_SOURCE_INVALID')
            candidate = Suggestion(**item.model_dump(), sourceRef=_ref(report), quotePath='', quoteOffset=0)
            location = quote_location(report, candidate, cutoff)
        except (Failure, ValueError, OverflowError) as exc:
            raise ModelFailure('MODEL_SOURCE_INVALID') from exc
        key = item.model_dump_json()
        if key not in seen:
            seen.add(key)
            found.append(candidate.model_copy(update=dict(quotePath=location.path, quoteOffset=location.offset)))
    return found


def process(pool: Engine, work: Claim, model: Model | None, *, disabled_code='MODEL_UNAVAILABLE') -> None:
    with pool.begin() as c:
        p, row, report = _check(c, work.row)
        if row is None:
            return
        code = 'MODEL_LEASE_EXHAUSTED' if work.exhausted else disabled_code if model is None else None
        if code:
            finalize(c, p, row, status='manual', code=code)
            return
        source = {'narrative': report.narrative or '', 'notes': _notes(report), 'submittedAt': report.submitted_at.isoformat()}

    assert model is not None
    try:
        # Only narrative needs extraction. Typed report blocks already have deterministic readers.
        extracted = model.candidates(source, Output.model_json_schema(), retry=row.attempts > 1) if (
            source['narrative'] or source['notes']) else {'candidates': []}
        code = None
    except ModelFailure as exc:
        extracted, code = None, exc.code

    with pool.begin() as c:
        p, row, report = _check(c, work.row)
        if row is None:
            return
        candidates = []
        if code is None:
            try:
                candidates = _validate(report, extracted, row.requested_at)
            except ModelFailure as exc:
                code = exc.code
        if code in RETRYABLE and row.attempts < 2:
            c.execute(runs.update().where(r.id == row.id).values(status='queued', lease_id=None, leased_until=None))
        elif code:
            finalize(c, p, row, status='manual', code=code)
        else:
            finalize(c, p, row, status='ready', candidates=candidates)


def dispatch(pool: Engine, limit: int) -> int:
    processed = 0
    for _ in range(limit):
        work = claim(pool)
        if work is None:
            break
        process(pool, work, Gateway.from_env(), disabled_code='MODEL_UNAVAILABLE' if enabled() else 'MODEL_DISABLED')
        processed += 1
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--limit', type=int, choices=range(1, 101), default=10, metavar='1..100')
    args = parser.parse_args()
    try:
        while True:
            count = dispatch(engine(), args.limit)
            if count or not args.watch:
                print(f'Report analysis assignments processed: {count}', flush=True)
            if not args.watch:
                return 0
            if not count:
                sleep(2)
    except (SQLAlchemyError, KeyError):
        print('REPORT_ANALYSIS_DATABASE_FAILURE', flush=True)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
