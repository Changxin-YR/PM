"""Verify a native pilot cold point and rehearse upgrades on a separate loopback cluster."""
import argparse
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import make_url

from pmo.contracts import RULE_VERSION
from pmo.database_roles import apply_roles
from scripts import pilot_local as local
from scripts import pilot_native as native

DRILL_PORT = 55445
PRESERVED = {
    'projects': 'id, code, stage, revision', 'reviews': 'id, project_id, type, status, revision',
    'reports': 'id, project_id, status, revision, content_hash',
    'cost_postings': 'id, project_id, status, type, amount_signed, verified_at, verified_by',
    'project_retrospectives': 'id, project_id, revision, answers',
    'audit_logs': 'id, project_id, request_id, rule_version',
    'evidence_files': 'id, storage_key, sha256, size_bytes',
}


def digest(path):
    result = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def files(root):
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('Linked objects are outside the cold restore contract.')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def connect(cfg, role):
    return psycopg.connect(host='127.0.0.1', port=DRILL_PORT, dbname=local.DB_NAME, user='pmo_' + role,
         password=cfg['passwords'][role], connect_timeout=4, options=f'-csearch_path={local.schema(cfg)}', autocommit=True)


def inventory(cfg, private):
    with connect(cfg, 'migrate') as conn, conn.transaction():
        if conn.execute('SELECT current_schema()').fetchone() != (local.schema(cfg),):
            raise ValueError('Wrong schema on the restored database.')
        rev, = conn.execute('SELECT version_num FROM alembic_version').fetchone()
        tables = {}
        for table, columns in PRESERVED.items():
            query = sql.SQL('SELECT row_to_json(t)::text FROM (SELECT {} FROM {} ORDER BY id) t').format(
                sql.SQL(columns), sql.Identifier(table))
            rows = conn.execute(query).fetchall()
            tables[table] = {'count': len(rows), 'sha256': sha256('\n'.join(row[0] for row in rows).encode()).hexdigest()}
        objects = conn.execute('SELECT storage_key, sha256, size_bytes FROM evidence_files ORDER BY storage_key').fetchall()
    if not tables['projects']['count'] or not objects:
        raise ValueError('The restored database must contain project data and private originals.')
    for key, checksum, size in objects:
        if not re.fullmatch(r'[0-9a-f]{32}', key):
            raise ValueError('Invalid object key in the restored database.')
        path = private / key
        if path.stat().st_size != size or digest(path) != checksum:
            raise ValueError('Restored private original differs from the committed database receipt.')
    return rev, tables


def acl(cfg):
    runtime = {}
    for role in ('api', 'outbox'):
        with connect(cfg, role) as conn:
            attrs = conn.execute('''SELECT rolsuper, rolcreaterole, rolcreatedb,
                pg_has_role('pmo_migrate', 'MEMBER'), has_schema_privilege(current_schema(), 'CREATE'),
                has_table_privilege('outbox_events', 'SELECT'), has_table_privilege('projects', 'SELECT'),
                has_table_privilege('projects', 'INSERT'), has_table_privilege('reports', 'INSERT')
                FROM pg_roles WHERE rolname=current_user''').fetchone()
            expected = (False, False, False, False, False, role == 'outbox', role == 'api', role == 'api', role == 'api')
            if attrs != expected:
                raise ValueError(f'Unexpected privilege matrix for restored {role} role.')
            for operation in ('CREATE TABLE pmo_forbidden_ddl(id int)', 'ALTER TABLE projects ADD COLUMN pmo_forbidden_ddl int'):
                try:
                    with conn.transaction():
                        conn.execute(operation)
                        raise ValueError(f'Restored {role} unexpectedly has DDL permission.')
                except psycopg.errors.InsufficientPrivilege:
                    pass
            runtime[role] = {'leastPrivilege': True, 'ddlRejected': True, 'projectWrite': role == 'api',
                             'outboxRead': role == 'outbox'}
    return runtime


def drill(point, targets, *, check_acl=True):
    local.private_home()
    cfg = local.settings()
    local.verify_database(cfg)
    point = point.absolute()
    if not local.NATIVE or point.resolve() != point or point.parent != local.HOME or not re.fullmatch(
            r'pre-\d{4}-\d{8}-\d{6}', point.name):
        raise ValueError('Select a protected cold point from this native pilot instance.')
    source_data, source_objects = point / 'pgdata', point / 'private-evidence'
    if any(path.resolve() != path or not path.is_dir() for path in (source_data, source_objects)):
        raise ValueError('Cold point must contain the database and private originals.')
    with local.bind(DRILL_PORT):
        pass
    started = datetime.now(UTC)
    start = monotonic()
    root = local.local_file(f'restore-closure-{uuid4().hex}')
    root.mkdir()
    data, originals = root / 'pgdata', root / 'private-evidence'
    source_db = files(source_data)
    source_db.pop('postmaster.pid', None)  # Transient marker from a stopped source. The recovery copy verifies every other byte.
    source_files = files(source_objects)
    shutil.copytree(source_data, data, ignore=shutil.ignore_patterns('postmaster.pid'))
    shutil.copytree(source_objects, originals)
    if files(data) != source_db or files(originals) != source_files:
        raise ValueError('The recovered cold files differ from their source.')
    native.start(data, root / 'postgres.log', DRILL_PORT, 'pmo_bootstrap', cfg['passwords']['bootstrap'])
    try:
        before, tables = inventory(cfg, originals)
        revisions = [before]
        url = make_url(local.database(cfg, 'migrate')).set(port=DRILL_PORT).render_as_string(hide_password=False)
        for revision in targets:
            subprocess.run([str(local.PYTHON), '-m', 'alembic', 'upgrade', revision], cwd=local.ROOT,
                           check=True, env=local.env_base() | {'DATABASE_URL': url}, timeout=120)
            after, actual = inventory(cfg, originals)
            if after != revision or tables != actual:
                raise ValueError(f'Upgrade to {revision} did not preserve the restored business receipts.')
            revisions.append(after)
        if check_acl:
            with connect(cfg, 'migrate') as conn:
                apply_roles(conn, 'pmo_api', 'pmo_outbox', synthetic_schema=local.schema(cfg))
        roles = acl(cfg) if check_acl else None
        if files(originals) != source_files:
            raise ValueError('Upgrade unexpectedly changed a recovered private original.')
        result = {'port': DRILL_PORT, 'ruleVersion': RULE_VERSION, 'point': point.name, 'clockStart': started.isoformat(),
                  'revisions': revisions, 'privateObjects': len(source_files), 'copiedDatabaseFiles': len(source_db),
                  'preserved': tables, 'roles': roles, 'durationSeconds': round(monotonic() - start, 2), 'directory': root.name}
        (root / 'evidence.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        return result
    finally:
        native.pg_tool('pg_ctl', '-D', data, 'stop', '-m', 'fast', '-w')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cold_point', type=Path)
    parser.add_argument('--legacy-stop', action='store_true', help='Rehearse the legacy path through 0037 and 0038.')
    args = parser.parse_args()
    targets = ('0037', '0038') if args.legacy_stop else ('0037', '0038', local.migration_head())
    print(json.dumps(drill(args.cold_point, tuple(dict.fromkeys(targets)), check_acl=not args.legacy_stop)))


if __name__ == '__main__':
    main()
