"""真实 Edge 检查分类对账、发布附件、Fact 和隔离送达。"""
import json
import os
from pathlib import Path
import subprocess
import sys

from pmo import schema_v1 as db
from browser_case import browser_sessions, run_browser, vite
from test_r5_reporting import 分类, 提交, 授权, 核验, 财务
from test_reports import body, data, headers, sections, team as team
from test_weekly_report_workflow import publish


def test_浏览器周报分类来源附件和隔离送达(live, team):
    pm, verifier = team.users['pm'], 财务(team)
    with live[0].begin() as connection:
        connection.execute(db.projects.update().where(db.projects.c.id == team.pid).values(
            code='R5-REPORT', name='报告分类与送达演练（合成）'))
    授权(team)
    path = f'projects/{team.pid}/cost-postings/categorized'
    existing = data(pm.post(path, headers=headers(), json={'type': 'effort', 'category': 'development',
        'amount': '2.50', 'period': '2025-01', 'sourceRef': '本期研发日报与签到凭证'}))
    data(核验(verifier, existing))
    report = publish(pm, 提交(team, parts=分类()))
    length = 980
    entries = [{'summary': '周报正文单页摘要中文排版校验。' * 14} for _ in range(4)]
    breakdown = [dict(item, basis=item['basis'] + '依据内容' * 220)
                 for item in 分类(管理='1.00')]
    breakdown.append({'category': 'unclassified', 'personDays': '0.50', 'basis': '待澄清' * 320})
    long = data(pm.post(team.path, headers=headers(), json=body(
        period='2025-W02', narrative='完整内容留档。' * length,
        structuredFields=sections(effort_person_days='7.00', effort_breakdown=breakdown,
            milestone_progress=entries, requirement_progress=entries, risks_and_blockers=entries))))
    long_report = publish(pm, long)
    output = Path(os.environ.get('PMO_R5_VISUAL_OUTPUT', '.local/r5-reporting-visual')).resolve()
    worker = subprocess.Popen([sys.executable, '-m', 'pmo.outbox', '--watch'], env=live[3])
    try:
        with vite(live[2]) as (web, url):
            run_browser(web, url, 'r5-reporting', {
                'PMO_TEST_SESSIONS': json.dumps(browser_sessions(team.users, 'pm', 'reviewer')),
                'PMO_R5_REPORTING_SCOPE': json.dumps((str(team.pid), report['reportId'], long_report['reportId'])),
                'PMO_R5_REPORTING_OUTPUT': str(output),
            })
    finally:
        worker.terminate()
        worker.wait(timeout=10)
