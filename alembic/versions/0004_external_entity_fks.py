"""appointment no longer owns doctor/patient rows (external directories)

Revision ID: 0004
Revises: 0003

Doctor and patient data belongs to the external directory services. Appointment
still stores their ids, but the local foreign keys would reject ids that do not
exist locally, which is exactly what the http directory mode produces. The
doctor/patient tables remain as the demo placeholder for the local mode.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE appointment DROP CONSTRAINT IF EXISTS appointment_doctor_id_fkey"
    )
    op.execute(
        "ALTER TABLE appointment DROP CONSTRAINT IF EXISTS appointment_patient_id_fkey"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE appointment ADD CONSTRAINT appointment_doctor_id_fkey "
        "FOREIGN KEY (doctor_id) REFERENCES doctor(id)"
    )
    op.execute(
        "ALTER TABLE appointment ADD CONSTRAINT appointment_patient_id_fkey "
        "FOREIGN KEY (patient_id) REFERENCES patient(id)"
    )
