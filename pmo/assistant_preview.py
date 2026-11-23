"""A preview receipt has no business write authority. Gateway calls never hold DB locks."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import sqlalchemy as sa

from . import assistant_model as model, model_gateway, schema_v1 as db
from .audit_store import audit, stable_refs
from .contracts import RULE_VERSION, Result, Strict
from .identity import Failure
from .projects import fingerprint, meta, run

OP = 'human_assistant_preview'
LEASE = timedelta(seconds=35)
i = db.idempotency_keys.c


class Preview(Strict):
    mode: model.Mode
    status: Literal['ready', 'empty', 'manual']
    basisHash: str
    items: list[model.Item]
    errorCode: str | None = None


@dataclass(frozen=True)
class Basis:
    parts: dict[str, model.Part]
    digest: str
    at: datetime

    @classmethod
    def from_parts(cls, parts, pid, revision, context, at):
        selected = model.prepare(parts)
        # Sampling a live projection again can change its calculation timestamp without changing its inputs.
        digest = fingerprint([str(pid), revision, RULE_VERSION, context,
            {key: part.model_dump(mode='json', exclude={'observedAt'}) for key, part in selected.items()}])
        return cls(selected, digest, at)

    @property
    def refs(self) -> list[str]:
        return stable_refs([ref for part in self.parts.values() for ref in part.sourceRefs])


def execute(authorization, pid, key, revision, command, authorize, collect) -> Result[Preview]:
    signature = fingerprint([str(pid), revision, command.model_dump(mode='json')])
    gateway = model_gateway.Gateway.for_previews()

    def context(c, actor):
        project = authorize(c, actor, pid, command.mode)
        match = (i.actor_type == actor.kind, i.actor_id == actor.credential, i.operation == OP, i.idempotency_key == key)
        receipt = c.execute(sa.select(db.idempotency_keys).where(*match)).mappings().one_or_none()
        if receipt and receipt.request_hash != signature:
            raise Failure(409, 'IDEMPOTENCY_CONFLICT', project_id=project.id)
        if project.revision != revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=project.id)
        at = c.scalar(sa.select(sa.func.clock_timestamp()))
        return project, receipt, collect(c, actor, project, at, command)

    def log(c, a, rid, receipt_id, basis, *, action=OP, result='success', code=None, digest=None):
        c.execute(audit.insert().values(actor_type=a.kind, actor_id=a.credential, org_id=a.org, project_id=pid,
            action=action, operation_id=receipt_id, request_id=rid, idempotency_key=key, before_hash=basis.digest,
            after_hash=digest, error_code=code, result=result, rule_version=RULE_VERSION, source_refs=basis.refs))

    def complete(c, a, rid, receipt_id, basis, *, status, items=(), code=None):
        info = meta(OP, stable_refs([ref for item in items for ref in item.sourceRefs]))
        info.asOf = basis.at
        result = Result[Preview](data=Preview(mode=command.mode, status=status, items=list(items),
            basisHash=basis.digest, errorCode=code), meta=info)
        payload = result.model_dump(mode='json', exclude_none=True)
        digest = fingerprint(payload)
        c.execute(db.idempotency_keys.update().where(i.id == receipt_id, i.status == 'pending').values(
            status='completed', completed_at=sa.func.clock_timestamp(), response_body=payload,
            response_hash=digest, response_status=200).returning(i.id)).scalar_one()
        log(c, a, rid, receipt_id, basis, result='failed' if code else 'success', code=code, digest=digest)
        return result

    def replay(receipt, basis):
        result = Result[Preview].model_validate(receipt.response_body)
        if result.data.basisHash != basis.digest:
            raise Failure(409, 'ASSISTANT_SOURCE_CHANGED', project_id=pid)
        return result

    def start(c, a, rid):
        _, prior, basis = context(c, a)
        cutoff = basis.at - LEASE
        if prior is not None:
            if prior.status == 'completed':
                return replay(prior, basis)
            if prior.created_at >= cutoff:
                raise Failure(409, 'REQUEST_IN_PROGRESS', project_id=pid)
            return complete(c, a, rid, prior.id, basis, status='manual', code='MODEL_INTERRUPTED')
        busy = c.scalar(sa.select(i.id).where(i.actor_type == a.kind, i.actor_id == a.credential,
            i.operation == OP, i.status == 'pending', i.created_at >= cutoff).limit(1))
        if busy is not None:
            raise Failure(409, 'REQUEST_IN_PROGRESS', project_id=pid)
        receipt_id = c.scalar(db.idempotency_keys.insert().values(actor_type=a.kind, actor_id=a.credential,
            org_id=a.org, operation=OP, resource_id=pid, idempotency_key=key, request_hash=signature, status='pending',
            created_at=basis.at, expires_at=basis.at + timedelta(days=90)).returning(i.id))
        if not model_gateway.previews_enabled():
            return complete(c, a, rid, receipt_id, basis, status='manual', code='MODEL_DISABLED')
        if not model_gateway.in_project_scope('ASSISTANT', pid):
            return complete(c, a, rid, receipt_id, basis, status='manual', code='MODEL_SCOPE_BLOCKED')
        if gateway is None:
            return complete(c, a, rid, receipt_id, basis, status='manual', code='MODEL_UNAVAILABLE')
        if not basis.parts:
            return complete(c, a, rid, receipt_id, basis, status='empty')
        log(c, a, rid, receipt_id, basis, action='assistant.preview.dispatch_authorized')
        return basis

    opened = run(authorization, OP, start)
    if isinstance(opened, Result):
        return opened
    assert gateway is not None

    def recheck(c, a, _):
        _, receipt, basis = context(c, a)
        if basis.digest != opened.digest:
            raise Failure(409, 'ASSISTANT_SOURCE_CHANGED', project_id=pid)
        return receipt, basis

    for attempt in range(2):
        try:
            items = model.generate(gateway, command.mode, opened.parts, getattr(command, 'topic', None), retry=bool(attempt))
            code = None
            break
        except model_gateway.ModelFailure as exc:
            items, code = [], exc.code
            if attempt or code not in ('MODEL_SCHEMA_INVALID', 'MODEL_SOURCE_INVALID'):
                break
            run(authorization, OP, recheck)

    def finish(c, a, rid):
        receipt, current = recheck(c, a, rid)
        if receipt.status == 'completed':
            return replay(receipt, current)
        return complete(c, a, rid, receipt.id, opened, status='manual' if code else 'ready' if items else 'empty',
                        items=items, code=code)

    return run(authorization, OP, finish)
