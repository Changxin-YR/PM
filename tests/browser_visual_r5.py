"""用真实 Edge 检查隔离业务工作台布局，生成可审查的页面截图。"""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess

import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.health_calculation import TZ, week_key
from test_health import prepare, sample
from test_cost_reassessment import 设置预测
from test_reports import body, sections
from test_reviews import data, headers, member, team as team
from browser_case import browser_sessions, vite


def test_隔离工作台真实布局和页面截图(live, team):
    pid, people, users, _ = team
    pm = users['pm']
    project, _ = prepare(live, team, priority='P0')
    设置预测(pm, project, '121.00')
    sample(pm, project)
    today = datetime.now(TZ).date()
    for kind, period in [('daily', today.isoformat()), ('weekly', week_key(today))]:
        report = data(pm.post(project + '/reports', headers=headers(), json=body(
            reportType=kind, period=period, structuredFields=sections(),
            narrative='合成验收：本期完成客户交付准备，同期申报研发投入 2.50 人天。')))
        path = f"reports/{report['reportId']}"
        if kind == 'weekly':
            data(pm.post(path + '/confirm', headers=headers(report['revision']), json={}))
    exception_project = data(pm.post('projects', headers=headers(), json={
        'code': 'R5-VIS-EXCEPTION', 'name': '需求例外布局演练（合成）'}))['projectId']
    with live[0].begin() as conn:
        conn.execute(db.projects.update().where(db.projects.c.id == pid).values(name='实施布局演练（合成）'))
        conn.execute(db.projects.update().where(db.projects.c.id == exception_project).values(
            stage='delivering', pm_id=people['pm']))
        member(conn, exception_project, people['pm'], people['pm'], 'pm')
        account = conn.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == people['initiator']))
        conn.execute(db.user_roles.insert().values(user_id=account, role='system_admin'))
    output = Path(os.environ.get('PMO_R5_VISUAL_OUTPUT', '.local/r5-visual')).resolve()
    with vite(live[2]) as (root, url):
        subprocess.run(['node', 'e2e/visual-r5.mjs'], cwd=root, check=True, timeout=600, env={
            **os.environ, 'PMO_BROWSER_URL': url, 'PMO_R5_VISUAL_OUTPUT': str(output),
            'PMO_TEST_SESSIONS': json.dumps(browser_sessions(users, 'pm', 'initiator')),
            'PMO_R5_VISUAL_PROJECTS': json.dumps({'execution': pid, 'exceptions': exception_project}),
        })
