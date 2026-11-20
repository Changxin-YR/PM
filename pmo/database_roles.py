"""Apply the explicit API and Outbox ACLs to a dedicated, migrated PMO database."""

import argparse
from collections.abc import Iterable
import os
import sys

import psycopg
from psycopg import sql
from sqlalchemy.exc import ArgumentError

from .recovery import RecoveryError, connection_args, database_url


class RoleError(Exception):
    pass


# SELECT is not implied for new tables. Extend the matrix alongside new handlers.
API_READ = frozenset('''organizations user_accounts user_roles persons projects
    project_members charters charter_versions milestones requirements
    stakeholders risks reviews reports decisions scorecards cost_entries cost_postings
    cost_plan_versions cost_baseline_history evidence_files evidence_links
    change_entries change_approval_grants change_decision_records approval_requests
    appointment_authorities role_invitations requirement_baseline_signatures
    requirement_transitions milestone_completions milestone_forecasts milestone_delay_episodes
    agent_credentials agent_project_grants agent_security_events agent_token_versions
    agent_collection_versions web_sessions login_windows idempotency_keys audit_logs
    rule_events review_submission_snapshots project_stage_transitions'''.split())
API_ADD = frozenset('''projects project_members charters charter_versions milestones requirements stakeholders
    risks reviews cost_entries cost_postings cost_plan_versions cost_baseline_history evidence_files evidence_links
    change_entries change_decision_records approval_requests role_invitations requirement_baseline_signatures
    requirement_transitions milestone_completions milestone_forecasts milestone_delay_episodes
    agent_credentials agent_project_grants agent_security_events agent_token_versions
    agent_collection_versions web_sessions login_windows idempotency_keys audit_logs outbox_events
    rule_events review_submission_snapshots project_stage_transitions'''.split())

# Postgres uses the same privilege for UPDATE and SELECT ... FOR SHARE. These
# tables need row locks but must not accept any UPDATE from the API login.
LOCKS = {
    'user_roles': 'id', 'persons': 'id', 'appointment_authorities': 'org_id',
    'change_approval_grants': 'project_id', 'agent_project_grants': 'project_id',
    'reviews': 'id', 'reports': 'id', 'decisions': 'id', 'stakeholders': 'id',
}
API_EDIT = {
    'user_accounts': 'last_login_at', 'web_sessions': 'revoked_at',
    'login_windows': 'started_at attempt_count',
    'agent_credentials': 'status revoked_at allowed_project_ids',
    'agent_collection_versions': 'revision', 'agent_token_versions': 'revision',
    'projects': 'name description revision updated_at pm_id sponsor_id stage',
    'project_members': 'assigned_by assignment_status accepted_at revoked_at invitation_decision_at invitation_expires_at updated_at',
    'role_invitations': 'status issuer_id expires_at decided_at revision',
    'charters': None, 'milestones': None, 'requirements': None, 'risks': None,
    'approval_requests': 'status decided_by decided_at reason updated_at',
    'change_entries': 'status approved_by revision effective_at updated_at',
    'cost_entries': 'is_current_baseline revision updated_at',
    'cost_postings': 'verified_by verified_at verification_note occurred_at status',
    'evidence_files': 'verified_by verified_at quarantine_status',
    'evidence_links': 'revision verification_note',
    'milestone_delay_episodes': 'closing_revision',
    'rule_events': 'status updated_at',
}
WORKER_READ = frozenset({'outbox_events', 'outbox_attempts', 'project_event_inbox', 'rule_events'})
WORKER_ADD = frozenset({'outbox_attempts', 'project_event_inbox', 'notifications'})
WORKER_EDIT = {
    'outbox_events': 'status retry_count locked_until next_attempt_at last_error updated_at published_at',
    'outbox_attempts': 'outcome finished_at', 'rule_events': 'notified_at',
}
KNOWN = API_READ | API_ADD | API_EDIT.keys() | WORKER_READ | WORKER_ADD | WORKER_EDIT.keys()


def id_list(names: Iterable[str]) -> sql.Composed:
    return sql.SQL(', ').join(sql.Identifier(name) for name in names)


def grant(conn: psycopg.Connection, role: str, table: str, privilege: str, columns: str | None = None) -> None:
    access: sql.Composable = sql.SQL(privilege)
    if columns:
        access += sql.SQL(' ({})').format(id_list(columns.split()))
    conn.execute(sql.SQL('GRANT {} ON TABLE {} TO {}').format(access, sql.Identifier('public', table), sql.Identifier(role)))


def role_check(conn: psycopg.Connection, role: str, owner: str) -> None:
    if role == owner or role.startswith('pg_'):
        raise RoleError('RUNTIME_ROLE_MUST_BE_DISTINCT')
    row = conn.execute('''SELECT r.rolcanlogin AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
                          AND NOT EXISTS(SELECT FROM pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid)
                          AND NOT EXISTS(SELECT FROM pg_shdepend d WHERE d.refclassid='pg_authid'::regclass
                              AND d.refobjid=r.oid AND d.deptype='o')
                          FROM pg_roles r WHERE r.rolname=%s''', (role,)).fetchone()
    if row != (True,):
        raise RoleError('RUNTIME_ROLE_MUST_BE_UNPRIVILEGED_AND_OWN_NOTHING')


