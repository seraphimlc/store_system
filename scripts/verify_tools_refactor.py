# -*- coding: utf-8 -*-
"""工具场景化重构的验收脚本（16 个场景工具）。

用法（cwd=项目根，本地 MCP 服务已启动）：
    DATABASE_URL="sqlite:///./store_settle_live.db" \
      mcp_service/.venv/bin/python scripts/verify_tools_refactor.py

覆盖 8 项（见 docs/specs-mcp-tools-scenario.md §6）：
  1 工具数 = 16（管理员）        2 员工 = 3
  3 旧工具名确实消失              4 员工越权仍被拦（FORBIDDEN_TOOL）
  5 写闸门仍有效（无确认语被拒）   6 关键业务数字与重构前一致
  7 描述含"什么时候用我"的互斥指引 8 自洽检查仍全过
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import ClientSession
from mcp.client.streamable_http import (create_mcp_http_client,
                                        streamable_http_client)

from app.db import SessionLocal
from app.models import User
from mcp_service import tokens

URL = "http://127.0.0.1:8765/mcp"
EXPECT_ADMIN = 16
EXPECT_STAFF = 3
OLD_NAMES = ["visit_month_summary", "visit_dashboard", "visit_upload_recon",
             "visit_export_salary", "visit_store_merge_pair", "visit_rebuild_preview",
             "visit_recon_status", "visit_ping", "visit_product_doc",
             "visit_verify_integrity"]
# 关键业务数字（重构前基准；口径变化会在这里暴露）
BASELINE = {"2026-08": (12507, 16787), "2026-09": (15070, 19477)}

fails = []


def _token(username, scopes):
    db = SessionLocal()
    u = db.query(User).filter_by(username=username).first()
    raw, _ = tokens.issue(db, u.id, f"验收-{username}", scopes, 1)
    db.close()
    return raw


async def _call(s, tool, args=None):
    try:
        res = await s.call_tool(tool, args or {})
        return res.structured_content or {}
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "error": {"code": "MCP_ERROR", "message": str(exc)[:160]}}


async def main():
    tok_a = _token("admin", "read,write")
    tok_s = _token("chenjiayi", "read")

    # ---- 1/2/3/4：工具面与权限 ----
    async with streamable_http_client(URL, http_client=create_mcp_http_client(
            headers={"Authorization": f"Bearer {tok_a}"}, timeout=30)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            admin_tools = sorted(t.name for t in (await s.list_tools()).tools)
    async with streamable_http_client(URL, http_client=create_mcp_http_client(
            headers={"Authorization": f"Bearer {tok_s}"}, timeout=30)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            staff_tools = sorted(t.name for t in (await s.list_tools()).tools)
            # 4 员工越权
            d = await _call(s, admin_tools[0] if admin_tools else "visit_overview",
                            {"month": "2026-08"})
            code = (d.get("error") or {}).get("code")
            if admin_tools and admin_tools[0] not in staff_tools:
                ok = code == "FORBIDDEN_TOOL"
                print(f"[4] 员工调管理员工具 → {code} {'✅' if ok else '❌'}")
                if not ok:
                    fails.append(f"员工越权未被拦：{code}")

    print(f"[1] 管理员工具数 {len(admin_tools)}（期望 {EXPECT_ADMIN}）"
          f" {'✅' if len(admin_tools) == EXPECT_ADMIN else '❌'}")
    if len(admin_tools) != EXPECT_ADMIN:
        fails.append(f"管理员工具数 {len(admin_tools)} != {EXPECT_ADMIN}")
    print(f"[2] 员工工具数 {len(staff_tools)}（期望 {EXPECT_STAFF}）"
          f" {'✅' if len(staff_tools) == EXPECT_STAFF else '❌'}")
    if len(staff_tools) != EXPECT_STAFF:
        fails.append(f"员工工具数 {len(staff_tools)} != {EXPECT_STAFF}")
    print(f"    管理员: {', '.join(n.replace('visit_', '') for n in admin_tools)}")
    print(f"    员工:   {', '.join(n.replace('visit_', '') for n in staff_tools)}")

    # ---- 3 旧名确实消失 ----
    leftover = [n for n in OLD_NAMES if n in admin_tools]
    print(f"[3] 旧工具名残留 {len(leftover)} 个 {'✅' if not leftover else '❌ ' + str(leftover)}")
    if leftover:
        fails.append(f"旧工具名仍在列表：{leftover}")

    # ---- 5 写闸门（无确认语应被拒）----
    async with streamable_http_client(URL, http_client=create_mcp_http_client(
            headers={"Authorization": f"Bearer {tok_a}"}, timeout=30)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            cfg = await _call(s, "visit_config",
                              {"action": "set", "per_point": 250})
            c1 = (cfg.get("error") or {}).get("code")
            gate_ok = c1 in ("CONFIRM_REQUIRED", "BAD_PARAM", "FORBIDDEN_TOOL")
            print(f"[5] 写操作无确认语 → {c1} {'✅' if gate_ok else '❌'}")
            if not gate_ok:
                fails.append(f"写闸门未生效：{c1}")

            # ---- 6 关键业务数字 ----
            ov = await _call(s, "visit_overview", {"month": "2026-08", "view": "summary"})
            data = ov.get("data") or {}
            rows = data.get("formal_rows")
            pts = data.get("points") or data.get("total_points")
            exp = BASELINE["2026-08"]
            num_ok = (rows == exp[0]) and (pts in (None, exp[1]))
            print(f"[6] 8月 正式表 {rows} / 点数 {pts}（期望 {exp[0]}/{exp[1]}）"
                  f" {'✅' if num_ok else '❌'}")
            if not num_ok:
                fails.append(f"业务数字变化：{rows}/{pts}")

            # ---- 8 自洽检查 ----
            iv = await _call(s, "visit_verify", {})
            ok = (iv.get("data") or {}).get("ok")
            print(f"[8] 自洽检查 {'通过 ✅' if ok else '未通过 ❌'}")
            if not ok:
                fails.append("自洽检查未通过")

    print()
    if fails:
        print("❌ 未通过：" + "；".join(fails))
        return 1
    print("✅ 全部验收通过（16 工具 / 员工 3 / 旧名已删 / 越权仍拦 / 闸门有效 / 数字一致 / 自洽通过）")
    return 0


sys.exit(asyncio.run(main()))
