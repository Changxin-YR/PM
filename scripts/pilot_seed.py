"""Repeatable, model-off seed for the two persistent synthetic PMO pilots."""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import json
import secrets
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, PngImagePlugin
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from pmo import schema_v1 as db
from pmo.bootstrap import bootstrap
from pmo.review_scores import ANCHORS
from pmo.sessions import hasher
from scripts import pilot_local as local

DATASET = 'PMO-SIM-1'
SEED = 104036
ORG = '[SIMULATED ONLY] PMO V1.0.4 pilots'
ROLES = ('admin', 'init', 'pm-a', 'pm-b', 'sponsor', 'review-01', 'review-02', 'customer', 'finance',
         'risk-receiver', 'outsider', 'appoint', 'reconcile')


def user_name(role):
    return 'sim-' + role


def engine(cfg):
    local.verify_database(cfg)
    pool = sa.create_engine(local.database(cfg, 'migrate'), hide_parameters=True)
    with pool.connect() as c:
        if c.exec_driver_sql('SELECT current_database(),current_schema(),current_user').one() != (
                local.DB_NAME, local.schema(cfg), 'pmo_migrate'):
            raise ValueError('Synthetic seed requires the dedicated migration login and schema.')
    return pool


def accounts(pool, cfg):
    credentials = local.read('synthetic-logins.json', {})
    missing = set(ROLES) - credentials.keys()
    if missing:
        credentials.update({r: secrets.token_urlsafe(25) for r in missing})
        local.save('synthetic-logins.json', credentials)
    with pool.begin() as c:
        existing = c.execute(sa.select(db.organizations.c.id, db.organizations.c.name)).all()
        if not existing:
            bootstrap(c, ORG, user_name('admin'), 'SIM-ADMIN', credentials['admin'])
            org = c.scalar(sa.select(db.organizations.c.id))
        elif len(existing) != 1 or existing[0].name != ORG:
            raise ValueError('Unexpected organization; refusing to change any accounts.')
        else:
            org = existing[0].id
        for role in ROLES[1:]:
            if c.scalar(sa.select(db.user_accounts.c.id).where(db.user_accounts.c.username == user_name(role))):
                continue
            account = c.scalar(db.user_accounts.insert().values(username=user_name(role),
                password_hash=hasher.hash(credentials[role]), is_active=True).returning(db.user_accounts.c.id))
            person = uuid4()
            c.execute(db.persons.insert().values(id=person, org_id=org, user_id=account,
                created_by=person, name=user_name(role).upper(), is_active=True))
            if role in ('init', 'finance', 'reconcile'):
                c.execute(db.user_roles.insert().values(user_id=account,
                    role='delegator' if role == 'init' else 'finance_verifier'))
            if role == 'appoint':
                for office in ('pm', 'sponsor'):
                    c.execute(sa.text('''INSERT INTO appointment_authorities (org_id,person_id,role,authority_ref,expires_at)
                        VALUES (:o,:p,:r,:ref,now()+interval '365 days')'''),
                        {'o': org, 'p': person, 'r': office, 'ref': f'{DATASET}:{cfg["id"]}:SIMULATED'})
    return credentials


def login(stack, credentials, role):
    client = stack.enter_context(httpx.Client(base_url=local.API, trust_env=False, timeout=25))
    response = client.post('auth/login', json={'username': user_name(role), 'password': credentials[role]})
    response.raise_for_status()
    client.headers['X-CSRF-Token'] = response.json()['csrfToken']
    return client


