from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from test_reports import data, deny, headers
from test_reports import team as team

from pmo import schema_v1 as db
from pmo.appointment_authority_admin import register
from pmo.cost_reconciliation import grants as cost_grants
from pmo.onboarding import revocations


def test_new_org_user_gets_project_only_after_independent_appointment(team, actor):
    manager = team.users['pm']
    officer = team.users['reporter']
    org = team.people['pm'].org
    operator_id = team.people['pm'].id
    issuer_id = team.people['reporter'].id
    path = f'projects/{team.pid}'
    nominate = path + '/member-invitations'
    token, _ = actor(extra_actions=('human_onboarding_people', 'human_member_assignment_propose'))

    def mandate(action, reference):
        with team.engine.begin() as c:
            return register(c, org_id=org, operator_id=operator_id, person_id=issuer_id, action=action,
                reference=reference, role='reviewer', expires_at=datetime.now(UTC)+timedelta(days=30))

    with team.engine.begin() as c:
        c.execute(db.user_roles.insert().values(user_id=team.people['pm'].account, role='system_admin'))
    mandate('grant-appointment', 'SYNTHETIC approval A')
    assert data(manager.get('project-overview?filters%5Bstage%5D=drafting'))['total'] == 0
    projects = data(manager.get('onboarding/projects?page=1&pageSize=1'))
    assert projects['total'] == 2 and len(projects['items']) == 1
    assert set(projects['items'][0]['roles']) == {'member', 'reviewer', 'client_representative'}
    assert data(manager.get('onboarding/projects?page=2&pageSize=1'))['items'][0] != projects['items'][0]
    assert data(officer.get('onboarding/projects'))['total'] == 0
    deny(officer.get('onboarding/people?keyword=reviewer'), 403, 'FORBIDDEN')
    with httpx.Client(base_url=team.url, headers={'Authorization': 'Bearer '+token}, trust_env=False) as agent:
        deny(agent.get('onboarding/people?keyword=reviewer'), 403, 'FORBIDDEN')
        deny(agent.post(nominate, headers=headers(1), json={'role':'reviewer','personId':str(issuer_id)}), 403, 'FORBIDDEN')

    invitation = manager.post('admin/users/invitations', headers=headers(),
                              json={'email':'new-reviewer@example.test','role':'reviewer'})
    data(invitation)
    password = token_urlsafe(24)
    username = 'new-'+uuid4().hex
    with httpx.Client(base_url=team.url, trust_env=False) as user:
        enrolled = data(user.post('auth/invitations/accept', headers=headers(1),
            json={'invitationCode':invitation.headers['X-PMO-Org-Invitation'], 'displayName':'New QA Reviewer',
                  'username':username,'password':password}))
        session = user.post('auth/login', json={'username':username,'password':password})
        assert session.status_code == 200, session.text
        user.headers['X-CSRF-Token'] = session.json()['csrfToken']
        person_id = enrolled['personId']
        assert data(user.get('project-overview'))['total'] == 0
        deny(user.get(path), 404, 'RESOURCE_NOT_FOUND')
        assert data(manager.get('onboarding/people?keyword=QA Reviewer'))['items'] == [
            {'personId':person_id,'name':'New QA Reviewer'}]
        proposal = {'personId':person_id,'role':'reviewer'}
        deny(manager.post(nominate, headers=headers(99), json=proposal), 409, 'VERSION_CONFLICT')
        deny(manager.post(nominate, headers=headers(1), json={**proposal,'personId':str(operator_id)}), 403, 'FORBIDDEN')
        appointment = data(manager.post(nominate, headers=headers(1), json=proposal))
        invite = 'role-invitations/'+appointment['invitationId']+'/'
        deny(manager.post(invite+'issue', headers=headers(1)), 403, 'FORBIDDEN')
        assert any(item['invitationId'] == appointment['invitationId'] for item in data(officer.get('me/appointment-queue')))
        data(officer.post(invite+'issue', headers=headers(1)))
        deny(user.get(path), 404, 'RESOURCE_NOT_FOUND')
        accepted_key = headers(2)
        data(user.post(invite+'accept', headers=accepted_key))
        assert data(user.get('project-overview'))['total'] == 1
        members = data(officer.get(path+'/members'))
        assert next(item for item in members['items'] if item['personId'] == person_id)['canRevoke']
        revoke = path+'/members/revoke'
        command = {**proposal,'reason':'Synthetic authorization ended'}
        deny(manager.post(revoke, headers=headers(members['projectRevision']), json=command), 403, 'FORBIDDEN')
        key = headers(members['projectRevision'])
        removed = data(officer.post(revoke, headers=key, json=command))
        assert data(officer.post(revoke, headers=key, json=command)) == removed
        deny(user.get(path), 404, 'RESOURCE_NOT_FOUND')
        deny(user.post(invite+'accept', headers=accepted_key), 403, 'AUTHORITY_INACTIVE')
        with team.engine.connect() as c:
            assert c.scalar(sa.select(sa.func.count()).select_from(revocations)) == 1

        appointment = data(manager.post(nominate, headers=headers(removed['projectRevision']), json=proposal))
        reissued = 'role-invitations/'+appointment['invitationId']+'/'
        data(officer.post(reissued+'issue', headers=headers(1)))
        mandate('revoke-appointment', 'SYNTHETIC revoke B')
        mandate('grant-appointment', 'SYNTHETIC approval C')
        deny(user.post(reissued+'accept', headers=headers(2)), 403, 'AUTHORITY_INACTIVE')
        assert data(user.get('me/role-invitations')) == []
        replacement = data(manager.post(nominate, headers=headers(removed['projectRevision']+1), json=proposal))
        fresh = 'role-invitations/'+replacement['invitationId']+'/'
        data(officer.post(fresh+'issue', headers=headers(1)))
        data(user.post(fresh+'accept', headers=headers(2)))
        assert data(user.get('project-overview'))['total'] == 1


