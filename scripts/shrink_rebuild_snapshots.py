# -*- coding: utf-8 -*-
"""把 `rebuild_snapshots` 历史行的**全量快照**瘦身成摘要（2026-10-17 用户拍板 A）。

背景：重算前的"证据"原来把整月正式表序列化成 JSON 存进 `payload_json`
（线上实测 2026-09 = 34,118 行 / 7.4MB），但**全仓没有任何代码读它**（回灌从未实现）
→ 只写不读 + 一份 `person_code`/`raw_record_id` 的个人数据副本。
写入端已改（`flow.rebuild_month` 只写摘要）；本脚本处理**数据库里已有的历史行**。

默认**干跑**（只报告会改成什么）；要真改加 `--apply`。
建议先 `pg_dump` 备份。

用法：
    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python \
        scripts/shrink_rebuild_snapshots.py            # 干跑
    ... --apply                                        # 真改
线上（容器内，注意 DATABASE_URL 已是 PG）：
    docker exec deploy-web-1 python scripts/shrink_rebuild_snapshots.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal                      # noqa: E402
from app.models import RebuildSnapshot               # noqa: E402

_NOTE = "只存摘要；全量快照已于 2026-10-17 移除（无人读，且是个人数据副本）"


def _digest_of(raw: str) -> str:
    """老行只有全量数组 → 能救出多少算多少（行数/点数合计/分布），其余丢弃。"""
    try:
        arr = json.loads(raw)
    except Exception:                                # noqa: BLE001
        arr = None
    if not isinstance(arr, list):
        return json.dumps({"kind": "digest", "note": _NOTE,
                           "dropped": "unparsable-or-already-digest"},
                          ensure_ascii=False)
    by_points: dict = {}
    pts = 0
    for it in arr:
        if not isinstance(it, dict):
            continue
        p = int(it.get("points") or 0)
        by_points[str(p)] = by_points.get(str(p), 0) + 1
        pts += p
    return json.dumps({"kind": "digest", "count": len(arr),
                       "points_sum": pts, "by_points": by_points,
                       "note": _NOTE}, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真改（默认干跑）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.query(RebuildSnapshot).order_by(RebuildSnapshot.month).all()
        if not rows:
            print("没有 rebuild_snapshots 行，无事可做。")
            return 0
        total_before = total_after = 0
        for r in rows:
            old = r.payload_json or ""
            new = _digest_of(old)
            total_before += len(old)
            total_after += len(new)
            print(f"{r.month}  row_count={r.row_count}  "
                  f"{len(old):>10,} → {len(new):>6,} 字节")
            if args.apply:
                r.payload_json = new
        if args.apply:
            db.commit()
            print("已提交。")
        else:
            print("（干跑，未改任何数据；加 --apply 才真改）")
        print(f"合计 {total_before:,} → {total_after:,} 字节 "
              f"（省 {100 * (1 - total_after / max(total_before, 1)):.1f}%）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