class Pilot:
    def __init__(self, pool, cfg, team, code, name, *, pm, money, effort, priorities):
        self.pool, self.cfg, self.team = pool, cfg, team
        self.code, self.name = code, name
        self.pm, self.money, self.effort, self.priorities = pm, money, effort, priorities
        self.file = f'{code}-receipts.json'
        self.evidence = local.read(self.file)
        if self.evidence is None:
            self.evidence = dict(dataset=DATASET, seed=SEED, batch=cfg['id'],
                                 baseTime=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), steps={})
            local.save(self.file, self.evidence)
        self.base = datetime.fromisoformat(self.evidence['baseTime'])
        self.steps = self.evidence['steps']

    def get(self, role, path, **kwargs):
        response = self.team[role].get(path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f'{self.code} {role} GET {path}: {response.status_code} {response.json().get("code")}')
        return response.json()['data']

    def write(self, label, role, path, body=None, revision=1, *, method='POST', expected=200, code=None, uploads=None):
        step = self.steps.get(label)
        if step is not None and 'status' in step:
            return step.get('data', {})
        key = sha256(f'{DATASET}:{SEED}:{self.cfg["id"]}:{self.code}:{label}'.encode()).hexdigest()
        if step is None:
            step = self.steps[label] = dict(role=role, path=path, method=method, revision=revision, body=body, key=key)
            local.save(self.file, self.evidence)
        if (step['role'], step['path'], step['method']) != (role, path, method):
            raise ValueError('Recorded operation does not match this dataset version.')
        kwargs = uploads or ({} if step['body'] is None else {'json': step['body']})
        response = self.team[role].request(method, path,
            headers={'If-Match': str(step['revision']), 'Idempotency-Key': key}, **kwargs)
        payload = response.json()
        if response.status_code != expected or (code is not None and payload.get('code') != code):
            raise RuntimeError(f'{self.code} {label} ({role}): {response.status_code} {payload.get("code")} '
                               f'from {response.headers.get("X-Request-ID")}')
        step.update(status=response.status_code, requestId=response.headers.get('X-Request-ID'),
                    code=payload.get('code'), data=payload.get('data', {}))
        local.save(self.file, self.evidence)
        return step['data']

    def current(self, role=None):
        return self.get(role or self.pm, self.path)['project']

    def access(self):
        people = {r: self.team[r].get('auth/me').raise_for_status().json() for r in self.team}
        path = self.path + '/role-invitations'
        for office, role in (('pm', self.pm), ('sponsor', 'sponsor')):
            if office + '-propose' in self.steps and 'status' in self.steps[office + '-propose']:
                revision = self.steps[office + '-propose']['revision']
            else:
                revision = self.current('init')['revision']
            proposal = self.write(office + '-propose', 'init', path,
                                  dict(personId=people[role]['personId'], role=office), revision)
            invitation = f'role-invitations/{proposal["invitationId"]}'
            for verb, who, inbox in (('issue', 'appoint', 'me/appointment-queue'), ('accept', role, 'me/role-invitations')):
                label = f'{office}-{verb}'
                saved = self.steps.get(label)
                revision = saved['revision'] if saved else next(row['revision'] for row in self.get(who, inbox)
                                                                if row['invitationId'] == proposal['invitationId'])
                self.write(label, who, invitation + '/' + verb, {}, revision)
        # Infrastructure only: there is no public registration operation for these independent authorities.
        if 'authorities' in self.evidence:
            return
        with self.pool.begin() as c:
            pid = UUID(self.pid)
            org = UUID(people['init']['orgId'])
            target = c.execute(sa.select(db.projects.c.org_id, db.projects.c.code).where(db.projects.c.id == pid)).one()
            if target != (org, self.code):
                raise ValueError('Refusing to grant authority outside the pilot project.')
            owner = UUID(people['init']['personId'])
            for who, role in (('review-01', 'reviewer'), ('review-02', 'reviewer'), ('customer', 'client_representative'),
                              ('finance', 'member'), ('risk-receiver', 'member'), ('reconcile', 'member')):
                person = UUID(people[who]['personId'])
                c.execute(insert(db.project_members).values(project_id=pid, person_id=person, assigned_by=owner,
                    role=role, assignment_status='accepted', accepted_at=sa.func.now()).on_conflict_do_nothing())
            c.execute(sa.text('''INSERT INTO change_approval_grants(project_id,person_id,target_type,authority_ref,expires_at)
                VALUES(:p,:a,'requirement',:ref,now()+interval '365 days') ON CONFLICT DO NOTHING'''),
                {'p': pid, 'a': UUID(people['sponsor']['personId']), 'ref': f'{DATASET}:{self.cfg["id"]}:SIMULATED'})
            c.execute(sa.text('''INSERT INTO cost_reconciliation_grants(project_id,person_id)
                SELECT :p,:a WHERE NOT EXISTS (SELECT FROM cost_reconciliation_grants WHERE project_id=:p AND person_id=:a)'''),
                {'p': pid, 'a': UUID(people['reconcile']['personId'])})
            customer = UUID(people['customer']['personId'])
            if not c.scalar(sa.select(db.stakeholders.c.id).where(db.stakeholders.c.project_id == pid,
                                                               db.stakeholders.c.linked_person_id == customer).limit(1)):
                c.execute(db.stakeholders.insert().values(project_id=pid, created_by=owner, name='Synthetic customer office',
                                                          type='customer', linked_person_id=customer))
        self.evidence['authorities'] = {r: people[r]['personId'] for r in people}
        local.save(self.file, self.evidence)

    def project(self):
        created = self.write('project', 'init', 'projects',
            dict(code=self.code, name=self.name,
                 description=f'SIMULATED ONLY | {DATASET} | seed {SEED} | batch {self.cfg["id"]}'))
        self.pid = created['projectId']
        self.path = 'projects/' + self.pid
        self.access()
        if 'status' in self.steps.get('charter-material', {}):
            return
        charter = self.get('init', self.path + '/charter')
        self.write('charter-material', 'init', self.path + '/charter', {
            'fieldsCharter': dict(valueProp='Synthetic delivery rehearsal', targetClient='Simulated client only',
                attractiveness='Bounded workflow qualification', expectedBenefit='Traceable acceptance evidence',
                successCriteria='Independent receipts across a synthetic project', roughCost=self.money,
                roughEffort=self.effort, roughDuration=21,
                topRisks=[dict(statement='Synthetic access delay', response='Check the simulated onboarding list')])},
            charter['expectedRevision'], method='PUT')

    def material(self):
        for idx, priority in enumerate(self.priorities, start=1):
            self.write(f'requirement-{idx}', self.pm, self.path + '/requirements',
                dict(desc=f'Synthetic requirement {idx:02d}: verify workflow output', priority=priority,
                     acceptanceCriteria=f'Simulated reader can reconcile sample output {idx:02d}',
                     plannedConfirmAt=(self.base.date() + timedelta(days=14)).isoformat()))
        for idx in range(1, 4):
            self.write(f'milestone-{idx}', self.pm, self.path + '/milestones',
                dict(name=f'SIM checkpoint {idx}', ordinal=idx, plannedDate=(self.base.date() + timedelta(days=7 * idx)).isoformat(), isKey=True))
        for idx, (kind, budget) in enumerate(zip(('money', 'effort'), (self.money, self.effort), strict=True), start=1):
            self.write('budget-' + kind, self.pm, self.path + '/cost-budgets', {'type': kind, 'budget': budget}, idx, method='PUT')

    def gate(self, kind, *, values=None):
        if 'status' in self.steps.get(f'{kind}-final-decision', {}):
            return
        requestor = 'init' if kind == 'charter' else self.pm
        submitted = self.write(f'{kind}-submit', requestor, self.path + '/reviews',
                               {'reviewType': kind}, self.current(requestor)['revision'])
        path = 'reviews/' + submitted['reviewId']
        for who in ('review-01', 'review-02') if kind != 'closing' else ():
            state = self.get(who, path + '/workbench')
            lead = next(ref for ref in state['materialRefs'] if ref.startswith('charter:' if kind == 'charter' else 'milestone:'))
            scores = values or [5 if kind == 'charter' else 4] * len(state['dimensions'])
            self.write(f'{kind}-score-{who}', who, path + '/scores',
                dict(reviewType=kind, round=state['round'], dimensions={dim['key']: dict(score=value,
                    selectedAnchorCode=ANCHORS[value - 1], rationale='Independent review of synthetic material only.', evidenceRefs=[lead])
                    for dim, value in zip(state['dimensions'], scores, strict=True)}), state['review']['revision'])
        if kind == 'charter':
            for role in ('sponsor', 'init'):
                snapshot = self.get(role, path + '/workbench')
                self.write('charter-confirm-' + role, role, path + '/confirmations',
                           {'role': 'initiator' if role == 'init' else role}, snapshot['review']['revision'])
        state = self.get('review-01', path + '/workbench')
        command = dict(verdict='pass', rationale='Simulated independent decision, not business signoff.',
                       approvalRequestId=state['approvalRequestId'])
        if kind == 'charter':
            self.write('charter-self-approval-denied', requestor, path + '/decisions', command,
                       state['review']['revision'], expected=403)
        self.write(f'{kind}-final-decision', 'review-01', path + '/decisions', command, state['review']['revision'])

    def execute(self):
        if 'status' in self.steps.get('feasibility-final-decision', {}):
            return
        self.gate('charter')
        self.material()
        self.risks()
        path = self.path + '/requirement-baseline'
        for role, receipt_role in ((self.pm, 'pm'), ('customer', 'client_representative')):
            baseline = self.get(role, path)
            self.write('baseline-' + receipt_role, role, path + '/sign',
                       dict(contentHash=baseline['contentHash'], role=receipt_role, reason='SIMULATED baseline only.'), baseline['revision'])
        self.gate('feasibility')

    def reports(self):
        period = self.base.date().isoformat()
        iso = self.base.isocalendar()
        week = f'{iso.year}-W{iso.week:02d}'
        for kind, day in (('daily', period), ('weekly', week)):
            fields = dict(milestone_progress=[dict(summary='Synthetic rehearsal checkpoint, no completion claimed.')],
                          effort_person_days='2.00', requirement_progress=[dict(summary='Sample field reviewed.')],
                          risks_and_blockers=[dict(summary='Simulated handoff still needs receiving confirmation.')])
            self.write('report-' + kind, self.pm, self.path + '/reports',
                dict(reportType=kind, period=day, structuredFields=fields,
                     narrative='Simulated work only. We logged 2.00 person-days on the trial export.'))

    def upload(self, label, kind, resource, content=None):
        revision = self.get(self.pm, self.path + '/evidence')['collectionRevision']
        if content is not None:
            source = ('synthetic.json', json.dumps(content).encode('utf-8'), 'application/json')
        else:
            png = BytesIO()
            info = PngImagePlugin.PngInfo()
            info.add_text('Synthetic', f'{DATASET}: {label}: {resource}')
            Image.new('RGB', (24, 24), '#637d89').save(png, format='PNG', pnginfo=info)
            source = ('synthetic.png', png.getvalue(), 'image/png')
        item = self.write(label + '-upload', self.pm, self.path + '/evidence', revision=revision,
            uploads={'files': {'file': source},
                     'data': dict(linkedResourceType=kind, linkedResourceId=resource, description=label + ' [SIMULATED]')})
        return self.write(label + '-verify', 'review-02', f'evidence/{item["evidenceId"]}/verify',
                          dict(reason='Read the original synthetic fixture, no actual customer evidence.', sha256=item['sha256']), item['revision'])

    def risks(self):
        risks = []
        for i in range(2):
            risks.append(self.write(f'risk-{i}', self.pm, self.path + '/risks',
                dict(statement=f'Synthetic blocking risk {i + 1}', response='Independent resolution requested.', prob=2, impact=3, isBlocking=True)))
        return risks

    def delivery(self, *, leave_pending=False):
        risks = self.risks()
        self.reports()
        requirements = self.get(self.pm, self.path + '/requirements', params={'pageSize': 100})['items']
        if Counter(r['priority'] for r in requirements) != Counter(self.priorities):
            raise ValueError('Pilot requirement makeup differs from the expected seed.')
        for idx, req in enumerate(requirements):
            if leave_pending and idx == 0:
                continue
            rid = req['requirementId']
            proof = self.upload('requirement-' + rid, 'requirement', rid)
            for action, role in (('deliver', self.pm), ('accept', self.pm), ('customer-confirm', 'customer')):
                req = self.write(f'{rid}-{action}', role, f'requirements/{rid}/{action}',
                                 {'evidenceRef': proof['evidenceRef']}, req['revision'])
        return risks

    def summary(self):
        actor = self.pm
        state = self.current(actor)
        return {'id': self.pid, 'stage': state['stage'], 'name': self.name,
                'requirements': len(self.priorities), 'priorityCounts': dict(Counter(self.priorities)),
                'writes': len([value for value in self.steps.values() if 'status' in value]),
                'reportId': self.steps.get('report-daily', {}).get('data', {}).get('reportId')}


