"""隔离的真实浏览器验收，覆盖快速立项备案和评分结果校准。"""
import json
import os
from pathlib import Path

from browser_case import browser_sessions, run_browser, vite
from test_lifecycle import (
    begin_execution,
    close_materials,
    complete_milestones,
    execute,
    gate,
    grant_closure_roles,
    propose,
)
from test_r5_review import 交卡
from test_r5_review import 快通沙箱 as 快通沙箱  # noqa: PLC0414
from test_reviews import data, decide, headers, start_charter
from test_reviews import team as team  # noqa: PLC0414


def 浏览器验收(port, scenario, pid, clients, **details):
    screenshots = Path(os.environ.get('PMO_R5_REVIEW_SCREENSHOTS', '.local/r5-review-screenshots')).resolve()
    with vite(port) as (root, url):
        run_browser(root, url, 'review-r5', {
            'PMO_R5_SCENARIO': scenario,
            'PMO_R5_REVIEW_SCREENSHOTS': str(screenshots),
            'PMO_TEST_SESSIONS': json.dumps(browser_sessions(clients, *clients)),
            'PMO_R5_DATA': json.dumps({'projectId': pid, **details}),
        })


def test_专业评审齐备后发起人从工作台人工备案(live, team, 快通沙箱):
    pid, _, _, _ = team
    review = start_charter(pid, 快通沙箱)
    交卡(快通沙箱['reviewer'], review, [5, 4, 4, 4])
    owner = 快通沙箱['initiator']
    port = owner.base_url.port
    浏览器验收(port, 'fast', pid, {'initiator': owner}, reviewId=review.split('/')[-1])
    assert data(owner.get(f'projects/{pid}'))['project']['stage'] == 'designing'


def test_在真实结项证据上显示原分实际分差值和待判定(live, team):
    pid, _, users, _ = team
    pm, reviewer, customer, reconciler = (users[key] for key in ('pm', 'reviewer', 'client_representative', 'outsider'))
    grant_closure_roles(live, team)
    project, requirement, risk = begin_execution(live, team, calibration=True)
    complete_milestones(pm, reviewer, project)
    data(execute(pm, project, data(propose(pm, project, 'delivering'))))
    _, risk_proof, cost_proof = close_materials(pm, reviewer, customer, project, requirement, risk)
    data(reviewer.post(f'risks/{risk}/close', headers=headers(), json={'resolutionEvidenceRef': risk_proof['evidenceRef']}))
    reconcile = project + '/cost-reconciliations'
    basis = data(reconciler.get(reconcile))
    data(reconciler.post(reconcile, headers=headers(basis['revision']), json={
        'costHash': basis['costHash'], 'evidenceRefs': [cost_proof['evidenceRef']],
        'overrunReasons': {'money': 'estimate', 'effort': 'scope'},
    }))
    closing = data(decide(reviewer, gate(pm, project, 'closing')))['decision']
    data(execute(pm, project, data(propose(pm, project, 'retrospective'))))
    data(reviewer.put(project + '/review-calibration/results/strategicAlignment', headers=headers(), json={
        'status': 'evaluated', 'score': '3', 'actualAnchorCode': 'score_3',
        'reasoning': '实际交付与部分主线目标相关，依据为本次已核验的结项材料。',
        'evidenceRefs': [cost_proof['evidenceRef']],
    }))
    data(reviewer.put(project + '/review-calibration/results/customerAttractiveness', headers=headers(), json={
        'status': 'incomparable', 'reasoning': '暂缺客户实际复用资料，待补证后比较。',
    }))
    浏览器验收(live[2], 'calibration', pid, {'reviewer': reviewer},
         closingDecisionRef=f"decision:{closing['decisionId']}@1")
