# -*- coding: utf-8 -*-
"""数据看板：逐月/员工维度指标、图表(SVG)与模型分析事实。

设计：不依赖前端图表库——折线图由服务端生成内联 SVG（稳定、离线可用）。
"""
from collections import defaultdict


def _months(db):
    from app.models import MonthPerfRecord
    return sorted({r.month for r in db.query(MonthPerfRecord).all()})


def monthly_series(db):
    """逐月指标：人数/有效店/1点2点/点数/工资/人均/2点率 + 环比。"""
    from app.models import MonthPerfRecord
    out = []
    for mo in _months(db):
        rows = db.query(MonthPerfRecord).filter(
            MonthPerfRecord.month == mo).all()
        if not rows:
            continue
        p1 = sum(r.p1 or 0 for r in rows)
        p2 = sum(r.p2 or 0 for r in rows)
        recs = sum(r.records or 0 for r in rows)
        pts = sum(r.points or 0 for r in rows)
        amt = sum(r.salary or 0 for r in rows)
        n = len(rows)
        out.append({
            "month": mo, "employees": n, "records": recs, "p1": p1, "p2": p2,
            "points": pts, "amount": amt,
            "p2rate": (p2 / recs) if recs else 0.0,
            "per_emp_points": (pts / n) if n else 0,
            "per_emp_amount": (amt / n) if n else 0,
            "per_emp_records": (recs / n) if n else 0,
            "per_store_points": (pts / recs) if recs else 0,
        })
    for i in range(1, len(out)):
        cur, prev = out[i], out[i - 1]
        for k in ("points", "amount", "records", "employees",
                  "per_emp_points", "per_emp_amount", "per_emp_records",
                  "p2rate"):
            b = prev[k] or 0
            cur[f"d_{k}"] = ((cur[k] - b) / b) if b else None
    return out


def staff_series(db, code):
    """某员工逐月指标（含重复巡店数；dups 读物化表，避免全表扫描）。"""
    from app.models import MonthPerfRecord
    out = []
    for mo in _months(db):
        r = db.query(MonthPerfRecord).filter(
            MonthPerfRecord.month == mo,
            MonthPerfRecord.person_code == code).first()
        if r is None:
            continue
        dups = month_payloads(db, mo).get("dup_map", {}).get(code, 0)
        out.append({"month": mo, "records": r.records or 0,
                    "p1": r.p1 or 0, "p2": r.p2 or 0,
                    "points": r.points or 0, "amount": r.salary or 0,
                    "dups": dups})
    return out


def staff_options(db):
    from app.models import Person
    return [(p.code, p.display_name or p.code)
            for p in db.query(Person).order_by(Person.code).all()]


def staff_changes(db, month):
    """该月人员进出：新人（上月无）/ 未出现（上月有）。"""
    from app.models import MonthPerfRecord
    ms = _months(db)
    if month not in ms:
        return [], []
    i = ms.index(month)
    cur = {r.person_code: (r.points or 0, r.salary or 0) for r in
           db.query(MonthPerfRecord).filter(
               MonthPerfRecord.month == month).all()}
    if i == 0:
        return sorted(cur.items(), key=lambda x: -x[1][0]), []
    prev = {r.person_code: (r.points or 0, r.salary or 0) for r in
            db.query(MonthPerfRecord).filter(
                MonthPerfRecord.month == ms[i - 1]).all()}
    names = _person_names(db)
    new = [(c, cur[c]) for c in cur if c not in prev]
    gone = [(c, prev[c]) for c in prev if c not in cur]
    new.sort(key=lambda x: -x[1][0])
    gone.sort(key=lambda x: -x[1][0])
    return ([(names.get(c, c), v) for c, v in new],
            [(names.get(c, c), v) for c, v in gone])


def _person_names(db):
    from app.models import Person
    return {p.code: (p.display_name or p.code) for p in db.query(Person).all()}


def top_staff(db, month, n=8):
    """该月点数/工资排行（Top n）。"""
    from app.models import MonthPerfRecord
    rows = db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month).all()
    names = _person_names(db)
    out = [{"code": r.person_code, "name": names.get(r.person_code,
                                                     r.person_code),
            "points": r.points or 0, "amount": r.salary or 0,
            "records": r.records or 0, "p1": r.p1 or 0, "p2": r.p2 or 0}
           for r in rows]
    return sorted(out, key=lambda x: -x["points"])[:n]


