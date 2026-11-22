"""Joint PostgreSQL and private-evidence backup, and isolated restore drills."""

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Annotated, BinaryIO, Literal
from uuid import uuid4

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from .evidence_storage import LIMIT, root as evidence_root
from .identity import Failure


class RecoveryError(Exception):
    """An operator-safe error that does not include data, paths or credentials."""


class Digest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    size: Annotated[int, Field(gt=0)]
    sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class Object(Digest):
    key: Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
    size: Annotated[int, Field(gt=0, le=LIMIT)]


POINT = r'[0-9]{8}T[0-9]{6}Z_[0-9a-f]{32}'


class Manifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    formatVersion: Literal[1]
    pointId: Annotated[str, Field(pattern=f'^{POINT}$')]
    snapshotAt: datetime
    revision: Annotated[str, Field(pattern=r'^[a-zA-Z0-9_]+$')]
    dump: Digest
    objects: list[Object]
    unreferencedCandidates: Annotated[int, Field(ge=0)]


def private_path(path: Path) -> Path:
    path = path.resolve()
    repo = Path(__file__).resolve().parents[1]
    if path.is_relative_to(repo) and not path.is_relative_to(repo / '.local'):
        raise RecoveryError('RECOVERY_PATH_MUST_BE_PRIVATE')
    return path


def directory(path: Path) -> Path:
    path = private_path(path)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name == 'posix' and path.stat().st_mode & 0o077:
        raise RecoveryError('RECOVERY_DIRECTORY_PERMISSIONS')
    return path


def disjoint(left: Path, right: Path) -> None:
    if left.is_relative_to(right) or right.is_relative_to(left):
        raise RecoveryError('RECOVERY_PATHS_OVERLAP')


@contextmanager
def staged(parent: Path) -> Iterator[Path]:
    temp = TemporaryDirectory(dir=parent, prefix='.incomplete-')
    path = Path(temp.name)
    try:
        yield path
    finally:
        if path.resolve().parent != parent or path.is_symlink() or path.is_junction():
            raise RecoveryError('RECOVERY_DIRECTORY_MOVED')
        temp.cleanup()


def sync_dir(path: Path) -> None:
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def publish(path: Path, parent: Path, name: str) -> Path:
    result = parent / name
    if path.resolve().parent != parent or result.resolve() != result or result.exists():
        raise RecoveryError('RECOVERY_DIRECTORY_CONFLICT')
    sync_dir(path)
    path.rename(result)
    try:
        sync_dir(parent)
    except OSError:
        if result.resolve() == result and path.resolve() == path and not path.exists():
            result.rename(path)
        raise
    return result


def digest(path: Path) -> Digest:
    if path.resolve() != path or not path.is_file():
        raise RecoveryError('RECOVERY_FILE_MISSING_OR_UNSAFE')
    with path.open('rb') as stream:
        return Digest(size=os.fstat(stream.fileno()).st_size, sha256=file_digest(stream, 'sha256').hexdigest())


def copy_object(source: Path, destination: Path, obj: Object) -> None:
    original = source / obj.key
    if original.resolve() != original or not original.is_file():
        raise RecoveryError('RECOVERY_OBJECT_MISSING_OR_UNSAFE')
    with original.open('rb') as stream:
        content = stream.read(obj.size + 1)
    if len(content) != obj.size or sha256(content).hexdigest() != obj.sha256:
        raise RecoveryError('RECOVERY_OBJECT_INTEGRITY_FAILED')
    with (destination / obj.key).open('xb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


PG_OPTIONS = {'sslmode', 'sslcert', 'sslkey', 'sslrootcert', 'sslcrl', 'sslcrldir'}


def database_url(value: str) -> URL:
    url = make_url(value)
    if (url.get_backend_name() != 'postgresql' or not url.host or not url.username
            or not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]{0,62}', url.database or '')
            or set(url.query) - PG_OPTIONS or any(not isinstance(k, str) for k in url.query.values())):
        raise RecoveryError('RECOVERY_DATABASE_URL_INVALID')
    return url


def connection_args(url: URL) -> dict:
    address = {'hostaddr': url.host} if url.host in ('127.0.0.1', '::1') else {}
    return dict(host=url.host, port=url.port or 5432, dbname=url.database, user=url.username,
                password=url.password or '', connect_timeout=10, **address, **url.query)


