# -*- coding: utf-8 -*-
"""payroll_period_rows 加分期店数快照 6 列

上半月/下半月各自的有效店、1点店、2点店在同步时固化落库，
发薪指标全部来自快照，不随人×日表后续变化漂移。
"""
import sqlalchemy as sa
from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade():
    for col in ("half1_records", "half1_p1", "half1_p2",
                "half2_records", "half2_p1", "half2_p2"):
        op.add_column("payroll_period_rows",
                      sa.Column(col, sa.Integer(), nullable=False,
                                server_default="0"))


def downgrade():
    for col in ("half2_p2", "half2_p1", "half2_records",
                "half1_p2", "half1_p1", "half1_records"):
        op.drop_column("payroll_period_rows", col)
