# -*- coding: utf-8 -*-
"""种子导入：从 `万总/team_task/` 的 6 份 Excel 建团队 + 队长 + 车站/任务。

用户口径（2026-10-03）：
- 「这些站点是已经定义好的任务」→ **每个站点 = 一个任务**
- 队名 = 文件名里的队名；**队长**按姓名在 `persons` 里匹配（允许"小川"→"小川逸"）
- **队员名单资料里没有** → 只挂队长，队员由管理员在 `/teams` 页手工加
- 车站默认派给该队，`assign_date` = 运行日（可用 `--assign-date` 指定）

用法（**默认 dry-run，只打印不改库**）：

    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python \
        scripts/bd_seed_team_task.py --src "/Users/liuchang/Desktop/万总/team_task"

    # 真写：
    ... --apply

    # 只建队不建站：
    ... --apply --teams-only
"""
import argparse
import glob
import os
import sys
import unicodedata
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal                      # noqa: E402
from app.models import BdStation, BdTask, BdTeam, BdTeamMember, Person  # noqa: E402
from app.services import bd_teams, bd_tasks          # noqa: E402


def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def read_stations(path):
    """读一份队名单 Excel，返回车站名列表（第 1 列，跳过表头）。"""
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True, read_only=True).active
    out = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue                                  # 表头：车站/担当/开始日/完成日/状态
        if row and norm(row[0]):
            out.append(norm(row[0]))
    return out


def team_name_of(path):
    """文件名 → 队名：`小川队_负责车站及进度.xlsx` → `小川队`。"""
    base = os.path.basename(path)
    return norm(base.split("_")[0])


def match_leader(db, team_name):
    """队名去掉末尾"队" → 在 persons.display_name 里匹配。

    返回 (person, candidates)；**命中多条或 0 条都不猜**，交给调用方报告。
    """
    key = team_name[:-1] if team_name.endswith("队") else team_name
    people = db.query(Person).all()
    exact = [p for p in people if norm(p.display_name) == key]
    if exact:
        return (exact[0] if len(exact) == 1 else None), exact
    fuzzy = [p for p in people if key and key in norm(p.display_name)]
    if len(fuzzy) == 1:
        return fuzzy[0], fuzzy
    return None, fuzzy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True,
                    help="team_task 目录（含 6 份 〈队名〉_负责车站及进度.xlsx）")
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run）")
    ap.add_argument("--teams-only", action="store_true", help="只建队与队长")
    ap.add_argument("--stations-only", action="store_true", help="只建车站与任务")
    ap.add_argument("--assign-date", default="", help="分配日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--by", default="seed", help="创建人标记")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.src, "*.xlsx")))
    if not files:
        print("✗ 目录里没有 xlsx：%s" % args.src)
        return 2
    assign_date = (datetime.strptime(args.assign_date, "%Y-%m-%d").date()
                   if args.assign_date else date.today())

    db = SessionLocal()
    report, warnings = [], []
    try:
        for f in files:
            tname = team_name_of(f)
            stations = read_stations(f)
            team = db.query(BdTeam).filter(BdTeam.name == tname).first()
            created_team = False
            if team is None:
                if args.apply:
                    team = bd_teams.create_team(db, tname, by=args.by)
                    db.flush()
                created_team = True
            # 队长
            leader, cands = match_leader(db, tname)
            if leader is None:
                warnings.append("队长未确定：%s（候选 %d 人：%s）"
                                % (tname, len(cands),
                                   "、".join(c.display_name for c in cands[:5])))
            else:
                if args.apply and team is not None:
                    active = {m.person_code for m in db.query(BdTeamMember)
                              .filter(BdTeamMember.team_id == team.id,
                                      BdTeamMember.end_date.is_(None)).all()}
                    if leader.code not in active:
                        db.add(BdTeamMember(team_id=team.id,
                                            person_code=leader.code,
                                            role="leader", start_date=date.today()))
            n_st = 0
            if not args.teams_only and team is not None:
                # 现有车站名（用于判重，不覆盖）
                for nm in stations:
                    key = bd_tasks.norm_name(nm)
                    exist = (db.query(BdStation)
                             .filter(BdStation.name_norm == key).first())
                    if exist is None:
                        n_st += 1                       # dry-run 也报"会新建几个"
                        if args.apply:
                            exist = bd_tasks.create_station(db, nm, by=args.by)
                            db.flush()
                    if args.apply and exist is not None:
                        task = (db.query(BdTask)
                                .filter(BdTask.station_id == exist.id).first())
                        if task is None:
                            bd_tasks.create_tasks(db, [exist.id], by=args.by,
                                                  team_id=team.id,
                                                  assign_date=assign_date)
                        else:
                            bd_tasks.set_task_team(db, [task.id], team.id,
                                                   assign_date=assign_date)
                        db.flush()
            report.append((tname, len(stations), n_st, created_team,
                           leader.display_name if leader else "—",
                           team.id if team is not None else None))
        if args.apply:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()

    mode = "已写入" if args.apply else "DRY-RUN（未改库）"
    print("=" * 78)
    print("种子导入 · %s · 分配日期 %s" % (mode, assign_date))
    print("-" * 78)
    print("%-10s %8s %8s %8s %-12s %s" % ("队", "文件站数", "新建站", "新建队", "队长", "队id"))
    tot_file = tot_new = 0
    for tname, nf, nn, ct, ld, tid in report:
        tot_file += nf
        tot_new += nn
        print("%-10s %8d %8d %8s %-12s %s"
              % (tname, nf, nn, "是" if ct else "", ld, tid or "—"))
    print("-" * 78)
    print("合计：文件里 %d 个车站，新建 %d 个" % (tot_file, tot_new))
    for w in warnings:
        print("⚠️  " + w)
    if not args.apply:
        print("\n提示：加 --apply 才真正写库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
