# -*- coding: utf-8 -*-
"""认证端到端验收：用**真实员工 token** 经 MCP 实际试着越权。

覆盖（规格 §5 验收 1–7）：
  1) 员工调"我的"工具 → 只返回本人数据
  2) 员工调管理员工具（公司汇总/上传/导出/改配置）→ FORBIDDEN_TOOL
  3) 员工状态改 leave → token 立即失效；改回 active → 恢复
  4) 被拒绝的调用在审计里留痕
用法：mcp_service/.venv/bin/python scripts/e2e_auth_check.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import ClientSession
from mcp.client.streamable_http import (create_mcp_http_client,
                                        streamable_http_client)

from app.db import SessionLocal
from app.models import McpAuditLog, User
from mcp_service import tokens

URL = "http://127.0.0.1:8765/mcp"
USERNAME = "chenjiayi"


def issue_staff_token():
    db = SessionLocal()
    u = db.query(User).filter_by(username=USERNAME).first()
    raw, row = tokens.issue(db, u.id, "e2e-员工token", "read", 90)
    info = (u.id, u.person_code, row.id)
    db.close()
    return raw, info


def set_status(status):
    db = SessionLocal()
    u = db.query(User).filter_by(username=USERNAME).first()
    u.status = status
    if status in ("disabled", "resigned"):
        u.is_active = False
    else:
        u.is_active = True
    db.commit()
    db.close()


def audit_rows(tool):
    db = SessionLocal()
    rows = db.query(McpAuditLog).filter(McpAuditLog.tool == tool).all()
    out = [(r.ok, r.error_code, r.user_id) for r in rows]
    db.close()
    return out


async def call(s, tool, args=None):
    try:
        res = await s.call_tool(tool, args or {})
    except Exception as exc:      # noqa: BLE001  打印服务端真实错误，便于定位
        return {"ok": False, "error": {
            "code": "MCP_ERROR", "message": str(exc)[:200],
            "hint": str(getattr(exc, "data", ""))[:300]}}
    d = res.structured_content or {}
    if res.is_error:
        return {"ok": False, "error": {"code": "TOOL_ERROR",
                                       "message": str(res.content)[:120]}}
    return d


async def main():
    set_status("active")          # 复位（上次中断可能留下 leave）
    try:
        return await _run()
    finally:
        set_status("active")      # 无论如何都恢复，避免影响后续测试
        print("\n（已复位员工状态为 active）")


async def _run():
    raw, (uid, pcode, tokid) = issue_staff_token()
    print(f"=== 已为员工 {USERNAME}（人员 {pcode}）签发 token（id={tokid}）===")
    fails = []
    async with streamable_http_client(
            URL, http_client=create_mcp_http_client(
                headers={"Authorization": f"Bearer {raw}"})) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # 1) 员工自己的绩效
            d = await call(s, "visit_my_perf", {})
            ok = d.get("ok")
            print(f"\n[1] visit_my_perf → ok={ok}")
            if ok:
                rows = d["data"].get("rows") or []
                print(f"    返回 {len(rows)} 条；本人人员编号 {pcode}")
                others = [x for x in rows if x.get("person_code") not in (None, pcode)]
                print(f"    {'✅ 只返回本人' if not others else '❌ 含他人数据：' + str(others[:2])}")
                if others:
                    fails.append("my_perf 泄露他人数据")
            else:
                print(f"    ⚠️ {d.get('error')}")
                fails.append("my_perf 不可用")

            # 2) 越权：管理员工具
            print("\n[2] 员工调管理员工具（应全部 FORBIDDEN_TOOL）")
            for tool, args in (("visit_overview", {"month": "2026-08"}),
                               ("visit_person", {"person": "P001",
                                                 "month": "2026-08"}),
                               ("visit_files", {"view": "tasks"}),
                               ("visit_recon_export", {"month": "2026-08"}),
                               ("visit_upload", {"filename": "x.xlsx"}),
                               ("visit_config", {"action": "set",
                                                 "per_point": 260,
                                                 "confirm_text":
                                                     "确认修改配置"})):
                d = await call(s, tool, args)
                code = (d.get("error") or {}).get("code")
                good = d.get("ok") is False and code == "FORBIDDEN_TOOL"
                print(f"    {'✅' if good else '❌'} {tool} → {code or 'ALLOWED!'}")
                if not good:
                    fails.append(f"{tool} 未被拦截（{code}）")

            # 3) 员工休假 → token 失效
            print("\n[3] 员工改为「请假」后调用")
            set_status("leave")
            d = await call(s, "visit_my_perf", {})
            code = (d.get("error") or {}).get("code")
            # 两种都算拦截成功：中间件在 HTTP 层 401（MCP_ERROR）或工具层 UNAUTHORIZED
            good = code in ("UNAUTHORIZED", "MCP_ERROR")
            print(f"    {'✅' if good else '❌'} visit_my_perf → {code} "
                  f"{(d.get('error') or {}).get('hint', '')[:40]}")
            if not good:
                fails.append("leave 后 token 未失效")

            print("\n[4] 复工后恢复")
            set_status("active")
            d = await call(s, "visit_my_perf", {})
            good = d.get("ok") is True
            print(f"    {'✅' if good else '❌'} visit_my_perf → ok={d.get('ok')}")
            if not good:
                fails.append("复工后未恢复")

    # 5) 审计留痕
    print("\n[5] 审计留痕（被拒绝的调用也要有）")
    for tool in ("visit_overview", "visit_upload"):
        rows = audit_rows(tool)
        denied = [r for r in rows if r[1] == "FORBIDDEN_TOOL"]
        print(f"    {'✅' if denied else '❌'} {tool}: {len(rows)} 行，其中被拒 "
              f"{len(denied)} 行（user_id={denied[0][2] if denied else '-'}）")
        if not denied:
            fails.append(f"{tool} 无被拒审计")

    print()
    if fails:
        print("❌ 未通过：" + "；".join(fails))
        return 1
    print("✅ 认证端到端验收全部通过：员工只能看自己、越权被拦、状态联动、审计留痕")
    return 0


sys.exit(asyncio.run(main()))
