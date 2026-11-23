"""Standalone Windows operator entry for the synthetic PMO pilot. Never uses dev.env."""
import argparse
from datetime import UTC, datetime
from getpass import getpass
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'PMO_PILOT_RUNTIME'
NATIVE = os.getenv(PROFILE) == 'native'
HOME = ROOT / '.local' / ('pmo-synthetic-pilot-native' if NATIVE else 'pmo-synthetic-pilot')
COMPOSE = ROOT / 'deploy' / 'pilot-db.compose.yaml'
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
DB_PORT, WEB_PORT, API_PORT = (55443, 18778, 18779) if NATIVE else (55441, 18775, 18776)
API = f'http://127.0.0.1:{API_PORT}/api/v1/'
WEB = f'http://127.0.0.1:{WEB_PORT}'
DB_NAME = 'pmo_pilot'
HEAD = '0037'
STARTUP_SECONDS = 100
CHANNELS = {'assistant': 'PMO_ASSISTANT_PREVIEWS', 'report': 'PMO_REPORT_ANALYSIS'}
PILOT_CODES = {'SIM-EXEC-001', 'SIM-NEW-001'}


def env_base():
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(('PMO_', 'PG', 'COMPOSE_'))
            and k.upper() != 'DATABASE_URL'} | {'PYTHONUTF8': '1', 'PYTHONPATH': str(ROOT)}


