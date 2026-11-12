# -*- coding: utf-8 -*-
"""BD 作业域**结构一致性**自检（只读；上线/改数据后跑一次）。

用户 2026-10-03 的死规定：**一个人同时只能在一个队**（无跨队借调，只有转出→转入）。

检查项：
1. 一人同时在多个队（现役）→ **违规**（数据库部分唯一索引应已挡住；没有即说明索引没建）
2. 任务的担当不是**该队现役成员** → 违规（换了队/移出后留下的悬空担当）
3. 现役成员里**没有队长**的队 → 警告（用户可用圈选界面补）
4. **部分唯一索引是否存在** → 违规（缺索引 = 死规定没落到 DB 层）

    DATABASE_URL="sqlite:///file:$PWD/store_settle_live.db?mode=ro&uri=true" \
      ./.venv/bin/python scripts/check_single_team.py
"""
import sys

from sqlalchemy import func, text

from app.db import SessionLocal
from app.models import BdTask, BdTaskAssign, BdTeam, BdTeamMember


def main():
    db = SessionLocal()
    bad = 0
    warn = 0
    try:
        # 4) 索引在不在（死规定的底层保障）
        try:
            idx = {r[0] for r in db.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='bd_team_member'")).all()}
        except Exception:
            idx = set()
        if idx and "uq_bd_team_member_active_person" not in idx:
            print("✗ 缺索引 uq_bd_team_member_active_person"
                  "（死规定没落到数据库层；跑 alembic upgrade head）")
            bad += 1
        elif idx:
            print("✓ 部分唯一索引在（一人现役只能一个队，数据库层强制）")

        # 1) 一人多队
        dup = db.execute(text(
            "SELECT person_code, COUNT(*) c FROM bd_team_member "
            "WHERE end_date IS NULL GROUP BY person_code HAVING c > 1")).all()
        if dup:
            print("✗ 一人在多个队（现役）：%s" % dup)
            bad += len(dup)
        else:
            print("✓ 没有一人在多个队")

        # 2) 悬空担当：担当不在该队现役成员里
        rows = (db.query(BdTaskAssign.person_code, BdTask.team_id,
                         BdTask.id, BdTask.state)
                .join(BdTask, BdTask.id == BdTaskAssign.task_id)
                .filter(BdTask.team_id.isnot(None)).all())
        active = {(m.team_id, m.person_code) for m in db.query(BdTeamMember)
                  .filter(BdTeamMember.end_date.is_(None)).all()}
        dangling = [(t_id, code) for code, team_id, t_id, st in rows
                    if (team_id, code) not in active]
        if dangling:
            print("✗ 担当不在该队现役成员里 %d 条（悬空担当）：%s"
                  % (len(dangling), dangling[:6]))
            bad += len(dangling)
        else:
            print("✓ 没有悬空担当（担当都还是该队现役成员）")

        # 3) 没队长的队
        teams = db.query(BdTeam).filter(BdTeam.status == "active").all()
        no_leader = []
        for t in teams:
            n = (db.query(func.count(BdTeamMember.id))
                 .filter(BdTeamMember.team_id == t.id,
                         BdTeamMember.role == "leader",
                         BdTeamMember.end_date.is_(None)).scalar() or 0)
            if not n:
                no_leader.append(t.name)
        if no_leader:
            print("⚠ 没有队长的队 %d 个：%s（可在 /teams 圈选界面指定）"
                  % (len(no_leader), "、".join(no_leader)))
            warn += len(no_leader)
        else:
            print("✓ 每个在岗队都有队长")

        print("—" * 40)
        print("违规 %d 项 / 提醒 %d 项" % (bad, warn))
        return 1 if bad else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
