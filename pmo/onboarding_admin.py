"""Reset an ordinary account from an authorized migration terminal."""

import argparse
import os
import sys
import warnings
from getpass import GetPassWarning, getpass
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from . import schema_v1 as db
from .identity import Actor
from .org_accounts import revoke_identity
from .projects import audit
from .recovery import RecoveryError, database_url
from .sessions import hasher

ACTION = 'admin_account_password_reset'
REASON = 'Password reset from authorized migration terminal.'


class AccountRecoveryError(ValueError):
    """A credential-free explanation of a denied recovery."""


def reset_password(c: Connection, org_id: UUID, operator_id: UUID, account_id: UUID, password: str) -> UUID:
    """Change the credential and revoke existing sessions within the caller's transaction."""
    if not 14 <= len(password) <= 256:
        raise AccountRecoveryError('Password must have 14–256 characters.')
    if operator_id == account_id:
        raise AccountRecoveryError('Choose an ordinary account in the specified organization.')

    # Generate the hash before taking locks that also protect login and session operations.
    password_hash = hasher.hash(password)
    org, users, people, roles = db.organizations, db.user_accounts, db.persons, db.user_roles
    if c.scalar(sa.select(org.c.id).where(org.c.id == org_id, org.c.status == 'active').with_for_update(read=True)) is None:
        raise AccountRecoveryError('The specified organization is unavailable.')

    active = sa.select(users.c.id, people.c.id.label('person_id')).select_from(
        users.join(people, people.c.user_id == users.c.id)).where(
        people.c.org_id == org_id, people.c.is_active, users.c.is_active)
    is_admin = sa.select(roles.c.id).where(roles.c.role == 'system_admin')
    operator = c.execute(active.where(users.c.id == operator_id,
        sa.or_(users.c.locked_until.is_(None), users.c.locked_until <= sa.func.now())).with_for_update(
        read=True, of=(users, people))).mappings().one_or_none()
    if operator is None or c.scalar(is_admin.where(roles.c.user_id == operator_id).with_for_update(read=True)) is None:
        raise AccountRecoveryError('An active organization administrator must authorize recovery.')

    # Avoid taking a conflicting account lock for an administrator targeted by mistake.
    target = c.execute(active.where(users.c.id == account_id,
        ~sa.exists(is_admin.where(roles.c.user_id == users.c.id))).with_for_update(of=(users, people))).mappings().one_or_none()
    if target is None or c.scalar(is_admin.where(roles.c.user_id == account_id).with_for_update(read=True)) is not None:
        raise AccountRecoveryError('Choose an active ordinary account in the specified organization.')

    at = c.scalar(sa.select(sa.func.clock_timestamp()))
    rid = uuid4()
    actor = Actor(operator.id, operator.person_id, operator.id, org_id, frozenset(), (), False, 'human')
    c.execute(users.update().where(users.c.id == account_id).values(password_hash=password_hash, updated_at=at))
    revoke_identity(c, target, actor, str(rid), REASON, at)
    c.execute(audit.insert().values(actor_type='human', actor_id=operator.id, org_id=org_id, action=ACTION,
        result='success', request_id=str(rid), source_refs=[f'account:{account_id}']))
    return rid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org-id', type=UUID, required=True)
    parser.add_argument('--operator-account-id', type=UUID, required=True)
    parser.add_argument('--target-account-id', type=UUID, required=True)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        print('Account recovery requires an interactive migration terminal.', file=sys.stderr)
        return 1
    try:
        url = database_url(os.environ['PMO_MIGRATION_DATABASE_URL']).set(drivername='postgresql+psycopg')
    except (ArgumentError, KeyError, RecoveryError):
        print('Configure PMO_MIGRATION_DATABASE_URL for the intended migration database.', file=sys.stderr)
        return 1

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', GetPassWarning)
            password = getpass('New password (14–256 characters): ')
            if password != getpass('Confirm new password: '):
                raise AccountRecoveryError('Passwords must match.')
        engine = sa.create_engine(url, hide_parameters=True, connect_args={'connect_timeout': 10})
        try:
            with engine.begin() as c:
                rid = reset_password(c, args.org_id, args.operator_account_id, args.target_account_id, password)
        finally:
            engine.dispose()
    except AccountRecoveryError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (EOFError, GetPassWarning, KeyboardInterrupt):
        print('Account recovery was canceled or secure password entry was unavailable.', file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print('Account recovery could not be committed. Check the authorized migration connection.', file=sys.stderr)
        return 1
    print(f'Password reset committed. Audit request ID {rid}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
