"""Idempotent submission keys; preserves the deployed 0001 migration."""
import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("request_key", sa.String(128), nullable=True))
    op.create_index("uq_jobs_owner_request_key", "jobs", ["owner_id", "request_key"], unique=True)


def downgrade():
    op.drop_index("uq_jobs_owner_request_key", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("request_key")
