"""LR-04 重估材料、独立裁决、历史和事务一致性。"""
from concurrent.futures import ThreadPoolExecutor

import sqlalchemy as sa

from pmo import schema_v1 as db
from test_health import code, prepare, sample
from test_lifecycle import upload
from test_reviews import data, headers, issue, team as team


def 设置预测(pm, project, amount, kind='money'):
    revision = data(pm.get(project + '/costs'))['plan']['revision']
    data(pm.put(project + '/cost-forecasts', headers=headers(revision), json={
        'type': kind, 'forecastRemaining': amount, 'reason': '试点补充剩余投入预测依据。'}))


def 任务(pm, project, kind='money'):
    rows = data(pm.get(project + '/tasks'))['items']
    return next(row for row in rows if row.get('reassessment', {}).get('costType') == kind
                and row['reassessment']['state'] != 'stale')


def 材料(pm, reviewer, project):
    milestone = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    ref = upload(pm, reviewer, project, 'milestone', milestone)['evidenceRef']
    return {'currentSituation': '原目标保持，本期独立核验证据已归档。', 'attribution': 'scope',
            'recommendation': '维持核心范围和已有预算，分批处理变更需求。', 'evidenceRefs': [ref]}


def test_超支只经独立裁决关闭且预算与阶段保持(live, team):
    engine, api, _, _ = live
    pid, people, users, _ = team
    pm, reviewer, sponsor, outsider = (users[role] for role in ('pm', 'reviewer', 'sponsor', 'outsider'))
    project, _ = prepare(live, team)
    设置预测(pm, project, '115.01')
    assert sample(pm, project)['costLine'] == 'red'
    assert all('reassessment' not in row for row in data(pm.get(project + '/tasks'))['items'])
    设置预测(pm, project, '120.01')
    设置预测(pm, project, '12.01', 'effort')
    sample(pm, project)
    row = 任务(pm, project)
    assert 任务(pm, project, 'effort')['taskId'] != row['taskId']
    assert row['reassessment']['state'] == 'material_required' and row['reassessment']['budget'] == '100.00'
    task = 'tasks/' + row['taskId']
    write, decide = task + '/cost-reassessment', task + '/cost-reassessment/decision'
    code(pm.patch(task, headers=headers(row['revision']), json={'fieldsTask': {'status': 'done'}}),
         422, 'COST_REASSESSMENT_CONTROLLED')
    command = 材料(pm, reviewer, project)
    code(outsider.post(write, headers=headers(row['revision']), json=command), 404, 'RESOURCE_NOT_FOUND')
    token = issue(pm, pid, ['pmo_task_list'])
    code(api.post('/api/v1/' + write, headers={**headers(row['revision']), 'Authorization': 'Bearer ' + token},
                  json=command), 403, 'FORBIDDEN')
    key = headers(row['revision'])
    response = pm.post(write, headers=key, json=command)
    saved = data(response)
    assert pm.post(write, headers=key, json=command).json() == response.json()
    assert saved['status'] == 'in_progress' and not saved['reassessment']['canDecide']
    submission = saved['reassessment']['history'][0]['recordId']
    request = {'submissionId': submission, 'option': 'continue', 'rationale': '依原预算继续核心范围，并持续观察差额。',
               'conditions': '下一报告周期复核实际投入与剩余预测。'}
    code(pm.post(decide, headers=headers(saved['revision']), json=request), 403, 'FORBIDDEN')
    code(sponsor.post(decide, headers=headers(saved['revision']), json={**request, 'submissionId': row['taskId']}),
         409, 'COST_REASSESSMENT_SUBMISSION_CHANGED')
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER 重估事务回滚 BEFORE INSERT ON outbox_events '
                          'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        code(sponsor.post(decide, headers=headers(saved['revision']), json=request), 500, 'DATABASE_FAILURE')
        assert 任务(pm, project)['status'] == 'in_progress'
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER 重估事务回滚 ON outbox_events')
    key = headers(saved['revision'])
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: sponsor.post(decide, headers=key, json=request), range(2)))
    done = data(responses[0])
    assert responses[1].json() == responses[0].json()
    assert done['status'] == 'done' and done['reassessment']['state'] == 'decided'
    assert sample(pm, project)['costLine'] == 'red'
    assert data(pm.get(project + '/costs'))['money']['budget'] == '100.00'
    assert data(pm.get(project))['project']['stage'] == 'executing'
    with engine.begin() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(db.decisions).where(
            db.decisions.c.subject == 'cost-reassessment:' + row['taskId'])) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(db.audit_logs).where(db.audit_logs.c.project_id == pid,
            db.audit_logs.c.action == 'human_cost_reassessment_decide', db.audit_logs.c.result == 'success')) == 1
        c.execute(db.project_members.update().where(db.project_members.c.project_id == pid,
            db.project_members.c.person_id == people['sponsor'], db.project_members.c.role == 'sponsor')
            .values(revoked_at=sa.func.now()))
    assert sponsor.post(decide, headers=key, json=request).status_code in (403, 404)


def test_成本依据实质变化后拒绝旧决议并保留记录(live, team):
    _, _, users, _ = team
    pm, reviewer, sponsor = (users[role] for role in ('pm', 'reviewer', 'sponsor'))
    project, _ = prepare(live, team)
    设置预测(pm, project, '121.00')
    sample(pm, project)
    row = 任务(pm, project)
    task = 'tasks/' + row['taskId'] + '/cost-reassessment'
    old = data(pm.post(task, headers=headers(row['revision']), json=材料(pm, reviewer, project)))
    设置预测(pm, project, '122.00')
    request = {'submissionId': old['reassessment']['history'][0]['recordId'], 'option': 'adjust',
               'rationale': '重新编制本项目剩余成本预测。'}
    code(sponsor.post(task + '/decision', headers=headers(old['revision']), json=request), 409, 'COST_REASSESSMENT_STALE')
    sample(pm, project)
    assert 任务(pm, project)['taskId'] != row['taskId']
    historic = data(pm.get(project + '/tasks', params={'taskId': row['taskId']}))['items'][0]
    assert historic['status'] == 'cancelled' and historic['reassessment']['state'] == 'stale'
    assert historic['reassessment']['history'] == old['reassessment']['history']
