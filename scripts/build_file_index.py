# -*- coding: utf-8 -*-
"""生成**全仓文件索引**：`docs/文件索引.tsv`（机器查）+ `docs/文件索引.md`（人看）。

为什么要有它（用户 2026-10-06："token 用量太大…把现有文件都索引一下，后面改到哪个功能
再往上下文里放哪个功能"）：
- 让 agent **按关键词/功能域找文件**，而不是把整个仓库读进上下文；
- 索引里只放"路径 + 功能域 + 一句话 + 关键入口"，很小（几十 KB → 单文件一行）。

详细描述尽量**自动从源码里抽**（服务层/脚本都有一句话 docstring；路由抽路由路径；
迁移抽 revision；模板抽页面标题），这样重跑一次就能刷新，不用手写维护。

用法：
    ./.venv/bin/python scripts/build_file_index.py            # 重新生成
    ./.venv/bin/python scripts/idx.py 车站                    # 查（推荐日常用这个）
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 目录/文件名 → 功能域（先匹配先生效）
DOMAINS = [
    (r"^app/services/bd_", "作业域·服务"),
    (r"^app/routers/bd_r\.py$", "作业域·路由"),
    (r"^app/templates/(bd_|_ai_suggest|_pager)", "作业域·模板"),
    (r"^app/services/bd_cjk", "作业域·中日字形"),
    (r"^app/services/date_plan|^app/services/plan_leave", "日期计划"),
    (r"^app/services/daily_report|^app/services/report_", "员工填报"),
    (r"^app/services/(perf|formal|flow|payroll|period|recon)", "结算域"),
    (r"^app/services/(store|staff_|sysconfig)", "主档/员工"),
    (r"^app/services/(ai_chat|oauth)", "AI/鉴权基座"),
    (r"^app/routers/", "路由"),
    (r"^app/templates/my_", "员工端·模板"),
    (r"^app/templates/", "模板"),
    (r"^app/services/", "服务层"),
    (r"^app/static/", "前端资源"),
    (r"^app/(models|db|main|config|auth|i18n|forms|templating)\.py$", "基座"),
    (r"^app/", "应用其它"),
    (r"^migrations/", "数据库迁移"),
    (r"^tests_web/", "测试·Web"),
    (r"^tests/", "测试·结算"),
    (r"^mcp_service/tests/", "测试·MCP"),
    (r"^mcp_service/", "MCP 服务"),
    (r"^scripts/", "脚本"),
    (r"^deploy/", "部署"),
    (r"^docs/superpowers|^docs/agent-protocol", "agent 协议"),
    (r"^docs/", "文档"),
    (r"^data/", "数据"),
    (r"^store_settle_live\.db", "本地数据库（二进制/备份）"),
    (r"^tests_web/", "测试·Web"),
    (r"^tests/", "测试·结算"),
    (r"^mcp_service/", "MCP 服务"),
    (r"^scripts/", "脚本"),
    (r"^deploy/", "部署"),
    (r"^docs/", "文档"),
    (r"^(README|AGENTS)\.md$", "入口文档"),
    (r"^\.", "仓库配置"),
    (r"^(requirements|pytest|alembic|package|pnpm|docker|Makefile|\.env)", "仓库配置"),
    (r"\.(txt|toml|cfg|ini|json|yml|yaml|sh)$", "仓库配置"),
]


def domain_of(path: str) -> str:
    for pat, name in DOMAINS:
        if re.search(pat, path):
            return name
    return "其它"


def _first_docline(text: str, maxlen: int = 90) -> str:
    """取模块/脚本的**第一句 docstring**（作者都写得挺准，直接拿来当一句话）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ""
    doc = ast.get_docstring(tree) or ""
    if not doc:
        return ""
    for line in doc.split("\n"):
        line = line.strip()
        if line and not line.startswith(("-", ">", "#", "⚠️")):
            return line[:maxlen]
    return ""


def _routes(text: str, limit: int = 6) -> str:
    """抽路由路径（路由文件最有用的一列）。"""
    paths = re.findall(r'@router\.(?:get|post)\("([^"]+)"', text)
    uniq = list(dict.fromkeys(paths))
    if len(uniq) > limit:
        return "、".join(uniq[:limit]) + " 等 %d 条" % len(uniq)
    return "、".join(uniq)


def _migration(text: str) -> str:
    rev = re.search(r"^revision[^=]*=\s*['\"]([^'\"]+)", text, re.M)
    down = re.search(r"^down_revision[^=]*=\s*['\"]([^'\"]+)", text, re.M)
    desc = _first_docline(text)
    bits = []
    if rev:
        bits.append("rev " + rev.group(1))
    if down:
        bits.append("↓" + (down.group(1) or "base"))
    return (" ｜ ".join(bits) + (" ｜ " + desc if desc else ""))[:110]


def _template(text: str, path: str) -> str:
    """模板：抽页面标题/主要文案（够用来判断这是哪个页面）。"""
    m = re.search(r"<h1>([^<]{1,40})</h1>", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"page-title[\s\S]{0,200}?t\('([^']{1,30})'\)", text)
    if m:
        return m.group(1)
    m = re.search(r"\{\{\s*t\('([^']{1,30})'\)\s*\}\}", text)
    return m.group(1) if m else ""


def _script(text: str) -> str:
    return _first_docline(text, 100)


