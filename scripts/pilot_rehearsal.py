"""Complete model-off pilot rehearsals without consuming the two handoff projects."""
import argparse
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
import json
import subprocess
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from pmo import schema_v1 as db
from scripts import pilot_local as local
from scripts import pilot_seed as seed

ANSWERS = [
    'Compared approved plan and verified synthetic actuals, including the signed dual-unit balance.',
    'Reviewed the before/after planning dates, confirmed deliveries and independent risk receipts.',
    'Keep the original synthetic ledger and the receiving scope with the workflow receipt.',
    'Request independent sources and receiving responsibility before formal closing.',
]


class Rehearsal(seed.Pilot):
    def profiles(self):
        if self.evidence.get('rehearsalProfiles'):
            return
        people = {r: self.team[r].get('auth/me').raise_for_status().json() for r in self.team}
        pid = UUID(self.pid)
        org = UUID(people['init']['orgId'])
        with self.pool.begin() as c:
            assert c.execute(sa.select(db.projects.c.org_id, db.projects.c.code).where(
                db.projects.c.id == pid)).one() == (org, self.code)
            t = db.project_members
            # Authority provisioning only. No change, gate, reconciliation, or risk receipt is seeded here.
            for role in ('reconcile', 'review-02'):
                who = UUID(people[role]['personId'])
                c.execute(insert(t).values(project_id=pid, person_id=who, assigned_by=UUID(people['init']['personId']),
                    role='reviewer' if role == 'review-02' else 'member', assignment_status='accepted',
                    accepted_at=sa.func.now()).on_conflict_do_nothing())
            for role in ('reconcile', 'finance'):
                c.execute(sa.text('''INSERT INTO cost_reconciliation_grants(project_id,person_id)
                    SELECT :p,:a WHERE NOT EXISTS (SELECT FROM cost_reconciliation_grants
                        WHERE project_id=:p AND person_id=:a)'''),
                    {'p': pid, 'a': UUID(people[role]['personId'])})
            for role, offset in (('sponsor', 365), ('review-02', -1)):
                c.execute(sa.text('''INSERT INTO change_approval_grants(project_id,person_id,target_type,authority_ref,expires_at)
                    VALUES (:p,:a,'milestone',:ref,now()+make_interval(days=>:offset)) ON CONFLICT DO NOTHING'''),
                    {'p': pid, 'a': UUID(people[role]['personId']), 'offset': offset,
                     'ref': f'{seed.DATASET}:{self.cfg["id"]}:SIMULATED'})
        self.evidence['rehearsalProfiles'] = True
        local.save(self.file, self.evidence)

    def milestone(self, milestone):
        mid = milestone['milestoneId']
        proof = self.upload('milestone-proof-' + mid, 'milestone', mid)
        mark = self.evidence.setdefault('milestoneDate', datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
        return self.write('complete-milestone-' + mid, self.pm, f'milestones/{mid}/complete',
            dict(actualDate=mark, evidenceRef=proof['evidenceRef'], reason='Verified synthetic delivery output.'),
            milestone['revision'])

    def change_cases(self):
        verdict = {'rationale': 'Independent review of the bound synthetic milestone only.'}
        changes = self.path + '/changes'
        for idx, kind in enumerate(('applied', 'stale', 'pending'), 1):
            m = self.steps[f'milestone-{idx}']['data']
            draft = dict(targetType='milestone', targetId=m['milestoneId'], before={'plannedDate': m['plannedDate']},
                         after={'plannedDate': (self.base.date() + timedelta(days=idx * 7 + 1)).isoformat()},
                         reason=f'Simulated {kind} milestone change.')
            create = self.write('change-' + kind, self.pm, changes, draft, m['revision'])
            action = f'changes/{create["changeId"]}/approve'
            if kind == 'applied':
                self.write('change-self-denied', self.pm, action, verdict, expected=403, code='INDEPENDENT_APPROVER_REQUIRED')
                result = self.write('change-independently-applied', 'sponsor', action, verdict)
                assert result['status'] == 'applied'
            elif kind == 'stale':
                self.milestone(m)
                self.write('change-stale-denied', 'sponsor', action, verdict, expected=409, code='APPROVAL_STALE')
            else:
                self.write('change-expired-authority-denied', 'review-02', action, verdict,
                           expected=403, code='INDEPENDENT_APPROVER_REQUIRED')
        page = self.get('sponsor', changes)
        return {'approvable': self.steps['change-pending']['data']['changeId'] in page['approvableIds'],
                'stale': self.steps['change-stale']['data']['changeId'] not in page['approvableIds']}

    def risk_cases(self, *, finish):
        close, handoff = self.risks()
        target = handoff['riskId']
        proof = self.upload('handoff-original', 'risk', target)
        receiver = self.team['risk-receiver'].get('auth/me').raise_for_status().json()['personId']
        command = dict(receiverPersonId=receiver, acceptanceEvidenceRef=proof['evidenceRef'])
        path = f'risks/{target}/handoff'
        details = dict(**command, acceptanceScope='Monitor the synthetic export and escalate unresolved issues.')
        self.write('request-risk-acceptance', 'review-01', path + '/acceptance', dict(**details, kind='request'))
        self.write('risk-handoff-blocked', 'review-01', path, command, expected=422, code='RISK_ACCEPTANCE_REQUIRED')
        if finish:
            self.write('risk-wrong-recipient-denied', 'review-02', path + '/acceptance',
                       dict(**details, kind='confirm', decision='accept'), expected=403, code='RISK_RECEIVER_REQUIRED')
            self.write('risk-received', 'risk-receiver', path + '/acceptance', dict(**details, kind='confirm', decision='accept'))
            self.write('risk-handoff-applied', 'review-01', path, command)
            target = close['riskId']
            proof = self.upload('risk-close-original', 'risk', target)
            self.write('risk-closed', 'review-01', f'risks/{target}/close', dict(resolutionEvidenceRef=proof['evidenceRef']))

    def cost_cases(self, *, finish):
        when = self.evidence.setdefault('costOccurrence', datetime.now(UTC).isoformat())
        lines = []
        for index, (kind, amount) in enumerate(zip(('money', 'effort'), ('12000.00', '12.00'), strict=True), 3):
            source = f'SIM-{self.code}-{kind}'
            posting = self.write('post-' + kind, self.pm, self.path + '/cost-postings',
                dict(type=kind, amount=amount, period=datetime.fromisoformat(when).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m'),
                     sourceRef=source))
            self.write('verify-' + kind, 'finance', f'cost-postings/{posting["postingId"]}/verify',
                dict(reason='Independently compared the synthetic posting with its original.', occurredAt=when))
            if finish:
                self.write('forecast-' + kind, self.pm, self.path + '/cost-forecasts',
                    dict(type=kind, forecastRemaining='0.00', reason='Simulated commitments all completed.'), index, method='PUT')
            lines.append(dict(type=kind, netActual=amount, sourceRef=source + '-external'))
        proof = self.upload('reconciliation-original', 'project', self.pid,
            dict(schemaVersion=1, projectId=self.pid, coveredThrough=when, lines=lines))
        path = self.path + '/cost-reconciliations'
        state = self.get('reconcile', path)
        cmd = dict(costHash=state['costHash'], evidenceRefs=[proof['evidenceRef']])
        if not finish:
            assert not state['canCreate'] and not state.get('current')
            self.write('reconciliation-awaits-delivery', 'reconcile', path, cmd, state['revision'],
                       expected=422, code='RECONCILIATION_LOCKED')
            return
        if 'status' not in self.steps.get('reconciled-current', {}):
            assert state['canCreate']
        self.write('posting-verifier-cannot-reconcile', 'finance', path, cmd, state['revision'],
                   expected=403, code='INDEPENDENT_RECONCILER_REQUIRED')
        self.write('reconciled', 'reconcile', path, cmd, state['revision'], expected=422, code='RECONCILIATION_SOURCE_COVERAGE')
        cutoff = self.evidence.setdefault('reconciliationCutoff', datetime.now(UTC).isoformat())
        current = self.upload('reconciliation-current-original', 'project', self.pid,
            dict(schemaVersion=1, projectId=self.pid, coveredThrough=cutoff, lines=lines))
        self.write('reconciled-current', 'reconcile', path,
                   dict(costHash=state['costHash'], evidenceRefs=[current['evidenceRef']]), state['revision'])

    def transition(self, stage, *, code=None):
        req = self.write('transition-request-' + (code or stage), self.pm,
            self.path + '/transition-requests', dict(toStage=stage, reason='Synthetic workflow rehearsal.'),
            self.current()['revision'], expected=422 if code else 200, code=code)
        if not code and stage != 'archived':
            self.write('transition-applied-' + stage, self.pm, self.path + '/transitions',
                       dict(toStage=stage, approvalRequestId=req['requestId']), req['revision'])
        return req

    def archive(self):
        if self.evidence.get('rehearsalComplete'):
            assert self.current()['stage'] == 'archived'
            return
        if 'status' not in self.steps.get('closing-final-decision', {}):
            self.transition('delivering', code='MILESTONE_COMPLETION_REQUIRED')
            for m in self.get(self.pm, self.path + '/milestones')['items']:
                self.milestone(m)
            self.delivery()
            self.transition('delivering')
            check = self.get(self.pm, self.path + '/closing/check')
            if 'status' not in self.steps.get('risk-closed', {}):
                assert 'BLOCKING_RISK_UNRESOLVED' in {gap['code'] for gap in check['missing']}
            if 'status' not in self.steps.get('reconciled-current', {}):
                assert 'COST_RECONCILIATION_REQUIRED' in {gap['code'] for gap in check['missing']}
            self.risk_cases(finish=True)
            self.cost_cases(finish=True)
            assert self.get(self.pm, self.path + '/closing/check')['eligible']
            self.gate('closing')
        self.transition('retrospective')
        retro = self.path + '/retrospective'
        self.transition('archived', code='RETROSPECTIVE_ANSWERS_REQUIRED')
        self.write('retrospective-answers', self.pm, retro, {'answers': ANSWERS}, self.get(self.pm, retro)['revision'], method='PUT')
        self.transition('archived', code='CONFIRMED_LESSON_REQUIRED')
        lesson = self.write('lesson-proposed', self.pm, self.path + '/lessons', dict(category='governance',
            what='Independent synthetic evidence makes delivery reproducible.', rootCause='Different actors checked each source.',
            advice='Prepare the cost source and risk receiver before closing.'))
        self.write('lesson-confirmed', 'review-01', f'lessons/{lesson["lessonId"]}/confirm', {}, lesson['revision'])
        req = self.transition('archived')
        path = f'approvals/{req["requestId"]}/decide'
        body = dict(decision='approve', rationale='Independently reviewed simulated answers and confirmed lesson.')
        self.write('archive-self-denied', self.pm, path, body, req['revision'], expected=403)
        self.write('archive-applied', 'review-01', path, body, req['revision'])
        self.write('archived-retro-read-only', self.pm, retro, {'answers': ANSWERS},
                   self.get(self.pm, retro)['revision'], method='PUT', expected=422)
        assert self.current()['stage'] == 'archived'
        self.evidence['rehearsalComplete'] = True
        local.save(self.file, self.evidence)


def rehearse():
    local.private_home()
    cfg = local.settings()
    manifest = local.read('pilots.json', {})
    if not local.ready() or local.read('runtime.json', {}).get('models') != {} or not local.PILOT_CODES <= manifest.keys():
        raise ValueError('Rehearse only on the seeded model-off pilot instance.')
    with ExitStack() as stack:
        pool = seed.engine(cfg)
        stack.callback(pool.dispose)
        credentials = seed.accounts(pool, cfg)
        team = {r: seed.login(stack, credentials, r) for r in seed.ROLES if r != 'admin'}
        results = {}
        for code, label, pm, money, effort, counts in (
                ('SIM-EXEC-001', 'SIM execution handoff', 'pm-a', '100000.00', '100.00', (4, 5, 3)),
                ('SIM-EXEC-001-DRILL', 'SIM execution lifecycle drill', 'pm-a', '100000.00', '100.00', (4, 5, 3)),
                ('SIM-NEW-001-DRILL', 'SIM initiation lifecycle drill', 'pm-b', '80000.00', '80.00', (3, 4, 3))):
            item = Rehearsal(pool, cfg, team, code, label, pm=pm, money=money, effort=effort,
                priorities=tuple(p for p, count in zip(('P0', 'P1', 'P2'), counts, strict=True) for _ in range(count)))
            if code in manifest:
                item.pid = manifest[code]['id']
                item.path = 'projects/' + item.pid
                assert item.current()['stage'] == 'executing'
                item.profiles()
                if not item.evidence.get('showcaseCases'):
                    change = item.change_cases()
                    assert all(change.values())
                    item.risk_cases(finish=False)
                    item.cost_cases(finish=False)
                    item.evidence['showcaseCases'] = change
                    local.save(item.file, item.evidence)
            else:
                item.project()
                item.profiles()
                item.execute()
                item.archive()
            results[code] = item.summary()
            print(json.dumps({code: results[code]}, ensure_ascii=False), flush=True)
        b = manifest['SIM-NEW-001']
        assert team['init'].get(f'projects/{b["id"]}').raise_for_status().json()['data']['project']['stage'] == 'drafting'
        local.save('rehearsals.json', results)


def browser():
    local.private_home()
    local.verify_container(local.settings())
    if not local.ready() or local.read('runtime.json', {}).get('models') != {}:
        raise ValueError('The pilot needs to be available with models off.')
    pilots = local.read('pilots.json')
    drill = local.read('rehearsals.json')['SIM-EXEC-001-DRILL']
    users = local.read('synthetic-logins.json')
    run = {'url': local.WEB, 'a': pilots['SIM-EXEC-001'], 'b': pilots['SIM-NEW-001'], 'otherReport': drill['reportId'],
           'logins': {k: {'username': seed.user_name(k), 'password': users[k]} for k in ('pm-a', 'init')}}
    files = local.local_file('edge-evidence')
    files.mkdir(exist_ok=True)
    subprocess.run(['node', 'e2e/pilot-handoff.mjs'], cwd=local.ROOT / 'web', env=local.env_base(),
                   input=json.dumps(run | {'screenshots': str(files)}), text=True, encoding='utf-8', check=True, timeout=85)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='rehearse', choices=('rehearse', 'browser'))
    (browser if parser.parse_args().action == 'browser' else rehearse)()
