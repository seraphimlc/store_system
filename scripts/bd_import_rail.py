# -*- coding: utf-8 -*-
"""导入一都三県全部线路 + 车站，并**自动给每个车站建任务**（车站即任务）。

数据文件：`scripts/bd_kanto_rail.json`（由 `bd_fetch_rail.py` 从 国土数値情報 N02 生成）。
⚠️ 默认 **dry-run**；`--apply` 才写库（先备份）。

用法：
    DATABASE_URL="sqlite:///./store_settle_live.db" PYTHONPATH=. \
        ./.venv/bin/python scripts/bd_import_rail.py             # 试算
    … --apply                                                    # 落库
    … --no-tasks                                                 # 只导车站，不建任务
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run）")
    ap.add_argument("--no-tasks", action="store_true", help="只导线路/车站，不自动建任务")
    ap.add_argument("--json", default="", help="数据文件路径（默认 scripts/bd_kanto_rail.json）")
    args = ap.parse_args()

    from app.config import get_settings
    from app.db import SessionLocal
    from app.services import bd_import_rail as imp

    url = get_settings().database_url
    print("库:", url)
    payload = imp.load_payload(args.json or None)
    print("数据源:", payload.get("source"))
    print("线路 %d 条 / 车站 %d 个" % (len(payload["lines"]), len(payload["stations"])))

    db = SessionLocal()
    try:
        rep = imp.import_rail(db, payload, dry=not args.apply,
                              make_tasks=not args.no_tasks)
        if not args.apply:
            db.rollback()
    finally:
        if args.apply:
            path = url.replace("sqlite:///", "").split("?")[0]
            if os.path.exists(path):
                bak = "%s.pre_railimport_%s" % (path, time.strftime("%Y%m%d_%H%M%S"))
                shutil.copy2(path, bak)
                print("已备份 →", bak)
            db.commit()
        db.close()

    print("\n=== %s ===" % ("试算（未写库）" if not args.apply else "导入完成"))
    print("线路：新建 %d / 已有 %d" % (rep["lines_created"], rep["lines_existing"]))
    print("车站：新建 %d / 认领历史行 %d / 已存在 %d"
          % (rep["stations_new"], rep["stations_adopted"], rep["stations_existing"]))
    print("任务：自动新建 %d" % rep["tasks_created"])
    print("按县：", {k: v for k, v in sorted(rep["prefs"].items())})
    if rep["unmatched"]:
        print("\n⚠️ 同名多候选、认领不了的历史站 %d 个（会各成一行 → 可能重复任务）："
              % len(rep["unmatched"]))
        for u in rep["unmatched"][:20]:
            print("   %s（%s %s）历史: %s" % (u["name"], u["operator"], u["line"],
                                          u["legacy"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
