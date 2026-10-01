# -*- coding: utf-8 -*-
"""staff_date_plans：日期计划（半月出勤登记）。

规格 `docs/specs-date-plan.md`：一天一条 `(person_code, plan_date)`，
`available=True` 默认可出勤；**只加表，不改任何现有业务表**。
"""
from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4a5b6"
down_revision = "b3c4d5e6f7a8"


def upgrade():
    op.create_table(
        "staff_date_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_code", sa.String(length=32),
                  sa.ForeignKey("persons.code"), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False,
                  server_default="web"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("person_code", "plan_date", name="uq_sdp_person_date"),
    )
    op.create_index("ix_sdp_date", "staff_date_plans", ["plan_date"])


def downgrade():
    op.drop_index("ix_sdp_date", table_name="staff_date_plans")
    op.drop_table("staff_date_plans")
