"""Human-controlled publication export and delivery to the isolated proof inbox."""
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field
from sqlalchemy.dialects.postgresql import insert

from . import reports as reporting, schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .identity import Failure
from .materials import replay
from .policy import guard as guard_policy, require as require_policy, synthetic_confirmation
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_member
from .weekly_report_document import render

EXPORT = 'human_weekly_report_export'
LIST = 'human_weekly_report_delivery_list'
QUEUE = 'human_weekly_report_delivery_queue'
DELIVER = 'report.weekly.delivery'
POLICY = 'report.delivery'
CHANNEL = 'synthetic_inbox'
router = APIRouter(prefix='/api/v1', tags=['Reports'])
PATH = '/reports/{reportId}/delivery'
sha = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
ts = sa.DateTime(timezone=True)

authorizations = sa.table('weekly_delivery_authorizations',
    *(sa.column(name, sa.Uuid) for name in ('id', 'project_id', 'approved_by')),
    *(sa.column(name, ts) for name in ('approved_at', 'expires_at', 'revoked_at')),
    *(sa.column(name, sa.String) for name in ('recipient_key', 'display_name', 'channel', 'approval_hash')))
deliveries = sa.table('weekly_deliveries',
    *(sa.column(name, sa.Uuid) for name in ('id', 'project_id', 'report_id', 'authorization_id', 'requested_by', 'provider_message_id')),
    *(sa.column(name, ts) for name in ('queued_at', 'updated_at', 'channel_received_at')),
    *(sa.column(name, sa.Integer) for name in ('report_version', 'report_revision')),
    *(sa.column(name, sa.String) for name in ('report_hash', 'attachment_hash', 'authorization_hash', 'recipient_key',
                                              'channel', 'status', 'error_code')))
inbox = sa.table('weekly_delivery_inbox', sa.column('message_id', sa.Uuid), sa.column('recipient_key', sa.String),
    sa.column('attachment_hash', sa.String), sa.column('attachment', sa.LargeBinary), sa.column('received_at', ts))


class Attachment(Strict):
    filename: str
    html: str
    sha256: sha


class Choice(Strict):
    authorizationId: UUID
    displayName: str
    channel: Literal['synthetic_inbox']
    expiresAt: datetime


class Send(Strict):
    authorizationId: UUID
    reportHash: sha
    attachmentHash: sha


class Delivery(Strict):
    deliveryId: UUID
    reportVersion: int
    reportRevision: int
    reportHash: sha
    attachmentHash: sha
    recipient: Choice
    status: Literal['pending', 'submitted', 'delivered', 'blocked', 'unknown', 'failed']
    queuedAt: datetime
    channelReceivedAt: datetime | None = None
    providerMessageId: UUID | None = None
    errorCode: str | None = None
    externalReceiptVerified: Literal[False] = False


class DeliveryView(Strict):
    recipients: list[Choice]
    deliveries: list[Delivery]
    channelReady: bool


def _now(c):
    return c.scalar(sa.select(sa.func.clock_timestamp()))


def _choice(approval) -> Choice:
    return Choice(authorizationId=approval.id, displayName=approval.display_name, channel=approval.channel,
                  expiresAt=approval.expires_at)


def _view(row, approval) -> Delivery:
    return Delivery(deliveryId=row.id, reportVersion=row.report_version, reportRevision=row.report_revision,
        reportHash=row.report_hash, attachmentHash=row.attachment_hash, recipient=_choice(approval), status=row.status,
        queuedAt=row.queued_at, providerMessageId=row.provider_message_id,
        channelReceivedAt=row.channel_received_at, errorCode=row.error_code)


def _publication(report, at, *, current):
    fields = (reporting.current_publication_fields if current else reporting.publication_fields)(report, at)
    if report.type != 'weekly' or fields is None:
        raise Failure(422, 'DELIVERY_REQUIRES_PUBLISHED_WEEKLY', project_id=report.project_id)
    return fields


def _authorization(c, p, approval_id):
    a = authorizations.c
    return c.execute(sa.select(authorizations).where(a.project_id == p.id, a.id == approval_id)).mappings().one_or_none()


def _valid(approval, now):
    return bool(approval and approval.approved_at <= now < approval.expires_at and approval.revoked_at is None)


def _can_approve(c, p, approval):
    return p.sponsor_id == approval.approved_by and p.pm_id != approval.approved_by and active_member(
        c, p, approval.approved_by, 'sponsor')


def _manage(c, actor, report_id, *, write=False):
    p, report, pm = reporting._context(c, actor, report_id=report_id, write=write)
    if not pm:
        raise Failure(403, 'FORBIDDEN', project_id=p.id)
    return p, report


