# -*- coding: utf-8 -*-
"""员工每日填报（staff_daily_reports）+ 对比分析报告（staff_report_analyses）。

规格：docs/记录-员工填报.md。
两张新表，不改动任何现有表；填报数据不参与工资计算。
"""
from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "f7e8d9c0b1a2"


def upgrade():
    op.create_table(
        "staff_daily_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_code", sa.String(32),
                  sa.ForeignKey("persons.code"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"),
                  nullable=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("area", sa.String(64), nullable=False, server_default=""),
        sa.Column("p1_cnt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p2_cnt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cnt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("client_ts", sa.String(40), nullable=False, server_default=""),
        sa.Column("source", sa.String(16), nullable=False, server_default="web"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("person_code", "report_date",
                            name="uq_sdr_person_date"),
    )
    op.create_index("ix_sdr_date", "staff_daily_reports", ["report_date"])

    op.create_table(
        "staff_report_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False,
                  server_default="pending"),
        sa.Column("summary", sa.JSON(), nullable=False,
                  server_default=sa.text("('{}')")),
        sa.Column("payload", sa.JSON(), nullable=False,
                  server_default=sa.text("('{}')")),
        sa.Column("ai_model", sa.String(64), nullable=False, server_default=""),
        sa.Column("ai_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sra_period", "staff_report_analyses",
                    ["period_start", "period_end"])


def downgrade():
    op.drop_index("ix_sra_period", table_name="staff_report_analyses")
    op.drop_table("staff_report_analyses")
    op.drop_index("ix_sdr_date", table_name="staff_daily_reports")
    op.drop_table("staff_daily_reports")
