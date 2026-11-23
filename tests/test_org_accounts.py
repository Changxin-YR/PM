from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.org_accounts import MEMBERS, STATUS, USERS, u
from pmo.org_invitations import INVITE, LIST, REVOKE, i, invites
from test_reports import _token, data, deny, headers, team as team
from test_reviews import data_login, issue


@pytest.fixture
def admins(team):
    with team.engine.begin() as c:
        for role in ('pm', 'reviewer'):
            c.execute(db.user_roles.insert().values(user_id=team.people[role].account, role='system_admin'))
    return team.users['pm'], team.users['reviewer']


def invite(user, email, *, role='member', key=None):
    return user.post('admin/users/invitations', headers=key or headers(), json={'email':email,'role':role})


def command(code):
    return {'invitationCode': code, 'username': 'join-'+uuid4().hex, 'displayName':'Synthetic colleague', 'password':token_urlsafe(24)}


def attend(user, payload, key=None):
    return user.post('auth/invitations/accept', headers=key or headers(1), json=payload)


def change(user, uid, target, key=None):
    return user.patch(f'admin/users/{uid}/status', headers=key or headers(1),
        json={'isActive':target, 'reason':'Synthetic change for audit'})


def test_admin_scope_closed_dtos_http_agent_and_self_denial(team, admins, actor):
    pm, reviewer = admins
    _, credential = actor()
    with team.engine.begin() as c:
        t = db.agent_credentials.c
        other = c.scalar(sa.select(t.delegated_by).where(t.id==credential))
        c.execute(db.persons.update().where(db.persons.c.user_id==team.people['outsider'].account).values(role='system_admin'))
    outsider = team.users['outsider']
    deny(outsider.get('admin/users'),403,'FORBIDDEN')
    deny(outsider.get('admin/members'),403,'FORBIDDEN')
    deny(invite(outsider, 'fake@example.test'),403,'FORBIDDEN')
    deny(change(pm, other, False),404,'RESOURCE_NOT_FOUND')
    for who in [team.people['pm'], team.people['reviewer']]:
        deny(change(pm, who.account, False),403,'ADMIN_ACCOUNT_PROTECTED')
    person_ids = {str(w.id) for w in team.people.values()}
    assert {row['personId'] for row in data(pm.get('admin/members'))['items']}==person_ids
    accounts = data(pm.get('admin/users?page=2&pageSize=2'))
    assert accounts['total']==4 and len(accounts['items'])==2
    assert all(row['personId'] in person_ids for row in accounts['items'])
    assert 'password' not in pm.get('admin/users').text.lower()
    token = _token(team, person='pm', actions=(MEMBERS, USERS, STATUS, INVITE, LIST, REVOKE))
    with httpx.Client(base_url=team.url, headers=token, trust_env=False) as agent:
        for path in ('admin/users','admin/members','admin/users/invitations'):
            deny(agent.get(path),403,'FORBIDDEN')
        deny(invite(agent, 'agent@example.test'),403,'FORBIDDEN')
        deny(change(agent, team.people['reporter'].account, False),403,'FORBIDDEN')
        deny(attend(agent, command(token_urlsafe(32))),403,'FORBIDDEN')
    assert data(reviewer.get('admin/users'))['total']==4


