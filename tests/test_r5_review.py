from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy

import pytest
import sqlalchemy as sa
from httpx import Client
from test_database_roles import serve
from test_reviews import (
    CHARTER,
    bench,
    data,
    decide,
    headers,
    member,
    score_command,
    start_charter,
)
from test_reviews import team as team  # noqa: PLC0414

from pmo import policy
from pmo import schema_v1 as db
from pmo.review_scores import ANCHORS, check_rationale


@pytest.mark.parametrize(('理由', '预期'), (
    ('客户是谁尚不清楚，尚无意向', 'conflict'),
    ('尚未确认是否愿意付费', 'unknown'),
    ('并非客户是谁尚不清楚', 'unknown'),
    ('受访人曾说“客户是谁尚不清楚”', 'unknown'),
    ('如果客户是谁尚不清楚，尚无意向，则重新调查', 'unknown'),
    ('未来计划重新确认，尚无意向', 'unknown'),
), ids=('明确矛盾', '证据不足', '双重否定', '引用', '假设', '未来目标'))
def test_评分只匹配基于材料的当前断言(理由, 预期):
    确认 = check_rationale('customerAttractiveness', {
        'score': 5, 'selectedAnchorCode': ANCHORS[4], 'rationale': 理由, 'evidenceRefs': ['charter:fixture@2'],
    }, {'id': 'fixture', 'revision': 2, 'target_client': '客户是谁尚不清楚', 'attractiveness': '尚无意向'})
    assert 确认.kind == 预期


def test_评分的正面锚点需要所有条件有明确材料支持():
    吸引力 = '客户真实需求已核验；付费意愿已验证；已验证复制效应'
    材料 = {'id': 'fixture', 'revision': 2, 'target_client': '客户明确', 'attractiveness': 吸引力}
    评分 = {'score': 5, 'selectedAnchorCode': ANCHORS[4], 'rationale': f'客户明确。{吸引力}',
            'evidenceRefs': ['charter:fixture@2']}
    确认 = check_rationale('customerAttractiveness', 评分, 材料)
    assert 确认.kind == 'consistent' and len(确认.findings) == 4
    assert check_rationale('customerAttractiveness', {**评分, 'evidenceRefs': ['charter:old@1']}, 材料).kind == 'unknown'


def test_评审人预检矛盾保留分数和人工说明(live, team):
    _, api, _, _ = live
    pid, _, users, bearer = team
    path = start_charter(pid, users, fields={**CHARTER, 'targetClient': '客户是谁尚不清楚', 'attractiveness': '尚无意向'})
    reviewer, owner = users['reviewer'], users['initiator']
    state = bench(reviewer, path)
    command = score_command(state, [3, 5, 3, 3])
    customer = command['dimensions']['customerAttractiveness']
    customer.update(rationale='客户是谁尚不清楚，尚无意向', retainedReason='保留独立评分，另行人工核对客户调研材料。')
    key = headers(state['review']['revision'])
    preview = path + '/scores/check'
    result = data(reviewer.post(preview, headers=key, json=command))['dimensions']['customerAttractiveness']
    assert result['kind'] == 'conflict' and result['selectedAnchorCode'] == 'score_5'
    assert {item['rationaleQuote'] for item in result['findings']} == {'客户是谁尚不清楚', '尚无意向'}
    assert all(item['sourceRef'] == customer['evidenceRefs'][0] for item in result['findings'])
    assert bench(reviewer, path)['scores'] == []
    assert owner.post(preview, headers=key, json=command).status_code == 403
    assert api.post('/api/v1/' + preview, headers={**key, **bearer}, json=command).status_code == 403
    wrong = deepcopy(command)
    wrong['dimensions']['customerAttractiveness']['evidenceRefs'] = ['charter:wrong-project@1']
    assert reviewer.post(preview, headers=key, json=wrong).json()['code'] == 'REVIEW_SOURCE_NOT_FROZEN'
    written = reviewer.post(path + '/scores', headers=key, json=command)
    card, = data(written)['scores']
    assert card['rationaleChecks']['customerAttractiveness'] == result
    assert card['dimensions']['customerAttractiveness'] == customer
    assert written.json() == reviewer.post(path + '/scores', headers=key, json=command).json()
    assert reviewer.post(preview, headers=key, json=command).json()['code'] == 'VERSION_CONFLICT'