def quality_stats(db, month):
    """该月数据质量：raw 判定分布（过滤原因）+ 每日覆盖。"""
    from app.models import RawRecord
    from collections import Counter
    cnt = Counter()
    days = set()
    for r in db.query(RawRecord).all():
        if (r.modified_raw or "")[:7] != month:
            continue
        cnt[r.clean_status or "?"] += 1
        if r.clean_status == "valid":
            days.add((r.modified_raw or "")[:10])
    return {"by_status": dict(cnt), "valid_days": len(days),
            "total": sum(cnt.values())}


def svg_line(points, labels, width=640, height=170, color="#2f6fed",
             fmt="{:,.0f}"):
    """服务端生成内联 SVG 折线图（含网格/坐标/月份标签）。"""
    if not points:
        return "<p class='hint'>暂无数据</p>"
    mx = max(points) or 1
    n = len(points)
    pl, pr, pt, pb = 64, 12, 12, 26
    iw, ih = width - pl - pr, height - pt - pb

    def X(i):
        return pl + (iw * (i / (n - 1) if n > 1 else 0.5))

    def Y(v):
        return pt + ih - ih * (v / mx if mx else 0)

    poly = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(points))
    dots = "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3" '
                   f'fill="{color}"/>' for i, v in enumerate(points))
    grid = "".join(
        f'<line x1="{pl}" y1="{Y(mx * f):.1f}" x2="{width - pr}" '
        f'y2="{Y(mx * f):.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        f'<text x="{pl - 8}" y="{Y(mx * f) + 4:.1f}" font-size="10" '
        f'text-anchor="end" fill="#6b7280">{fmt.format(mx * f)}</text>'
        for f in (0, 0.5, 1))
    xlabels = "".join(
        f'<text x="{X(i):.1f}" y="{height - 8}" font-size="10" '
        f'text-anchor="middle" fill="#6b7280">{lbl}</text>'
        for i, lbl in enumerate(labels))
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" '
            f'style="max-width:100%;height:auto">'
            f'{grid}'
            f'<polyline points="{poly}" fill="none" stroke="{color}" '
            f'stroke-width="2"/>{dots}{xlabels}</svg>')


def fact_text(db, staff_code=None):
    """给模型的事实文本（尽量丰富：趋势/结构/人均/人员流动/排行/质量）。"""
    ms = monthly_series(db)
    lines = ["各月经营数据（日企巡店结算，点数=巡店业绩点，工资=点数×单价+奖金）："]
    for m in ms:
        lines.append(
            f"{m['month']}: 员工{m['employees']}人 有效店{m['records']} "
            f"1点店{m['p1']} 2点店{m['p2']} 2点率{m['p2rate']:.1%} "
            f"总点数{m['points']} 工资{m['amount']}円 "
            f"人均点数{m['per_emp_points']:.1f} 人均工资{m['per_emp_amount']:.0f} "
            f"人均店数{m['per_emp_records']:.1f} 店均点数{m['per_store_points']:.2f}")
    if len(ms) >= 2:
        cur, prev = ms[-1], ms[-2]
        lines.append(
            f"环比({prev['month']}→{cur['month']})：点数{cur.get('d_points') or 0:+.1%} "
            f"工资{cur.get('d_amount') or 0:+.1%} 有效店{cur.get('d_records') or 0:+.1%} "
            f"2点率{(cur['p2rate'] - prev['p2rate']) * 100:+.1f}个百分点 "
            f"人均点数{cur.get('d_per_emp_points') or 0:+.1%}")
        new, gone = staff_changes(db, cur["month"])
        if new:
            lines.append("本期新增人员：" + "、".join(
                f"{n}({v[0]}点)" for n, v in new[:10]))
        if gone:
            lines.append("本期未出现人员：" + "、".join(
                f"{n}(上月{v[0]}点)" for n, v in gone[:10]))
        tops = top_staff(db, cur["month"], 5)
        lines.append("点数前列：" + "、".join(
            f"{t['name']}{t['points']}点/{t['records']}店" for t in tops))
    if ms:
        q = quality_stats(db, ms[-1]["month"])
        st = q["by_status"]
        lines.append(
            f"{ms[-1]['month']} 数据质量：原始{st.get('valid', 0) + sum(v for k, v in st.items() if k != 'valid')}条，"
            f"有效{st.get('valid', 0)} 跨文件重复{st.get('cross_file_dup', 0)} "
            f"疑似迟交{st.get('master_late', 0)} 从档{st.get('from_sub', 0)} "
            f"空白{st.get('visible_blank', 0)}；有数据天数{q['valid_days']}")
    if staff_code:
        ss = staff_series(db, staff_code)
        nm = dict(staff_options(db)).get(staff_code, staff_code)
        lines.append(f"员工 {nm}({staff_code}) 逐月：" + "；".join(
            f"{s['month']} {s['points']}点/{s['records']}店/(1点{s['p1']},2点{s['p2']})/"
            f"重复巡店{s.get('dups', 0)}/{s['amount']}円" for s in ss))
    return "\n".join(lines)


