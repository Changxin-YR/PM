"""Private, resumable R6 onboarding and project-scoped policy originals."""
from datetime import datetime, timedelta
from hashlib import sha256
import secrets
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa

from pmo import appointment_authority_admin as mandates, policy, policy_admin as admin, schema_v1 as db
from pmo.bootstrap import bootstrap
from scripts import pilot_local as local

ORG = '澄明协作中心（合成演练）'
ROSTER = {'admin': '何静·组织登记', 'appoint': '周宁·人员任命', 'sign': '许岚·政策签署',
          'verify': '罗敏·原件核验', 'activate': '顾平·策略激活',
          'q-init': '林悦', 'q-pm': '沈青', 'q-sponsor': '江帆', 'q-review1': '胡可', 'q-review2': '秦禾',
          'm-init': '杜衡', 'm-pm': '陶然', 'm-sponsor': '陆遥', 'm-review1': '杨柳', 'm-review2': '谭峰',
          'm-customer': '宋兰', 'm-finance': '钱雅', 'm-reconcile': '任川'}
VERSION = 'r6-v1'


def headers(key, revision=1):
    return {'Idempotency-Key': key, 'If-Match': str(revision)}


def checked(response, label):
    if response.status_code != 200:
        raise RuntimeError(f'{label}: HTTP {response.status_code} {response.json().get("code")} '
                           f'({response.headers.get("X-Request-ID")})')
    return response


def onboard(pool, stack, required):
    passwords = local.read('r6-logins.json', {})
    if any(role not in passwords for role in ROSTER):
        for role in ROSTER:
            if role not in passwords:
                passwords[role] = secrets.token_urlsafe(28)
        local.save('r6-logins.json', passwords)
    with pool.begin() as c:
        organizations = c.scalars(sa.select(db.organizations.c.name)).all()
        if not organizations:
            bootstrap(c, ORG, 'r6-admin', ROSTER['admin'], passwords['admin'])
        elif organizations != [ORG]:
            raise ValueError('Unexpected organization in the R6 pilot')

    team, people = {}, {}
    invitations = local.read('r6-onboarding.json', {})
    for role, name in ROSTER.items():
        client = stack.enter_context(httpx.Client(base_url=local.API, timeout=30, trust_env=False))
        if role != 'admin' and 'accepted' not in invitations.get(role, {}):
            # Persist the one-time secret only in the private instance until redemption succeeds.
            if role not in invitations:
                invitations[role] = {'issueKey': uuid4().hex, 'acceptKey': uuid4().hex}
                local.save('r6-onboarding.json', invitations)
            item = invitations[role]
            if 'code' not in item:
                issued = checked(team['admin'].post('admin/users/invitations', headers=headers(item['issueKey']),
                    json={'email': f'{role}@r6.example.invalid', 'role': 'member'}), '签发开户邀请')
                invite = issued.json()['data']
                if 'X-PMO-Org-Invitation' not in issued.headers:
                    # A response lost before the private checkpoint cannot redeliver the secret.
                    checked(team['admin'].post(f'admin/users/invitations/{invite["invitationId"]}/revoke',
                        headers=headers(item['issueKey'] + '-revoke', invite['revision']),
                        json={'reason': '恢复合成开户流程，替换未交付的一次性邀请码'}), '撤销未交付邀请')
                    item['issueKey'] = uuid4().hex
                    local.save('r6-onboarding.json', invitations)
                    raise RuntimeError('Uncollected invitation revoked; rerun to issue its replacement')
                item.update(code=issued.headers['X-PMO-Org-Invitation'], invitationId=invite['invitationId'],
                            issued=issued.headers['X-Request-ID'])
                local.save('r6-onboarding.json', invitations)
            accepted = checked(client.post('auth/invitations/accept', headers=headers(item['acceptKey']),
                json={'invitationCode': item['code'], 'username': 'r6-' + role,
                      'displayName': name, 'password': passwords[role]}), '接受组织邀请')
            item['accepted'] = accepted.headers['X-Request-ID']
            del item['code']
            local.save('r6-onboarding.json', invitations)
        if role not in required:
            with pool.connect() as c:
                people[role] = c.execute(sa.select(db.persons.c.id).join(db.user_accounts,
                    db.user_accounts.c.id == db.persons.c.user_id).where(db.persons.c.org_id == org,
                    db.user_accounts.c.username == 'r6-' + role, db.persons.c.is_active, db.user_accounts.c.is_active)).scalar_one()
            continue
        login = checked(client.post('auth/login', json={'username': 'r6-' + role, 'password': passwords[role]}), '登录')
        client.headers['X-CSRF-Token'] = login.json()['csrfToken']
        me = checked(client.get('auth/me'), '核对组织身份').json()
        team[role], people[role] = client, UUID(me['personId'])
        if role == 'admin':
            org = UUID(me['orgId'])
        elif UUID(me['orgId']) != org:
            raise ValueError('Account belongs to a different organization')
    return team, people, passwords, org


