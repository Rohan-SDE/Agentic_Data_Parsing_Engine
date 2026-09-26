"""Bind verified external identities without linking by user-supplied email."""
import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("external_subject", sa.String(64), nullable=True))
    op.create_index("uq_users_external_subject", "users", ["external_subject"], unique=True)

def downgrade():
    op.drop_index("uq_users_external_subject", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("external_subject")
