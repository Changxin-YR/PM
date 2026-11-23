"""Run test commands on a loopback-only cluster separate from both shared and pilot data."""
import argparse
import os
import re
import secrets
import subprocess
import sys

import psycopg
from sqlalchemy import URL

from pmo import recovery
from scripts import pilot_local as local

NAME = 'pmo104-pilot-regression'
NETWORK = NAME + '-net'
PORT = 55442
NATIVE_PORT = 55444
USERNAME = 'pmo_regression'


def docker(*args, check=True, env=None):
    return subprocess.run(['docker', *args], check=check, env=env or local.env_base(), timeout=120,
                          capture_output=True, text=True, encoding='utf-8')


def require_local_docker():
    if not docker('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}').stdout.strip().startswith('npipe:'):
        raise ValueError('Regression runner requires local Docker Desktop.')


def pg_tools(admin):
    require_local_docker()
    endpoint = '127.0.0.1', PORT, USERNAME, {}
    if (admin.host, admin.port, admin.username, dict(admin.query), admin.database) != (*endpoint, USERNAME):
        raise ValueError('Wrong PostgreSQL endpoint for the regression CLI.')
    cid, label = docker('inspect', '--type', 'container', '--format',
                        '{{.Id}} {{index .Config.Labels "pmo.role"}}', NAME).stdout.split()
    if not re.fullmatch('[0-9a-f]{64}', cid) or label != 'synthetic-regression':
        raise ValueError('Wrong PostgreSQL container for the regression CLI.')
    if docker('port', cid, '5432/tcp').stdout.strip() != f'127.0.0.1:{PORT}':
        raise ValueError('Regression DB port mismatch.')

    class RegressionPgTools(recovery.PgTools):
        def location(self, url):
            if (url.host, url.port, url.username, dict(url.query)) != endpoint or (
                    url.database != USERNAME and not re.fullmatch(r'pmo_(backup_test|restore)_[a-f0-9]{32}', url.database or '')):
                raise recovery.RecoveryError('RECOVERY_TARGET_MUST_BE_DEDICATED_REGRESSION_DB')
            return ['docker', 'exec', '-i', cid]

    return RegressionPgTools()


def ensure():
    local.private_home()
    require_local_docker()
    settings = local.read('regression.json')
    if settings is None:
        if docker('inspect', '--type', 'container', NAME, check=False).returncode == 0:
            raise ValueError('Won\'t adopt an existing regression container.')
        with local.bind(PORT):
            settings = {'password': secrets.token_hex(32)}
            local.save('regression.json', settings)
    current = docker('inspect', '--type', 'container', '--format', '{{index .Config.Labels "pmo.role"}}', NAME, check=False)
    if current.returncode:
        with local.bind(PORT):
            pass
        if docker('network', 'inspect', NETWORK, check=False).returncode:
            docker('network', 'create', '--subnet', '10.253.72.0/27', '--label', 'pmo.role=synthetic-regression', NETWORK)
        docker('run', '-d', '--name', NAME, '--network', NETWORK, '--label', 'pmo.role=synthetic-regression',
               '-p', f'127.0.0.1:{PORT}:5432', '--env', f'POSTGRES_USER={USERNAME}', '--env', 'POSTGRES_PASSWORD',
               'postgres:16.14-alpine', env=local.env_base() | {'POSTGRES_PASSWORD': settings['password']})
    elif current.stdout.strip() != 'synthetic-regression':
        raise ValueError('Regression container has the wrong label.')
    else:
        docker('start', NAME)
    if docker('port', NAME, '5432/tcp').stdout.strip() != f'127.0.0.1:{PORT}':
        raise ValueError('Regression DB port mismatch.')
    url = URL.create('postgresql+psycopg', username=USERNAME, password=settings['password'],
                     host='127.0.0.1', port=PORT, database=USERNAME)

    def available():
        try:
            with psycopg.connect(host=url.host, port=PORT, dbname=USERNAME, user=USERNAME, password=url.password,
                                 connect_timeout=2) as connection:
                return connection.execute('SELECT current_database(),current_user').fetchone() == (USERNAME, USERNAME)
        except psycopg.OperationalError:
            return False

    if not local.wait_for(available):
        raise RuntimeError('Dedicated regression DB is not ready.')
    return url.render_as_string(hide_password=False)


def native_env():
    from scripts import pilot_native

    local.private_home()
    settings = local.read('regression.json')
    if settings is None:
        with local.bind(NATIVE_PORT):
            settings = {'password': secrets.token_hex(32)}
            local.save('regression.json', settings)
    directory = local.local_file('regression-db')
    password = settings['password']
    pilot_native.start(directory, local.local_file('regression-postgres.log'), NATIVE_PORT, USERNAME, password)
    with pilot_native.connect(directory, NATIVE_PORT, USERNAME, password) as db:
        if not db.execute('SELECT 1 FROM pg_database WHERE datname = %s', (USERNAME,)).fetchone():
            db.execute('CREATE DATABASE pmo_regression')
    url = URL.create('postgresql+psycopg', username=USERNAME, password=password, host='127.0.0.1',
                     port=NATIVE_PORT, database=USERNAME)
    return {'PMO_TEST_DATABASE_URL': url.render_as_string(hide_password=False),
            'PATH': str(pilot_native.BIN) + os.pathsep + os.environ['PATH']}


def main():
    if sys.argv[1:2] == ['restore-drill']:
        admin = recovery.database_url(os.environ['PMO_RESTORE_ADMIN_URL'])
        return recovery.main(tools=pg_tools(admin))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args().pytest_args
    if args[:1] == ['--']:
        args = args[1:]
    if local.NATIVE:
        env = local.env_base() | native_env()
    else:
        env = local.env_base() | {'PMO_TEST_DATABASE_URL': ensure(), 'PMO_TEST_PG_CONTAINER': NAME}
    for key in ('PMO_TEST_MCP_ENTRYPOINT',):
        if key in os.environ:
            env[key] = os.environ[key]
    print(f'Regression PostgreSQL: dedicated loopback {NATIVE_PORT if local.NATIVE else PORT}; '
          'pilot and shared DBs excluded.', flush=True)
    return subprocess.run([sys.executable, '-m', 'pytest', *args], cwd=local.ROOT, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
