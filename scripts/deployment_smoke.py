"""Use an empty, disposable loopback Compose project; exercises a real DB outage."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'deploy' / 'compose.yaml'


def run(*args, stdin=None):
    return subprocess.run(args, cwd=ROOT, input=stdin, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', check=True, timeout=120).stdout.strip()


def wait_for(check, timeout=60):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return
        time.sleep(1)
    raise RuntimeError(f'Timed out: {check.__name__}')


def main():
    if not __debug__:
        raise SystemExit('Smoke checks require assertions enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', help='A fresh pmo104-rc-<8 hex digits> project')
    args = parser.parse_args()
    if not re.fullmatch(r'pmo104-rc-[0-9a-f]{8}', args.project):
        parser.error('Expected a disposable pmo104-rc-<8 hex digits> project')
    envfile = ROOT / '.local' / f'{args.project}.env'
    compose = ('docker', 'compose', '-p', args.project, '--env-file', str(envfile), '-f', str(CONFIG))

    def dc(*args, stdin=None):
        return run(*compose, *args, stdin=stdin)

    def sql(query):
        return dc('exec', '-T', 'db', 'psql', '-U', 'pmo_bootstrap', '-d', 'pmo',
                  '-v', 'ON_ERROR_STOP=1', '-Atc', query)

    def runtime(service, code):
        return dc('exec', '-T', service, 'python', '-c', code)

    for service in ('api', 'scheduler', 'outbox', 'web', 'db'):
        container = dc('ps', '-q', service)
        assert container, service
        labels = json.loads(run('docker', 'inspect', '--format', '{{json .Config.Labels}}', container))
        assert labels['com.docker.compose.project'] == args.project
        assert labels['com.docker.compose.service'] == service
        assert Path(labels['com.docker.compose.project.config_files']).resolve() == CONFIG
        assert Path(labels['com.docker.compose.project.environment_file']).resolve() == envfile
        bindings = json.loads(run('docker', 'inspect', '--format', '{{json .HostConfig.PortBindings}}', container))
        if service == 'web':
            assert set(bindings) == {'8080/tcp'} and len(bindings['8080/tcp']) == 1
            assert bindings['8080/tcp'][0]['HostIp'] == '127.0.0.1'
        else:
            assert not bindings, service
    port = dc('port', 'web', '8080')
    assert re.fullmatch(r'127\.0\.0\.1:[0-9]+', port)
    head = sql('SELECT version_num FROM alembic_version;')
    assert sql('SELECT count(*) FROM organizations;') == '0', 'Smoke requires a brand-new database'
    for service, role in [('api', 'pmo_api'), ('scheduler', 'pmo_api'), ('outbox', 'pmo_outbox')]:
        connected = runtime(service, 'from pmo.db import engine\nwith engine().connect() as c:\n'
            ' print(c.exec_driver_sql("SELECT current_user || chr(58) || rolsuper::text FROM pg_roles WHERE rolname=current_user").scalar_one())')
        assert connected == f'{role}:false', service
    assert runtime('api', 'import os; print(os.getenv("PMO_REPORT_ANALYSIS"))') == '0'
    print(f'project={args.project} migration={head} roles=restricted model=off', flush=True)

    password = secrets.token_urlsafe(24)
    dc('run', '--rm', '-T', '--no-deps', 'migrate', 'python', '-c',
       'import sys\nfrom pmo.bootstrap import bootstrap\nfrom pmo.db import engine\n'
       'with engine().begin() as c:\n bootstrap(c,"Isolated synthetic rehearsal","synthetic-admin","Synthetic administrator",sys.stdin.readline().strip())',
       stdin=password + '\n')
    url = f'http://{port}'
    with httpx.Client(base_url=url, timeout=12, trust_env=False) as client:
        assert client.get('/').raise_for_status().headers['Content-Type'].startswith('text/html')
        assert client.get('/health/live').json() == {'status': 'live'}
        assert client.get('/health/ready').json() == {'status': 'ready'}
        assert client.get('/api/v1/projects').status_code == 401
        login = client.post('/api/v1/auth/login', json={'username': 'synthetic-admin', 'password': password}).raise_for_status()
        csrf = login.json()['csrfToken']
        assert client.get('/api/v1/auth/me').raise_for_status().json()['isSystemAdmin']
        def write(path, body):
            return client.post(path, headers={'X-CSRF-Token': csrf, 'Idempotency-Key': uuid4().hex},
                               json=body).raise_for_status().json()['data']

        for i in range(1, 16):
            project = write('/api/v1/projects', {'code': f'RC-SMOKE-{i:02}', 'name': f'RC project {i:02}'})
            assert (project['stage'], project['revision']) == ('drafting', 1)
            milestone = write(f'/api/v1/projects/{project["projectId"]}/milestones',
                              {'name': f'Acceptance milestone {i:02}', 'plannedDate': '2027-01-10'})
            assert milestone['projectId'] == project['projectId']
        assert sql("SELECT count(*) FROM project_members WHERE role<>'initiator';") == '0'

        def delivered():
            return sql("SELECT count(*) FROM outbox_events e JOIN project_event_inbox i ON e.id=i.event_id "
                       "WHERE e.event_type='project.created' AND e.status='sent';") == '15'

        wait_for(delivered)
        print('login=200 projects=15 governance_roles=0 outbox_committed_receipts=15', flush=True)
        result = subprocess.run(['node', 'e2e/deployment-performance.mjs'], cwd=ROOT / 'web', timeout=120, check=True,
            env={**os.environ, 'PMO_BROWSER_URL': url, 'PMO_SYNTHETIC_SESSION': json.dumps({
                'cookie': client.cookies['pmo_session'], 'csrf': csrf})})
        assert result.returncode == 0

        def probe():
            assert client.get('/health/live').status_code == 200
            ready = client.get('/health/ready')
            return (ready.status_code, ready.json())

        assert probe() == (200, {'status': 'ready'})
        try:
            dc('stop', 'db')
            wait_for(lambda: probe() == (503, {'status': 'not_ready'}), timeout=20)
            print('database_stopped live=200 ready=503', flush=True)
        finally:
            dc('up', '-d', '--wait', 'db')
            # Bounded worker restarts can exhaust during a sustained outage.
            dc('up', '-d', '--wait', 'api', 'scheduler', 'outbox', 'web')
        wait_for(lambda: probe() == (200, {'status': 'ready'}))
        assert client.get('/api/v1/projects').raise_for_status().json()['data']['total'] == 15
        assert delivered()
        assert client.post('/api/v1/auth/logout', headers={'X-CSRF-Token': csrf}).status_code == 204
    print('database_recovered live=200 ready=200 projects_preserved=15 outbox_deduped=15', flush=True)


if __name__ == '__main__':
    main()
