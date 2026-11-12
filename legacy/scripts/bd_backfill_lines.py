# -*- coding: utf-8 -*-
"""把 `scripts/bd_station_lines.json` 里的「线路 → 站名」补到 `bd_station.line`。

数据由 `scripts/bd_extract_lines.py` 从 5 份执行说明 PDF 抽出（**只跑一次**），
所以本脚本**不需要 pypdf**。

- 默认 dry-run；`--apply` 才写
- **只补空值**（`--overwrite` 才覆盖已有线路）——避免覆盖人工维护过的值
- 每次写入都留 `bd_log`（domain=station, action=update, field=线路）

    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python \
        scripts/bd_backfill_lines.py --lines scripts/bd_station_lines.json --apply
"""
import argparse
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal                    # noqa: E402
from app.models import BdStation                   # noqa: E402
from app.services import bd_log                    # noqa: E402


def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).strip().replace(" ", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", default="scripts/bd_station_lines.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--overwrite", action="store_true",
                    help="覆盖已有线路（默认只补空值）")
    args = ap.parse_args()

    path = os.path.abspath(args.lines)
    if not os.path.exists(path):
        print("✗ 找不到线路文件：%s（先跑 scripts/bd_extract_lines.py）" % path)
        return 2
    data = json.load(open(path, encoding="utf-8"))
    m = {norm(k): v for k, v in (data.get("station_to_line") or {}).items()}
    print("线路文件：%s（来源 %s）共 %d 个站名"
          % (os.path.basename(path), data.get("source", "?"), len(m)))

    db = SessionLocal()
    filled = skipped = missing = 0
    per_line = {}
    try:
        for st in db.query(BdStation).all():
            line = m.get(norm(st.name))
            if not line:
                missing += 1
                continue
            if st.line and not args.overwrite:
                skipped += 1
                continue
            if st.line == line:
                continue
            if args.apply:
                old = st.line or ""
                st.line = line
                bd_log.log(db, "station", "update", ref_id=st.id,
                           ref_label=st.name, field="线路", old=old, new=line,
                           actor="seed", actor_name="线路补全")
            filled += 1
            per_line[line] = per_line.get(line, 0) + 1
        if args.apply:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()

    print("=" * 70)
    print("%s：补上线路 %d 个站；跳过（已有线路）%d；文件里没有映射 %d"
          % ("已写入" if args.apply else "DRY-RUN（未改库）",
             filled, skipped, missing))
    for k, v in sorted(per_line.items(), key=lambda x: -x[1])[:15]:
        print("   %-28s %d" % (k, v))
    if not args.apply:
        print("\n提示：加 --apply 才真正写库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
