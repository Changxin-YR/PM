from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from pmo import outbox, schema_v1 as db
from pmo.database_roles import WORKER_ADD, WORKER_EDIT, WORKER_READ, grant
from pmo.weekly_delivery import DELIVER, authorizations, deliveries, inbox
from test_reports import body, data, deny, headers, sections, team as team
from test_weekly_report_workflow import publish


def 分类(研发='2.50', 实施='3.00', 管理='1.50'):
    return [{'category': category, 'personDays': amount, 'basis': basis} for category, amount, basis in (
        ('development', 研发, '本期接口研发签到记录'), ('implementation', 实施, '本期客户实施工单'),
        ('management', 管理, '本期项目协调会议记录'))]


def 提交(team, *, period='2025-W01', parts=None, total='7.00', **values):
    fields = sections(effort_person_days=total)
    if parts is not None:
        fields['effort_breakdown'] = parts
    return data(team.users['pm'].post(team.path, headers=headers(), json=body(
        period=period, structuredFields=fields, **values)))


def 路径(report, tail):
    return f"reports/{report['reportId']}/{tail}"


def 核验(client, posting, *, at='2025-01-02T10:00:00Z', evidence=None):
    payload = {'reason': '独立核对本期投入原始凭证', 'occurredAt': at}
    if evidence:
        payload['evidenceRef'] = evidence
    return client.post(f"cost-postings/{posting['postingId']}/verify", headers=headers(1), json=payload)


def 财务(team):
    with team.engine.begin() as c:
        c.execute(db.user_roles.insert(), [{'user_id': team.people[name].account, 'role': 'finance_verifier'}
            for name in ('pm', 'reviewer')])
    return team.users['reviewer']


def test_分类周报候选精确对平并独立核验(team):
    pm, verifier = team.users['pm'], 财务(team)
    submitted = 提交(team, parts=分类(), narrative='历史累计投入99.00人天，本期申报以分类项目为准。')
    path = 路径(submitted, 'effort-posting')
    deny(pm.get(path), 422, 'WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION')
    report = publish(pm, submitted)
    assert report['structuredFields']['effort_breakdown'] == 分类()
    preview = data(pm.get(path))
    assert preview['declaredPersonDays'] == preview['candidatePersonDays'] == '7.00'
    assert preview['verifiedPersonDays'] == '0.00'
    assert [item['candidatePersonDays'] for item in preview['breakdown']] == ['2.50', '3.00', '1.50']
    assert set(preview['allowedPeriods']) == {'2024-12', '2025-01'}

    def propose(category, amount, *, month='2025-01', input_headers=None):
        payload = {'period': month, 'expectedAmount': amount}
        if category is not None:
            payload['category'] = category
        return pm.post(path, headers=input_headers or headers(report['revision']), json=payload)

    deny(propose(None, '7.00'), 422, 'WEEKLY_EFFORT_CATEGORY_REQUIRED')
    deny(propose('procurement', '2.50'), 422, 'VALIDATION_FAILED')
    deny(propose('development', '2.50', month='2025-02'), 422, 'WEEKLY_EFFORT_PERIOD_INVALID')
    deny(propose('development', '2.50', input_headers=headers(report['revision']-1)), 409, 'VERSION_CONFLICT')
    stable = headers(report['revision'])
    first = data(propose('development', '2.50', input_headers=stable))
    assert data(propose('development', '2.50', input_headers=stable)) == first
    deny(propose('development', '1.00', input_headers=stable), 409, 'IDEMPOTENCY_CONFLICT')
    with ThreadPoolExecutor(2) as pool:
        replayed = list(pool.map(lambda _: data(propose('development', '2.50')), range(2)))
    assert replayed == [first, first]
    second = data(propose('implementation', '3.00'))
    third = data(propose('management', '1.50'))
    assert data(pm.get(f'projects/{team.pid}/costs'))['effort']['actual'] == '0.00'
    deny(核验(pm, first), 403, 'INDEPENDENT_VERIFIER_REQUIRED')
    deny(核验(verifier, first, at='2025-01-07T10:00:00Z'), 422, 'WEEKLY_EFFORT_OCCURRENCE_OUTSIDE_WEEK')
    deny(核验(verifier, first, evidence=f'evidence:{uuid4()}'), 404, 'RESOURCE_NOT_FOUND')
    for item in (first, second, third):
        assert data(核验(verifier, item))['status'] == 'verified'
    assert data(pm.get(f'projects/{team.pid}/costs'))['effort']['actual'] == '7.00'
    assert data(pm.get(path))['candidatePersonDays'] == '0.00'
    rows = data(pm.get(f'projects/{team.pid}/cost-postings'))['items']
    assert {row['category']: row['amountSigned'] for row in rows} == {
        'development': '2.50', 'implementation': '3.00', 'management': '1.50'}
    superseding = publish(pm, 提交(team, parts=分类()))
    deny(pm.get(path), 422, 'WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION')
    assert data(pm.get(路径(superseding, 'effort-posting')))['candidatePersonDays'] == '0.00'
    export = data(pm.get(路径(report, 'weekly-export')))
    assert '研发 2.50 人天' in export['html'] and '管理 1.50 人天' in export['html']


