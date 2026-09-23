"""Initial immutable relational schema."""
import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False))
    op.create_table("auth_sessions", sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False), sa.Column("expires_at", sa.Float(), nullable=False))
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table("datasets", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False), sa.Column("format", sa.String(12), nullable=False),
        sa.Column("storage_name", sa.String(80), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False))
    op.create_index("ix_datasets_owner_id", "datasets", ["owner_id"])
    op.create_index("ix_datasets_sha256", "datasets", ["sha256"])
    op.create_table("jobs", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False), sa.Column("thresholds", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(36)), sa.Column("lease_until", sa.Float()),
        sa.Column("created_at", sa.Float(), nullable=False), sa.Column("started_at", sa.Float()),
        sa.Column("available_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float()), sa.Column("error", sa.Text()), sa.Column("result", sa.JSON()))
    for col in ("owner_id", "dataset_id", "status"):
        op.create_index("ix_jobs_" + col, "jobs", [col])
    op.create_index("ix_jobs_claim", "jobs", ["status", "created_at"])
    op.create_table("audit_events", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36)), sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(64)), sa.Column("created_at", sa.Float(), nullable=False))
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_table("rate_buckets", sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False), sa.Column("expires_at", sa.Float(), nullable=False))
    op.create_index("ix_rate_buckets_expires_at", "rate_buckets", ["expires_at"])
    op.create_table("worker_heartbeats", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("seen_at", sa.Float(), nullable=False))


def downgrade():
    for name in ("worker_heartbeats", "rate_buckets", "audit_events", "jobs", "datasets", "auth_sessions", "users"):
        op.drop_table(name)
