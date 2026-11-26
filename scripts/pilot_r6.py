"""Exercise the Chinese R6 projects through the loopback workbench API with models off."""
import argparse
from contextlib import ExitStack
from datetime import timedelta
import os
import sys
from uuid import UUID

import sqlalchemy as sa

from pmo import policy, schema_v1 as db
from pmo.review_scores import ANCHORS
from scripts import pilot_local as local, pilot_r6_setup as setup, pilot_seed as seed

CHARTER = ('review.charter', policy.FAST_CHARTER)
MOBILE = (*CHARTER, 'review.feasibility', 'review.closing', 'review.calibrate', 'requirement.customer-confirm',
          'cost.category', 'cost.reconciliation', 'report.effort', 'report.delivery', 'change.milestone', 'transition.archive')
CASES = (
    ('R6-QA-FAST', '企业行政智能问答系统', 'fast'),
    ('R6-QA-NORMAL', '企业行政智能问答系统（普通立项）', 'normal'),
    ('R6-QA-APPEAL', '企业行政智能问答系统（评分复议）', 'appeal'),
    ('R6-QA-REFILE', '企业行政智能问答系统（否决后重报）', 'refile'),
    ('R6-QA-LOW', '企业行政智能问答系统（低分复核）', 'low'),
    ('R6-MOBILE', '企业移动工单管理系统', 'mobile'),
)


