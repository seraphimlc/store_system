# -*- coding: utf-8 -*-
"""aug5 续：模拟全部任务超时自动认可 → 逐文件 finalize → 绩效/工资对账基准。
用法: DATABASE_URL=sqlite:///./store_settle_aug5.db ./.venv/bin/python tools_finalize_aug.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import os
from datetime import datetime, timedelta
os.environ.setdefault("DATABASE_URL", "sqlite:///./store_settle_aug5.db")
from collections import Counter
import app.db as appdb
from app.models import ImportFile, ConfirmTask, RawRecord, FormalRecord
from app.services import v3_flow, v3_perf


def main():
    db = appdb.SessionLocal()
    files = db.query(ImportFile).order_by(ImportFile.id).all()
    for imp in files:
        # 超时默认认可：把所有 open 任务 due 改为过去并 settle
        db.query(ConfirmTask).filter(ConfirmTask.import_id == imp.id,
                                     ConfirmTask.status == "open").update(
            {"due_at": datetime.utcnow() - timedelta(hours=1)})
        db.commit()
        n = v3_flow.settle_timeout_tasks(db, imp.id)
        r = v3_flow.finalize_import(db, imp.id)
        nf = db.query(FormalRecord).filter(FormalRecord.import_id == imp.id).count()
        print(f"imp{imp.id} {imp.file_name}: settle={n} finalize={r} formal={nf}")
    # 8 月绩效汇总
    print("\n== formal 全库 ==")
    fr = db.query(FormalRecord).all()
    print("formal 总行:", len(fr))
    c8 = Counter((str(x.japan_date or ""))[:7] for x in fr)
    print("按月份:", dict(c8))
    aug_f = [x for x in fr if (str(x.japan_date or "")).startswith("2026-08")]
    p1 = sum(1 for x in aug_f if x.points == 1)
    p2 = sum(1 for x in aug_f if x.points == 2)
    total_pts = p1 + p2 * 2
    print(f"8月 formal: {len(aug_f)} | 1点{p1} 2点{p2} 总点{total_pts}")
    print("工资合计(全库按8月人员):",
          sum(v3_perf.salary_for(m["points"]) for m in v3_perf.month_perf(db, "2026-08")))
    # 人员明细
    print("\n== 8月人员绩效 top ==")
    for m in v3_perf.month_perf(db, "2026-08")[:5]:
        print("  ", m)
    db.close()


if __name__ == "__main__":
    main()