#: 关键文件**手写一句话**（自动抽不准的，就人写；新页面/新服务记得补一行）
MANUAL = {
    "app/services/bd_tasks.py": "作业域核心：车站池/任务板/状态机/派队/分派/进展/驳回/导出/线路下拉口径",
    "app/services/bd_teams.py": "团队：建队/圈人（一人一队唯一索引）/队长同步/成员状态",
    "app/services/bd_lines.py": "线路主档 + line_label()（运营商简称+线名）/线路下拉",
    "app/services/bd_places.py": "物理车站层：rebuild_places（同 group_code+坐标合并）",
    "app/services/bd_import_rail.py": "N02 一都三県地铁数据导入（幂等，认领 515 历史站）",
    "app/services/bd_assign_ai.py": "AI 派工建议（数字程序算、模型只分组写理由、只建议不写库）",
    "app/services/bd_cjk.py": "中日字形归一（to_jp/to_zh/线路中文名/搜索变体）",
    "app/services/bd_msg.py": "站内消息（一条消息+N 收件人行；收件人隔离唯一入口）",
    "app/services/bd_leave.py": "假期模式 + 派工可用性提醒（只读跨域，绝不写）",
    "app/services/bd_perm.py": "角色能力表（判权与按钮共用一份）",
    "app/services/bd_log.py": "通用追加日志（任务/团队/成员/车站）",
    "app/routers/bd_r.py": "作业域全部路由：/teams /stations /tasks /my/tasks /messages /logs",
    "app/templates/bd_tasks.html": "任务页（队伍汇总→tab→筛选→列表；未分配=车站池可批量派队）",
    "app/templates/bd_stations.html": "车站页（资产维护；筛选只有 关键词+线路）",
    "app/templates/bd_teams.html": "团队列表页",
    "app/templates/bd_team_detail.html": "团队详情：圈选队员（一人一队）",
    "app/templates/my_tasks.html": "员工/队长任务页（队长多 未分配/进行中/待确认/已完成）",
    "app/templates/base.html": "全站基座（顶栏/底栏/侧栏/未读角标/语言切换）",
    "app/templates/_ai_suggest.html": "AI 派工建议的 htmx 片段",
    "app/services/ai_chat.py": "公共 AI 调用（**默认直连**，AI_PROXY 可配代理）",
    "app/services/date_plan.py": "日期计划：半月窗口/格状态/写透/矩阵",
    "app/services/daily_report.py": "员工每日填报（一天一条，联动计划表）",
    "app/services/report_compare.py": "自报 vs 系统对比（准确率/分数占比唯一算法来源）",
    "app/services/report_ai.py": "对比报告 AI 评语（数据指纹复用）",
    "app/services/perf.py": "绩效：点数/工资/看板统计",
    "app/services/flow.py": "文件流水线：入表→判定→正式表→自动后处理",
    "mcp_service/scenario_ops.py": "MCP 场景化工具注册唯一入口（16 个）",
    "docs/文件索引.tsv": "全仓文件索引（本文件生成）",
    "AGENTS.md": "**入口手册（保持短）**：功能→读什么对照表",
}


def _clean_jinja(s: str) -> str:
    s = re.sub(r"\{\{\s*t\('([^']*)'\)\s*\}\}", r"\1", s)
    s = re.sub(r"\{\{[^}]*\}\}", "", s)
    return re.sub(r"\s+", " ", s).strip()


def describe(path: str, text: str) -> str:
    if path in MANUAL:
        return MANUAL[path]
    d = domain_of(path)
    if d == "数据库迁移":
        return _migration(text)
    if path.startswith("app/routers/"):
        r = _routes(text)
        doc = _first_docline(text, 50)
        return (r + (" ｜ " + doc if doc else ""))[:150]
    if path.startswith("docs/"):
        for line in text.split("\n")[:6]:
            if line.startswith("# "):
                return line[2:].strip()[:110]
        return ""
    if path.startswith("app/templates/"):
        return _clean_jinja(_template(text, path))
    if path.endswith(".py"):
        return _first_docline(text, 110)
    return ""


def main() -> int:
    # ⚠️ `git ls-files` 默认会给非 ASCII 路径加引号转义（中文路径 → 归类全失效）；
    #    `-z` 用 NUL 分隔、不转义
    files = [f for f in subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
        text=True).stdout.split("\0") if f]
    rows = []
    for rel in files:
        full = os.path.join(ROOT, rel)
        try:
            with open(full, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            text = ""
        rows.append((rel, domain_of(rel), describe(rel, text), len(text)))
    rows.sort(key=lambda r: (r[1], r[0]))
    tsv = os.path.join(ROOT, "docs", "文件索引.tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("path\t功能域\t一句话/关键入口\t字节\n")
        for rel, dom, desc, n in rows:
            f.write("%s\t%s\t%s\t%d\n" % (rel, dom, desc.replace("\t", " "), n))

    # 人看的 md：按功能域分组
    md = ["# 文件索引（自动生成 · 别手改）", "",
          "> 生成：`./.venv/bin/python scripts/build_file_index.py`；查询：`./.venv/bin/python scripts/idx.py 关键词`",
          "> 用途：**只把要改的功能相关的文件读进上下文**（用户 2026-10-06 的省 token 要求）。", ""]
    cur = None
    for rel, dom, desc, n in rows:
        if dom != cur:
            md.append("\n## %s\n" % dom)
            md.append("| 文件 | 一句话 / 关键入口 |")
            md.append("|---|---|")
            cur = dom
        md.append("| `%s` | %s |" % (rel, desc or "—"))
    open(os.path.join(ROOT, "docs", "文件索引.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")

    print("索引 %d 个文件 → docs/文件索引.tsv / .md（%.1f KB）"
          % (len(rows), os.path.getsize(tsv) / 1024))
    from collections import Counter
    for k, v in Counter(r[1] for r in rows).most_common():
        print("   %-14s %d" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
