"""align column types with ORM models (TEXT -> varchar)

Revision ID: 0003
Revises: 0002

The 0001 migration created these columns as TEXT while the ORM declares
String(n). Alembic autogenerate therefore reported permanent drift. This
migration makes the database match the models, which also gives PostgreSQL
real length constraints.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

WIDTHS = [
    ("appointment", "status", "varchar(16)"),
    ("appointment", "cancel_reason", "varchar(500)"),
    ("appointment", "cancelled_by", "varchar(64)"),
    ("appointment_events", "event_type", "varchar(32)"),
    ("appointment_events", "actor_id", "varchar(64)"),
    ("appointment_events", "actor_role", "varchar(32)"),
    ("appointment_events", "from_status", "varchar(16)"),
    ("appointment_events", "to_status", "varchar(16)"),
    ("appointment_events", "reason", "varchar(500)"),
    ("clinic", "name", "varchar(120)"),
    ("clinic", "timezone", "varchar(64)"),
    ("doctor", "name", "varchar(120)"),
    ("idempotency_keys", "actor_id", "varchar(64)"),
    ("idempotency_keys", "key", "varchar(128)"),
    ("idempotency_keys", "request_hash", "varchar(64)"),
    ("patient", "name", "varchar(120)"),
]

EXCLUSIONS = [
    """ALTER TABLE appointment ADD CONSTRAINT ex_doctor_no_overlap
        EXCLUDE USING gist (doctor_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
        WHERE (status IN ('pending','booked'))""",
    """ALTER TABLE appointment ADD CONSTRAINT ex_patient_no_overlap
        EXCLUDE USING gist (patient_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
        WHERE (status IN ('pending','booked'))""",
]


def upgrade() -> None:
    # A partial-index predicate references appointment.status, so the exclusion
    # constraints must be dropped before the type change and rebuilt after it.
    op.execute("ALTER TABLE appointment DROP CONSTRAINT IF EXISTS ex_doctor_no_overlap")
    op.execute("ALTER TABLE appointment DROP CONSTRAINT IF EXISTS ex_patient_no_overlap")
    for table, column, sqltype in WIDTHS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {sqltype} "
            f"USING {column}::{sqltype}"
        )
    for statement in EXCLUSIONS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE appointment DROP CONSTRAINT IF EXISTS ex_doctor_no_overlap")
    op.execute("ALTER TABLE appointment DROP CONSTRAINT IF EXISTS ex_patient_no_overlap")
    for table, column, _ in WIDTHS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE text USING {column}::text"
        )
    for statement in EXCLUSIONS:
        op.execute(statement)