def private_home():
    if os.name != 'nt' or HOME.resolve() != HOME:
        raise ValueError('Use a regular Windows checkout and a non-linked private pilot directory.')
    HOME.mkdir(parents=True, exist_ok=True)
    identity = subprocess.check_output(['whoami.exe', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = re.search(r'S-1-[\d-]+', identity)
    if not sid:
        raise RuntimeError('Cannot verify the current Windows account.')
    subprocess.run(['icacls.exe', str(HOME), '/inheritance:r', '/grant:r', f'*{sid[0]}:(OI)(CI)F'],
                   stdout=subprocess.DEVNULL, check=True)
    # Reject unexpected explicit ACEs, rather than removing another principal's access.
    command = ('(Get-Acl -LiteralPath $env:PMO_CHECK_ACL -ErrorAction Stop).Access | ForEach-Object '
               '{ $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }')
    acl = subprocess.check_output(['pwsh.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                  env=env_base() | {'PMO_CHECK_ACL': str(HOME)}, text=True).splitlines()
    if not acl or set(acl) != {sid[0]}:
        raise RuntimeError('Unexpected ACL on the pilot directory; no secrets written.')


def local_file(name):
    path = HOME / name
    if path.parent != HOME or path.resolve() != path:
        raise ValueError('Unsafe pilot file path.')
    return path


def save(name, data):
    target = local_file(name)
    temp = local_file(f'.tmp-{uuid4().hex}')
    with temp.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temp.replace(target)


def read(name, default=None):
    path = local_file(name)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def settings():
    cfg = read('instance.json')
    if not cfg:
        raise RuntimeError('Run the pilot init command first.')
    if not re.fullmatch(r'[a-f0-9]{32}', cfg['id']):
        raise ValueError('Invalid pilot instance identifier.')
    return cfg


def project(cfg):
    return 'pmo104-pilot-' + cfg['id'][:8]


def schema(cfg):
    return 'pmo_test_' + cfg['id']


def database(cfg, role, *, scoped=True):
    return URL.create('postgresql+psycopg', username=f'pmo_{role}', password=cfg['passwords'][role],
                      host='127.0.0.1', port=DB_PORT, database=DB_NAME,
                      query={'options': f'-csearch_path={schema(cfg)}'} if scoped else {}).render_as_string(hide_password=False)


def db_connect(cfg, role='migrate', *, scoped=True):
    return psycopg.connect(host='127.0.0.1', port=DB_PORT, dbname=DB_NAME, user='pmo_' + role,
                          password=cfg['passwords'][role], connect_timeout=4,
                          options=f'-csearch_path={schema(cfg)}' if scoped else '')


def compose(cfg, *args, capture=False):
    command = ['docker', 'compose', '-p', project(cfg), '--env-file', str(local_file('database.env')),
               '-f', str(COMPOSE), *args]
    return subprocess.run(command, cwd=ROOT, env=env_base(), check=True, capture_output=capture,
                          text=True, encoding='utf-8', timeout=150).stdout


def verify_database(cfg):
    if NATIVE:
        from scripts.pilot_native import verify_pilot
        verify_pilot(cfg)
        return
    cid = compose(cfg, 'ps', '-q', 'db', capture=True).strip()
    if not re.fullmatch('[0-9a-f]{12,64}', cid):
        raise ValueError('Dedicated pilot DB container is not running.')
    raw = subprocess.check_output(['docker', 'inspect', '--format',
        '{{json .Config.Labels}}\n{{json .HostConfig.PortBindings}}\n{{json .Mounts}}', cid],
        cwd=ROOT, env=env_base(), encoding='utf-8').splitlines()
    labels, ports, mounts = map(json.loads, raw)
    if (labels.get('com.docker.compose.project') != project(cfg)
            or labels.get('com.docker.compose.service') != 'db'
            or Path(labels['com.docker.compose.project.config_files']).resolve() != COMPOSE
            or ports != {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(DB_PORT)}]}
            or not any(m.get('Name') == project(cfg) + '_dbdata' for m in mounts)):
        raise ValueError('Pilot DB ownership/loopback checks failed.')


def init(*, upgrade_existing=False):
    from pmo.database_roles import apply_roles

    if upgrade_existing and read('runtime.json'):
        raise ValueError('Stop the dedicated pilot before upgrading its database.')
    cfg = read('instance.json')
    if not cfg:
        cfg = {'id': uuid4().hex, 'passwords': {r: secrets.token_hex(32) for r in ('bootstrap', 'migrate', 'api', 'outbox')}}
        with bind(DB_PORT):
            save('instance.json', cfg)
            if not NATIVE:
                with local_file('database.env').open('x', encoding='ascii') as stream:
                    for role, value in cfg['passwords'].items():
                        key = 'DB' if role == 'bootstrap' else role.upper()
                        stream.write(f'PMO_DEPLOY_{key}_PASSWORD={value}\n')
                    stream.write(f'PMO_PILOT_DB_PORT={DB_PORT}\n')
    cfg = settings()
    if NATIVE:
        from scripts.pilot_native import init_pilot
        init_pilot(cfg)
    else:
        endpoint = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'],
                                           env=env_base(), encoding='utf-8').strip()
        if not endpoint.startswith('npipe:'):
            raise RuntimeError('Expected the local Windows Docker context.')
        compose(cfg, 'up', '-d', '--wait', 'db')
    verify_database(cfg)
    with db_connect(cfg, scoped=False) as conn:
        if conn.execute('SELECT current_database(), current_user').fetchone() != (DB_NAME, 'pmo_migrate'):
            raise ValueError('Not the dedicated pilot database.')
        if conn.execute("SELECT to_regclass('public.alembic_version')").fetchone() != (None,):
            raise ValueError('Expected an unused public schema in the pilot database.')
        namespaces = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')").fetchall()
        if namespaces not in ([], [(schema(cfg),)]):
            raise ValueError('Unexpected schema on the dedicated pilot database.')
        conn.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema(cfg))))
    with db_connect(cfg) as conn:
        current = conn.execute("SELECT current_schema(), to_regclass('alembic_version') IS NOT NULL").fetchone()
        if current is None or current[0] != schema(cfg):
            raise ValueError('Pilot search path mismatch.')
        version = conn.execute('SELECT version_num FROM alembic_version').fetchone() if current[1] else None
    if version is None or (upgrade_existing and version == ('0036',)):
        subprocess.run([str(PYTHON), '-m', 'alembic', 'upgrade', HEAD], cwd=ROOT, check=True,
                       env=env_base() | {'DATABASE_URL': database(cfg, 'migrate')})
    elif version != (HEAD,):
        raise ValueError('Existing pilot schema is not the expected head; no upgrade attempted.')
    with db_connect(cfg, scoped=False) as conn:
        conn.autocommit = True
        apply_roles(conn, 'pmo_api', 'pmo_outbox', synthetic_schema=schema(cfg))
    print(f'{project(cfg)} runtime={"native" if NATIVE else "docker"} database={DB_NAME} port={DB_PORT} schema={schema(cfg)} head={HEAD}')


def bind(port):
    sock = socket.socket()
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', port))
        return sock
    except OSError:
        sock.close()
        raise


def wait_for(check, seconds=45):
    until = monotonic() + seconds
    while monotonic() < until:
        if check():
            return True
        sleep(.35)
    return False


def ready():
    try:
        with httpx.Client(trust_env=False, timeout=15) as client:
            return (client.get(WEB).status_code == 200
                    and client.get(API.removesuffix('/api/v1/') + '/health/ready').json() == {'status': 'ready'})
    except (httpx.HTTPError, ValueError):
        return False