# ---------------- 员工月度分析（存库） ----------------

def staff_sample_ok(db, code, month) -> bool:
    """样本门槛：至少 2 个月有数据，且各月有效店合计 >= 5（8月无/9月仅一两条 → 不分析）。"""
    ss = staff_series(db, code)
    if len(ss) < 2:
        return False
    return sum(x["records"] for x in ss) >= 5


def ensure_staff_analysis(db, code, month, force=False) -> str:
    """为某员工生成/更新月度分析（写入 staff_analyses 表）；样本不足返回空串。"""
    from app.models import StaffAnalysis
    from app.services.ai_chat import configured, chat
    if not configured():
        return ""
    if not staff_sample_ok(db, code, month):
        return ""
    row = db.query(StaffAnalysis).filter(
        StaffAnalysis.person_code == code,
        StaffAnalysis.month == month).first()
    if row and not force:
        return row.content
    ss = staff_series(db, code)
    ms = monthly_series(db)
    avg = (ms[-1]["per_emp_points"] if ms else 0)
    s_last = ss[-1]
    prev = ss[-2] if len(ss) >= 2 else None
    lines = [
        f"员工 {dict(staff_options(db)).get(code, code)}({code}) 逐月：",
        "；".join(
            f"{x['month']} {x['points']}点/{x['records']}店"
            f"(1点{x['p1']},2点{x['p2']})重复{x.get('dups', 0)}/{x['amount']}円"
            for x in ss),
        f"全公司最近月人均点数：{avg:.1f}",
        f"本人最近月({s_last['month']})点数{s_last['points']}，"
        f"店数{s_last['records']}，重复巡店{s_last.get('dups', 0)}，"
        f"重复率{(s_last.get('dups', 0) / s_last['records'] if s_last['records'] else 0):.0%}，"
        f"2点率{(s_last['p2'] / (s_last['p1'] + s_last['p2']) if (s_last['p1'] + s_last['p2']) else 0):.0%}",
    ]
    if prev:
        lines.append(
            f"较上一月({prev['month']})：点数{(s_last['points'] - prev['points']) / (prev['points'] or 1):+.1%}，"
            f"店数{(s_last['records'] - prev['records']) / (prev['records'] or 1):+.1%}")
    prompt = (
        "你是巡店结算系统的员工绩效分析师。下面是该员工与全公司的数据：\n\n"
        + "\n".join(lines) +
        "\n请用中文输出，精炼要点式（不要段落废话，每条一句话，关键数字用**加粗**）：\n"
        "1) **一句话结论**：该员工本期表现（好/一般/需关注）；\n"
        "2) **对比结论**（最多2条）：vs 上月与全公司，哪里好/差；\n"
        "3) **归因判断**：结合数据明确判断主因——是市场/任务量、还是**重复巡店太多**（重复率）、"
        "还是投放质量（2点率）等，一句定论；\n"
        "4) **建议**（最多2条）。\n"
        "只依据数据判断，不得臆测。")
    try:
        content = chat(prompt, max_tokens=1800, timeout=240)
    except Exception as e:  # noqa: BLE001
        return ""
    if row is None:
        db.add(StaffAnalysis(person_code=code, month=month,
                             content=content))
    else:
        row.content = content
    db.commit()
    return content


