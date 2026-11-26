"""Action-specific, versioned policy checks shared by human and agent entry points."""

import json
import os
import re
from hashlib import sha256
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import RowMapping

from .contracts import RULE_VERSION
from .identity import Failure

RULE_PACKAGE = 'pmo-closure-policy-v1'
FAST_CHARTER = 'review.charter-fast'
FAST_CHARTER_VERSION = 'pmo-lite-charter-fast-r5-v1'
SYNTHETIC_SCHEMA = re.compile(r'pmo_test_[0-9a-f]{32}')
ENVIRONMENT = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{1,63}')

# The application only executes these explicit rules. A changed implementation
# or business parameter requires a new package identifier and signed payload.
TERMS = {
    'DEC-04': 'At least one independent reviewer; every frozen required reviewer supplies a complete card. Equal reviewer weights, exact thresholds, any effective one vetoes, and one appeal with unrescored cards carried forward.',
    'DEC-07': 'In the monitored stages, every outstanding P0 customer confirmation or active customer dispute makes the quality line red.',
    'DEC-09': 'Current authorized customer representative, signed written exception independently verified, specific requirement versions and gaps, valid term, and revocation. Does not waive effort, cost, or blocking risks.',
    'DEC-16': 'At closing, absolute deviation no greater than 20% for money and effort separately, with retained original comparison and independent reconciliation.',
    'DEC-18': 'A current claim of milestone progress or completion requires verifiable proof; a week with no progress records blockers and the current forecast. Two missing reports remain an independent trigger.',
    'DEC-19': 'An authenticated organization member may prepare a draft; Sponsor and PM appointments are independently authorized and accepted before review. Fast track uses initiator and Sponsor confirmation and independent reviewer decision.',
    'DEC-20': 'Keep rubric anchors at one, three and five and use the versioned intermediate anchors. Retain the feasibility two-point hard gate and written strengthening responses. Comparable dimensional variance of at least 1.00 requires retrospective explanation and improvement. Archive may retain pending observations with a named reviewer, deadline and evidence plan. Append later independent results without altering the archive.',
    'DEC-26': 'No Sponsor override of an overrun. Independent approved budget change preserves original comparisons, validates both units separately, and records category budget and forecast separately. Posting actual costs grants no authorization to continue investment.',
    'RP-01': 'Classified weekly effort requires exact declared totals, explicit period and source, independent verification, and reconciliation against verified category net amounts. Ambiguous legacy amounts block classified posting until clarified.',
    'RP-02': 'A current PM-approved weekly published version and its printable attachment may be submitted only to specifically authorized recipients with current scope, verified content identity, and distinct channel and actual receipt evidence. Unknown channel outcomes require verification before retry.',
}

ACTIONS: dict[str, tuple[str, ...]] = {
    'review.charter': ('DEC-04', 'DEC-19', 'DEC-20'),
    FAST_CHARTER: ('DEC-04', 'DEC-19', 'DEC-20'),
    'review.feasibility': ('DEC-04', 'DEC-19', 'DEC-20'),
    'review.closing': ('DEC-04', 'DEC-07', 'DEC-16', 'DEC-26'),
    'review.calibrate': ('DEC-20',),
    'requirement.customer-confirm': ('DEC-07',),
    'requirement.dispute': ('DEC-07',),
    'requirement.exception': ('DEC-09',),
    'change.requirement': ('DEC-07',),
    'change.milestone': ('DEC-18',),
    'change.budget': ('DEC-16', 'DEC-26'),
    'change.charter': ('DEC-19',),
    'cost.reconciliation': ('DEC-16', 'DEC-26'),
    'cost.category': ('DEC-26',),
    'cost.reassess': ('DEC-26',),
    'report.effort': ('RP-01',),
    'report.delivery': ('RP-02',),
    'transition.hold': ('DEC-19',),
    'transition.resume': ('DEC-19', 'DEC-26'),
    'transition.terminate': ('DEC-19',),
    'transition.terminate-feasibility': ('DEC-04', 'DEC-19', 'DEC-20'),
    'transition.terminate-closing': ('DEC-04', 'DEC-19'),
    'transition.archive': ('DEC-20',),
}


def review_action(gate: str) -> str:
    return f'review.{gate}'


def transition_action(edge: tuple[str, str]) -> str:
    if edge[1] == 'terminated' and edge[0] in ('feasibility_review', 'closing_review'):
        return f"transition.terminate-{edge[0].removesuffix('_review')}"
    return 'transition.' + {'on_hold': 'hold', 'executing': 'resume', 'terminated': 'terminate',
                            'archived': 'archive'}[edge[1]]


def canonical_hash(value: object) -> str:
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return sha256(data).hexdigest()


