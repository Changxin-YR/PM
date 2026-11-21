"""Queue project notifications; delivery remains an at-least-once Outbox concern."""
from sqlalchemy.dialects.postgresql import insert

from .schema_v1 import outbox_events


def notify(c, pid, event_id, recipient):
    if recipient is None:
        return
    c.execute(insert(outbox_events).values(event_type='rule.notify',
        dedupe_key=f'notify:{event_id}:{recipient}', status='pending',
        payload={'projectId': str(pid), 'eventId': str(event_id), 'recipientId': str(recipient)})
        .on_conflict_do_nothing(index_elements=[outbox_events.c.dedupe_key]))