@pytest.fixture
def 快通沙箱(live, team, tmp_path):
    engine, _, _, env = live
    schema = env['PMO_SYNTHETIC_POLICY_SCHEMA']
    with serve(engine.url, tmp_path, evidence_dir=env['PMO_EVIDENCE_DIR'], extra_env={
        'PMO_SYNTHETIC_POLICY_SCHEMA': schema, 'PMO_R5_FAST_TRACK_SCHEMA': schema,
    }) as scoped, ExitStack() as clients:
        yield {role: clients.enter_context(Client(base_url=scoped.base_url, trust_env=False, cookies=source.cookies,
                headers={'X-CSRF-Token': source.headers['X-CSRF-Token']})) for role, source in team[2].items()}


def 交卡(client, path, values):
    state = bench(client, path)
    payload = score_command(state, values)
    for dimension in payload['dimensions'].values():
        dimension['rationale'] = '已独立核对当前送审版本的材料和立项评分锚点。'
    return data(client.post(path + '/scores', headers=headers(state['review']['revision']), json=payload))


def test_四点二快通由发起人一次确认备案并原子推进(live, team, 快通沙箱):
    engine, api, _, _ = live
    pid, people, users, bearer = team
    owner, reviewer, second, sponsor = (快通沙箱[role] for role in ('initiator', 'reviewer', 'outsider', 'sponsor'))
    with engine.begin() as c:
        member(c, pid, people['outsider'], people['initiator'], 'reviewer')
    path = start_charter(pid, 快通沙箱)
    url = path + '/confirmations'
    command = {'role': 'initiator', 'fastTrackReason': '确认独立评审满足快速立项条件，登记评审会备案。'}
    state = 交卡(reviewer, path, [5, 4, 4, 4])
    assert not bench(owner, path)['canConfirmInitiator']
    assert owner.post(url, headers=headers(state['review']['revision']), json=command).json()['code'] == (
        'REVIEW_REQUIRED_SCORES_INCOMPLETE')
    complete = 交卡(second, path, [5, 4, 4, 4])
    key = headers(complete['review']['revision'])
    assert complete['assessment']['total'] == '4.20'
    assert bench(owner, path)['canConfirmInitiator'] and not bench(sponsor, path)['canConfirmSponsor']
    assert sponsor.post(url, headers=key, json=command).json()['code'] == 'CONFIRMATION_ROLE_REQUIRED'
    assert owner.post(url, headers=key, json={'role': 'initiator'}).json()['code'] == 'FAST_TRACK_INITIATOR_FILING_REQUIRED'
    assert decide(reviewer, path).json()['code'] == 'FAST_TRACK_INITIATOR_FILING_REQUIRED'
    assert users['initiator'].post(url, headers=key, json=command).json()['code'] == 'POLICY_PENDING'
    assert api.post('/api/v1/' + url, headers={**key, **bearer}, json=command).status_code == 403
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER r5_filing_failure BEFORE INSERT ON outbox_events '
                          'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        assert owner.post(url, headers=key, json=command).status_code == 500
        pending = bench(owner, path)
        assert pending['review']['revision'] == complete['review']['revision']
        assert 'decision' not in pending and pending['confirmations'] == []
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER r5_filing_failure ON outbox_events')
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda h: owner.post(url, headers=h, json=command), [key, headers(complete['review']['revision'])]))
    assert sorted(result.status_code for result in replies) == [200, 409]
    receipt = next(result for result in replies if result.is_success)
    used = dict(receipt.request.headers)
    assert owner.post(url, headers={'If-Match': used['if-match'], 'Idempotency-Key': used['idempotency-key']},
                      json=command).json() == receipt.json()
    assert owner.post(url, headers={'If-Match': used['if-match'], 'Idempotency-Key': used['idempotency-key']},
                      json={**command, 'fastTrackReason': '改写同一请求的备案说明。'}).json()['code'] == 'IDEMPOTENCY_CONFLICT'
    result = data(receipt)['decision']
    assert set(result['sourceRefs']) >= {f"decision:{result['decisionId']}@1", f"review-filing:{result['decisionId']}"}
    assert result['fastTrackFiling']['ruleVersion'] == policy.FAST_CHARTER_VERSION
    assert result['decidedBy'] == result['fastTrackFiling']['filedBy'] == str(people['initiator'])
    assert result['stage'] == data(owner.get(f'projects/{pid}'))['project']['stage'] == 'designing'
    assert data(owner.get(path + '/meeting'))['minutes'] == '快速立项备案：' + command['fastTrackReason']
    rid = receipt.headers['X-Request-ID']
    with engine.begin() as c:
        decisions = c.execute(sa.select(db.decisions.c.id).where(db.decisions.c.project_id == pid)).scalars().all()
        audited = c.scalar(sa.select(db.audit_logs.c.decision_id).where(db.audit_logs.c.request_id == rid))
        event = c.scalar(sa.select(db.outbox_events.c.payload).where(db.outbox_events.c.dedupe_key == rid))
        assert len(decisions) == 1 and str(decisions[0]) == str(audited) == event['decisionId'] == result['decisionId']
        c.execute(db.project_members.update().where(db.project_members.c.project_id == pid,
            db.project_members.c.person_id == people['initiator'], db.project_members.c.role == 'initiator')
            .values(revoked_at=sa.func.now()))
    assert owner.post(url, headers={'If-Match': used['if-match'], 'Idempotency-Key': used['idempotency-key']},
                      json=command).status_code in (403, 404)


