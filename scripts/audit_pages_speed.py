# -*- coding: utf-8 -*-
"""页面测速（只读）：把每个 GET 页面真跑一遍，按耗时排序找慢页。

**为什么在容器里跑**：直连 `http://127.0.0.1:8000`（uvicorn），排掉公网/nginx
gzip 的干扰 —— 量的是**应用自己的耗时**，和用户体感同源（页面慢是服务端慢）。

用法：
    容器内 `docker exec -i deploy-web-1 python - < scripts/audit_pages_speed.py`
    本地   `./.venv/bin/python scripts/audit_pages_speed.py --base https://store.visitworld.me`
可选环境变量：`USER_NAME` / `USER_PW`（默认 admin/demo123）、`RUNS`（默认 2）、
`TIMEOUT`（默认 60 秒）、`ONLY`（子串过滤，只跑匹配的页面）。

⚠️ 每页跑 `RUNS` 次：**第 1 次和第 2 次都报** —— 差值就是"缓存/预热"的效果
（如公司分析页命中缓存时第 1 次就该快；AI 生成页第 1 次慢、第 2 次快）。
"""
import http.cookiejar as cookiejar
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
for i, a in enumerate(sys.argv):
    if a == "--base" and i + 1 < len(sys.argv):
        BASE = sys.argv[i + 1].rstrip("/")
USER_NAME = os.environ.get("USER_NAME", "admin")
USER_PW = os.environ.get("USER_PW", "demo123")
RUNS = int(os.environ.get("RUNS", "2"))
TIMEOUT = float(os.environ.get("TIMEOUT", "60"))
ONLY = os.environ.get("ONLY", "")

#: 页面清单：路径 → 说明。带 {month} 的用最近有数据的月份。
MONTH = "2026-10"
PAGES = [
    ("/", "首页"),
    ("/dashboard", "模型分析·总体"),
    ("/dashboard/staff", "模型分析·员工"),
    ("/dashboard/analysis", "公司总体分析（AI）"),
    ("/perf?month=" + MONTH, "绩效页"),
    ("/v3/perf?month=" + MONTH, "绩效页(v3)"),
    ("/perf/export?month=" + MONTH, "绩效导出"),
    ("/payroll-settle?month=" + MONTH, "发薪结算"),
    ("/payroll-settle/export?month=" + MONTH, "发薪表导出"),
    ("/recon?month=" + MONTH, "对账页"),
    ("/v3/recon?month=" + MONTH, "对账页(v3)"),
    ("/recon/export?month=" + MONTH, "对账导出"),
    ("/recon/report?month=" + MONTH, "对账报告"),
    ("/recon/result?month=" + MONTH, "对账结果"),
    ("/config", "配置页"),
    ("/stores", "店铺"),
    ("/stores/entities", "店铺主档"),
    ("/files", "文件列表"),
    ("/files/1/report", "文件报告"),
    ("/files/1/layout", "文件版式"),
    ("/employees", "员工列表"),
    ("/staff-admin", "人事管理"),
    ("/staff-admin/accounts-export", "账号导出"),
    ("/staff-plans", "出勤计划"),
    ("/staff-plans/export", "计划导出"),
    ("/staff-reports", "员工日报"),
    ("/staff-reports/compare", "日报对比"),
    ("/staff-reports/export", "日报导出"),
    ("/my/perf", "我的绩效"),
    ("/my/tasks", "我的任务"),
    ("/my/plan", "我的计划"),
    ("/my/report", "我的日报"),
    ("/my/password", "改密码"),
    ("/teams", "队伍"),
    ("/teams/1", "队伍详情"),
    ("/stations", "车站资产"),
    ("/stations/export", "车站导出"),
    ("/tasks", "任务(未分配)"),
    ("/tasks?tab=assigned", "任务(已分配)"),
    ("/tasks?tab=done", "任务(已完成)"),
    ("/tasks?tab=all", "任务(全部)"),
    ("/tasks?tab=assigned&team=5", "任务(按队)"),
    ("/tasks/export", "任务导出"),
    ("/tasks/import", "任务导入"),
    ("/tasks/reviews", "进展复核"),
    ("/tasks/new", "建任务"),
    ("/tasks/1", "任务详情"),
    ("/logs", "操作日志"),
    ("/messages", "消息"),
    ("/product", "产品说明"),
    ("/product/raw", "产品说明(原始)"),
]

jar = cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
HDR = {"Accept-Encoding": "identity", "User-Agent": "dsh-pagespeed"}


def hit(path):
    t0 = time.perf_counter()
    try:
        with opener.open(urllib.request.Request(BASE + path, headers=HDR),
                         timeout=TIMEOUT) as r:
            body = r.read()
            return (time.perf_counter() - t0) * 1000.0, r.status, len(body), ""
    except urllib.error.HTTPError as e:
        return ((time.perf_counter() - t0) * 1000.0, e.code,
                len(e.read() or b""), "")
    except Exception as e:                                   # noqa: BLE001
        return (time.perf_counter() - t0) * 1000.0, 0, 0, type(e).__name__


# 登录
login = urllib.parse.urlencode({"username": USER_NAME, "password": USER_PW,
                                "next": "/"}).encode()
with opener.open(urllib.request.Request(BASE + "/login", data=login, headers=HDR),
                 timeout=TIMEOUT) as r:
    r.read()
print("登录 %s → cookie %s   （BASE=%s，每页 %d 次）"
      % (USER_NAME, [c.name for c in jar], BASE, RUNS))

rows = []
for path, label in PAGES:
    if ONLY and ONLY not in path:
        continue
    times, status, size, err = [], 0, 0, ""
    for _ in range(RUNS):
        ms, status, size, err = hit(path)
        times.append(ms)
        if err:
            break
    rows.append((max(times), times, status, size, err, path, label))

rows.sort(reverse=True)
print("\n%-9s %-9s %-5s %-8s %s" % ("最慢(ms)", "首次(ms)", "HTTP", "大小", "页面"))
print("-" * 78)
for worst, times, status, size, err, path, label in rows:
    flag = ""
    if err:
        flag = "  ← " + err
    elif worst >= 3000:
        flag = "  ← 慢"
    elif worst >= 1000:
        flag = "  ← 偏慢"
    print("%-9.0f %-9.0f %-5s %-8s %s%s%s"
          % (worst, times[0], status or "-", ("%.0fKB" % (size / 1024.0)),
             label, ("  " + path) if path not in ("/",) else " /", flag))
slow = [r for r in rows if r[0] >= 3000]
print("\n≥3 秒的页面：%d 个 %s" % (len(slow), [r[5] for r in slow] or "无 ✓"))
