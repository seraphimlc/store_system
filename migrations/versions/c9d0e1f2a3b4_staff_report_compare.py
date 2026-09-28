# -*- coding: utf-8 -*-
"""核对结果物化：staff_report_compare_person（人×区间）+ staff_report_compare_day（人×日）。

报告生成时落表；员工端核对页只读本表（不再实时跑 compare），见
docs/superpowers/specs/2026-09-27-staff-daily-report-design.md。
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"


def _common_cols():
    return [
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("sys_p1", sa.Integer(), nullable=True),
        sa.Column("sys_p2", sa.Integer(), nullable=True),
        sa.Column("sys_total", sa.Integer(), nullable=True),
        sa.Column("rep_p1", sa.Integer(), nullable=True),
        sa.Column("rep_p2", sa.Integer(), nullable=True),
        sa.Column("rep_total", sa.Integer(), nullable=True),
        sa.Column("d1", sa.Integer(), nullable=True),
        sa.Column("d2", sa.Integer(), nullable=True),
    ]


def upgrade():
    op.create_table(
        "staff_report_compare_person",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(),
                  sa.ForeignKey("staff_report_analyses.id"), nullable=False),
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        *_common_cols(),
        sa.Column("d_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("acc", sa.Float(), nullable=True),
        sa.Column("acc1", sa.Float(), nullable=True),
        sa.Column("acc2", sa.Float(), nullable=True),
        sa.Column("days_filled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("days_system", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("days_both", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gaps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("abs_dt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("analysis_id", "person_code",
                            name="uq_srcp_analysis_person"),
    )
    op.create_index("ix_srcp_person", "staff_report_compare_person",
                    ["person_code"])

    op.create_table(
        "staff_report_compare_day",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(),
                  sa.ForeignKey("staff_report_analyses.id"), nullable=False),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default=""),
        *_common_cols(),
        sa.Column("dt", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("analysis_id", "person_code", "ref_date",
                            name="uq_srcd_analysis_person_date"),
    )
    op.create_index("ix_srcd_person", "staff_report_compare_day", ["person_code"])


def downgrade():
    op.drop_index("ix_srcd_person", table_name="staff_report_compare_day")
    op.drop_table("staff_report_compare_day")
    op.drop_index("ix_srcp_person", table_name="staff_report_compare_person")
    op.drop_table("staff_report_compare_person")