def staff_analysis(db, code, month="") -> str:
    """读库返回该员工最新月度分析（无则空串）。"""
    from app.models import StaffAnalysis
    q = db.query(StaffAnalysis).filter(StaffAnalysis.person_code == code)
    if month:
        q = q.filter(StaffAnalysis.month == month)
    row = q.order_by(StaffAnalysis.month.desc()).first()
    return row.content if row else ""


def headcount_changes(db):
    """逐月人员变化：每月新增/流失人数。"""
    from app.models import MonthPerfRecord
    ms = _months(db)
    out = []
    prev = set()
    for mo in ms:
        cur = {r.person_code for r in db.query(MonthPerfRecord).filter(
            MonthPerfRecord.month == mo).all()}
        out.append({"month": mo, "new": len(cur - prev),
                    "gone": len(prev - cur), "employees": len(cur)})
        prev = cur
    return out


# ---------------- 分析文本 → 结构化 HTML 渲染 ----------------

def render_analysis_html(text: str) -> str:
    """把模型输出的分析文本渲染成清爽的结构化 HTML：
    `##`/`#` 小节标题 → 彩色小节条；`-`/`1)` 列表；`**加粗**` 高亮关键数字/结论。"""
    import html as _h
    import re as _re

    def _fmt(s: str) -> str:
        s = _h.escape(s or "")
        s = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = _re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return s

    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _re.match(r"^#{1,6}\s*(.+)$", line)
        if m:
            out.append(f"<h5 class='a-sec'>{_fmt(m.group(1))}</h5>")
            continue
        m = _re.match(r"^([0-9]+)[)\.、]\s*(.+)$", line)
        if m:
            body = _fmt(m.group(2))
            if out and out[-1].startswith("<ol>"):
                out[-1] = out[-1][:-5] + f"<li>{body}</li></ol>"
            else:
                out.append(f"<ol><li>{body}</li></ol>")
            continue
        if line.startswith(("- ", "• ")):
            body = _fmt(line[2:])
            if out and out[-1].startswith("<ul>"):
                out[-1] = out[-1][:-5] + f"<li>{body}</li></ul>"
            else:
                out.append(f"<ul><li>{body}</li></ul>")
            continue
        out.append(f"<p>{_fmt(line)}</p>")
    return '<div class="analysis">' + "".join(out) + "</div>"


# ---------------- 看板统计物化（dash_metrics：月份-统计项-数值） ----------------

_METRICS = ("total_points", "total_amount", "records", "employees", "p1",
            "p2", "p2rate", "per_emp_points", "per_emp_amount",
            "per_emp_records", "per_store_points", "dup_total",
            "new_staff", "gone_staff", "q_total", "q_valid",
            "q_cross_file_dup", "q_master_late", "q_from_sub",
            "q_visible_blank", "q_valid_days")


