from dataclasses import dataclass
from hmac import compare_digest
from hashlib import sha256
from uuid import UUID
import sqlalchemy as sa
from . import schema_v1 as db
from .session_schema import SESSION_ID, sessions
from .tool_routes import ROUTES

explicit_grants = sa.table('agent_project_grants', sa.column('credential_id', sa.Uuid),
                           sa.column('project_id', sa.Uuid), sa.column('granted_by', sa.Uuid))

class Failure(Exception):
    def __init__(self, status: int, code: str, actor: 'Actor | None'=None, *, project_id: UUID | None=None):
        self.status, self.code, self.actor = status, code, actor
        self.project_id = project_id

@dataclass(frozen=True)
class Actor:
    credential: UUID
    person: UUID
    account: UUID
    org: UUID
    actions: frozenset[str]
    projects: tuple[UUID,...]
    create: bool
    kind: str = 'agent'
    session_id: UUID | None = None

@dataclass(frozen=True)
class Browser:
    cookie: str
    csrf: str
    writing: bool

def active_browser_session(conn, criterion):
    s,p,u,o=sessions,db.persons,db.user_accounts,db.organizations
    # Serialize the account's browser writes without blocking audit foreign-key checks
    # in an Agent transaction already holding its project lock.
    row=conn.execute(sa.select(u.c.id,p.c.id.label('person'),p.c.org_id,s.c.csrf_hash,SESSION_ID.label('session_id')).select_from(s.join(u,u.c.id==s.c.account_id).join(p,p.c.user_id==u.c.id).join(o,o.c.id==p.c.org_id)).where(
        criterion,s.c.revoked_at.is_(None),s.c.expires_at>sa.func.clock_timestamp(),p.c.is_active,u.c.is_active,o.c.status=='active',sa.or_(u.c.locked_until.is_(None),u.c.locked_until<=sa.func.now())).with_for_update(of=u, key_share=True)).mappings().one_or_none()
    # The first SELECT may have waited for a logout holding the same account lock.
    if row is not None and conn.scalar(sa.select(SESSION_ID).where(SESSION_ID==row.session_id,
            s.c.revoked_at.is_(None),s.c.expires_at>sa.func.clock_timestamp())):
        return row
    return None


HUMAN_ONLY_OPERATIONS = frozenset({
        'auth_logout','auth_me','human_charter_get',
        'human_agent_token_create','human_agent_tokens_list','human_agent_token_revoke','admin_agent_tokens_list','admin_agent_token_revoke',
        'human_requirement_baseline_get','human_requirement_baseline_sign','human_requirement_accept','human_customer_confirm','human_changes_list','human_change_approve','human_change_reject','human_requirement_metrics',
        'human_requirement_addition_propose',
        'human_requirement_disputes_list','human_requirement_dispute_open','human_requirement_dispute_withdraw',
        'human_evidence_upload','human_evidence_list','human_evidence_get','human_evidence_verify','human_milestone_complete',
        'human_cost_postings_list','human_verify_cost_posting','human_reverse_cost_posting','human_alerts_overview','human_weekly_health_list',
        'human_project_overview_list','human_project_overview_get',
        'admin_audits_list','human_members_list','human_stakeholder_list','human_appointment_queue','human_my_role_invitations','human_issue_role_invitation',
        'human_accept_role_assignment','human_decline_role_assignment',
        'human_review_workbench','human_review_score','human_review_appeal','human_review_decide',
        'human_review_confirm','human_review_withdraw','human_closing_check',
        'human_cost_reconciliation_get','human_cost_reconciliation_create',
        'human_requirement_exception_get','human_requirement_exception_sign','human_requirement_exception_revoke',
        'human_task_create','human_strengthening_queue','human_report_draft_create','human_report_draft_update','human_report_submit_existing','human_report_confirm',
        'human_weekly_report_generate',
        'human_health_history','human_health_refresh','human_health_zero_budget','human_appeal_health','human_review_health_appeal',
        'human_report_fact_list','human_fact_verify','human_fact_clarify','human_fact_history',
        'human_report_analyze','human_report_analysis_list',
        'human_assistant_charter','human_assistant_process','human_assistant_closing','human_assistant_ledger','human_assistant_preview',
        'admin_members_list','admin_users_list','admin_user_update_status',
        'admin_user_invite','admin_user_invitation_list','admin_user_invitation_revoke',
        'human_risk_close','human_risk_handoff','human_risk_handoff_acceptance','human_risk_resolution_get',
        'human_execute_transition','human_approval_inbox','human_decide_approval','human_charter_resubmit',
        'human_termination_confirmation_inbox','human_termination_confirm',
        'human_retrospective_get','human_retrospective_save','human_lessons_list','human_lessons_draft','human_confirm_lesson',
})
BROWSER_OPERATIONS = frozenset(ROUTES) | HUMAN_ONLY_OPERATIONS


