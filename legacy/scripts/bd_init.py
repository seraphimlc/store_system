# -*- coding: utf-8 -*-
"""BD 作业域 P0 初始化：建表 → 装官方行政区划 → 建门店宇宙。

用法（cwd=项目根）：
```bash
DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/bd_init.py
DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/bd_init.py --skip-download
```

做三件事（**全部只新增作业域 bd_* 表，不碰结算域**）：
1. `bd_area`：総務省「全国地方公共団体コード」（都道府県 / 市区町村 / 政令市の区）
2. `bd_store`：从 `raw_records.original_row` 提取地址/业态 → 门店宇宙
3. 打印就绪度自检

数据源 Excel 会缓存在 `data/bd_soumu_codes.xlsx`（首次自动下载）。
"""
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.db import Base, SessionLocal          # noqa: E402
import app.models                              # noqa: F401,E402  注册模型
from app.services import bd_area, bd_store     # noqa: E402

SOUMU_PAGE = "https://www.soumu.go.jp/denshijiti/code.html"
CACHE = os.path.join(ROOT, "data", "bd_soumu_codes.xlsx")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

SKIP_DL = "--skip-download" in sys.argv


def log(msg):
    print(msg, flush=True)


def ensure_tables():
    """本地不跑 alembic → 用 create_all 补建缺失的表（只建缺失的）。"""
    from app.db import _ENGINE
    Base.metadata.create_all(_ENGINE)
    log("✓ 表已就绪（bd_area / bd_store）")


def fetch_codes(force=False):
    if os.path.exists(CACHE) and not force:
        log(f"✓ 用缓存 {os.path.relpath(CACHE, ROOT)}")
        return CACHE
    log("→ 下载総務省 全国地方公共団体コード …")
    req = urllib.request.Request(SOUMU_PAGE, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    import re
    m = re.search(r'href="(/main_content/\d+\.xlsx)"', html)
    if not m:
        raise SystemExit("找不到 Excel 链接，请手动下载后放到 " + CACHE)
    url = "https://www.soumu.go.jp" + m.group(1)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    r = urllib.request.Request(url, headers={"User-Agent": UA})
    data = urllib.request.urlopen(r, timeout=60).read()
    with open(CACHE, "wb") as f:
        f.write(data)
    log(f"✓ 已缓存 {os.path.relpath(CACHE, ROOT)}（{len(data)/1024:.0f} KB）")
    return CACHE


def main():
    log("=" * 68)
    log("BD 作业域 P0 初始化")
    log("=" * 68)
    log(f"DATABASE_URL = {os.environ.get('DATABASE_URL', '(未设置!)')}")
    if not os.environ.get("DATABASE_URL"):
        log("⚠️  未设置 DATABASE_URL —— app.db 在 import 时固化 engine，"
            "会静默连到空库。请显式设置。")

    ensure_tables()
    db = SessionLocal()
    try:
        # 1) 行政区划基底
        if SKIP_DL and not os.path.exists(CACHE):
            log("⚠️  --skip-download 但无缓存，跳过行政区划")
        else:
            path = fetch_codes()
            st = bd_area.sync_areas(db, path)
            log(f"✓ bd_area 同步：共 {st['total']} 行 "
                f"(都道府県 {st['pref']} / 市区町村 {st['city']} / "
                f"政令区 {st['ward']})，新增 {st['added']} / 更新 {st['updated']}")

        # 2) 门店宇宙
        log("→ 从 raw_records 构建门店宇宙（只读源表）…")
        st2 = bd_store.build_stores(db)
        log(f"✓ bd_store：扫过 {st2['raw_rows']} 条 raw → "
            f"{st2['stores']} 家门店（其中 {st2['with_address']} 家提到地址）、"
            f"新增 {st2['inserted']} / 更新 {st2['updated']}")

        # 3) 就绪度
        log("-" * 68)
        log("门店宇宙就绪度：")
        for k, v in bd_store.zones_ready(db).items():
            log(f"   {k:16s} = {v}")
        log("-" * 68)
        log("下一步（P1）需要 Google 结算开通后跑地理编码/Places 补齐坐标。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