def test_已有分类抵减且未分类净额拦截新分项(team):
    pm, verifier = team.users['pm'], 财务(team)
    url = f'projects/{team.pid}/cost-postings'
    existing = data(pm.post(url + '/categorized', headers=headers(), json={
        'type': 'effort', 'category': 'development', 'amount': '2.50', 'period': '2025-01', 'sourceRef': '本期研发日报原始凭证'}))
    data(核验(verifier, existing))
    week = publish(pm, 提交(team, parts=分类()))
    path = 路径(week, 'effort-posting')
    preview = data(pm.get(path))
    assert preview['candidatePersonDays'] == '4.50'
    unknown = data(pm.post(url, headers=headers(), json={
        'type': 'effort', 'amount': '7.00', 'period': '2025-01', 'sourceRef': '历史未分类合计'}))
    data(核验(verifier, unknown, at='2025-01-08T10:00:00Z'))
    next_week = publish(pm, 提交(team, parts=分类(), period='2025-W02'))
    next_path = 路径(next_week, 'effort-posting')
    preview = data(pm.get(next_path))
    assert preview['reviewRequired'] and not preview['canPropose'] and preview['candidatePersonDays'] == '0.00'
    deny(pm.post(next_path, headers=headers(next_week['revision']), json={
        'period': '2025-01', 'category': 'development', 'expectedAmount': '2.50'}), 409, 'WEEKLY_EFFORT_CLASSIFICATION_UNCLEAR')


