"""Record approved onboarding authority in a protected terminal."""
import argparse
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from . import schema_v1 as db
from .appointments import MEMBER_ROLES, SINGLETON_ROLES, authority, eligible_person
from .audit_store import audit
from .contracts import RULE_VERSION
from .cost_reconciliation import grants as cost_grants
from .requirement_baselines import active_member

records = sa.table('onboarding_authority_records',
    *(sa.column(name, sa.Uuid) for name in ('id', 'org_id', 'project_id', 'person_id', 'recorded_by')),
    sa.column('action'), sa.column('authority_ref'))
change_grants = sa.table('change_approval_grants', sa.column('project_id', sa.Uuid),
    sa.column('person_id', sa.Uuid), sa.column('target_type'), sa.column('authority_ref'),
    sa.column('expires_at', sa.DateTime(timezone=True)), sa.column('revoked_at', sa.DateTime(timezone=True)))
ACTIONS = ('grant-appointment', 'revoke-appointment', 'bind-customer', 'grant-finance-verifier',
           'grant-cost-reconciliation', 'grant-change-approval')
TARGETS = ('requirement', 'milestone', 'budget', 'charter')


def register(c, *, org_id, operator_id, person_id, action, reference, role=None,
             expires_at=None, project_id=None, stakeholder_id=None, target_type=None):
    reference = reference.strip()
    if not 4 <= len(reference) <= 240 or any(ord(char) < 32 for char in reference):
        raise ValueError('An approved, non-sensitive reference of 4–240 characters is required')
    if operator_id == person_id:
        raise ValueError('The registration operator must be independent of the recipient')
    u, n, roles, o = db.user_accounts, db.persons, db.user_roles, db.organizations
    actor = sa.select(u.c.id).select_from(u.join(n, n.c.user_id == u.c.id)
        .join(roles, roles.c.user_id == u.c.id).join(o, n.c.org_id == o.c.id)).where(
        db.persons.c.id == operator_id, db.persons.c.org_id == org_id, db.persons.c.is_active,
        db.user_accounts.c.is_active, db.user_roles.c.role == 'system_admin', db.organizations.c.status == 'active')
    actor_id = c.scalar(actor.with_for_update(read=True, of=db.user_roles))
    if action in ('grant-appointment', 'revoke-appointment', 'grant-finance-verifier'):
        # Serialize the absent-row and replacement cases for one recipient.
        c.scalar(sa.select(db.persons.c.id).where(db.persons.c.id == person_id).with_for_update(of=db.persons))
    if not actor_id or not eligible_person(c, person_id, org_id):
        raise ValueError('The operator and recipient must be active accounts in the selected organization')
    now = datetime.now(UTC)
    future = expires_at is not None and expires_at.utcoffset() is not None and expires_at > now

    if action in ('grant-appointment', 'revoke-appointment'):
        if role not in (*SINGLETON_ROLES, *MEMBER_ROLES):
            raise ValueError('Unsupported appointment role')
        scope = (authority.c.org_id == org_id, authority.c.person_id == person_id, authority.c.role == role)
        existing = c.execute(sa.select(authority).where(*scope).with_for_update()).mappings().one_or_none()
        live = existing is not None and existing.revoked_at is None and existing.expires_at > now
        if action == 'grant-appointment':
            if not future or live:
                raise ValueError('Supply a future expiry including its timezone and a vacant mandate')
            values = {'authority_ref': reference, 'expires_at': expires_at, 'revoked_at': None, 'generation': uuid4()}
            c.execute(insert(authority).values(org_id=org_id, person_id=person_id, role=role, **values)
                      .on_conflict_do_update(index_elements=['org_id', 'person_id', 'role'], set_=values))
            source = f'appointment_authority:{values["generation"]}'
        else:
            if not live:
                raise ValueError('No current mandate to revoke')
            c.execute(authority.update().where(*scope).values(revoked_at=now))
            source = f'appointment_authority:{existing.generation}'
        project_id = None
    elif action == 'grant-finance-verifier':
        account = c.scalar(sa.select(n.c.user_id).where(n.c.id == person_id))
        if c.scalar(sa.select(roles.c.id).where(roles.c.user_id == account, roles.c.role == 'finance_verifier')):
            raise ValueError('The selected account already holds finance verifier qualification')
        grant_id = c.scalar(roles.insert().values(user_id=account, role='finance_verifier').returning(roles.c.id))
        source = f'user_role:{grant_id}'
        project_id = None
    elif action in ('bind-customer', 'grant-cost-reconciliation', 'grant-change-approval'):
        p, s = db.projects, db.stakeholders
        project = c.execute(sa.select(p).where(p.c.id == project_id, p.c.org_id == org_id)
                            .with_for_update(of=p)).mappings().one_or_none()
        if not project or project.stage in ('closed', 'archived', 'retrospective', 'terminated', 'rejected'):
            raise ValueError('The selected project is unavailable for authorization registration')
        if action == 'bind-customer':
            if not active_member(c, project, person_id, 'client_representative'):
                raise ValueError('The recipient must first accept a client representative appointment')
            contact = c.execute(sa.select(s).where(s.c.id == stakeholder_id, s.c.project_id == project_id,
                                s.c.type == 'customer').with_for_update(of=s)).mappings().one_or_none()
            if not contact or (contact.linked_person_id is not None and active_member(
                    c, project, contact.linked_person_id, 'client_representative')):
                raise ValueError('Select a customer contact without an active representative')
            c.execute(s.update().where(s.c.id == stakeholder_id).values(linked_person_id=person_id,
                        revision=contact.revision+1, updated_at=now))
            source = f'stakeholder:{stakeholder_id}@{contact.revision+1}'
        elif not future:
            raise ValueError('Supply a future expiry including its timezone')
        elif action == 'grant-cost-reconciliation':
            qualifying = c.scalar(sa.select(roles.c.id).join(n, n.c.user_id == roles.c.user_id).where(
                n.c.id == person_id, roles.c.role == 'finance_verifier'))
            if not qualifying or person_id in (project.pm_id, project.initiator_id, project.sponsor_id) or not any(
                    active_member(c, project, person_id, role) for role in ('member', 'reviewer')):
                raise ValueError('Independent reconciliation requires effective membership and finance qualification')
            g = cost_grants
            if c.scalar(sa.select(g.c.id).where(g.c.project_id == project_id, g.c.person_id == person_id,
                    g.c.revoked_at.is_(None), sa.or_(g.c.expires_at.is_(None), g.c.expires_at > now)).limit(1)):
                raise ValueError('An existing reconciliation mandate is still active')
            grant_id = c.scalar(g.insert().values(project_id=project_id, person_id=person_id, expires_at=expires_at).returning(g.c.id))
            source = f'cost_reconciliation_grant:{grant_id}'
        else:
            if target_type not in TARGETS or not (active_member(c, project, person_id, 'reviewer') or
                    project.sponsor_id == person_id and active_member(c, project, person_id, 'sponsor')):
                raise ValueError('Change approval requires the relevant project role and a specific change type')
            g = change_grants
            if c.scalar(sa.select(g.c.person_id).where(g.c.project_id == project_id, g.c.person_id == person_id,
                                                      g.c.target_type == target_type)):
                raise ValueError('Use the controlled renewal procedure for an existing change approval record')
            c.execute(g.insert().values(project_id=project_id, person_id=person_id, target_type=target_type,
                                       expires_at=expires_at, authority_ref=reference))
            source = f'project:{project_id}@{project.revision+1}'
        c.execute(p.update().where(p.c.id == project_id).values(revision=project.revision+1, updated_at=now))
    else:
        raise ValueError('Unsupported onboarding operation')

    record = c.scalar(records.insert().values(org_id=org_id, person_id=person_id, project_id=project_id,
        recorded_by=operator_id, action=action, authority_ref=reference).returning(records.c.id))
    request_id = str(uuid4())
    c.execute(audit.insert().values(action='onboarding.'+action, actor_type='human', actor_id=actor_id,
        org_id=org_id, project_id=project_id, request_id=request_id, result='success', rule_version=RULE_VERSION,
        source_refs=[f'onboarding_record:{record}', source]))
    return request_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=ACTIONS)
    for name in ('org-id', 'operator-id', 'person-id'):
        parser.add_argument('--'+name, required=True, type=UUID)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--role', choices=(*SINGLETON_ROLES, *MEMBER_ROLES))
    parser.add_argument('--expires-at', type=datetime.fromisoformat)
    parser.add_argument('--project-id', type=UUID)
    parser.add_argument('--stakeholder-id', type=UUID)
    parser.add_argument('--target-type', choices=TARGETS)
    args = vars(parser.parse_args())
    target = os.getenv('PMO_MIGRATION_DATABASE_URL')
    if not target:
        parser.error('Set the approved migration connection in PMO_MIGRATION_DATABASE_URL')
    engine = sa.create_engine(target, hide_parameters=True)
    try:
        with engine.begin() as connection:
            request_id = register(connection, **args)
    except ValueError as error:
        parser.error(str(error))
    finally:
        engine.dispose()
    print(f'Onboarding registration receipt {request_id}')


if __name__ == '__main__':
    main()