def sync_dash_metrics(db, month: str) -> int:
    """计算该月全部看板指标写入 dash_metrics（先删该月再写；幂等）。
    数值型指标一行一个 value；列表型（新增/未出现/排行/质量/每人重复）
    合并为单条 JSON(payload)——数据量小，一条即可。"""
    import json
    from app.models import DashMetric, MonthPerfRecord
    from app.services import perf as _p
    ms = monthly_series(db)
    row = next((m for m in ms if m["month"] == month), None)
    if row is None:
        return 0
    q = quality_stats(db, month)
    new_, gone_ = staff_changes(db, month)
    dup_map = _p.month_dup_map(db, month)
    tops = top_staff(db, month, 8)
    kv = {
        "total_points": row["points"], "total_amount": row["amount"],
        "records": row["records"], "employees": row["employees"],
        "p1": row["p1"], "p2": row["p2"], "p2rate": row["p2rate"],
        "per_emp_points": row["per_emp_points"],
        "per_emp_amount": row["per_emp_amount"],
        "per_emp_records": row["per_emp_records"],
        "per_store_points": row["per_store_points"],
        "dup_total": sum(dup_map.values()),
        "new_staff": len(new_), "gone_staff": len(gone_),
        "q_total": q["total"], "q_valid": q["by_status"].get("valid", 0),
        "q_cross_file_dup": q["by_status"].get("cross_file_dup", 0),
        "q_master_late": q["by_status"].get("master_late", 0),
        "q_from_sub": q["by_status"].get("from_sub", 0),
        "q_visible_blank": q["by_status"].get("visible_blank", 0),
        "q_valid_days": q["valid_days"],
    }
    payloads = {
        "new_staff": [{"name": n, "points": v[0], "amount": v[1]}
                      for n, v in new_],
        "gone_staff": [{"name": n, "points": v[0], "amount": v[1]}
                       for n, v in gone_],
        "top_staff": tops,
        "quality": {"total": q["total"], "valid": q["by_status"].get("valid", 0),
                    "cross_file_dup": q["by_status"].get("cross_file_dup", 0),
                    "master_late": q["by_status"].get("master_late", 0),
                    "from_sub": q["by_status"].get("from_sub", 0),
                    "visible_blank": q["by_status"].get("visible_blank", 0),
                    "valid_days": q["valid_days"]},
        "dup_map": dup_map,
    }
    db.query(DashMetric).filter(DashMetric.month == month).delete()
    for k, v in kv.items():
        db.add(DashMetric(month=month, metric=k, value=float(v or 0)))
    for k, v in payloads.items():
        db.add(DashMetric(month=month, metric=k, value=0,
                          payload=json.dumps(v, ensure_ascii=False)))
    db.commit()
    return len(kv) + len(payloads)


def monthly_series_from_db(db):
    """从 dash_metrics 读逐月指标（快，无实时聚合）。缺月→None 由调用方回填。"""
    from app.models import DashMetric
    rows = db.query(DashMetric).filter(
        DashMetric.person.is_(None)).all()
    g = {}
    for r in rows:
        g.setdefault(r.month, {})[r.metric] = r.value
    out = []
    for mo in sorted(g):
        v = g[mo]
        if "total_points" not in v:
            continue
        recs = v.get("records") or 0
        n = v.get("employees") or 0
        p1 = v.get("p1") or 0
        p2 = v.get("p2") or 0
        out.append({
            "month": mo, "employees": int(n), "records": int(recs),
            "p1": int(p1), "p2": int(p2),
            "points": int(v.get("total_points") or 0),
            "amount": int(v.get("total_amount") or 0),
            "p2rate": (p2 / recs) if recs else 0.0,
            "per_emp_points": int(v.get("total_points") or 0) / n if n else 0,
            "per_emp_amount": int(v.get("total_amount") or 0) / n if n else 0,
            "per_emp_records": recs / n if n else 0,
            "per_store_points": int(v.get("total_points") or 0) / recs if recs else 0,
        })
    for i in range(1, len(out)):
        cur, prev = out[i], out[i - 1]
        for k in ("points", "amount", "records", "employees",
                  "per_emp_points", "per_emp_amount", "per_emp_records",
                  "p2rate"):
            b = prev[k] or 0
            cur[f"d_{k}"] = ((cur[k] - b) / b) if b else None
    return out


def month_has_metrics(db, month: str) -> bool:
    from app.models import DashMetric
    return db.query(DashMetric).filter(
        DashMetric.month == month,
        DashMetric.person.is_(None)).count() >= 5


def month_payloads(db, month: str) -> dict:
    """读该月列表型指标 JSON（{metric: 对象}）；无则空。"""
    import json
    from app.models import DashMetric
    out = {}
    for r in db.query(DashMetric).filter(
            DashMetric.month == month,
            DashMetric.payload.isnot(None)).all():
        try:
            out[r.metric] = json.loads(r.payload)
        except Exception:  # noqa: BLE001
            out[r.metric] = {}
    return out


def analyze_all_staff(db, month: str) -> dict:
    """为当月全部员工预生成分析并落盘（幂等：已生成的行跳过）。
    样本不足的员工自动跳过；在算完工资后自动执行（后台线程）。"""
    from app.models import MonthPerfRecord
    codes = [r.person_code for r in db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month).all()]
    done = skipped = failed = 0
    for code in codes:
        if not staff_sample_ok(db, code, month):
            skipped += 1
            continue
        if ensure_staff_analysis(db, code, month):
            done += 1
        else:
            failed += 1
    return {"done": done, "skipped": skipped, "failed": failed}