def test_single_invite_atomic_acceptance_and_no_authority_from_role_hint(team, admins):
    pm, reviewer = admins
    email = 'invitee@example.test'
    deny(invite(pm, email, role='system_admin'),422,'VALIDATION_FAILED')
    deny(invite(pm, 'not-an-address'),422,'VALIDATION_FAILED')
    key = headers()
    with ThreadPoolExecutor(2) as pool:
        attempts = list(pool.map(lambda user:invite(user, email.upper(), role='reviewer', key=key), admins))
    issued, conflict = sorted(attempts, key=lambda r:r.status_code)
    invitation = data(issued)
    deny(conflict,409,'INVITATION_ALREADY_EXISTS')
    issuer = pm if issued is attempts[0] else reviewer
    code = issued.headers['X-PMO-Org-Invitation']
    assert len(code)==43 and invitation['email']==email and invitation['roleHint']=='reviewer'
    assert code not in issued.text and issued.headers['Cache-Control']=='no-store'
    replay = invite(issuer, email, role='reviewer', key=key)
    assert replay.json()==issued.json() and 'X-PMO-Org-Invitation' not in replay.headers
    deny(invite(issuer, email, role='pm', key=key),409,'IDEMPOTENCY_CONFLICT')
    assert inv_state(pm, invitation)=='pending'

    payload, accept_key = command(code), headers(1)
    with httpx.Client(base_url=team.url, trust_env=False) as member:
        deny(attend(member, payload, headers(2)),409,'VERSION_CONFLICT')
        deny(attend(member, {**payload, 'role':'system_admin'}),422,'VALIDATION_FAILED')
        deny(attend(member, {**payload, 'password':'weak'}),422,'VALIDATION_FAILED')
        with team.engine.begin() as c:
            c.exec_driver_sql('CREATE TRIGGER invite_fault BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
        try:
            failed = attend(member, payload, accept_key)
            deny(failed,500,'DATABASE_FAILURE')
            assert inv_state(pm, invitation)=='pending'
            with team.engine.connect() as c:
                assert c.scalar(sa.select(u.id).where(u.username==payload['username'])) is None
                assert c.scalar(sa.select(db.audit_logs.c.id).where(db.audit_logs.c.request_id==failed.headers['X-Request-ID'])) is None
        finally:
            with team.engine.begin() as c:
                c.exec_driver_sql('DROP TRIGGER invite_fault ON outbox_events')
        with ThreadPoolExecutor(2) as pool:
            pair = list(pool.map(lambda _:attend(member, payload, accept_key), range(2)))
        enrolled = data(pair[0])
        assert pair[0].json()==pair[1].json() and not enrolled['isSystemAdmin']
        deny(attend(member, payload),403,'INVITATION_INVALID')
        deny(attend(member, {**payload,'displayName':'Changed name'}, accept_key),409,'IDEMPOTENCY_CONFLICT')
        member.headers['X-CSRF-Token']=data_login(member,payload['username'],payload['password'])
        assert data(member.get('projects'))['total']==0
        deny(member.get('admin/users'),403,'FORBIDDEN')
    assert inv_state(pm, invitation)=='accepted'
    deny(invite(pm,email),409,'INVITATION_ALREADY_EXISTS')
    new_member, = [x for x in data(pm.get('admin/members'))['items'] if x['personId']==enrolled['personId']]
    assert new_member['displayRole']=='reviewer' and new_member['userId']==enrolled['userId']
    with team.engine.connect() as c:
        uid = UUID(enrolled['userId'])
        assert c.scalar(sa.select(i.code_hash).where(i.account_id==uid))==sha256(code.encode()).hexdigest()
        assert c.scalar(sa.select(u.password_hash).where(u.id==uid)).startswith('$argon2id$')
        assert c.scalar(sa.select(db.user_roles.c.id).where(db.user_roles.c.user_id==uid)) is None
        assert c.scalar(sa.select(db.project_members.c.id).where(db.project_members.c.person_id==UUID(enrolled['personId']))) is None


def inv_state(user, invite):
    found, = [x for x in data(user.get('admin/users/invitations'))['items'] if x['invitationId']==invite['invitationId']]
    return found['state']


def test_revoke_expiry_and_issuer_authority_is_live(team, admins):
    pm, reviewer = admins
    recipient = 'expired@example.test'
    expired = token_urlsafe(32)
    with team.engine.begin() as c:
        now = datetime.now(UTC)
        c.execute(invites.insert().values(org_id=team.people['pm'].org, issuer_id=team.people['pm'].account,
            email=recipient, role_hint='member', code_hash=sha256(expired.encode()).hexdigest(),
            issued_at=now-timedelta(days=2), expires_at=now-timedelta(days=1)))
    with httpx.Client(base_url=team.url, trust_env=False) as client:
        deny(attend(client, command(expired)),403,'INVITATION_INVALID')
        replacement = invite(pm,recipient)
        item = data(replacement)
        code = replacement.headers['X-PMO-Org-Invitation']
        path = f"admin/users/invitations/{item['invitationId']}/revoke"
        deny(team.users['outsider'].post(path, headers=headers(1), json={'reason':'No longer needed'}),403,'FORBIDDEN')
        deny(reviewer.post(path, headers=headers(2),json={'reason':'No longer needed'}),409,'VERSION_CONFLICT')
        revoked = data(reviewer.post(path, headers=headers(1),json={'reason':'No longer needed'}))
        assert revoked['state']=='revoked' and revoked['revision']==2
        deny(attend(client, command(code)),403,'INVITATION_INVALID')
        new = invite(pm,recipient)
        fresh = data(new)
        with team.engine.begin() as c:
            c.execute(db.user_roles.delete().where(db.user_roles.c.user_id==team.people['pm'].account,
                                                  db.user_roles.c.role=='system_admin'))
        deny(attend(client, command(new.headers['X-PMO-Org-Invitation'])),403,'INVITATION_INVALID')
        assert inv_state(reviewer, fresh)=='pending'


def test_status_concurrency_rollback_and_revocation_even_after_reenable(team, admins):
    pm, reviewer = admins
    target = team.people['reporter']
    reporter = team.users['reporter']
    with team.engine.begin() as c:
        c.execute(db.user_roles.insert().values(user_id=target.account, role='delegator'))
    tokens = [issue(reporter,str(team.pid), actions=('pmo_project_get',)) for _ in range(2)]
    key1, key2 = headers(1), headers(1)
    with httpx.Client(base_url=team.url, trust_env=False) as agent:
        access={'Authorization':'Bearer '+tokens[0]}
        assert agent.get(f'projects/{team.pid}',headers=access).is_success
        with team.engine.begin() as c:
            c.exec_driver_sql('CREATE TRIGGER suspend_fault BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
        try:
            failed = change(pm,target.account,False,key1)
            deny(failed,500,'DATABASE_FAILURE')
            assert reporter.get('auth/me').is_success and agent.get(f'projects/{team.pid}',headers=access).is_success
            with team.engine.connect() as c:
                assert c.scalar(sa.text('SELECT count(*) FROM account_status_changes WHERE account_id=:a'),{'a':target.account})==0
                assert c.scalar(sa.select(db.audit_logs.c.id).where(db.audit_logs.c.request_id==failed.headers['X-Request-ID'])) is None
        finally:
            with team.engine.begin() as c:
                c.exec_driver_sql('DROP TRIGGER suspend_fault ON outbox_events')
        with ThreadPoolExecutor(2) as pool:
            replies=list(pool.map(lambda arg:change(arg[0],target.account,False,arg[1]), [(pm,key1),(reviewer,key2)]))
        winner, rejected=sorted(replies,key=lambda r:r.status_code)
        stopped=data(winner)
        assert not stopped['isActive'] and stopped['personActive'] and stopped['revision']==2
        deny(rejected,409,'VERSION_CONFLICT')
        index=replies.index(winner)
        assert change(admins[index],target.account,False,(key1,key2)[index]).json()==winner.json()
        deny(reporter.get('auth/me'),401,'UNAUTHORIZED')
        deny(agent.get(f'projects/{team.pid}',headers=access),401,'UNAUTHORIZED')
        active=data(change(pm,target.account,True,headers(2)))
        assert active['isActive'] and active['revision']==3
        deny(reporter.get('auth/me'),401,'UNAUTHORIZED')
        deny(agent.get(f'projects/{team.pid}',headers=access),401,'UNAUTHORIZED')
    with team.engine.connect() as c:
        rows=c.execute(sa.text('SELECT revision,is_active,reason FROM account_status_changes WHERE account_id=:a ORDER BY revision'),
            {'a':target.account}).all()
        assert rows==[(2,False,'Synthetic change for audit'),(3,True,'Synthetic change for audit')]
        assert c.scalar(sa.text("""SELECT count(*) FROM agent_security_events e JOIN agent_credentials g ON g.id=e.token_id
            WHERE g.delegated_by=:a AND e.action='revoke' AND g.status='revoked'"""), {'a':target.account})==2
        assert c.scalar(sa.text("""SELECT count(*) FROM outbox_events WHERE event_type='org.account.status_changed'
            AND payload->>'resourceId'=:a"""), {'a':str(target.account)})==2