def test_protected_registration_requires_current_membership_and_keeps_evidence(team):
    pm = team.people['pm']
    reviewer = team.people['reviewer']
    outsider = team.people['outsider']
    reference = 'SYNTHETIC authorization record'
    expires = datetime.now(UTC)+timedelta(days=30)
    with team.engine.begin() as c:
        c.execute(db.user_roles.insert().values(user_id=pm.account, role='system_admin'))
        with pytest.raises(ValueError, match='operator and recipient'):
            register(c, org_id=pm.org, operator_id=outsider.id, person_id=reviewer.id,
                     action='grant-finance-verifier', reference=reference)
        with pytest.raises(ValueError, match='finance qualification'):
            register(c, org_id=pm.org, operator_id=pm.id, person_id=reviewer.id,
                     action='grant-cost-reconciliation', reference=reference, project_id=team.pid, expires_at=expires)
        register(c, org_id=pm.org, operator_id=pm.id, person_id=reviewer.id,
                 action='grant-finance-verifier', reference=reference)
        register(c, org_id=pm.org, operator_id=pm.id, person_id=reviewer.id,
                 action='grant-cost-reconciliation', reference=reference, project_id=team.pid, expires_at=expires)
        assert c.scalar(sa.select(cost_grants.c.person_id).where(cost_grants.c.project_id == team.pid)) == reviewer.id
        register(c, org_id=pm.org, operator_id=pm.id, person_id=reviewer.id, action='grant-change-approval',
                 reference=reference, project_id=team.pid, target_type='requirement', expires_at=expires)
        contact = c.scalar(db.stakeholders.insert().values(project_id=team.pid, created_by=pm.id,
            name='Synthetic contact', type='customer').returning(db.stakeholders.c.id))
        with pytest.raises(ValueError, match='client representative appointment'):
            register(c, org_id=pm.org, operator_id=pm.id, person_id=reviewer.id, action='bind-customer',
                     reference=reference, project_id=team.pid, stakeholder_id=contact)
        c.execute(db.project_members.insert().values(project_id=team.pid, person_id=outsider.id,
            role='client_representative', assigned_by=pm.id, assignment_status='accepted', accepted_at=sa.func.now()))
        register(c, org_id=pm.org, operator_id=pm.id, person_id=outsider.id, action='bind-customer',
                 reference=reference, project_id=team.pid, stakeholder_id=contact)
        assert c.scalar(sa.select(db.stakeholders.c.linked_person_id).where(db.stakeholders.c.id == contact)) == outsider.id
        assert c.scalar(sa.select(sa.func.count()).select_from(db.audit_logs).where(
            db.audit_logs.c.action.startswith('onboarding.'), db.audit_logs.c.org_id == pm.org)) == 4
