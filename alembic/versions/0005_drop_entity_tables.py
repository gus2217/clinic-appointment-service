"""drop doctor/patient/clinic tables (external directories)

Revision ID: 0005
Revises: 0004

Doctor and patient data is owned by the directory services. The demo and tests
use in-memory fixtures, so these tables have no remaining reader. Appointment
already stores opaque UUIDs (migration 0004 dropped the foreign keys).
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = ("doctor_time_off", "doctor_working_hours", "doctor", "patient", "clinic")


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
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
