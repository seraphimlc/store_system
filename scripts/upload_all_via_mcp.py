# -*- coding: utf-8 -*-
"""零人工干预导入：按线上顺序经 MCP 上传全部文件（模拟 WorkBuddy 调用）。

顺序：8 月巡店（4 份）→ 8 月对账（1 份）→ 9 月巡店（1 份）
目的：验证**上传链路自动完成**候选对生成/同店自动合并/受影响月重算，无需人工介入。
"""
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

TOK = os.environ.get("VISIT_MCP_TOKEN", "visit-test-rw-2026")
BASE = "/Users/liuchang/Desktop/万总"
FILES = [
    ("0726-0805_MarsNavi_STORE_VISIT_RECORD.xlsx", "visit"),
    ("0806-815_MarsNavi_STORE_VISIT_RECORD.xlsx", "visit"),
    ("0816-25 MarsNavi.xlsx", "visit"),
    ("MarsNavi 0826-0831 store visit record.xlsx", "visit"),
    ("Alipay8月结算数据.xlsx", "recon"),
    ("0901-0915MarsNavi store visit.xlsx", "visit"),
]


async def main():
    async with streamable_http_client(
            "http://127.0.0.1:8765/mcp",
            http_client=create_mcp_http_client(
                headers={"Authorization": f"Bearer {TOK}"})) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for name, kind in FILES:
                path = os.path.join(BASE, name)
                if not os.path.isfile(path):
                    print(f"❌ 文件不存在：{path}")
                    continue
                t0 = time.time()
                res = await s.call_tool("visit_upload",
                                        {"filename": name, "path": path})
                d = res.structured_content or {}
                el = time.time() - t0
                if not d.get("ok"):
                    print(f"❌ {name}: {d.get('error')}")
                    continue
                data = d["data"]
                det = data.get("detected", {})
                if det.get("kind") == "recon" or data.get("task_id"):
                    print(f"✅ [对账] {name} ({el:.0f}s) 任务#{data.get('task_id')} "
                          f"月={data.get('month')} 差异{data.get('task', {}).get('diff_persons')}人/"
                          f"{data.get('task', {}).get('diff_amount')}円 "
                          f"覆盖={data.get('is_overwrite')}")
                else:
                    print(f"✅ [巡店] {name} ({el:.0f}s) 入表 {data.get('formal_rows')} 条 "
                          f"月={data.get('affected_months')} "
                          f"自动合并组={data.get('auto_merged_groups')} "
                          f"重算月={data.get('recomputed_months')} "
                          f"{'（跳过合并：' + str(data.get('auto_merge_skipped')) + '）' if data.get('auto_merge_skipped') else ''}")
    return 0


sys.exit(asyncio.run(main()))