class Project(seed.Pilot):
    dataset = 'PMO-R6-中文演练'
    seed = 6

    def done(self, label):
        return 'status' in self.steps.get(label, {})

    def appoint(self, people, prefix):
        roles = {'pm': 'pm', 'sponsor': 'sponsor', 'review1': 'reviewer', 'review2': 'reviewer'}
        if prefix == 'm':
            roles.update(customer='client_representative', finance='member', reconcile='member')
        for who, role in roles.items():
            label = 'appointment-' + who
            if self.done(label + '-accept'):
                continue
            endpoint = 'role-invitations' if role in ('pm', 'sponsor') else 'member-invitations'
            item = self.write(label, 'init', f'{self.path}/{endpoint}',
                dict(personId=str(people[f'{prefix}-{who}']), role=role), self.current('init')['revision'])
            path = 'role-invitations/' + item['invitationId']
            for verb, actor, inbox in [('issue', 'appoint', 'me/appointment-queue'), ('accept', who, 'me/role-invitations')]:
                step = label + '-' + verb
                saved = self.steps.get(step)
                revision = saved['revision'] if saved else next(row['revision'] for row in self.get(actor, inbox)
                    if row['invitationId'] == item['invitationId'])
                self.write(step, actor, path + '/' + verb, {}, revision)

    def charter(self, variant):
        mobile = variant == 'mobile'
        label = 'charter-material' if variant != 'amended' else 'charter-amended'
        if self.done(label):
            return
        fields = dict(valueProp='一线班组可用手机派单、回传工单并跟踪超期。' if mobile else '行政同事可检索带出处的现行制度并确认适用范围。',
            targetClient='企业现场运维班组' if mobile else '企业行政服务台与新入职员工',
            attractiveness='试点班组已整理报修记录，愿意逐单核验。' if mobile else '行政服务台每周重复答复差旅和报销问题，可抽样验证。',
            expectedBenefit='减少重复派工，保留签收回执。' if mobile else '缩短制度定位时间，保留制度版本和引用。',
            successCriteria='十份样例工单均可追溯派发、接单及签收记录。' if mobile else '十道制度题逐条核对出处，过期制度返回人工复核。',
            roughCost=self.money, roughEffort=self.effort, roughDuration=21,
            topRisks=[dict(statement='资料版本可能早于现行流程。', response='指定业务代表复核版本与适用日期。')])
        if variant == 'amended':
            fields.update(targetClient='行政、人事两个服务组的制度专员',
                          successCriteria='两个服务组共抽取二十道问题，逐条对照生效版本并复核引用页码。')
        charter = self.get('init', self.path + '/charter')
        self.write(label, 'init', self.path + '/charter', {'fieldsCharter': fields}, charter['expectedRevision'], method='PUT')

    def calibration(self, who, *, amended=False):
        label = 'calibration-amended' if amended else 'calibration-plan'
        if self.done(label):
            return
        path = self.path + '/review-calibration'
        expected = {'strategicAlignment': '逐条对照立项卡的目标与验收记录。',
                    'customerAttractiveness': '请使用部门独立核对实际使用样例。',
                    'financialResourceReturn': '据本期核验的投入与效益比较预期。',
                    'riskControllability': '检查已登记风险的发生与处置记录。'}
        plan = {key: dict(expected=text, reviewerId=str(who), observeAfter=self.base.date().isoformat())
                for key, text in expected.items()}
        state = self.get('init', path)
        self.write(label, 'init', path + '/plan', {'dimensions': plan}, state['planRevision'], method='PUT')

    def submit(self, label, kind):
        who = 'init' if kind == 'charter' else 'pm'
        if not self.done(label):
            preflight = self.get(who, self.path + '/reviews/prepare', params={'gate': kind})['gateCheck']
            if not preflight['eligible']:
                raise RuntimeError(f'{self.code} {kind}: {[gap["code"] for gap in preflight["missing"]]}')
        item = self.write(label, who, self.path + '/reviews', {'reviewType': kind}, self.current(who)['revision'])
        return 'reviews/' + item['reviewId']

    def score(self, label, path, who, kind, values):
        label += '-anchored' if kind == 'charter' else '-criteria'
        if self.done(label):
            return self.steps[label]['data']
        state = self.get(who, path + '/workbench')
        lead = next(ref for ref in state['materialRefs'] if ref.startswith('charter:' if kind == 'charter' else 'milestone:'))
        reasons = ('逐项核对立项卡的受益对象、试点反馈及验收样例。', '独立复核现有依据，各项结论以冻结材料为准。')
        cards = {dim['key']: dict(score=val, selectedAnchorCode=ANCHORS[val-1],
                 rationale=(f'针对{dim["label"]}，' + (reasons[who == 'review2'] if val >= 3 else '材料尚缺现场复核记录。')),
                 evidenceRefs=[lead]) for dim, val in zip(state['dimensions'], values, strict=True)}
        for dim in state['dimensions']:
            card = cards[dim['key']]
            card['judgments'] = [{'criterion': criterion['key'], 'value': 'unknown'}
                for criterion in dim['criteria'] if criterion['minimumScore'] <= card['score']]
            card['retainedReason'] = '按冻结材料保留本次评分，尚缺的依据交由已登记的评价人在观察时复核。'
        return self.write(label, who, path + '/scores', dict(reviewType=kind, round=state['round'], dimensions=cards),
                          state['review']['revision'])

    def decide(self, label, path, verdict, **kwargs):
        if self.done(label):
            return self.steps[label]['data']
        state = self.get('review1', path + '/workbench')
        return self.write(label, 'review1', path + '/decisions', {'verdict': verdict,
            'rationale': '核对本轮冻结材料、独立评分和遗留条件后作出裁决。', 'approvalRequestId': state['approvalRequestId'],
            **kwargs.pop('fields', {})}, state['review']['revision'], **kwargs)

    def charter_review(self, variant, *, label='charter'):
        path = self.submit(label + '-submit', 'charter')
        if self.done(label + '-decision'):
            return
        scores = {'fast': [5, 4, 4, 4], 'normal': [4, 3, 3, 3], 'mobile': [4, 4, 4, 4],
                  'appeal': [1, 5, 5, 5], 'refile': [1, 4, 4, 4], 'low': [3, 3, 3, 3]}[variant]
        self.score(label + '-card-1', path, 'review1', 'charter', scores)
        state = self.score(label + '-card-2', path, 'review2', 'charter', [5]*4 if variant == 'appeal' else scores)
        expected = {'fast': 'fast_track', 'appeal': 'veto', 'refile': 'veto', 'low': 'below_threshold'}.get(variant, 'normal')
        assert state['assessment']['classification'] == expected
        if expected in ('veto', 'below_threshold'):
            self.decide(label + '-denied-pass', path, 'pass', expected=422,
                        code='CHARTER_SCORE_VETO' if expected == 'veto' else 'CHARTER_SCORE_BELOW_THRESHOLD')
            if variant != 'appeal':
                self.decide(label + '-decision', path, 'reject')
                return
            self.write(label + '-appeal', 'init', path + '/appeals', {'reason': '请重新核对立项卡已登记的受益对象和试点依据。'},
                       state['review']['revision'])
            state = self.score(label + '-rescored', path, 'review1', 'charter', [3, 5, 5, 5])
            assert (state['round'], state['assessment']['classification']) == (2, 'fast_track')
        if state['assessment']['classification'] == 'fast_track':
            self.write(label + '-decision', 'init', path + '/confirmations',
                {'role': 'initiator', 'fastTrackReason': '两份独立评分卡齐备，依据冻结立项卡确认本轮快速立项备案。'},
                state['review']['revision'])
        else:
            self.decide(label + '-decision', path, 'pass')

    def planning(self, people, org, passwords):
        if self.done('feasibility-decision'):
            return
        contact = self.write('customer-contact', 'pm', self.path + '/stakeholders',
            dict(name='宋兰', stakeholderType='customer', influence='high', expectation='按工单版本抽查接单记录和交付回执。'))
        with self.pool.begin() as c:
            setup.admin.authenticate_operator(c, org, people['admin'], passwords['admin'])
            args = {'project_id': UUID(self.pid)}
            setup.mandate(c, org, people, 'm-customer', 'bind-customer', self.code + '／客户代表绑定',
                          **args, stakeholder_id=UUID(contact['stakeholderId']))
            setup.mandate(c, org, people, 'm-reconcile', 'grant-cost-reconciliation', self.code + '／独立对账',
                          **args, expires_at=self.base + timedelta(days=60))
            setup.mandate(c, org, people, 'm-sponsor', 'grant-change-approval', self.code + '／里程碑变更',
                          **args, target_type='milestone', expires_at=self.base + timedelta(days=60))
        for i, (title, criteria, priority) in enumerate((
            ('班组收到派工后确认接单', '十张样例均记录实际接单人和接单时点', 'P0'),
            ('维修人员回传处理结果', '逐单显示状态、回传说明与附件', 'P1'),
            ('客户代表可查看工单签收记录', '签收明细与工单版本逐条一致', 'P2')), 1):
            self.write(f'requirement-{i}', 'pm', self.path + '/requirements', dict(desc=title, priority=priority,
                acceptanceCriteria=criteria, plannedConfirmAt=(self.base.date()+timedelta(days=14)).isoformat()))
        for i, title in enumerate(('工单流程评审', '现场联调及回传', '客户签收与移交'), 1):
            self.write(f'milestone-{i}', 'pm', self.path + '/milestones', dict(name=title, ordinal=i, isKey=True,
                plannedDate=(self.base.date()+timedelta(days=7*i)).isoformat()))
        amounts = dict(procurement='40000', travel='10000', cloud='20000', allocation='10000',
                       development='40', implementation='30', management='10')
        self.write('budget-classified', 'pm', self.path + '/cost-categories/budget-draft',
                   {'amounts': amounts}, method='PUT')
        self.write('forecast-classified', 'pm', self.path + '/cost-categories/forecast',
                   {'amounts': amounts, 'reason': '开工时逐项核对采购、差旅、云服务、分摊和三类岗位的剩余投入。'}, method='PUT')
        self.write('risk', 'pm', self.path + '/risks', dict(statement='现场网络可能延迟回单。',
            response='保留本地回单记录并在恢复后逐单核验。', prob=2, impact=2, isBlocking=False))
        for who, office in [('pm', 'pm'), ('customer', 'client_representative')]:
            path = self.path + '/requirement-baseline'
            baseline = self.get(who, path)
            self.write('baseline-'+who, who, path+'/sign', dict(role=office, contentHash=baseline['contentHash'],
                       reason='逐条核对派工、回单和签收的验收标准。'), baseline['revision'])
        path = self.submit('feasibility-submit', 'feasibility')
        for who in ('review1', 'review2'):
            self.score('feasibility-'+who, path, who, 'feasibility', [4]*5)
        self.decide('feasibility-decision', path, 'conditional_pass', fields={'conditions': [dict(
            title='抽查三张离线回单的同步记录', assigneeId=str(people['m-pm']), dueAt=(self.base+timedelta(days=7)).isoformat())]})


