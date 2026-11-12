# -*- coding: utf-8 -*-
"""核对结果物化：staff_report_compare_person（人×区间）+ staff_report_compare_day（人×日）。

报告生成时落表；员工端核对页只读本表（不再实时跑 compare），见
docs/记录-员工填报.md。
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"


def _person_cols():
    """人×区间：与 ORM 一致（NOT NULL + server_default 0）。"""
    return [sa.Column(c, sa.Integer(), nullable=False, server_default="0")
            for c in ("sys_p1", "sys_p2", "sys_total", "rep_p1", "rep_p2",
                      "rep_total", "d1", "d2")]


def _day_cols():
    """人×日：与 ORM 一致（可空：某侧没有数据时就是 NULL）。"""
    return [sa.Column(c, sa.Integer(), nullable=True)
            for c in ("sys_p1", "sys_p2", "sys_total", "rep_p1", "rep_p2",
                      "rep_total", "d1", "d2")]


def upgrade():
    op.create_table(
        "staff_report_compare_person",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(),
                  sa.ForeignKey("staff_report_analyses.id"), nullable=False),
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        *_person_cols(),
        sa.Column("d_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("acc", sa.Float(), nullable=True),
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
        sa.Column("person_code", sa.String(32), nullable=False),
        sa.Column("ref_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default=""),
        *_day_cols(),
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