def _artifact(report, fields):
    try:
        return render(report, fields)
    except (TypeError, ValueError, KeyError) as exc:
        raise Failure(422, 'REPORT_CONTENT_INVALID', project_id=report.project_id) from exc


@router.get('/reports/{reportId}/weekly-export', operation_id=EXPORT, openapi_extra=reporting.HUMAN)
def export(reportId: UUID, authorization: Auth) -> Result[Attachment]:
    def read(c, a, _):
        _, report, _ = reporting._context(c, a, report_id=reportId)
        fields = _publication(report, _now(c), current=False)
        filename, attachment, digest = _artifact(report, fields)
        return Result[Attachment](data=Attachment(filename=filename, html=attachment.decode('utf-8'), sha256=digest),
                                  meta=meta(EXPORT, [reporting._ref(report)]))
    return run(authorization, EXPORT, read)


@router.get(PATH, operation_id=LIST, response_model_exclude_none=True, openapi_extra=reporting.HUMAN)
def listing(reportId: UUID, authorization: Auth) -> Result[DeliveryView]:
    def read(c, a, _):
        p, report = _manage(c, a, reportId)
        now = _now(c)
        _publication(report, now, current=False)
        d, auth = deliveries.c, authorizations.c
        prior = c.execute(sa.select(deliveries).where(d.project_id == p.id, d.report_id == report.id)
                          .order_by(d.queued_at.desc(), d.id)).mappings()
        history = [_view(row, _authorization(c, p, row.authorization_id)) for row in prior]
        available = p.stage != 'archived' and report.is_current_confirmed and synthetic_confirmation(c)
        choices = []
        if available:
            rows = c.execute(sa.select(authorizations).where(auth.project_id == p.id, auth.revoked_at.is_(None),
                auth.approved_at <= now, auth.expires_at > now).order_by(auth.display_name, auth.id)).mappings()
            seen = {item.recipient.authorizationId for item in history}
            choices = [_choice(approval) for approval in rows if approval.id not in seen and _can_approve(c, p, approval)]
        return Result[DeliveryView](data=DeliveryView(recipients=choices, deliveries=history, channelReady=available),
                                    meta=meta(LIST, [reporting._ref(report)]))
    return run(authorization, LIST, read)


@router.post(PATH, operation_id=QUEUE, response_model_exclude_none=True, openapi_extra=reporting.HUMAN)
def queue(reportId: UUID, command: Send, authorization: Auth, key: Key, expected: Revision) -> Result[Delivery]:
    signature = fingerprint([str(reportId), int(expected), command.model_dump(mode='json')])

    def write(c, a, rid):
        p, report = _manage(c, a, reportId, write=True)
        approval = _authorization(c, p, command.authorizationId)
        if not _valid(approval, _now(c)) or not _can_approve(c, p, approval):
            raise Failure(403, 'RECIPIENT_AUTHORIZATION_INVALID', project_id=p.id)
        if p.stage == 'archived':
            raise Failure(422, 'ARCHIVED_READ_ONLY', project_id=p.id)
        guard_policy(c, p, POLICY)
        if not synthetic_confirmation(c):
            raise Failure(403, 'DELIVERY_CHANNEL_NOT_CONFIGURED', project_id=p.id)
        if prior := replay(c, a, QUEUE, key, signature, Result[Delivery]):
            return prior
        require_policy(c, p, POLICY, rid)
        if int(expected) != report.revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        fields = _publication(report, _now(c), current=True)
        _, _, digest = _artifact(report, fields)
        if (command.reportHash, command.attachmentHash) != (report.content_hash, digest):
            raise Failure(409, 'DELIVERY_CONTENT_CHANGED', project_id=p.id)
        d = deliveries.c
        prior = c.execute(sa.select(deliveries).where(d.report_id == report.id, d.report_revision == report.revision,
            d.recipient_key == approval.recipient_key, d.channel == approval.channel)).mappings().one_or_none()
        if prior is None:
            row = c.execute(deliveries.insert().values(project_id=p.id, report_id=report.id, report_version=report.version_no,
                report_revision=report.revision, report_hash=report.content_hash, attachment_hash=digest, authorization_id=approval.id,
                authorization_hash=approval.approval_hash, recipient_key=approval.recipient_key,
                channel=approval.channel, requested_by=a.person, status='pending').returning(*d)).mappings().one()
            c.execute(db.outbox_events.insert().values(event_type=DELIVER, dedupe_key=f'weekly-delivery:{row.id}',
                status='pending', payload={'projectId': str(p.id), 'deliveryId': str(row.id)}))
        else:
            if prior.authorization_id != approval.id or prior.authorization_hash != approval.approval_hash:
                raise Failure(409, 'DELIVERY_RECIPIENT_ALREADY_QUEUED', project_id=p.id)
            row = prior
        result = Result[Delivery](data=_view(row, approval), meta=meta(QUEUE,
            [reporting._ref(report), f'weekly-delivery:{row.id}', f'weekly-delivery-authorization:{approval.id}']))
        body = result.model_dump(mode='json', exclude_none=True)
        record_receipt(c, a, key, QUEUE, rid, row.id, p.id, signature, body, fingerprint(body))
        return result
    return run(authorization, QUEUE, write)


