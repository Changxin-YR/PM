"""Meeting records for human review coordination, separate from scoring and decisions."""
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import AwareDatetime, Field, field_validator

from . import schema_v1 as db
from .contracts import Result, Strict
from .gates import REVIEW, submitter
from .identity import Failure
from .materials import finish, replay
from .projects import fingerprint, meta, run
from .review_outcomes import context

READ = 'human_review_meeting_get'
WRITE = 'human_review_meeting_put'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
DECIDED = ('pass', 'conditional_pass', 'defer', 'redirect', 'reject')
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ScheduleMeeting(Strict):
    action: Literal['schedule']
    scheduledAt: AwareDatetime | None
    attendeeIds: list[UUID] = Field(max_length=32)

    @field_validator('attendeeIds')
    @classmethod
    def distinct_attendees(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError('Attendees must be unique')
        return values


class RecordMinutes(Strict):
    action: Literal['minutes']
    minutes: str = Field(min_length=1, max_length=8000, pattern=r'\S')

    @field_validator('minutes')
    @classmethod
    def nonempty(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('Minutes must not contain NUL')
        return value.strip()


MeetingCommand = Annotated[ScheduleMeeting | RecordMinutes, Field(discriminator='action')]


class Attendee(Strict):
    personId: UUID
    name: str


class Meeting(Strict):
    reviewId: UUID
    revision: int
    scheduledAt: datetime | None = None
    attendeeIds: list[UUID]
    attendeeOptions: list[Attendee]
    minutes: str | None = None
    canSchedule: bool
    canRecordMinutes: bool


def _allowed(c, actor, project, review):
    owner = actor.kind == 'human' and submitter(c, project, actor.person, review.type)
    if not project.materials_visible and not owner:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return (
        owner and review.status == 'pending' and project.stage == REVIEW[review.type],
        owner and project.stage != 'archived' and review.status == 'decided' and review.verdict in DECIDED,
    )


def _people(c, project, *, roster=False, person_ids=()):
    people, members = db.persons.c, db.project_members.c
    statement = sa.select(people.id, people.name).where(people.org_id == project.org_id)
    if roster:
        statement = statement.where(people.is_active, sa.exists(sa.select(members.id).where(
            members.project_id == project.id, members.person_id == people.id, members.assignment_status == 'accepted',
            members.accepted_at.is_not(None), members.revoked_at.is_(None))))
    else:
        statement = statement.where(people.id.in_(person_ids))
    return [Attendee(personId=row.id, name=row.name) for row in c.execute(
        statement.order_by(people.name, people.id)).mappings()]


def _revision(review) -> int:
    # Meeting edits must not change the reviewed version cited by a formal decision.
    elapsed = review.updated_at.astimezone(UTC) - EPOCH
    return max(1, (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds)


def _attendee_ids(review) -> list[UUID]:
    ids = []
    if isinstance(review.attendees, list):
        for value in review.attendees:
            if not isinstance(value, str):
                continue
            try:
                ids.append(UUID(value))
            except ValueError:
                continue
    return ids


def _view(c, project, review, allowed):
    can_schedule, can_record_minutes = allowed
    ids = _attendee_ids(review)
    people = _people(c, project, roster=can_schedule, person_ids=ids)
    if can_schedule:
        known_ids = {person.personId for person in people}
        people.extend(_people(c, project, person_ids=[person_id for person_id in ids if person_id not in known_ids]))
    scheduled_at = review.scheduled_at.astimezone(UTC) if review.scheduled_at else None
    return Meeting(reviewId=review.id, revision=_revision(review), scheduledAt=scheduled_at, attendeeIds=ids,
        attendeeOptions=people, minutes=review.minutes if review.verdict in DECIDED else None,
        canSchedule=can_schedule, canRecordMinutes=can_record_minutes)


def _result(c, project, review, allowed, operation):
    return Result[Meeting](data=_view(c, project, review, allowed),
        meta=meta(operation, [f'review:{review.id}@{review.revision}', f'review-meeting:{review.id}@{_revision(review)}'],
                  project_id=project.id))


def read(auth, review_id):
    def query(c, actor, _):
        project, review, _frozen = context(c, actor, review_id)
        return _result(c, project, review, _allowed(c, actor, project, review), READ)
    return run(auth, READ, query)


def write(auth, review_id, command, expected, key):
    signature = fingerprint([str(review_id), expected, command.model_dump(mode='json')])

    def update(c, actor, request_id):
        project, review, _frozen = context(c, actor, review_id, write=True)
        allowed = _allowed(c, actor, project, review)
        if actor.kind != 'human' or not submitter(c, project, actor.person, review.type):
            raise Failure(403, 'FORBIDDEN', project_id=project.id)
        if saved := replay(c, actor, WRITE, key, signature, Result[Meeting]):
            return saved
        if project.stage == 'archived':
            raise Failure(422, 'ARCHIVED_READ_ONLY', project_id=project.id)
        if _revision(review) != expected:
            raise Failure(409, 'VERSION_CONFLICT', project_id=project.id)
        if isinstance(command, ScheduleMeeting) and allowed[0]:
            people = {person.personId for person in _people(c, project, roster=True)}
            # Historical participants may be retained after a role change, but cannot be newly added.
            people.update(_attendee_ids(review))
            if any(person_id not in people for person_id in command.attendeeIds):
                raise Failure(422, 'REVIEW_ATTENDEE_NOT_IN_PROJECT', project_id=project.id)
            changes = {'scheduled_at': command.scheduledAt, 'attendees': list(map(str, command.attendeeIds))}
        elif isinstance(command, RecordMinutes) and allowed[1]:
            changes = {'minutes': command.minutes}
        else:
            raise Failure(422, 'REVIEW_MEETING_NOT_EDITABLE', project_id=project.id)
        before = fingerprint(_view(c, project, review, allowed).model_dump(mode='json'))
        updated = c.execute(db.reviews.update().where(db.reviews.c.id == review.id).values(
            **changes, updated_at=sa.func.greatest(sa.func.clock_timestamp(),
                db.reviews.c.updated_at + sa.text("interval '1 microsecond'"))).returning(db.reviews)).mappings().one()
        return finish(c, actor, WRITE, key, request_id, project.id, signature,
            _result(c, project, updated, allowed, WRITE), before)
    return run(auth, WRITE, update)