class PgTools:
    def __init__(self, dev_container: str | None = None):
        self.dev_container = dev_container

    def location(self, url: URL) -> list[str]:
        if not self.dev_container:
            return []
        if (url.host, url.port, url.username, dict(url.query)) != ('127.0.0.1', 55439, 'pmo', {}):
            raise RecoveryError('RECOVERY_DEV_CONTAINER_REQUIRES_LOCAL_COMPOSE')
        return ['docker', 'exec', '-i', self.dev_container]

    def run(self, url: URL, executable: str, args: list[str], *,
            source: BinaryIO | None = None, output: BinaryIO | None = None) -> None:
        prefix = self.location(url)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
        if not prefix:
            env.update({f'PG{k.upper()}': str(v) for k, v in connection_args(url).items() if k != 'dbname'})
            env['PGAPPNAME'] = 'pmo_recovery'
        proc = subprocess.run([*prefix, executable, '--no-password', '--username', str(url.username),
                               '--dbname', str(url.database), *args],
                              env=env, stdin=source or subprocess.DEVNULL,
                              stdout=output or subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
        if proc.returncode:
            raise RecoveryError('RECOVERY_' + executable.upper() + '_FAILED')


def inventory(conn: psycopg.Connection) -> tuple[str, list[Object]]:
    revisions = conn.execute('SELECT version_num FROM public.alembic_version').fetchall()
    if len(revisions) != 1:
        raise RecoveryError('RECOVERY_MIGRATION_REVISION_INVALID')
    if conn.execute("""SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relname='evidence_files' AND c.relkind IN ('r','p') ORDER BY n.nspname""").fetchall() != [('public',)]:
        raise RecoveryError('RECOVERY_EVIDENCE_SCHEMA_UNSUPPORTED')
    items = [Object(key=key, size=size, sha256=sha) for key, size, sha in conn.execute(
        'SELECT storage_key, size_bytes, sha256 FROM public.evidence_files ORDER BY storage_key')]
    if len({obj.key for obj in items}) != len(items):
        raise RecoveryError('RECOVERY_DUPLICATE_OBJECT_KEY')
    return revisions[0][0], items


def point_stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')


def daily_points(root: Path) -> int:
    days = set()
    for path in root.iterdir():
        if not re.fullmatch(POINT, path.name) or path.resolve() != path or not (path / 'manifest.json').is_file():
            continue
        try:
            ts = datetime.strptime(path.name[:16], '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)
        except ValueError:
            continue
        days.add(ts.astimezone(timezone(timedelta(hours=8))).date())
    return len(days)


def backup(url: URL, originals: Path, parent: Path, tools: PgTools) -> tuple[Path, Manifest]:
    parent, originals = private_path(parent), private_path(originals)
    disjoint(parent, originals)
    tools.location(url)
    parent = directory(parent)
    with staged(parent) as stage:
        objects = stage / 'evidence'
        objects.mkdir(mode=0o700)
        archive = stage / 'database.dump'
        with psycopg.connect(**connection_args(url)) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            snap = conn.execute('SELECT pg_export_snapshot(), transaction_timestamp()').fetchone()
            assert snap is not None
            snapshot, at = snap
            revision, items = inventory(conn)
            with archive.open('xb') as file:
                tools.run(url, 'pg_dump', ['--format=custom', '--snapshot', snapshot, '--lock-wait-timeout=30s'], output=file)
                file.flush()
                os.fsync(file.fileno())
        # Committed originals are immutable; never include later uploads or remove candidates here.
        for obj in items:
            copy_object(originals, objects, obj)
        known = {obj.key for obj in items}
        candidates = sum(p.name not in known for p in originals.iterdir()) if originals.exists() else 0
        sync_dir(objects)
        point = point_stamp(at) + '_' + uuid4().hex
        manifest = Manifest(formatVersion=1, pointId=point, snapshotAt=at.astimezone(UTC), revision=revision,
                            dump=digest(archive), objects=items, unreferencedCandidates=candidates)
        with (stage / 'manifest.json').open('xb') as file:
            file.write(manifest.model_dump_json(indent=2).encode('utf-8'))
            file.flush()
            os.fsync(file.fileno())
        return publish(stage, parent, point), manifest


def load_point(point: Path) -> Manifest:
    manifest_file = point / 'manifest.json'
    if manifest_file.resolve() != manifest_file or not manifest_file.is_file():
        raise RecoveryError('RECOVERY_MANIFEST_MISSING_OR_UNSAFE')
    manifest = Manifest.model_validate_json(manifest_file.read_bytes())
    if (manifest.pointId != point.name or manifest.snapshotAt.utcoffset() is None
            or not point.name.startswith(point_stamp(manifest.snapshotAt) + '_')
            or manifest.dump != digest(point / 'database.dump')):
        raise RecoveryError('RECOVERY_MANIFEST_OR_DUMP_MISMATCH')
    objects = point / 'evidence'
    if objects.resolve() != objects or not objects.is_dir():
        raise RecoveryError('RECOVERY_OBJECT_DIRECTORY_MISSING_OR_UNSAFE')
    keys = {obj.key for obj in manifest.objects}
    if len(keys) != len(manifest.objects) or {entry.name for entry in objects.iterdir()} != keys:
        raise RecoveryError('RECOVERY_OBJECT_INVENTORY_MISMATCH')
    return manifest


def restore_drill(admin: URL, point: Path, parent: Path, tools: PgTools) -> tuple[str, Path, Manifest]:
    if admin.host not in ('127.0.0.1', '::1'):
        raise RecoveryError('RECOVERY_TARGET_MUST_BE_ISOLATED_LOOPBACK')
    tools.location(admin)
    point, parent = private_path(point), private_path(parent)
    disjoint(point.parent, parent)
    disjoint(evidence_root(), parent)
    manifest = load_point(point)
    parent = directory(parent)
    name = 'pmo_restore_' + uuid4().hex
    ident = sql.Identifier(name)
    with staged(parent) as stage:
        for obj in manifest.objects:
            copy_object(point / 'evidence', stage, obj)
        with psycopg.connect(**connection_args(admin), autocommit=True) as control:
            control.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0 ALLOW_CONNECTIONS false').format(ident))
            try:
                control.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(ident))
                control.execute(sql.SQL('ALTER DATABASE {} ALLOW_CONNECTIONS true').format(ident))
                target = admin.set(database=name)
                with (point / 'database.dump').open('rb') as archive:
                    tools.run(target, 'pg_restore', ['--exit-on-error', '--single-transaction', '--no-owner',
                                                     '--no-privileges', '--no-tablespaces'], source=archive)
                with psycopg.connect(**connection_args(target)) as verification:
                    if inventory(verification) != (manifest.revision, manifest.objects):
                        raise RecoveryError('RECOVERY_RESTORED_INVENTORY_MISMATCH')
                restored = publish(stage, parent, name)
            except (Exception, KeyboardInterrupt):
                try:
                    control.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(ident))
                except psycopg.Error as exc:
                    raise RecoveryError(f'RECOVERY_DRILL_CLEANUP_FAILED:{name}') from exc
                raise
    return name, restored, manifest