def apply_roles(conn: psycopg.Connection, api: str, worker: str) -> None:
    if api == worker:
        raise RoleError('RUNTIME_ROLES_MUST_BE_DISTINCT')
    with conn.transaction():
        conn.execute('SET LOCAL search_path=pg_catalog')
        conn.execute("SET LOCAL lock_timeout='10s'")
        owner_info = conn.execute('''SELECT current_database(), current_user, pg_get_userbyid(datdba)
                                      FROM pg_database WHERE datname=current_database()''').fetchone()
        assert owner_info is not None
        name, owner, database_owner = owner_info
        if name in ('postgres', 'template0', 'template1') or owner != database_owner:
            raise RoleError('USE_THE_DEDICATED_PMO_DATABASE_OWNER')
        if conn.execute("""SELECT nspname FROM pg_namespace WHERE left(nspname,3)<>'pg_'
                           AND nspname NOT IN ('public','information_schema')""").fetchone():
            raise RoleError('DEDICATED_DATABASE_REQUIRED')
        for role in (api, worker):
            role_check(conn, role, owner)
        relations: dict[str, str] = dict(conn.execute('''SELECT relname, pg_get_userbyid(relowner) FROM pg_class
                            WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p','v','m','f','S')''').fetchall())
        required = KNOWN | {'alembic_version', 'cost_baseline_history_seq_seq'}
        if required - relations.keys() or any(user != owner for user in relations.values()):
            raise RoleError('MIGRATED_SCHEMA_AND_OWNER_REQUIRED')

        # Close inherited PUBLIC paths first. Only apply against a dedicated app database.
        logins = id_list((api, worker))
        db = sql.Identifier(name)
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC, {}').format(db, logins))
        conn.execute(sql.SQL('REVOKE ALL ON SCHEMA public FROM PUBLIC, {}').format(logins))
        conn.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, {}').format(logins))
        conn.execute(sql.SQL('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, {}').format(logins))
        conn.execute(sql.SQL('REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA public FROM PUBLIC, {}').format(logins))
        # PostgreSQL keeps column ACLs independently of table ACLs.
        for table, columns in conn.execute('''SELECT c.relname,array_agg(a.attname ORDER BY a.attnum)
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            WHERE c.relnamespace='public'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
              AND a.attacl IS NOT NULL GROUP BY c.relname'''):
            conn.execute(sql.SQL('REVOKE ALL ({}) ON TABLE {} FROM PUBLIC, {}').format(
                id_list(columns), sql.Identifier('public', table), logins))
        conn.execute('ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC')
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(db, logins))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(logins))

        conn.execute('''CREATE OR REPLACE FUNCTION public.pmo_lock_only_guard() RETURNS trigger
            LANGUAGE plpgsql SET search_path=pg_catalog AS $$
            BEGIN
                IF current_user <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) THEN
                    RAISE insufficient_privilege USING MESSAGE = 'Row-lock privilege does not permit updates';
                END IF;
                RETURN NULL;
            END $$''')
        conn.execute('''CREATE OR REPLACE FUNCTION public.pmo_zero_legacy_actual() RETURNS trigger
            LANGUAGE plpgsql SET search_path=pg_catalog AS $$
            BEGIN
                IF NEW.actual IS DISTINCT FROM 0 AND
                   current_user <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) THEN
                    RAISE insufficient_privilege USING MESSAGE = 'Actual is derived from verified postings';
                END IF;
                RETURN NEW;
            END $$''')
        conn.execute('''CREATE OR REPLACE TRIGGER pmo_zero_legacy_actual BEFORE INSERT ON public.cost_entries
            FOR EACH ROW EXECUTE FUNCTION public.pmo_zero_legacy_actual()''')
        for table, column in LOCKS.items():
            target = sql.Identifier('public', table)
            conn.execute(sql.SQL('CREATE OR REPLACE TRIGGER pmo_lock_only_guard BEFORE UPDATE ON {} '
                                'FOR EACH STATEMENT EXECUTE FUNCTION public.pmo_lock_only_guard()').format(target))
            grant(conn, api, table, 'UPDATE', column)
        for role, read, add, edit in ((api, API_READ, API_ADD, API_EDIT), (worker, WORKER_READ, WORKER_ADD, WORKER_EDIT)):
            for table in sorted(read):
                grant(conn, role, table, 'SELECT')
            for table in sorted(add):
                grant(conn, role, table, 'INSERT')
            for table, columns in edit.items():
                grant(conn, role, table, 'UPDATE', columns)
        # A narrowly scoped grant for the version log's identity value.
        conn.execute(sql.SQL('GRANT USAGE ON SEQUENCE public.cost_baseline_history_seq_seq TO {}').format(sql.Identifier(api)))
        # ON CONFLICT and RETURNING require SELECT on the referenced columns only.
        grant(conn, api, 'outbox_events', 'SELECT', 'id')
        grant(conn, worker, 'notifications', 'SELECT', 'rule_event_id recipient_person_id')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-role', required=True)
    parser.add_argument('--worker-role', required=True)
    parser.add_argument('--dedicated-database', required=True, action='store_true')
    args = parser.parse_args()
    try:
        # autocommit leaves a single explicit transaction controlled by apply_roles().
        with psycopg.connect(**connection_args(database_url(os.environ['PMO_MIGRATION_DATABASE_URL'])), autocommit=True) as conn:
            apply_roles(conn, args.api_role, args.worker_role)
    except RoleError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (psycopg.Error, RecoveryError, ArgumentError, KeyError, ValueError):
        print('ROLE_PROVISIONING_FAILED: check migration owner, database and role configuration', file=sys.stderr)
        return 1
    print('API and Outbox database privileges applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
