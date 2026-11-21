"""An addition starts as an intent, not an effective requirement."""
from datetime import date

import sqlalchemy as sa
from pydantic import Field, ValidationError, field_validator

from .projects import fingerprint
from .requirement_baselines import s as signatures
from .requirements import Create

OP = 'human_requirement_addition_propose'


class Fields(Create):
    plannedConfirmAt: date

    @field_validator('desc', 'acceptanceCriteria')
    @classmethod
    def clean(cls, value: str) -> str:
        if '\x00' in value or len(value.strip()) < 5:
            raise ValueError('Expected substantive UTF-8 text')
        value.encode('utf-8')
        return value.strip()


class Addition(Fields):
    reason: str = Field(min_length=3, max_length=2000, pattern=r'\S')

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value: str) -> str:
        if '\x00' in value or len(value.strip()) < 3:
            raise ValueError('Expected substantive UTF-8 text')
        value.encode('utf-8')
        return value.strip()


def initial_signature(c, pid):
    s = signatures.c
    return c.execute(sa.select(signatures).where(s.project_id == pid, s.signer_role == 'client_representative')
        .order_by(s.revision.desc()).limit(1)).mappings().one_or_none()


def origin(pid, signed):
    if signed and isinstance(signed.snapshot, list) and fingerprint([str(pid), signed.snapshot]) == signed.content_hash:
        return {'present': False, 'initialBaselineRevision': signed.revision, 'initialBaselineHash': signed.content_hash}
    return None


def addition(change):
    return change.target_type == 'requirement' and change.before.get('present') is False


def valid_origin(before, stamp):
    return stamp is not None and fingerprint(before) == fingerprint(stamp)


def applied_addition(decision, stamp):
    """Require the signed entry point and the exact approved initial payload."""
    if (stamp is None or decision.base_revision != stamp['initialBaselineRevision']
            or not valid_origin(decision.before, stamp) or not valid_origin(decision.old_state, stamp)):
        return False
    try:
        fields = Fields.model_validate(decision.after)
    except ValidationError:
        return False
    saved = decision.new_state
    return isinstance(saved, dict) and (saved.get('baseline_version'), saved.get('status'), saved.get('desc'),
        saved.get('priority'), saved.get('acceptance_criteria'), saved.get('planned_confirm_at')) == (
        1, 'baseline', fields.desc, fields.priority, fields.acceptanceCriteria, fields.plannedConfirmAt.isoformat())
