"""phase2 core schema

Revision ID: 23bc99c82160
Revises:
Create Date: 2026-11-27 16:19:18.180803

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "23bc99c82160"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("hash_prev", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hash"),
        schema="core",
    )
    op.create_table(
        "broker_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broker", sa.String(length=50), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("account_ref", sa.String(length=100), nullable=False),
        sa.Column(
            "mode_allowed",
            sa.Enum("simulation", "paper", "live", name="trading_mode", schema="core"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broker", "account_ref"),
        schema="core",
    )
    op.create_table(
        "risk_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="core",
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("viewer", "researcher", "trader", "admin", name="role", schema="core"),
            nullable=False,
        ),
        sa.Column("mfa_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="core",
    )
    op.create_index(op.f("ix_core_users_email"), "users", ["email"], unique=True, schema="core")
    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broker_account_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["broker_account_id"], ["core.broker_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broker_account_id", "kind"),
        schema="core",
    )
    # Audit log is append-only at the database level.
    op.execute("""
        CREATE FUNCTION core.audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'core.audit_log is append-only'; END $$;
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON core.audit_log
        FOR EACH ROW EXECUTE FUNCTION core.audit_log_immutable();
    """)
    # At most one active risk configuration.
    op.create_index(
        "uq_risk_config_single_active",
        "risk_config",
        ["active"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON core.audit_log")
    op.execute("DROP FUNCTION IF EXISTS core.audit_log_immutable()")
    op.drop_table("credentials", schema="core")
    op.drop_index(op.f("ix_core_users_email"), table_name="users", schema="core")
    op.drop_table("users", schema="core")
    op.drop_table("risk_config", schema="core")
    op.drop_table("broker_accounts", schema="core")
    op.drop_table("audit_log", schema="core")
    op.execute("DROP TYPE IF EXISTS core.role")
    op.execute("DROP TYPE IF EXISTS core.trading_mode")
