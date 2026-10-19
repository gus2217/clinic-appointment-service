"""lifecycle statuses, hold expiry, events table

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("ALTER TABLE appointment DROP CONSTRAINT ck_status")
    op.execute("""ALTER TABLE appointment ADD CONSTRAINT ck_status CHECK (
        status IN ('pending','booked','cancelled','completed','no_show','rejected','expired'))""")
    op.execute("ALTER TABLE appointment ADD COLUMN hold_expires_at timestamptz")
    op.execute("""ALTER TABLE appointment ADD CONSTRAINT ck_hold CHECK (
        (status = 'pending') = (hold_expires_at IS NOT NULL))""")
    op.execute("ALTER TABLE appointment DROP CONSTRAINT ck_cancel_consistent")
    op.execute("""ALTER TABLE appointment ADD CONSTRAINT ck_cancel_consistent CHECK (
        (status = 'cancelled') = (cancel_reason IS NOT NULL))""")
    op.execute("ALTER TABLE appointment DROP CONSTRAINT ex_doctor_no_overlap")
    op.execute("""ALTER TABLE appointment ADD CONSTRAINT ex_doctor_no_overlap
        EXCLUDE USING gist (doctor_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
        WHERE (status IN ('pending','booked'))""")
    op.execute("ALTER TABLE appointment DROP CONSTRAINT ex_patient_no_overlap")
    op.execute("""ALTER TABLE appointment ADD CONSTRAINT ex_patient_no_overlap
        EXCLUDE USING gist (patient_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
        WHERE (status IN ('pending','booked'))""")
    op.execute("""CREATE TABLE appointment_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        appointment_id uuid NOT NULL REFERENCES appointment(id) ON DELETE CASCADE,
        event_type text NOT NULL,
        actor_id text,
        actor_role text,
        from_status text,
        to_status text,
        reason text,
        payload jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("CREATE INDEX ix_events_appointment ON appointment_events (appointment_id, created_at)")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS appointment_events")