def mandate(c, org, people, who, action, label, **kwargs):
    reference = '合成授权／' + label
    records = mandates.records.c
    if not c.scalar(sa.select(records.id).where(records.org_id == org, records.person_id == people[who],
                                               records.authority_ref == reference, records.action == action)):
        mandates.register(c, org_id=org, operator_id=people['admin'], person_id=people[who],
                          action=action, reference=reference, **kwargs)


def original(name, fields):
    if not local.local_file(name).exists():
        local.save(name, fields)
    return admin.private_original(local.local_file(name))


def qualifications(pool, org, people, passwords, codes):
    with pool.begin() as c:
        admin.protected_owner(c)
        admin.authenticate_operator(c, org, people['admin'], passwords['admin'])
        begin = admin.now(c)
        end = begin + timedelta(days=90)
        for office in ('pm', 'sponsor', 'reviewer', 'client_representative', 'member'):
            mandate(c, org, people, 'appoint', 'grant-appointment', '人员任命／' + office, role=office, expires_at=end)
        for role in ('m-finance', 'm-reconcile'):
            mandate(c, org, people, role, 'grant-finance-verifier', '组织财务核验／' + ROSTER[role])
        grants = [(who, capability, code) for code in sorted(codes)
                  for who, capability in [('sign', 'approve'), ('verify', 'verify')]]
        for who, capability, scope in [*grants, ('activate', 'activate', local.R6_ENVIRONMENT)]:
            ref = f'合成资格／{who}／{scope}'
            g = policy.authority.c
            if c.scalar(sa.select(g.id).where(g.org_id == org, g.authority_ref == ref)):
                continue
            file = f'r6-mandate-{who}-{scope}.json'
            fields = dict(用途='隔离开发演练', 组织=str(org), 人员=str(people[who]), 能力=capability,
                          范围=scope, 起始=begin.isoformat(), 截止=end.isoformat())
            raw = original(file, fields)
            saved = local.read(file)
            admin.grant(c, org, people['admin'], people[who], capability, scope, ref, raw,
                        datetime.fromisoformat(saved['截止']), datetime.fromisoformat(saved['起始']))


def activate(pool, org, people, passwords, project, actions):
    codes = sorted({code for action in actions for code in policy.ACTIONS[action]})
    for code in codes:
        scope = sorted(action for action in actions if code in policy.ACTIONS[action])
        contents = policy.payload(code, scope)
        digest = policy.canonical_hash(contents)
        file = f'r6-approval-{project}-{code}.json'
        with pool.begin() as c:
            admin.protected_owner(c)
            start = admin.now(c)
            approval = dict(orgId=str(org), projectId=str(project), environment=local.R6_ENVIRONMENT,
                decisionCode=code, version=VERSION, actions=scope, policyHash=digest, evidenceRef=file,
                approverId=str(people['sign']), approvedAt=start.isoformat(), effectiveFrom=start.isoformat(),
                effectiveUntil=(start + timedelta(days=60)).isoformat())
            if not local.local_file(file).is_file():
                admin.authenticate_operator(c, org, people['sign'], passwords['sign'])
                local.save(file, {'用途': '隔离开发演练', '批准索引': approval, '批准内容': contents})
            raw = admin.private_original(local.local_file(file))
            saved = local.read(file)
            stmt = admin.Statement.model_validate({**saved['批准索引'], 'evidenceHash': sha256(raw).hexdigest()})
            if (stmt.orgId, stmt.projectId, stmt.policyHash, stmt.approverId, saved['批准内容']) != (
                    org, project, digest, people['sign'], contents):
                raise ValueError('Saved original does not match the requested policy')
            v = policy.versions.c
            existing = c.execute(sa.select(v.id, v.evidence_hash).where(v.org_id == org, v.project_id == project,
                v.environment == local.R6_ENVIRONMENT, v.decision_code == code, v.version == VERSION)).one_or_none()
            if existing is not None:
                if existing.evidence_hash != stmt.evidenceHash:
                    raise ValueError('Registered original digest differs from private file')
                continue  # Never reactivate a revoked approval on a rerun.
            admin.authenticate_operator(c, org, people['verify'], passwords['verify'])
            identifier = admin.register(c, stmt, people['verify'], raw)
            admin.authenticate_operator(c, org, people['activate'], passwords['activate'])
            admin.change_state(c, org, identifier, people['activate'], 'active', '隔离复核／' + code, raw)
    with pool.connect() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id == project)).mappings().one()
        for action in actions:
            basis = policy.selection(c, p, action)
            if not basis or any('id' not in item for item in basis):
                raise RuntimeError(f'{action} requires an active registered version in R6')
