# -*- coding: utf-8 -*-
"""staff_report_compare_day 加单日准确率 acc（规格 §7 acc_day）。"""
from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"


def upgrade():
    op.add_column("staff_report_compare_day",
                  sa.Column("acc", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("staff_report_compare_day", "acc")