def test_分类事实固定引用同类别及本期结构化数值(team):
    pm, reviewer = team.users['pm'], team.users['reviewer']
    report = publish(pm, 提交(team, parts=分类(研发='2.50', 实施='2.50', 管理='2.00'),
        narrative='上期累计投入2.50人天。'))
    path = 路径(report, 'facts/proposals')
    command = {'subject': '周报投入', 'predicate': '本期实施人天', 'typedValue': '2.50', 'quote': '2.50',
        'asOf': '2025-01-02T10:00:00+08:00', 'effortCategory': 'implementation',
        'sourceRef': f"report:{report['reportId']}@{report['revision']}"}

    def propose(**values):
        return pm.post(path, headers=headers(report['revision']), json={**command, **values})

    deny(propose(quote='上期累计投入2.50人天'), 422, 'FACT_EFFORT_SOURCE_MISMATCH')
    deny(propose(asOf='2025-01-08T10:00:00+08:00'), 422, 'FACT_EFFORT_SOURCE_MISMATCH')
    deny(propose(typedValue='2.00'), 422, 'FACT_EFFORT_SOURCE_MISMATCH')
    first = data(propose())
    second = data(propose(effortCategory='development', predicate='本期研发人天'))
    loaded = {item['factId']: item for item in data(pm.get(路径(report, 'facts')))['items']}
    assert loaded[first['factId']]['quotePath'] == '/structuredFields/effort_breakdown/1/personDays'
    assert loaded[second['factId']]['quotePath'] == '/structuredFields/effort_breakdown/0/personDays'
    assert all(item['validationStatus'] == 'candidate' for item in loaded.values())

    def verify(fact):
        return reviewer.post(f"facts/{fact['factId']}/verify", headers=headers(fact['revision']), json={
            'trust': 'medium', 'evidenceRef': command['sourceRef'], 'reason': '独立复核本期申报中的实施人天'})

    assert data(verify(first))['validationStatus'] == 'verified'
    assert data(pm.get(f'projects/{team.pid}/costs'))['effort']['actual'] == '0.00'
    publish(pm, 提交(team, parts=分类()))
    deny(propose(), 409, 'FACT_SOURCE_STALE')
    deny(verify(second), 409, 'FACT_SOURCE_STALE')
    assert any(item['factId'] == first['factId'] and item['validationStatus'] == 'verified'
               for item in data(pm.get(路径(report, 'facts')))['items'])


@pytest.mark.parametrize('parts', [分类(管理='2.50'), 分类(研发='-2.50'), 分类(研发='2.501'),
    [{'category': 'travel', 'personDays': '7.00', 'basis': '交通发票'}],
    [{'category': 'management', 'personDays': '7.00', 'basis': ' '}]],
    ids=['合计不等', '负数', '超精度', '金额分类', '依据缺失'])
def test_拒绝无效周报分类(team, parts):
    response = team.users['pm'].post(team.path, headers=headers(), json=body(
        structuredFields=sections(effort_person_days='7.00', effort_breakdown=parts)))
    deny(response, 422, 'VALIDATION_FAILED')


def 授权(team, pid=None, key='isolated-customer'):
    sponsor = team.people['reporter'].id
    pid = pid or team.pid
    p = db.projects.c
    with team.engine.begin() as c:
        c.execute(db.projects.update().where(p.id == pid).values(sponsor_id=sponsor))
        m = db.project_members.c
        c.execute(db.project_members.update().where(m.project_id == pid, m.person_id == sponsor, m.role == 'member')
                  .values(role='sponsor'))
        at = c.scalar(sa.select(sa.func.clock_timestamp()))
        return c.scalar(authorizations.insert().values(project_id=pid, display_name='隔离收件样例', recipient_key=key,
            channel='synthetic_inbox', approved_by=sponsor, approval_hash='1'*64, approved_at=at,
            expires_at=at+timedelta(days=1)).returning(authorizations.c.id))