def browser_actor(conn,identity):
    row=active_browser_session(conn,sessions.c.token_hash==sha256(identity.cookie.encode()).hexdigest())
    if not row: raise Failure(401,'UNAUTHORIZED')
    actor=Actor(row.id,row.person,row.id,row.org_id,BROWSER_OPERATIONS,(),True,'human',row.session_id)
    if identity.writing and not compare_digest(sha256(identity.csrf.encode()).hexdigest(),row.csrf_hash):
        raise Failure(403,'CSRF_INVALID',actor)
    return actor

def authenticate(conn, header: str | Browser | None) -> Actor:
    if isinstance(header,Browser): return browser_actor(conn,header)
    if not header or not header.startswith('Bearer ') or len(header)>1024:
        raise Failure(401,'UNAUTHORIZED')
    digest = sha256(header[7:].encode()).hexdigest()
    a,p,u,o=db.agent_credentials,db.persons,db.user_accounts,db.organizations
    issued=sa.table('agent_security_events',sa.column('token_id',sa.Uuid),sa.column('action'))
    managed=sa.exists(sa.select(1).select_from(issued).where(issued.c.token_id==a.c.id,issued.c.action=='issue'))
    delegator=sa.exists(sa.select(1).select_from(db.user_roles).where(db.user_roles.c.user_id==u.c.id,db.user_roles.c.role=='delegator'))
    query = sa.select(a,p.c.id.label('person')).select_from(a.join(u,u.c.id==a.c.delegated_by).join(p,p.c.user_id==u.c.id).join(o,o.c.id==a.c.org_id)).where(
        a.c.token_hash==digest,a.c.revoked_at.is_(None),a.c.status=='active',a.c.expires_at>sa.func.now(),
        p.c.org_id==a.c.org_id,p.c.is_active,u.c.is_active,o.c.status=='active',sa.or_(~managed,delegator),
        sa.or_(u.c.locked_until.is_(None),u.c.locked_until<=sa.func.now())).with_for_update(of=a)
    row=conn.execute(query).mappings().one_or_none()
    if row is None: raise Failure(401,'UNAUTHORIZED')
    return Actor(row.id,row.person,row.delegated_by,row.org_id,frozenset(row.allowed_actions),tuple(map(UUID,row.allowed_project_ids)),row.allow_project_create)

def explicit_project_scope(actor):
    return sa.exists(sa.select(explicit_grants.c.project_id).where(
        explicit_grants.c.credential_id==actor.credential,explicit_grants.c.project_id==db.projects.c.id,
        explicit_grants.c.granted_by==actor.account))


def visible(actor, *, include_initiator=False):
    p,m=db.projects,db.project_members
    accepted=(m.c.project_id==p.c.id,m.c.person_id==actor.person,m.c.assignment_status=='accepted',m.c.accepted_at.is_not(None),m.c.revoked_at.is_(None))
    member=sa.exists(sa.select(m.c.id).where(*accepted,m.c.role!='initiator'))
    own=sa.and_(p.c.initiator_id==actor.person,sa.exists(sa.select(m.c.id).where(*accepted,m.c.role=='initiator')))
    own_draft=sa.and_(own,p.c.stage=='drafting')
    if actor.kind=='agent':
        return sa.and_(p.c.org_id==actor.org,p.c.id.in_(actor.projects),
                       sa.or_(own_draft,sa.and_(explicit_project_scope(actor),sa.or_(member,own) if include_initiator else member)))
    return sa.and_(p.c.org_id==actor.org,sa.or_(member,own if include_initiator else own_draft))