def _still_assigned(c, p, person, role) -> bool:
    m, n, u = db.project_members.c, db.persons.c, db.user_accounts.c
    return c.scalar(sa.select(sa.exists(sa.select(m.person_id).join(db.persons, n.id == m.person_id)
        .join(db.user_accounts, u.id == n.user_id).where(m.project_id == p.id, m.person_id == person, m.role == role,
            m.assignment_status == 'accepted', m.accepted_at.is_not(None), m.revoked_at.is_(None), n.org_id == p.org_id,
            n.is_active, u.is_active, sa.or_(u.locked_until.is_(None), u.locked_until <= sa.func.now())))))


def _state(c, delivery_id, status, **values):
    c.execute(deliveries.update().where(deliveries.c.id == delivery_id)
              .values(status=status, updated_at=sa.func.clock_timestamp(), **values))


def deliver(c, payload):
    """The inbox insert and verified synthetic receipt share a commit with consumption."""
    delivery_id, pid = UUID(payload['deliveryId']), UUID(payload['projectId'])
    if not synthetic_confirmation(c):
        raise ValueError('Synthetic inbox not available outside isolated pilot')
    row = c.execute(sa.select(deliveries).where(deliveries.c.id == delivery_id, deliveries.c.project_id == pid)
                    .with_for_update()).mappings().one_or_none()
    if row is None:
        raise ValueError('Unknown report delivery')
    if row.status != 'pending':
        return
    project = db.projects.c
    p = c.execute(sa.select(project.id, project.org_id, project.pm_id, project.sponsor_id, project.stage)
                  .where(project.id == pid)).mappings().one()
    r = reporting.r
    report = c.execute(sa.select(r.id, r.project_id, r.type, r.version_no, r.revision, r.content_hash, r.status,
        r.is_current_confirmed, r.confirmed_by, r.confirmed_at, r.submitted_by, r.submitted_at, r.author_id,
        r.structured_fields, r.narrative, r.period, r.period_start, r.period_end, r.weekly_basis)
        .where(r.project_id == pid, r.id == row.report_id)).mappings().one()
    approval = _authorization(c, p, row.authorization_id)
    at = _now(c)
    if (not _valid(approval, at) or approval.approval_hash != row.authorization_hash or
            approval.recipient_key != row.recipient_key or approval.channel != CHANNEL or
            p.sponsor_id != approval.approved_by or not _still_assigned(c, p, approval.approved_by, 'sponsor')):
        _state(c, row.id, 'blocked', error_code='RECIPIENT_AUTHORIZATION_INVALID')
        return
    if p.pm_id != row.requested_by or not _still_assigned(c, p, row.requested_by, 'pm') or p.stage == 'archived':
        _state(c, row.id, 'blocked', error_code='DELIVERY_REQUESTER_NO_LONGER_AUTHORIZED')
        return
    try:
        fields = _publication(report, at, current=True)
        _, data, digest = _artifact(report, fields)
    except Failure as exc:
        _state(c, row.id, 'blocked', error_code=exc.code)
        return
    if (report.version_no, report.revision, report.content_hash, digest) != (
            row.report_version, row.report_revision, row.report_hash, row.attachment_hash):
        _state(c, row.id, 'blocked', error_code='DELIVERY_CONTENT_CHANGED')
        return
    c.execute(insert(inbox).values(message_id=row.id, recipient_key=row.recipient_key, attachment=data,
                                   attachment_hash=digest).on_conflict_do_nothing())
    saved = c.execute(sa.select(inbox).where(inbox.c.message_id == row.id)).mappings().one()
    if (saved.recipient_key, saved.attachment_hash, sha256(saved.attachment).hexdigest()) != (row.recipient_key, digest, digest):
        _state(c, row.id, 'unknown', error_code='DELIVERY_RECEIPT_MISMATCH')
        return
    _state(c, row.id, 'delivered', provider_message_id=saved.message_id, channel_received_at=saved.received_at)


def dead_letter(c, payload):
    delivery_id = UUID(payload['deliveryId'])
    row = c.scalar(sa.select(deliveries.c.id).where(deliveries.c.id == delivery_id,
        deliveries.c.project_id == UUID(payload['projectId']), deliveries.c.status == 'pending').with_for_update())
    if row is not None:
        _state(c, row, 'failed', error_code='DELIVERY_DEAD_LETTER')