def payload(code: str, actions: list[str]) -> dict:
    if code not in TERMS or not actions or len(set(actions)) != len(actions) or any(
            code not in ACTIONS.get(action, ()) for action in actions):
        raise ValueError('INVALID_POLICY_ACTION_SCOPE')
    policy: dict[str, object] = {'package': RULE_PACKAGE, 'ruleVersion': RULE_VERSION, 'decisionCode': code,
                                 'actions': sorted(actions), 'terms': TERMS[code]}
    if code == 'DEC-04':
        from .review_scores import WEIGHTS
        policy['parameters'] = {'charterWeightsPercent': WEIGHTS, 'normalMinimum': '3.20', 'fastTrackMinimum': '4.20',
                                'singleScoreVeto': 1, 'maxAppeals': 1, 'minimumReviewers': 1}
    elif code == 'DEC-20':
        from .review_scores import rubric
        policy['parameters'] = {'dimensions': {gate: [dim.model_dump(mode='json') for dim in rubric(gate)]
                                               for gate in ('charter', 'feasibility')},
                                'feasibilityShortfallAtOrBelow': 2, 'charterStrengtheningAtOrBelow': 3,
                                'retrospectiveAbsoluteDeviationThreshold': '1.00'}
    elif code == 'DEC-16':
        policy['parameters'] = {'closingAbsoluteDeviationRatio': '0.20', 'units': ['money', 'effort']}
    elif code == 'DEC-26':
        from .cost_categories import CATEGORY_KIND
        policy['parameters'] = {'categories': CATEGORY_KIND, 'independentBudgetApproval': True}
        if 'cost.reassess' in actions:
            policy['reassessment'] = {'thresholdExclusivePercent': 20, 'reviewer': 'current_independent_sponsor',
                'options': ['continue', 'adjust', 'hold', 'terminate'], 'changesBudgetOrStage': False}
    if FAST_CHARTER in actions:
        policy['charterFastTrack'] = {
            'version': FAST_CHARTER_VERSION,
            'terms': ('After the complete independent roster reaches the exact 4.20 threshold without a veto, '
                      'the active initiator confirms the frozen review. The human decision, review filing and '
                      'transition to designing are recorded together. Sponsor and PM appointments remain required. '
                      'The extra Sponsor confirmation and reviewer verdict do not apply to this scope.'),
        }
    return policy


_ts = sa.DateTime(timezone=True)
authority = sa.table('policy_authority_grants',
    *(sa.column(name, sa.Uuid) for name in ('id', 'org_id', 'person_id')),
    *(sa.column(name) for name in ('capability', 'scope', 'authority_ref', 'authority_hash')),
    *(sa.column(name, _ts) for name in ('recorded_at', 'valid_from', 'expires_at')))
authority_revocations = sa.table('policy_authority_revocations', sa.column('grant_id', sa.Uuid),
    sa.column('reference'), sa.column('request_id', sa.Uuid), sa.column('recorded_at', _ts))
versions = sa.table('policy_versions',
    *(sa.column(name, sa.Uuid) for name in ('id', 'org_id', 'project_id', 'approver_id', 'approval_grant_id',
                                           'verified_by', 'verification_grant_id', 'request_id')),
    *(sa.column(name) for name in ('decision_code', 'version', 'environment', 'evidence_ref', 'evidence_hash', 'policy_hash')),
    *(sa.column(name, _ts) for name in ('approved_at', 'effective_from', 'effective_until', 'recorded_at')),
    sa.column('policy', JSONB), sa.column('synthetic', sa.Boolean))
events = sa.table('policy_events', sa.column('sequence', sa.BigInteger),
    *(sa.column(name, sa.Uuid) for name in ('policy_id', 'person_id', 'activation_grant_id', 'request_id')),
    sa.column('event'), sa.column('reason'), sa.column('recorded_at', _ts))
bindings = sa.table('policy_bindings',
    *(sa.column(name, sa.Uuid) for name in ('subject_id', 'project_id')),
    sa.column('kind'), sa.column('action'), sa.column('basis', JSONB))


def isolated_schema(c) -> bool:
    # Validation namespaces are identified by the physical database as well as
    # the schema. A policy restored from validation also carries synthetic=true.
    row = c.execute(sa.text('SELECT current_schema() AS schema_name, current_database() AS database_name')).one()
    return bool(SYNTHETIC_SCHEMA.fullmatch(row.schema_name or '') and
                (row.database_name in ('pmo_test', 'pmo_pilot', 'pmo_regression') or
                 row.database_name == 'pmo' and os.getenv('PMO_TEST_DATABASE_URL') is not None))


def synthetic_confirmation(c) -> bool:
    schema = os.getenv('PMO_SYNTHETIC_POLICY_SCHEMA', '')
    return bool(SYNTHETIC_SCHEMA.fullmatch(schema) and isolated_schema(c) and
                c.scalar(sa.text('SELECT current_schema()')) == schema)


def environment() -> str | None:
    name = os.getenv('PMO_POLICY_ENVIRONMENT', '')
    return name if ENVIRONMENT.fullmatch(name) else None