def configure_models():
    from pmo.model_gateway import PROTOCOLS, endpoint

    pilots = read('pilots.json', {})
    if not PILOT_CODES <= pilots.keys():
        raise ValueError('Create both pilot projects before setting the model scope.')
    print(f'{WEB} | {DB_NAME} | synthetic projects only. No network request or service change on save.')
    print('Choose pmo_json (PMO contract) or chat_completions_json (complete JSON-mode chat endpoint).')
    for name in CHANNELS:
        if input(f'Configure {name} gateway? [y/N] ').strip().lower() != 'y':
            continue
        protocol = input('Protocol [pmo_json]: ').strip() or 'pmo_json'
        if protocol not in PROTOCOLS:
            raise ValueError('Unsupported model protocol.')
        uri = endpoint(input('Complete endpoint URL (no path will be appended): ').strip())
        version = input('Actual model ID/version (for pmo_json select it at the gateway): ').strip()
        if not version or len(version) > 160 or not version.isprintable():
            raise ValueError('Model version label is required.')
        raw = input('Project codes [SIM-EXEC-001,SIM-NEW-001]: ').strip()
        codes = [code.strip() for code in raw.split(',')] if raw else sorted(PILOT_CODES)
        if not set(codes) <= PILOT_CODES or len(set(codes)) != len(codes):
            raise ValueError('Only the two synthetic pilot projects can be selected.')
        token = getpass('Gateway bearer token (blank if none): ')
        if token and (not token.isascii() or any(c.isspace() or not c.isprintable() for c in token)):
            raise ValueError('Invalid bearer token format.')
        save(f'model-{name}.json', dict(url=str(uri), version=version, protocol=protocol, projects=codes, token=token))
        print(f'{name}: saved locally; not loaded or called.')


def model_env(active):
    from pmo.model_gateway import PROTOCOLS, endpoint

    pilots = read('pilots.json', {})
    api = {flag: '0' for flag in CHANNELS.values()}
    worker = {'PMO_REPORT_ANALYSIS': '0'}
    loaded = {}
    if active:
        for name, flag in CHANNELS.items():
            config = read(f'model-{name}.json')
            if not config:
                continue
            codes = config['projects']
            protocol = config.get('protocol', 'pmo_json')
            version = config['version']
            token = config['token']
            if (not codes or len(set(codes)) != len(codes) or not set(codes) <= PILOT_CODES or not set(codes) <= pilots.keys()
                    or protocol not in PROTOCOLS or not version.strip() or len(version) > 160 or not version.isprintable()
                    or not token.isascii() or any(c.isspace() or not c.isprintable() for c in token)):
                raise ValueError('Invalid synthetic gateway scope.')
            dest = api if name == 'assistant' else worker
            prefix = 'PMO_' + name.upper() + '_MODEL'
            dest[prefix + '_URL'] = str(endpoint(config['url']))
            dest[prefix + '_TOKEN'] = token
            dest[prefix + '_PROTOCOL'] = protocol
            dest[prefix + '_VERSION'] = version
            dest[prefix + '_PROJECTS'] = api[prefix + '_PROJECTS'] = ','.join(pilots[code]['id'] for code in codes)
            dest[flag] = api[flag] = '1'
            loaded[name] = {'version': version, 'protocol': protocol, 'projects': codes}
        if not loaded:
            raise ValueError('No gateway has been configured.')
    return api, worker, loaded