def test_正式周报复用下载原件并区分隔离回执(team, live, monkeypatch):
    monkeypatch.setenv('PMO_SYNTHETIC_POLICY_SCHEMA', live[3]['PMO_SYNTHETIC_POLICY_SCHEMA'])
    pm, reviewer, outsider = (team.users[key] for key in ('pm', 'reviewer', 'outsider'))
    authorization, other = 授权(team), 授权(team, team.other)
    pending = 提交(team, parts=分类(), narrative='<script>内容须转义</script>')
    path = 路径(pending, 'delivery')
    deny(pm.get(路径(pending, 'weekly-export')), 422, 'DELIVERY_REQUIRES_PUBLISHED_WEEKLY')
    tentative = {'authorizationId': str(authorization), 'reportHash': pending['contentHash'], 'attachmentHash': 'a'*64}
    deny(pm.post(path, headers=headers(pending['revision']), json=tentative), 422, 'DELIVERY_REQUIRES_PUBLISHED_WEEKLY')
    report = publish(pm, pending)
    choices = data(pm.get(path))
    assert [x['authorizationId'] for x in choices['recipients']] == [str(authorization)]
    deny(outsider.get(path), 404, 'RESOURCE_NOT_FOUND')
    deny(reviewer.get(path), 403, 'FORBIDDEN')
    artifact = data(reviewer.get(路径(report, 'weekly-export')))
    expected = sha256(artifact['html'].encode('utf-8')).hexdigest()
    assert artifact['sha256'] == expected and '<script>' not in artifact['html']
    send = {**tentative, 'attachmentHash': expected}
    deny(pm.post(path, headers=headers(report['revision']), json={**send, 'authorizationId': str(other)}),
         403, 'RECIPIENT_AUTHORIZATION_INVALID')
    deny(pm.post(path, headers=headers(report['revision']), json={**send, 'attachmentHash': 'a'*64}),
         409, 'DELIVERY_CONTENT_CHANGED')
    stable = headers(report['revision'])
    first = data(pm.post(path, headers=stable, json=send))
    assert first['status'] == 'pending' and first['externalReceiptVerified'] is False
    assert data(pm.post(path, headers=stable, json=send)) == first
    deny(pm.post(path, headers=stable, json={**send, 'attachmentHash': 'b'*64}), 409, 'IDEMPOTENCY_CONFLICT')
    assert data(pm.post(path, headers=headers(report['revision']), json=send)) == first
    with team.engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(deliveries).where(deliveries.c.project_id == team.pid)) == 1
    outbox.dispatch(team.engine, 500)
    receipt, = data(pm.get(path))['deliveries']
    assert receipt['status'] == 'delivered' and receipt['externalReceiptVerified'] is False
    assert receipt['attachmentHash'] == expected and receipt['channelReceivedAt']
    assert data(pm.post(path, headers=stable, json=send)) == first
    with team.engine.connect() as c:
        stored = c.execute(sa.select(inbox).where(inbox.c.message_id == UUID(receipt['providerMessageId']))).mappings().one()
        assert stored.attachment.decode() == artifact['html'] and stored.attachment_hash == expected
        sent = c.execute(sa.select(db.outbox_events).where(db.outbox_events.c.dedupe_key == f"weekly-delivery:{first['deliveryId']}"))
        event = sent.mappings().one()
        assert event.event_type == DELIVER and event.status == 'sent'


def test_撤销授权或发布新版后隔离任务不得发送(team, live, monkeypatch):
    monkeypatch.setenv('PMO_SYNTHETIC_POLICY_SCHEMA', live[3]['PMO_SYNTHETIC_POLICY_SCHEMA'])
    pm = team.users['pm']
    approval, next_approval = 授权(team), 授权(team, key='separate-inbox')
    report = publish(pm, 提交(team))
    path = 路径(report, 'delivery')
    digest = data(pm.get(路径(report, 'weekly-export')))['sha256']
    request = {'reportHash': report['contentHash'], 'attachmentHash': digest}
    ids = []
    for item in (approval, next_approval):
        ids.append(data(pm.post(path, headers=headers(report['revision']), json={**request,
            'authorizationId': str(item)}))['deliveryId'])
    with team.engine.begin() as c:
        c.execute(authorizations.update().where(authorizations.c.id == approval).values(revoked_at=sa.func.clock_timestamp()))
    publish(pm, 提交(team))
    deny(pm.post(path, headers=headers(report['revision']), json={**request, 'authorizationId': str(next_approval)}),
         422, 'DELIVERY_REQUIRES_PUBLISHED_WEEKLY')
    outbox.dispatch(team.engine, 500)
    history = {item['deliveryId']: item for item in data(pm.get(path))['deliveries']}
    assert [history[key]['errorCode'] for key in ids] == ['RECIPIENT_AUTHORIZATION_INVALID', 'DELIVERY_REQUIRES_PUBLISHED_WEEKLY']
    assert all(item['status'] == 'blocked' for item in history.values())


