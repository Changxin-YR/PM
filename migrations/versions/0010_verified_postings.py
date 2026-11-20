"""Protect verified ledger rows, independent sign-off and reversal linkage."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'


def upgrade():
    op.execute('''CREATE FUNCTION pmo_guard_posting() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE entry cost_entries%ROWTYPE; original cost_postings%ROWTYPE;
    BEGIN
      IF TG_OP <> 'INSERT' AND OLD.status='verified' THEN RAISE EXCEPTION 'Immutable verified posting'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      SELECT * INTO STRICT entry FROM cost_entries WHERE id=NEW.cost_entry_id FOR SHARE;
      IF entry.project_id<>NEW.project_id OR entry.type<>NEW.type OR NEW.source_type<>NEW.type
        OR NEW.period<>to_char(NEW.occurred_at AT TIME ZONE 'UTC','YYYY-MM')
        OR (NEW.occurred_at AT TIME ZONE 'UTC')::date NOT BETWEEN entry.period_start AND entry.period_end
        THEN RAISE EXCEPTION 'Inconsistent cost period or entry'; END IF;
      IF NEW.amount_signed<0 OR NEW.posting_of_id IS NOT NULL THEN
        SELECT * INTO STRICT original FROM cost_postings WHERE id=NEW.posting_of_id FOR UPDATE;
        IF original.status<>'verified' OR original.amount_signed<=0 OR original.posting_of_id IS NOT NULL
          OR original.amount_signed<>-NEW.amount_signed OR original.cost_entry_id<>NEW.cost_entry_id
          OR original.period<>NEW.period OR original.occurred_at<>NEW.occurred_at
          OR nullif(trim(NEW.reversal_reason),'') IS NULL THEN RAISE EXCEPTION 'Invalid full reversal'; END IF;
      END IF;
      IF NEW.status='verified' AND (NEW.verified_by IS NULL OR NEW.verified_by=NEW.created_by
        OR NEW.verified_at IS NULL OR NEW.verified_at<NEW.occurred_at OR NEW.verified_at>clock_timestamp()
        OR NEW.source_fingerprint IS NULL OR nullif(trim(NEW.verification_note),'') IS NULL)
        THEN RAISE EXCEPTION 'Independent verification required'; END IF;
      RETURN NEW;
    END $$''')
    op.execute('CREATE TRIGGER validate_cost_posting BEFORE INSERT OR UPDATE OR DELETE ON cost_postings FOR EACH ROW EXECUTE FUNCTION pmo_guard_posting()')
    op.execute('CREATE TRIGGER no_truncate_cost_posting BEFORE TRUNCATE ON cost_postings FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c=op.get_bind()
    c.exec_driver_sql('LOCK TABLE cost_postings IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM cost_postings)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.execute('DROP TRIGGER no_truncate_cost_posting ON cost_postings')
    op.execute('DROP TRIGGER validate_cost_posting ON cost_postings')
    op.execute('DROP FUNCTION pmo_guard_posting()')