def main(*, tools: PgTools | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev-compose', action='store_true', help='Use tools in the repository development container')
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('backup')
    create.add_argument('output', type=Path)
    verify = sub.add_parser('restore-drill')
    verify.add_argument('point', type=Path)
    verify.add_argument('output', type=Path)
    verify.add_argument('--trusted-backup', required=True, action='store_true',
                        help='Only restore a protected point produced by the trusted source server')
    ns = parser.parse_args()
    if tools is not None and ns.dev_compose:
        parser.error('Cannot combine an injected client with --dev-compose')
    tools = tools if tools is not None else PgTools('pmo104dev-db-1' if ns.dev_compose else None)
    try:
        if ns.action == 'backup':
            point, manifest = backup(database_url(os.environ['DATABASE_URL']), evidence_root(), ns.output, tools)
            print(json.dumps(dict(point=point.name, revision=manifest.revision, objects=len(manifest.objects),
                                  unreferencedCandidates=manifest.unreferencedCandidates,
                                  daysPresent=daily_points(point.parent))))
        else:
            name, _, manifest = restore_drill(database_url(os.environ['PMO_RESTORE_ADMIN_URL']), ns.point, ns.output, tools)
            print(json.dumps(dict(database=name, evidenceDirectory=name, revision=manifest.revision,
                                  objects=len(manifest.objects), result='verified')))
    except RecoveryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Failure:
        print('RECOVERY_PRIVATE_EVIDENCE_DIRECTORY_REQUIRED', file=sys.stderr)
        return 1
    except (OSError, psycopg.Error, subprocess.SubprocessError, ValidationError, ArgumentError, KeyError, ValueError):
        print('RECOVERY_FAILED: check PostgreSQL connectivity, migration, archive and private paths', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