def test_最小权限工作进程重启后不重复发送(team, live, monkeypatch):
    from psycopg import sql

    env = live[3]['PMO_SYNTHETIC_POLICY_SCHEMA']
    monkeypatch.setenv('PMO_SYNTHETIC_POLICY_SCHEMA', env)
    pm = team.users['pm']
    approval = 授权(team)
    report = publish(pm, 提交(team))
    path = 路径(report, 'delivery')
    digest = data(pm.get(路径(report, 'weekly-export')))['sha256']
    first = data(pm.post(path, headers=headers(report['revision']), json={
        'authorizationId': str(approval), 'reportHash': report['contentHash'], 'attachmentHash': digest}))
    role = 'r5_weekly_worker_' + uuid4().hex
    with closing(team.engine.raw_connection()) as proxy:
        conn = proxy.driver_connection
        conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(env), sql.Identifier(role)))
        for table in WORKER_READ:
            grant(conn, role, table, 'SELECT', schema=env)
        for table in WORKER_ADD:
            grant(conn, role, table, 'INSERT', schema=env)
        for table, columns in WORKER_EDIT.items():
            grant(conn, role, table, 'UPDATE', columns, schema=env)
        for table, columns in {
            'notifications': 'rule_event_id recipient_person_id', 'projects': 'id org_id pm_id sponsor_id stage',
            'reports': 'id project_id type version_no revision content_hash status is_current_confirmed confirmed_by '
                       'confirmed_at submitted_by submitted_at author_id structured_fields narrative period '
                       'period_start period_end weekly_basis',
            'project_members': 'project_id person_id role assignment_status accepted_at revoked_at',
            'persons': 'id org_id user_id is_active', 'user_accounts': 'id is_active locked_until',
        }.items():
            grant(conn, role, table, 'SELECT', columns, schema=env)
        conn.commit()
    worker = sa.create_engine(team.engine.url.update_query_dict({'options': f'-csearch_path={env} -crole={role}'}))
    try:
        while lease := outbox.claim(worker):
            assert outbox.consume(worker, lease)
            if lease.event_type == DELIVER:
                break  # 模拟进程在持久回执提交后还未确认队列即重启。
            assert outbox.acknowledge(worker, lease)
        assert lease and lease.event_type == DELIVER
        with team.engine.begin() as c:
            c.execute(db.outbox_events.update().where(db.outbox_events.c.id == lease.event_id)
                      .values(locked_until=sa.func.clock_timestamp()))
        assert outbox.dispatch(worker, 20) == 0
        with team.engine.connect() as c:
            assert c.scalar(sa.select(sa.func.count()).select_from(inbox).where(inbox.c.message_id == UUID(first['deliveryId']))) == 1
        receipt, = data(pm.get(path))['deliveries']
        assert receipt['status'] == 'delivered' and receipt['externalReceiptVerified'] is False
    finally:
        worker.dispose()
        with team.engine.begin() as c:
            c.exec_driver_sql(f'DROP OWNED BY {role}')
            c.exec_driver_sql(f'DROP ROLE {role}')


def test_非隔离工作进程不得发送正式周报(team, live, monkeypatch):
    pm = team.users['pm']
    approval = 授权(team)
    report = publish(pm, 提交(team))
    path = 路径(report, 'delivery')
    digest = data(pm.get(路径(report, 'weekly-export')))['sha256']
    pending = data(pm.post(path, headers=headers(report['revision']), json={
        'authorizationId': str(approval), 'reportHash': report['contentHash'], 'attachmentHash': digest}))
    monkeypatch.delenv('PMO_SYNTHETIC_POLICY_SCHEMA', raising=False)
    with team.engine.begin() as c:
        with pytest.raises(ValueError, match='Synthetic inbox not available'):
            from pmo.weekly_delivery import deliver

            deliver(c, {'projectId': str(team.pid), 'deliveryId': pending['deliveryId']})
        assert c.scalar(sa.select(inbox.c.message_id).where(inbox.c.message_id == UUID(pending['deliveryId']))) is None
    assert data(pm.get(path))['deliveries'][0]['status'] == 'pending'