def serve(active):
    cfg = settings()
    verify_database(cfg)
    with db_connect(cfg, 'api') as connection:
        identity = connection.execute('SELECT current_database(), current_schema(), current_user FROM alembic_version WHERE version_num=%s', (HEAD,)).fetchone()
        if identity != (DB_NAME, schema(cfg), 'pmo_api'):
            raise ValueError('Pilot runtime DB guard failed.')
    evidence = local_file('private-evidence')
    evidence.mkdir(exist_ok=True)
    api_model, worker_model, loaded = model_env(active)
    base = env_base()
    separate = base | {'DATABASE_URL': database(cfg, 'api'), 'PMO_SYNTHETIC_POLICY_SCHEMA': schema(cfg)}
    with bind(WEB_PORT), bind(API_PORT):
        pass  # Fail when any unrelated service owns either port.
    procs = []
    with local_file('services.log').open('a', encoding='utf-8') as log:
        def start(command, directory, env):
            child = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            procs.append(child)
            return child.pid

        try:
            app_env = separate | {'PMO_LOCAL_HTTP': '1', 'PMO_EVIDENCE_DIR': str(evidence)}
            ids = {'api': start([str(PYTHON), '-m', 'uvicorn', 'pmo.api:app', '--port', str(API_PORT), '--no-access-log'],
                                ROOT, app_env | api_model),
                   'web': start(['node', str(ROOT / 'web/node_modules/vite/bin/vite.js'), 'preview', '--host',
                                 '127.0.0.1', '--port', str(WEB_PORT), '--strictPort'], ROOT / 'web',
                                base | {'PMO_API_PORT': str(API_PORT)}),
                   'outbox': start([str(PYTHON), '-m', 'pmo.outbox', '--watch'], ROOT,
                                   base | {'DATABASE_URL': database(cfg, 'outbox')})}
            if worker_model['PMO_REPORT_ANALYSIS'] == '1':
                ids['reportWorker'] = start([str(PYTHON), '-m', 'pmo.report_analysis_worker', '--watch'], ROOT,
                                            separate | worker_model)
            if (not wait_for(lambda: ready() or any(p.poll() is not None for p in procs), seconds=STARTUP_SECONDS)
                    or any(p.poll() is not None for p in procs)):
                raise RuntimeError('Pilot services could not start; see services.log.')
            sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            save('runtime.json', dict(pids=ids, web=WEB, api=API, initialHead=sha, models=loaded, startedAt=datetime.now(UTC).isoformat()))
            while not local_file('stop.request').exists() and all(p.poll() is None for p in procs):
                sleep(.5)
        finally:
            for child in procs:
                if child.poll() is None:
                    child.terminate()
            for child in procs:
                child.wait(timeout=12)
            local_file('runtime.json').unlink(missing_ok=True)
            local_file('stop.request').unlink(missing_ok=True)


def stop():
    if not read('runtime.json'):
        return
    local_file('stop.request').touch()
    if not wait_for(lambda: not local_file('runtime.json').exists(), seconds=20):
        raise RuntimeError('Pilot supervisor did not acknowledge stop; no process was killed by PID.')


def launch(active=False):
    if read('runtime.json'):
        raise RuntimeError('Pilot already owns the runtime; stop it before restarting.')
    if not (ROOT / 'web/dist/index.html').is_file():
        raise RuntimeError('Run npm.cmd --prefix web run build before starting the pilot.')
    with local_file('supervisor.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen([str(PYTHON), '-m', 'scripts.pilot_local', 'serve-models' if active else 'serve'], cwd=ROOT,
                                env=env_base() | ({PROFILE: 'native'} if NATIVE else {}),
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    if (not wait_for(lambda: local_file('runtime.json').is_file() or child.poll() is not None, seconds=STARTUP_SECONDS + 45)
            or child.poll() is not None):
        raise RuntimeError('Pilot did not become ready. See the protected supervisor and service logs.')
    print(json.dumps(read('runtime.json'), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'start', 'stop', 'status', 'configure-models', 'activate-models',
                                          'serve', 'serve-models'))
    parser.add_argument('--upgrade-existing', action='store_true',
                        help='Allow a stopped, dedicated pilot schema at 0036 to upgrade to 0037.')
    args = parser.parse_args()
    if args.upgrade_existing and args.action != 'init':
        parser.error('--upgrade-existing is only valid with init')
    private_home()
    if args.action == 'init':
        init(upgrade_existing=args.upgrade_existing)
    elif args.action == 'start':
        launch()
    elif args.action == 'stop':
        stop()
    elif args.action == 'status':
        print(json.dumps({'running': ready(), 'runtime': read('runtime.json'),
            'saved': {name: local_file(f'model-{name}.json').is_file() for name in CHANNELS}}, ensure_ascii=False))
    elif args.action == 'configure-models':
        configure_models()
    elif args.action == 'activate-models':
        model_env(True)
        if input('Type SEND SYNTHETIC ONLY to enable the configured synthetic gateway tests: ') != 'SEND SYNTHETIC ONLY':
            print('Not activated.')
            return
        stop()
        launch(True)
    else:
        serve(args.action == 'serve-models')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, psycopg.Error, subprocess.SubprocessError) as error:
        # Subprocess errors can include commands; user inputs and database DSNs must not be echoed.
        print(f'Pilot operation failed ({type(error).__name__}).', file=sys.stderr)
        raise SystemExit(1) from None
