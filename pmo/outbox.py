"""Bounded local delivery worker. A committed receipt survives a lost acknowledgement."""
import argparse
import json
from dataclasses import dataclass
from time import sleep
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from .db import engine
from .org_events import ORGANIZATION_EVENTS
from .observability import emit
from .weekly_delivery import DELIVER as WEEKLY_DELIVERY, dead_letter as weekly_dead_letter, deliver as deliver_weekly

LEASE_SECONDS = 30
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Lease:
    event_id: UUID
    attempt: int
    event_type: str = ''


def received(c: Connection, event_id: UUID) -> bool:
    return bool(c.scalar(text('''SELECT EXISTS(SELECT FROM project_event_inbox WHERE event_id=:id)
        OR EXISTS(SELECT FROM organization_event_inbox WHERE event_id=:id)'''), {'id': event_id}))


def finish_attempt(c: Connection, lease: Lease, outcome: str) -> None:
    c.execute(text('''UPDATE outbox_attempts SET outcome=:outcome,finished_at=clock_timestamp()
        WHERE event_id=:id AND attempt=:attempt AND outcome='processing' '''),
        dict(id=lease.event_id, attempt=lease.attempt, outcome=outcome))


def claim(db: Engine) -> Lease | None:
    with db.begin() as c:
        while True:
            row = c.execute(text('''SELECT id,status,retry_count,event_type,payload FROM outbox_events
                WHERE (status='pending' AND next_attempt_at<=clock_timestamp()) OR
                      (status='processing' AND locked_until<=clock_timestamp())
                ORDER BY next_attempt_at,id LIMIT 1 FOR UPDATE SKIP LOCKED''')).mappings().one_or_none()
            if row is None:
                return None
            eid, previous = row['id'], row['retry_count']
            if row['status']=='processing':
                prior = Lease(eid, previous)
                if received(c, eid):
                    mark_sent(c, prior)
                    continue
                finish_attempt(c, prior, 'lease_expired')
                if previous >= MAX_ATTEMPTS:
                    c.execute(text("UPDATE outbox_events SET status='dead_letter',last_error='LEASE_EXHAUSTED',locked_until=NULL,updated_at=now() WHERE id=:id"), {'id':eid})
                    _weekly_dead_letter(c, row)
                    continue
            lease = Lease(eid, previous + 1, row['event_type'])
            c.execute(text("""UPDATE outbox_events SET status='processing',retry_count=:n,
                locked_until=clock_timestamp()+:seconds*interval '1 second',updated_at=now() WHERE id=:id"""),
                dict(id=eid, n=lease.attempt, seconds=LEASE_SECONDS))
            c.execute(text("INSERT INTO outbox_attempts(event_id,attempt,outcome) VALUES(:id,:n,'processing')"),
                      dict(id=eid,n=lease.attempt))
            return lease


def current(c: Connection, lease: Lease) -> RowMapping | None:
    return c.execute(text("""SELECT id,event_type,payload,dedupe_key FROM outbox_events WHERE id=:id
        AND retry_count=:attempt AND status='processing' AND locked_until>clock_timestamp() FOR UPDATE"""),
        dict(id=lease.event_id, attempt=lease.attempt)).mappings().one_or_none()


def consume(db: Engine, lease: Lease) -> bool:
    # A local inbox receipt must be consistent with publication and scope checks.
    source = db.execution_options(isolation_level='SERIALIZABLE') if lease.event_type == WEEKLY_DELIVERY else db
    with source.begin() as c:
        row = current(c, lease)
        if row is None:
            return False
        if row['event_type'] in ORGANIZATION_EVENTS:
            org = UUID(row['payload']['orgId'])
            UUID(row['payload']['resourceId'])
            if 'projectId' in row['payload']:
                raise ValueError('Organization events cannot have project scope')
            c.execute(text('''INSERT INTO organization_event_inbox(dedupe_key,event_id,org_id,event_type)
                VALUES(:key,:id,:org,:kind) ON CONFLICT(dedupe_key) DO NOTHING'''),
                dict(key=row['dedupe_key'], id=lease.event_id, org=org, kind=row['event_type']))
            return True
        pid = UUID(row['payload']['projectId'])
        if row['event_type'] == WEEKLY_DELIVERY:
            deliver_weekly(c, row['payload'])
        if row['event_type']=='rule.notify':
            event=UUID(row['payload']['eventId']); person=UUID(row['payload']['recipientId'])
            args=dict(id=event,pid=pid,person=person)
            if not c.scalar(text('SELECT id FROM rule_events WHERE id=:id AND project_id=:pid FOR UPDATE'),args):
                raise ValueError('Unknown rule event')
            c.execute(text('''INSERT INTO notifications(project_id,rule_event_id,recipient_person_id,delivery_status,delivered_at,payload)
                SELECT :pid,:id,:person,'delivered',now(),jsonb_build_object('ruleId',rule_id,'sourceRefs',source_refs)
                FROM rule_events WHERE id=:id ON CONFLICT(rule_event_id,recipient_person_id) WHERE rule_event_id IS NOT NULL DO NOTHING'''),args)
            c.execute(text('UPDATE rule_events SET notified_at=coalesce(notified_at,now()) WHERE id=:id'),args)
        c.execute(text('''INSERT INTO project_event_inbox(dedupe_key,event_id,project_id,event_type)
            VALUES(:key,:id,:pid,:kind) ON CONFLICT(dedupe_key) DO NOTHING'''),
            dict(key=row['dedupe_key'], id=lease.event_id, pid=pid, kind=row['event_type']))
        return True


