"""Common transaction checks for project planning material, not a generic write API."""
import sqlalchemy as sa
from . import schema_v1 as db
from .identity import Failure, visible
from .projects import fingerprint, record_write


def project_access(c, actor, pid, *, edit=False):
    p = db.projects
    stmt = sa.select(p).where(p.c.id == pid, visible(actor))
    project = c.execute(stmt.with_for_update() if edit else stmt).mappings().one_or_none()
    if project is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    if not edit:
        return project
    if project.stage not in ('drafting', 'designing'):
        raise Failure(422, 'BASELINE_FROZEN')
    if project.stage == 'drafting' and project.initiator_id == actor.person:
        return project
    m = db.project_members
    pm = c.scalar(sa.select(m.c.id).where(m.c.project_id == pid, m.c.person_id == actor.person,
                  m.c.role == 'pm', m.c.assignment_status == 'accepted', m.c.accepted_at.is_not(None), m.c.revoked_at.is_(None)))
    if not pm or project.pm_id != actor.person:
        raise Failure(403, 'FORBIDDEN')
    return project


def replay(c, actor, op, key, signature, model):
    i = db.idempotency_keys
    row = c.execute(sa.select(i.c.request_hash, i.c.response_body).where(i.c.actor_id == actor.credential,
                    i.c.operation == op, i.c.idempotency_key == key)).mappings().one_or_none()
    if row is None:
        return None
    if row.request_hash != signature:
        raise Failure(409, 'IDEMPOTENCY_CONFLICT')
    return model.model_validate(row.response_body)


def finish(c, actor, op, key, request_id, pid, signature, result, before=None):
    body = result.model_dump(mode='json', exclude_none=True)
    record_write(c, actor, key, op, request_id, pid, signature, body, fingerprint(body), op, before)
    return result
