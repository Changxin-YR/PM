"""Optional local PostgreSQL runtime when Docker Desktop is unavailable."""
from contextlib import contextmanager
from pathlib import Path
import subprocess

import psycopg
from psycopg import sql

from scripts import pilot_local as local

BIN = local.ROOT / '.local' / 'pmo-synthetic-pilot' / 'pg16-runtime' / 'pgsql' / 'bin'


def pg_tool(tool, *args, check=True):
    return subprocess.run([str(BIN / (tool + '.exe')), *map(str, args)], env=local.env_base(),
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=check, timeout=90)


@contextmanager
def connect(directory, port, user, password, *, database='postgres'):
    with psycopg.connect(host='127.0.0.1', port=port, user=user, password=password,
                         dbname=database, connect_timeout=4, autocommit=True) as conn:
        actual_dir, address, actual_port = conn.execute(
            "SELECT current_setting('data_directory'), host(inet_server_addr()), inet_server_port()"
        ).fetchone()
        if (address != '127.0.0.1' or actual_port != port or Path(actual_dir).resolve() != directory
                or conn.info.server_version // 10000 != 16):
            raise ValueError('Not the expected dedicated native PostgreSQL cluster.')
        yield conn


def start(directory, logfile, port, user, password):
    if directory.resolve() != directory or not directory.is_relative_to(local.HOME) or directory == local.HOME:
        raise ValueError('Unsafe native data directory.')
    if not (directory / 'PG_VERSION').is_file():
        if directory.exists():
            raise ValueError('Refusing to adopt an existing native directory.')
        with local.bind(port):
            pgpass = local.local_file(f'init-{port}.pw')
            with pgpass.open('x', encoding='ascii') as stream:
                stream.write(password + '\n')
            try:
                pg_tool('initdb', '-D', directory, '-U', user, '--pwfile', pgpass,
                        '--auth=scram-sha-256', '--encoding=UTF8', '--locale=C')
            finally:
                pgpass.unlink()
    ready = pg_tool('pg_ctl', 'status', '-D', directory, check=False).returncode == 0
    if not ready:
        with local.bind(port):
            pass
        pg_tool('pg_ctl', 'start', '-D', directory, '-l', logfile, '-w', '-t', '25',
                '-o', f'-h 127.0.0.1 -p {port}')
    with connect(directory, port, user, password):
        pass


def verify_pilot(cfg):
    with connect(local.local_file('pgdata'), local.DB_PORT, 'pmo_bootstrap',
                 cfg['passwords']['bootstrap'], database=local.DB_NAME) as db:
        owner, = db.execute('SELECT pg_get_userbyid(datdba) FROM pg_database '
                            'WHERE datname = current_database()').fetchone()
        if owner != 'pmo_migrate':
            raise ValueError('Not the dedicated native pilot database.')


def init_pilot(cfg):
    directory = local.local_file('pgdata')
    bootstrap = cfg['passwords']['bootstrap']
    start(directory, local.local_file('postgres.log'), local.DB_PORT, 'pmo_bootstrap', bootstrap)
    with connect(directory, local.DB_PORT, 'pmo_bootstrap', bootstrap) as db:
        exists = db.execute('SELECT 1 FROM pg_database WHERE datname = %s', (local.DB_NAME,)).fetchone()
        if not exists:
            with db.transaction():
                for role in ('migrate', 'api', 'outbox'):
                    user = 'pmo_' + role
                    if not db.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (user,)).fetchone():
                        db.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(
                            sql.Identifier(user), sql.Literal(cfg['passwords'][role])))
            db.execute(sql.SQL('CREATE DATABASE {} OWNER pmo_migrate').format(sql.Identifier(local.DB_NAME)))
        db.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(local.DB_NAME)))
    with connect(directory, local.DB_PORT, 'pmo_bootstrap', bootstrap, database=local.DB_NAME) as db:
        db.execute('ALTER SCHEMA public OWNER TO pmo_migrate')
        db.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