def mark_sent(c: Connection, lease: Lease) -> None:
    c.execute(text("""UPDATE outbox_events SET status='sent',published_at=now(),locked_until=NULL,
        last_error=NULL,updated_at=now() WHERE id=:id"""), dict(id=lease.event_id))
    finish_attempt(c, lease, 'sent')


def acknowledge(db: Engine, lease: Lease) -> bool:
    with db.begin() as c:
        if current(c, lease) is None or not received(c, lease.event_id):
            return False
        mark_sent(c, lease)
        return True


def fail(db: Engine, lease: Lease, *, invalid: bool) -> None:
    with db.begin() as c:
        row = current(c, lease)
        if row is None:
            return
        if received(c, lease.event_id):
            mark_sent(c, lease)
            return
        dead = lease.attempt >= MAX_ATTEMPTS
        code = 'INVALID_PROJECT_EVENT' if invalid else 'DELIVERY_FAILED'
        c.execute(text('''UPDATE outbox_events SET status=:status,last_error=:code,locked_until=NULL,
            updated_at=now(),next_attempt_at=clock_timestamp()+:delay*interval '1 second' WHERE id=:id'''),
            dict(id=lease.event_id, status='dead_letter' if dead else 'pending',
                 code=code, delay=min(300,2**lease.attempt)))
        finish_attempt(c, lease, 'dead_letter' if dead else 'retry')
        if dead:
            _weekly_dead_letter(c, row)
    emit('outbox.dead_letter' if dead else 'outbox.retry', 'error' if dead else 'warning',
         eventId=str(lease.event_id), attempt=lease.attempt, code=code)


def _weekly_dead_letter(c, event):
    if event['event_type'] == WEEKLY_DELIVERY:
        try:
            weekly_dead_letter(c, event['payload'])
        except (ValueError, KeyError, TypeError):
            pass  # Invalid payloads cannot identify a delivery to update.


def dispatch(db: Engine, limit: int) -> int:
    count = 0
    for _ in range(limit):
        lease = claim(db)
        if lease is None:
            break
        try:
            if consume(db, lease) and acknowledge(db, lease):
                count += 1
        except (ValueError, KeyError, TypeError, AttributeError):
            fail(db, lease, invalid=True)
        except SQLAlchemyError:
            fail(db, lease, invalid=False)
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--dead-letters', action='store_true')
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error('--limit must be 1..500')
    if args.dead_letters and args.watch:
        parser.error('--dead-letters and --watch are mutually exclusive')
    try:
        db = engine()
        if args.dead_letters:
            with db.connect() as c:
                rows = c.execute(text("SELECT id,event_type,retry_count,last_error FROM outbox_events WHERE status='dead_letter' ORDER BY updated_at,id LIMIT :n"), {'n':args.limit}).mappings()
                print(json.dumps([dict(row) for row in rows], default=str))
            return 0
        while True:
            count = dispatch(db, args.limit)
            if not args.watch:
                print(json.dumps({'acknowledged': count}))
                return 0
            if count:
                emit('outbox.batch', acknowledged=count)
            if count < args.limit:
                sleep(2)
    except (SQLAlchemyError, KeyError):
        emit('outbox.failed', 'error', code='OUTBOX_DATABASE_FAILURE')
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
