# -*- coding: utf-8 -*-
"""回滚 4 家店修复：以 2026-08_巡回最终结算.xlsx（公司内部权威）为准，
两种写法的店都算有效 → 恢复上轮按支付宝删除的 4 条：
  ざくろ 銀座店(55097)/ココカラファイン□銀座４丁目店(84411) → Jing Feiran
  Dog Salon Zero(48200) → 陳偉鋒
  京橋ハラミ屋(19734) → 李文强
恢复 raw valid + 重建 formal + 重算 8 月；使系统与结算文件 34 人店数全对齐。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app.db as appdb
from app.models import RawRecord, FormalRecord
from app.services import v3_perf, v3_period
from datetime import date as _d


def _formal_for_raw(rr, imp_id):
    from store_settle.rules import point_for
    d = (rr.modified_raw or "")[:10]
    jd = None
    if len(d or "") == 10:
        try:
            jd = _d.fromisoformat(d)
        except ValueError:
            jd = None
    pts = point_for(jd, rr.deploy_raw or "", _d(2026, 7, 9)) if jd else 1
    return FormalRecord(import_id=imp_id, raw_record_id=rr.id,
                        person_code=rr.submitter_code or rr.submitter_raw,
                        store_id_raw=rr.store_id_raw, japan_date=jd, points=pts)


db = appdb.SessionLocal()
TARGET = ["0101047092026031200555097", "0202047092026032480084411",
          "0101047092026081903348200", "0101047092026060970019734"]
raws = db.query(RawRecord).filter(RawRecord.store_id_raw.in_(TARGET)).all()
restored = 0
for rr in raws:
    if rr.clean_status != "valid":
        rr.clean_status = "valid"
        rr.filter_reason = None
        rr.filtered_by_raw_id = None
        rr.confirm_state = "auto_approved"
        restored += 1
        # 重建 formal
        f = _formal_for_raw(rr, rr.import_id)
        db.add(f)
db.commit()
print("恢复 raw valid:", restored)

# 重算 8 月
v3_perf.sync_month_stats(db, "2026-08")
v3_perf.sync_month_perf(db, "2026-08")
v3_period.sync_period_table(db, "2026-08")

fr = db.query(FormalRecord).filter(
    FormalRecord.japan_date >= "2026-08-01", FormalRecord.japan_date < "2026-09-01").all()
mp = v3_perf.month_perf(db, "2026-08")
print("恢复后 8 月基准:")
print("  formal:", len(fr), "点数:", sum(x.points for x in fr))
print("  月绩效:", len(mp), "人 总点:", sum(x["points"] for x in mp),
      "工资:", sum(x["amount"] for x in mp))
rows = v3_period.period_rows(db, "2026-08")
print("  找平:", len(rows), "分期已发:", sum(r["half1_amt"]+r["half2_amt"] for r in rows),
      "偏差:", sum(r["diff"] for r in rows), "点")
db.close()