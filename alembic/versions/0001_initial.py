"""initial schema (GUID ids, exclusion constraints)

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("""
    CREATE TABLE clinic (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL,
        timezone text NOT NULL DEFAULT 'Africa/Nairobi'
    )""")
    op.execute("""
    CREATE TABLE doctor (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        clinic_id uuid NOT NULL REFERENCES clinic(id),
        name text NOT NULL,
        slot_minutes integer NOT NULL DEFAULT 30,
        is_active boolean NOT NULL DEFAULT true
    )""")
    op.execute("""
    CREATE TABLE doctor_working_hours (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        doctor_id uuid NOT NULL REFERENCES doctor(id) ON DELETE CASCADE,
        weekday integer NOT NULL,
        start_time time NOT NULL,
        end_time time NOT NULL,
        CONSTRAINT ck_weekday CHECK (weekday BETWEEN 0 AND 6),
        CONSTRAINT ck_hours_order CHECK (start_time < end_time)
    )""")
    op.execute("""
    CREATE TABLE doctor_time_off (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        doctor_id uuid NOT NULL REFERENCES doctor(id) ON DELETE CASCADE,
        starts_at timestamptz NOT NULL,
        ends_at timestamptz NOT NULL,
        CONSTRAINT ck_timeoff_order CHECK (ends_at > starts_at)
    )""")
    op.execute("""
    CREATE TABLE patient (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL
    )""")
    op.execute("""
    CREATE TABLE appointment (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        doctor_id uuid NOT NULL REFERENCES doctor(id),
        patient_id uuid NOT NULL REFERENCES patient(id),
        starts_at timestamptz NOT NULL,
        ends_at timestamptz NOT NULL,
        duration_minutes integer NOT NULL DEFAULT 30,
        status text NOT NULL DEFAULT 'booked',
        cancel_reason text,
        cancelled_by text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_status CHECK (status IN ('booked','cancelled')),
        CONSTRAINT ck_duration CHECK (ends_at = starts_at + interval '30 minutes'),
        CONSTRAINT ck_slot_aligned CHECK (extract(minute from starts_at)::int % 30 = 0
                                          AND extract(second from starts_at) = 0),
        CONSTRAINT ck_cancel_consistent CHECK (
            (status = 'cancelled' AND cancel_reason IS NOT NULL)
            OR (status = 'booked' AND cancel_reason IS NULL))
    )""")
    op.execute("""
    ALTER TABLE appointment ADD CONSTRAINT ex_doctor_no_overlap
      EXCLUDE USING gist (doctor_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
      WHERE (status = 'booked')""")
    op.execute("""
    ALTER TABLE appointment ADD CONSTRAINT ex_patient_no_overlap
      EXCLUDE USING gist (patient_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
      WHERE (status = 'booked')""")
    op.execute("CREATE INDEX ix_appt_doctor_day ON appointment (doctor_id, starts_at) WHERE status = 'booked'")
    op.execute("CREATE INDEX ix_appt_patient ON appointment (patient_id, starts_at) WHERE status = 'booked'")
    op.execute("""
    CREATE TABLE idempotency_keys (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        actor_id text NOT NULL,
        key text NOT NULL,
        request_hash text NOT NULL,
        status_code integer NOT NULL,
        response_body jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_idempotency_actor_key UNIQUE (actor_id, key)
    )""")

def downgrade() -> None:
    for table in ("idempotency_keys", "appointment", "patient", "doctor_time_off",
                  "doctor_working_hours", "doctor", "clinic"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