def run():
    local.private_home()
    cfg = local.settings()
    runtime = local.read('runtime.json', {})
    if not local.ready() or runtime.get('models') != {}:
        raise RuntimeError('Start the pilot with models off before seeding.')
    with ExitStack() as stack:
        pool = engine(cfg)
        stack.callback(pool.dispose)
        credentials = accounts(pool, cfg)
        team = {r: login(stack, credentials, r) for r in ROLES if r != 'admin'}
        specs = [('SIM-NEW-001', '知识服务立项项目（合成）', 'pm-b', '80000.00', '80.00', (3, 4, 3)),
                 ('SIM-EXEC-001', '移动工单实施项目（合成）', 'pm-a', '100000.00', '100.00', (4, 5, 3))]
        manifest = local.read('pilots.json', {})
        for code, name, pm, money, effort, weights in specs:
            priorities = tuple(priority for priority, n in zip(('P0', 'P1', 'P2'), weights, strict=True) for _ in range(n))
            item = Pilot(pool, cfg, team, code, name, pm=pm, money=money, effort=effort, priorities=priorities)
            # A completed seed is a readback, not a reset of user edits.
            if code in manifest:
                current = item.get(pm, f'projects/{manifest[code]["id"]}')["project"]
                print(f'{code} already seeded: {current["stage"]}', flush=True)
                continue
            item.project()
            if code == 'SIM-NEW-001':
                item.material()
                assert item.current()['stage'] == 'drafting'
            else:
                item.execute()
                assert item.current()['stage'] == 'executing'
                item.delivery(leave_pending=True)
            manifest[code] = item.summary()
            local.save('pilots.json', manifest)
            print(json.dumps({code: manifest[code]}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('seed', 'login'))
    parser.add_argument('role', default='pm-a', nargs='?', choices=ROLES)
    args = parser.parse_args()
    if args.action == 'seed':
        run()
    else:
        local.private_home()
        if not __import__('sys').stdout.isatty():
            raise SystemExit('Read synthetic sign-in details in a private interactive terminal only.')
        credentials = local.read('synthetic-logins.json')
        print(f'{local.WEB}\n{user_name(args.role)}\n{credentials[args.role]}')


if __name__ == '__main__':
    main()
