"""Source validation shared by interactive and background Fact proposals."""
from datetime import UTC, date, datetime
from decimal import Decimal
import re
from typing import Annotated, Literal, NamedTuple
from unicodedata import normalize
from uuid import UUID

import sqlalchemy as sa
from pydantic import AfterValidator, AwareDatetime, Field, StrictBool, StrictInt, StrictStr, field_validator

from . import evidence_storage, schema_v1 as db
from .contracts import Strict
from .evidence import stmt as evidence_query, links
from .identity import Failure
from .projects import fingerprint
from .reports import _content, _plain, _ref
from .requirement_baselines import active_member

Short = Annotated[StrictStr, Field(min_length=1, max_length=256, pattern=r'\S'), AfterValidator(_plain)]
Quote = Annotated[StrictStr, Field(min_length=1, max_length=3000, pattern=r'\S'), AfterValidator(_plain)]
Number = Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)]
Value = Quote | Number | StrictBool
Trust = Literal['high', 'medium', 'low']
Status = Literal['candidate', 'verified', 'disputed', 'rejected']


class CandidateFields(Strict):
    subject: Short
    predicate: Annotated[StrictStr, Field(min_length=1, max_length=120, pattern=r'\S'), AfterValidator(_plain)]
    typedValue: Value
    asOf: AwareDatetime
    quote: Quote

    @field_validator('asOf', mode='before')
    @classmethod
    def iso_time(cls, value):
        if not isinstance(value, (str, datetime)) or (isinstance(value, str) and value[10:11].upper() != 'T'):
            raise ValueError('Expected an ISO timestamp with timezone')
        return value


class Candidate(CandidateFields):
    sourceRef: Annotated[Short, Field(min_length=3)]


class Location(NamedTuple):
    path: str
    offset: int


def _leaves(value, path):
    if isinstance(value, dict):
        for key in sorted(value):
            escaped = key.replace('~', '~0').replace('/', '~1')
            yield from _leaves(value[key], f'{path}/{escaped}')
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            yield from _leaves(item, f'{path}/{idx}')
    elif isinstance(value, str):
        yield path, value


def locate(report, quote: str) -> Location | None:
    narrative = report.narrative or ''
    if (index := narrative.find(quote)) >= 0:
        return Location('/narrative', index)
    for path, text in _leaves(report.structured_fields, '/structuredFields'):
        if (index := text.find(quote)) >= 0:
            return Location(path, index)
    return None


def submitted(report) -> bool:
    return report.status in ('submitted', 'confirmed') and bool(
        report.submitted_by and report.submitted_at and _content(report) is not None)


def quote_location(report, command, now):
    if not submitted(report):
        raise Failure(422, 'FACT_REPORT_NOT_SUBMITTED', project_id=report.project_id)
    if command.asOf > now:
        raise Failure(422, 'FUTURE_AS_OF', project_id=report.project_id)
    if command.sourceRef != _ref(report) or (location := locate(report, command.quote)) is None:
        raise Failure(422, 'FACT_SOURCE_MISMATCH', project_id=report.project_id)
    if normal_value(command.typedValue)[0] == 'decimal' and not concrete(command.typedValue, command.quote):
        raise Failure(422, 'FACT_VALUE_NOT_IN_QUOTE', project_id=report.project_id)
    return location


def recheck(report, fact):
    saved = Location(fact.quote_path, fact.quote_offset)
    if (report.content_hash != fact.content_hash or report.revision < fact.report_revision
            or not submitted(report) or locate(report, fact.quote) != saved):
        raise Failure(409, 'FACT_SOURCE_STALE', project_id=fact.project_id)


def group_key(pid: UUID, command: Candidate) -> str:
    labels = [' '.join(normalize('NFKC', text).casefold().split()) for text in (command.subject, command.predicate)]
    return fingerprint([str(pid), *labels, command.asOf.astimezone(UTC).isoformat()])


def normal_value(value: str | int | bool):
    # Compare exact fixed-point values without binary floats or treating True as 1.
    if not isinstance(value, bool) and re.fullmatch(r'-?[0-9]{1,35}(?:\.[0-9]{1,18})?', str(value)):
        exact = format(Decimal(str(value)), 'f')
        if '.' in exact:
            exact = exact.rstrip('0').rstrip('.')
        return ['decimal', '0' if exact == '-0' else exact]
    return ['boolean', value] if isinstance(value, bool) else ['text', value]


def concrete(value: Value, quote: str) -> bool:
    kind, number = normal_value(value)
    if kind == 'decimal':
        mentions = re.findall(r'(?<![0-9.\-])-?[0-9]+(?:\.[0-9]+)?(?![0-9.]|[eE][+\-]?[0-9])', quote)
        return any(normal_value(mention) == [kind, number] for mention in mentions)
    if isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return value in quote
    return False


def can_propose(c, actor, project, report=None) -> bool:
    if project.stage == 'archived':
        return False
    pm = project.pm_id == actor.person and active_member(c, project, actor.person, 'pm')
    return pm or (actor.kind == 'human' and report is not None and report.author_id == actor.person
                  and active_member(c, project, actor.person, 'member'))


def high_evidence(c, project_id, report_id, ref):
    ev = db.evidence_files.c
    try:
        evidence_id = UUID(ref.removeprefix('evidence:')) if ref.startswith('evidence:') else None
    except ValueError:
        evidence_id = None
    if evidence_id is None:
        raise Failure(422, 'FACT_OBJECTIVE_EVIDENCE_REQUIRED', project_id=project_id)
    evidence = c.execute(evidence_query().where(ev.id == evidence_id, ev.project_id == project_id,
        links.c.resource_type == 'report', links.c.resource_id == report_id,
        ev.quarantine_status == 'verified', ev.verified_at.is_not(None), ev.verified_by != ev.uploaded_by)
        .with_for_update(of=db.evidence_files, read=True)).mappings().one_or_none()
    if evidence is None:
        raise Failure(422, 'FACT_OBJECTIVE_EVIDENCE_REQUIRED', project_id=project_id)
    evidence_storage.checked_bytes(evidence.storage_key, evidence.sha256, evidence.size_bytes)
    return f'evidence:{evidence.id}', evidence.sha256


def verified_source(c, report, row) -> bool:
    if row.validation_status != 'verified' or row.trust not in ('high', 'medium'):
        return False
    try:
        recheck(report, row)
        if row.trust == 'high':
            return high_evidence(c, row.project_id, report.id, row.evidence_ref or '') == (row.evidence_ref, row.evidence_hash)
    except Failure as exc:
        if exc.code not in ('FACT_SOURCE_STALE', 'FACT_OBJECTIVE_EVIDENCE_REQUIRED', 'EVIDENCE_INTEGRITY_FAILED'):
            raise
        return False
    return True
