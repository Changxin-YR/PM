from secrets import token_urlsafe
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from test_org_accounts import admins as admins  # noqa: PLC0414 - pytest fixture
from test_reports import deny
from test_reports import team as team  # noqa: PLC0414 - pytest fixture
from test_reviews import issue

from pmo import onboarding_admin as recovery
from pmo import schema_v1 as db
from pmo.projects import audit
from pmo.sessions import hasher


def test_reset_rotates_credential_revokes_sessions_and_audits(team, admins):
    operator, target = team.people['pm'], team.people['reporter']
    reporter = team.users['reporter']
    old_password, new_password = token_urlsafe(24), token_urlsafe(24)
    with team.engine.begin() as c:
        c.execute(db.user_accounts.update().where(db.user_accounts.c.id == target.account)
            .values(password_hash=hasher.hash(old_password)))
        c.execute(db.user_roles.insert().values(user_id=target.account, role='delegator'))
    token = issue(reporter, str(team.pid), actions=('pmo_project_get',))
    with httpx.Client(base_url=team.url, headers={'Authorization': 'Bearer ' + token}, trust_env=False) as agent:
        assert agent.get(f'projects/{team.pid}').is_success
        with team.engine.begin() as c:
            request_id = recovery.reset_password(c, operator.org, operator.account, target.account, new_password)
        deny(reporter.get('auth/me'), 401, 'UNAUTHORIZED')
        deny(agent.get(f'projects/{team.pid}'), 401, 'UNAUTHORIZED')

    assert admins[0].get('auth/me').is_success
    with httpx.Client(base_url=team.url, trust_env=False) as member:
        deny(member.post('auth/login', json={'username': target.username, 'password': old_password}), 401, 'UNAUTHORIZED')
        assert member.post('auth/login', json={'username': target.username, 'password': new_password}).is_success
        assert member.get('auth/me').is_success

    with team.engine.connect() as c:
        receipt = c.execute(sa.select(audit).where(audit.c.request_id == str(request_id))).mappings().one()
        assert receipt.action == recovery.ACTION and receipt.result == 'success'
        assert str(receipt.actor_id) == str(operator.account) and str(receipt.org_id) == str(operator.org)
        assert receipt.source_refs == [f'account:{target.account}']
        assert c.scalar(sa.select(db.user_roles.c.role).where(db.user_roles.c.user_id == target.account)) == 'delegator'


def test_recovery_rejects_cross_org_inactive_admin_and_unprivileged_accounts(team, admins, actor):
    operator, target, reviewer, outsider = (team.people[role] for role in ('pm', 'reporter', 'reviewer', 'outsider'))
    _, credential = actor()
    users = db.user_accounts
    with team.engine.begin() as c:
        cross_org = c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id == credential))
        c.execute(users.update().where(users.c.id == outsider.account).values(is_active=False))
        target_hash = c.scalar(sa.select(users.c.password_hash).where(users.c.id == target.account))
    password = token_urlsafe(24)
    for actor_id, account_id in ((target.account, operator.account), (operator.account, reviewer.account),
                                 (operator.account, operator.account), (operator.account, cross_org),
                                 (operator.account, outsider.account), (outsider.account, target.account)):
        with pytest.raises(recovery.AccountRecoveryError), team.engine.begin() as c:
            recovery.reset_password(c, operator.org, actor_id, account_id, password)
    with team.engine.connect() as c:
        assert c.scalar(sa.select(users.c.password_hash).where(users.c.id == target.account)) == target_hash
        assert c.scalar(sa.select(audit.c.id).where(audit.c.org_id == operator.org, audit.c.action == recovery.ACTION)) is None


def test_recovery_cli_rejects_noninteractive_and_mismatched_passwords(monkeypatch, capsys):
    args = ['--org-id', str(uuid4()), '--operator-account-id', str(uuid4()), '--target-account-id', str(uuid4())]
    monkeypatch.setattr(recovery.sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    assert recovery.main(args) == 1
    assert 'interactive migration terminal' in capsys.readouterr().err

    secret = token_urlsafe(24)
    answers = iter([secret, token_urlsafe(24)])
    monkeypatch.setattr(recovery.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(recovery, 'getpass', lambda _: next(answers))
    monkeypatch.setenv('PMO_MIGRATION_DATABASE_URL', 'postgresql+psycopg://fixture@127.0.0.1/isolated')
    assert recovery.main(args) == 1
    out = capsys.readouterr()
    assert 'Passwords must match' in out.err and secret not in out.err + out.out
