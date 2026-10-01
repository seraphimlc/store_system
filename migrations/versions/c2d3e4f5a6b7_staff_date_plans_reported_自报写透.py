# -*- coding: utf-8 -*-
"""staff_date_plans 加 `reported`：自报出勤写透到计划表（用户口径 2026-10-01）。

"如果员工自报了，可以直接改这个计划表里的状态" —— 加一列 `reported` 后，
管理端矩阵**只读这一张表**就能渲染三态（□ 已出勤 / ○ 可出勤 / × 不出勤），
不再关联 `staff_daily_reports`（不 join、不做第二条查询）。

本迁移同时把**已有的**自报回填进 `reported`（新表刚建、正常没有历史数据，
但 wherever 已经跑过上一版迁移并填过数据的环境都安全）。
"""
from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "c1d2e3f4a5b6"


def upgrade():
    op.add_column("staff_date_plans",
                  sa.Column("reported", sa.Boolean(), nullable=False,
                            server_default=sa.false()))
    # 回填：已经存在自报的日子 → reported=True（写路径改造前的历史数据）
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT person_code, plan_date FROM staff_date_plans")).fetchall()
    for code, d in rows:
        hit = conn.execute(sa.text(
            "SELECT 1 FROM staff_daily_reports "
            "WHERE person_code = :c AND report_date = :d"),
            {"c": code, "d": d}).first()
        if hit:
            conn.execute(sa.text(
                "UPDATE staff_date_plans SET reported = :r "
                "WHERE person_code = :c AND plan_date = :d"),
                {"r": True, "c": code, "d": d})


def downgrade():
    op.drop_column("staff_date_plans", "reported")