def test_快通策略改变时必须重新冻结审批而不借用旧版评审(live, team, 快通沙箱):
    pid, _, users, _ = team
    path = start_charter(pid, users)
    reviewer, owner = users['reviewer'], 快通沙箱['initiator']
    original = 交卡(reviewer, path, [5, 4, 4, 4])
    assert original['assessment']['classification'] == 'fast_track'
    after_policy = bench(owner, path)
    assert after_policy['stale'] and not after_policy['canConfirmInitiator']
    rejected = owner.post(path + '/confirmations', headers=headers(after_policy['review']['revision']), json={
        'role': 'initiator', 'fastTrackReason': '必须重新冻结适用快速立项的正式规则。',
    })
    assert rejected.json()['code'] == 'REVIEW_SNAPSHOT_STALE'
    assert data(owner.get(f'projects/{pid}'))['project']['stage'] == 'charter_review'


@pytest.mark.parametrize(('分数', '分类'), (([4, 3, 3, 3], 'normal'), ([1, 5, 5, 5], 'veto')), ids=('三点二普通', '一票否决'))
def test_版本化快通仍遵循普通评审和否决(live, team, 快通沙箱, 分数, 分类):
    pid, _, _, _ = team
    path = start_charter(pid, 快通沙箱)
    reviewer = 快通沙箱['reviewer']
    state = 交卡(reviewer, path, 分数)
    assert state['assessment']['classification'] == 分类
    owner = 快通沙箱['initiator']
    denied = owner.post(path + '/confirmations', headers=headers(state['review']['revision']),
                        json={'role': 'initiator', 'fastTrackReason': '登记本轮的快速立项人工确认。'})
    assert denied.json()['code'] == 'FAST_TRACK_NOT_AVAILABLE'
    if 分类 == 'normal':
        assert data(decide(reviewer, path))['decision']['stage'] == 'designing'
    else:
        assert decide(reviewer, path).json()['code'] == 'CHARTER_SCORE_VETO'
