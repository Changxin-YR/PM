"""Scoped Fact writes. The caller holds the project lock through audit and Outbox."""
from datetime import timedelta
from uuid import UUID

import sqlalchemy as sa

from . import schema_v1 as db
from .fact_sources import group_key, normal_value, quote_location
from .identity import Failure
from .projects import fingerprint
from .requirement_baselines import active_member

facts = db.facts
f = facts.c
sources = sa.table('fact_source_receipts', sa.column('fact_id', sa.Uuid), sa.column('report_revision', sa.BigInteger),
    sa.column('content_hash'), sa.column('quote_path'), sa.column('quote_offset', sa.Integer),
    sa.column('group_key'), sa.column('proposal_hash'))
s = sources.c
journal = sa.table('fact_history', sa.column('fact_id', sa.Uuid), sa.column('revision', sa.BigInteger),
    sa.column('action'), sa.column('actor_id', sa.Uuid), sa.column('request_id', sa.Uuid),
    sa.column('occurred_at', sa.DateTime(timezone=True)), sa.column('validation_status'), sa.column('trust'),
    sa.column('reason'), sa.column('evidence_ref'), sa.column('evidence_hash'))
j = journal.c
cases = db.fact_clarifications
k = cases.c


def query():
    return sa.select(facts, s.report_revision, s.content_hash, s.quote_path, s.quote_offset, s.group_key,
        j.reason.label('history_reason'), j.evidence_ref, j.evidence_hash).select_from(
        facts.join(sources, s.fact_id == f.id).join(journal, sa.and_(j.fact_id == f.id, j.revision == f.revision)))


def one(c, fact_id):
    return c.execute(query().where(f.id == fact_id)).mappings().one()


def fact_ref(row):
    return f'fact:{row.id}@{row.revision}'


def members(c, project, group):
    return list(c.execute(query().where(f.project_id == project.id, s.group_key == group,
        f.validation_status != 'rejected').order_by(f.created_at, f.id)).mappings())


def open_case(c, group):
    return c.execute(sa.select(cases).join(sources, s.fact_id == k.fact_id).where(s.group_key == group,
        k.status == 'open').order_by(k.created_at, k.id).limit(1)).mappings().one_or_none()


def escalate(c, project, fact, actor, why):
    case = open_case(c, fact.group_key)
    if case is None:
        case = c.execute(cases.insert().values(fact_id=fact.id, requested_by=actor, reason=why,
            status='open', source_ref=fact.source_ref).returning(cases)).mappings().one()
    t = db.tasks
    if (project.pm_id and active_member(c, project, project.pm_id, 'pm') and not c.scalar(
            sa.select(t.c.id).where(t.c.project_id == project.id, t.c.source_type == 'fact_clarification',
                t.c.source_id == case.id).limit(1))):
        at = c.scalar(sa.select(sa.func.clock_timestamp()))
        c.execute(t.insert().values(project_id=project.id, source_type='fact_clarification', source_id=case.id,
            title='Fact clarification', assignee_id=project.pm_id, created_by=actor,
            due_at=at + timedelta(days=7), status='todo'))


def record(c, row, actor_id, rid, action, reason, evidence_ref=None, evidence_hash=None):
    c.execute(journal.insert().values(fact_id=row.id, revision=row.revision, actor_id=actor_id, request_id=UUID(rid),
        action=action, reason=reason, validation_status=row.validation_status, trust=row.trust,
        evidence_ref=evidence_ref, evidence_hash=evidence_hash))


def revise(c, row, actor, rid, *, status, trust, action, reason, evidence_ref=None, evidence_hash=None):
    value = c.execute(facts.update().where(f.id == row.id, f.project_id == row.project_id, f.revision == row.revision)
        .values(validation_status=status, trust=trust, revision=row.revision+1,
            updated_at=sa.func.clock_timestamp()).returning(facts)).mappings().one()
    record(c, value, actor, rid, action, reason, evidence_ref, evidence_hash)
    return one(c, row.id)


def propose_locked(c, project, report, command, actor_id, rid):
    location = quote_location(report, command, c.scalar(sa.select(sa.func.clock_timestamp())))
    group = group_key(project.id, command)
    value = normal_value(command.typedValue)
    digest = fingerprint([group, str(report.id), report.content_hash, location, command.quote, value])
    duplicate = c.scalar(sa.select(s.fact_id).where(s.proposal_hash == digest))
    if duplicate:
        return one(c, duplicate)
    rows = members(c, project, group)
    # Bound a single contention group. A differently worded quote does not let a client grow the work unboundedly.
    if len(rows) >= 50:
        raise Failure(422, 'FACT_GROUP_LIMIT', project_id=project.id)
    conflict = any(normal_value(row.value) != value for row in rows)
    row = c.execute(facts.insert().values(project_id=project.id, report_id=report.id, created_by=actor_id,
        subject=command.subject, predicate=command.predicate, as_of=command.asOf, quote=command.quote,
        source_ref=command.sourceRef, value=command.typedValue, trust='low',
        validation_status='disputed' if conflict else 'candidate').returning(facts)).mappings().one()
    c.execute(sources.insert().values(fact_id=row.id, report_revision=report.revision, quote_path=location.path,
        quote_offset=location.offset, content_hash=report.content_hash, group_key=group, proposal_hash=digest))
    record(c, row, actor_id, rid, 'proposed', 'FACT_CONFLICT' if conflict else 'SOURCE_LOCATED')
    added = one(c, row.id)
    if conflict:
        for prior in rows:
            if prior.validation_status != 'disputed' or prior.trust != 'low':
                revise(c, prior, actor_id, rid, status='disputed', trust='low', action='disputed', reason='FACT_CONFLICT')
        escalate(c, project, added, actor_id, 'FACT_CONFLICT')
    return added


def close_case(c, project, case, actor, source):
    c.execute(cases.update().where(k.id == case.id, k.status == 'open').values(status='resolved',
        source_ref=source, resolved_by=actor, resolved_at=sa.func.clock_timestamp()))
    t = db.tasks
    c.execute(t.update().where(t.c.project_id == project.id, t.c.source_type == 'fact_clarification',
        t.c.source_id == case.id, t.c.status.in_(('todo','in_progress','blocked'))).values(status='done', revision=t.c.revision+1,
        completed_at=sa.func.clock_timestamp(), updated_at=sa.func.clock_timestamp()))