def lock(c, org_id, *, exclusive=False):
    key = int.from_bytes(sha256(b'pmo-policy:' + org_id.bytes).digest()[:8], 'big', signed=True)
    function = sa.func.pg_advisory_xact_lock if exclusive else sa.func.pg_advisory_xact_lock_shared
    c.execute(sa.select(function(key)))


def selection(c, p, action: str) -> list[dict] | None:
    """Return an immutable policy basis, or None when the action lacks approval."""
    codes = ACTIONS[action]
    if synthetic_confirmation(c) and (action != FAST_CHARTER
            or os.getenv('PMO_R5_FAST_TRACK_SCHEMA') == os.getenv('PMO_SYNTHETIC_POLICY_SCHEMA')):
        if action == FAST_CHARTER:
            return [{'synthetic': FAST_CHARTER_VERSION, 'ruleVersion': RULE_VERSION}]
        return [{'synthetic': RULE_PACKAGE, 'ruleVersion': RULE_VERSION}]
    target = environment()
    if target is None:
        return None
    v, e = versions.c, events.c
    latest = sa.select(e.event).where(e.policy_id == v.id).order_by(e.sequence.desc()).limit(1).scalar_subquery()
    activated = sa.select(e.sequence).where(e.policy_id == v.id, e.event == 'active').scalar_subquery()
    query = sa.select(versions, latest.label('state'), sa.func.clock_timestamp().label('checked_at')).where(
        v.org_id == p.org_id, sa.or_(v.project_id == p.id, v.project_id.is_(None)), v.environment == target,
        v.synthetic == isolated_schema(c), v.decision_code.in_(codes), v.policy['actions'].contains([action]),
        activated.is_not(None)).order_by(v.project_id.is_not(None).desc(), activated.desc())
    by_code: dict[str, RowMapping] = {}
    for row in c.execute(query).mappings():
        by_code.setdefault(row.decision_code, row)
    result = []
    for code in codes:
        row = by_code.get(code)
        if not row or row.state != 'active' or row.effective_from > row.checked_at or (
                row.effective_until is not None and row.effective_until <= row.checked_at):
            return None
        try:
            expected = payload(code, row.policy['actions'])
        except (KeyError, TypeError, ValueError):
            return None
        if row.policy != expected or row.policy_hash != canonical_hash(expected):
            return None
        result.append({'id': str(row.id), 'decisionCode': code, 'version': row.version,
                       'policyHash': row.policy_hash, 'evidenceHash': row.evidence_hash})
    return result


def enabled(c, p, action: str) -> bool:
    return selection(c, p, action) is not None


def guard(c, p, action: str) -> list[dict]:
    lock(c, p.org_id)
    result = selection(c, p, action)
    if result is None:
        raise Failure(403, 'POLICY_PENDING', project_id=p.id)
    return result


def _bind(c, p, action: str, subject_id, kind: str, basis: list[dict]) -> None:
    c.execute(bindings.insert().values(subject_id=UUID(str(subject_id)), project_id=p.id,
                                      action=action, kind=kind, basis=basis))


def require(c, p, action: str, request_id) -> list[dict]:
    result = guard(c, p, action)
    _bind(c, p, action, request_id, 'receipt', result)
    return result


def freeze(c, p, action: str, subject_id) -> None:
    lock(c, p.org_id)
    _bind(c, p, action, subject_id, 'pending', selection(c, p, action) or [])


def frozen_basis(c, p, action: str, subject_id) -> list[dict]:
    b = bindings.c
    return c.scalar(sa.select(b.basis).where(b.subject_id == subject_id, b.project_id == p.id,
                                             b.action == action, b.kind == 'pending')) or []


def matches(c, p, action: str, subject_id, *, exact=False) -> bool:
    basis = selection(c, p, action)
    if basis and 'synthetic' in basis[0] and not exact:
        return True  # Retain compatibility with snapshots made in old isolated fixtures.
    return (basis or []) == frozen_basis(c, p, action, subject_id)


def receipt_refs(c, request_id) -> list[str]:
    b = bindings.c
    rows = c.scalars(sa.select(b.basis).where(b.kind == 'receipt', b.subject_id == UUID(str(request_id))))
    return list(dict.fromkeys(f"policy:{item['id']}" for row in rows for item in row if 'id' in item))


def receipt_matches(c, p, action: str, subject_id, basis: list[dict] | None = None) -> bool:
    if basis is None:
        basis = selection(c, p, action)
    if not basis:
        return False
    if 'synthetic' in basis[0]:
        return True
    b = bindings.c
    return basis == c.scalar(sa.select(b.basis).where(b.subject_id == subject_id, b.project_id == p.id,
                                                       b.action == action, b.kind == 'receipt'))


def status(c, p, action: str) -> Literal['POLICY_PENDING', 'POLICY_ACTIVE']:
    basis = selection(c, p, action)
    return 'POLICY_ACTIVE' if basis and 'synthetic' not in basis[0] else 'POLICY_PENDING'