def run(case=None, *, cases=CASES, after_planning=None):
    if not local.R6_PREVIEW or os.getenv('PMO_SYNTHETIC_POLICY_SCHEMA') or not local.ready():
        raise RuntimeError('Start the dedicated R6 preview with registered policy and models off')
    local.private_home()
    cfg, runtime = local.settings(), local.read('runtime.json', {})
    if runtime.get('models') != {} or (runtime.get('web'), runtime.get('api')) != (local.WEB, local.API):
        raise RuntimeError('R6 runtime identity or model configuration mismatch')
    os.environ['PMO_POLICY_ENVIRONMENT'] = local.R6_ENVIRONMENT
    with ExitStack() as stack:
        pool = seed.engine(cfg)
        stack.callback(pool.dispose)
        lock = stack.enter_context(pool.connect())
        if not lock.scalar(sa.text("SELECT pg_try_advisory_lock(hashtext('pmo-r6-pilot-fixture'))")):
            raise RuntimeError('R6 fixture already running')
        cases = [item for item in cases if case is None or item[0] == case]
        groups = {'m' if variant == 'mobile' else 'q' for _, _, variant in cases}
        required = {role for role in setup.ROSTER if role.partition('-')[0] in groups} | {'admin', 'appoint'}
        clients, people, passwords, org = setup.onboard(pool, stack, required)
        setup.qualifications(pool, org, people, passwords, {code for action in MOBILE for code in policy.ACTIONS[action]})
        for code, name, variant in cases:
            group = 'm' if variant == 'mobile' else 'q'
            team = {key[2:]: client for key, client in clients.items() if key.startswith(group+'-')}
            item = Project(pool, cfg, team | {'appoint': clients['appoint']}, code, name, pm='pm',
                           money='80000.00', effort='80.00', priorities=())
            created = item.write('project', 'init', 'projects', dict(code=code, name=name,
                description='基于企业服务场景的中文隔离演练，按实际人员任命与阶段流程推进。'))
            item.pid = created['projectId']
            item.path = 'projects/' + item.pid
            item.appoint(people, group)
            item.charter(variant)
            item.calibration(people[group+'-review1'])
            setup.activate(pool, org, people, passwords, UUID(item.pid), MOBILE if group == 'm' else CHARTER)
            item.charter_review(variant)
            if variant == 'refile':
                item.write('resubmit', 'init', item.path+'/resubmit', {'reason': '补充跨服务组受益范围和抽题验收依据后重新送审。'},
                           item.current('init')['revision'])
                item.charter('amended')
                item.calibration(people['q-review1'], amended=True)
                item.charter_review('normal', label='refiled')
            if variant == 'mobile':
                item.planning(people, org, passwords)
                if after_planning is not None:
                    after_planning(item)
            state = item.current()
            print(f'{code} {state["stage"]} {len(item.steps)}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'login'), default='run', nargs='?')
    parser.add_argument('role', choices=setup.ROSTER, default='m-pm', nargs='?')
    parser.add_argument('--case', choices=[code for code, _, _ in CASES])
    args = parser.parse_args()
    if args.action == 'login':
        if not local.R6_PREVIEW or not sys.stdout.isatty():
            parser.error('Use a private interactive R6 terminal to view a synthetic login')
        local.private_home()
        print(f'{local.WEB}\nr6-{args.role}\n{local.read("r6-logins.json")[args.role]}')
    else:
        run(args.case)


if __name__ == '__main__':
    main()
