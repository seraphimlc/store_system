"""widen bd_log.action so audit action names fit"""
from alembic import op
import sqlalchemy as sa

revision = "b0a1c2d3e4f6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("bd_log") as batch:
        batch.alter_column(
            "action",
            existing_type=sa.String(length=24),
            type_=sa.String(length=40),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table("bd_log") as batch:
        batch.alter_column(
            "action",
            existing_type=sa.String(length=40),
            type_=sa.String(length=24),
            existing_nullable=False,
        )
