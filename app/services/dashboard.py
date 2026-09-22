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
    """某员工逐月指标（含重复巡店数）。"""
    from app.models import MonthPerfRecord
    from app.services import perf as _p
    out = []
    for mo in _months(db):
        r = db.query(MonthPerfRecord).filter(
            MonthPerfRecord.month == mo,
            MonthPerfRecord.person_code == code).first()
        if r is None:
            continue
        dups = _p.month_dup_map(db, mo).get(code, 0)
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
        "\n请用中文输出，结论导向（每点先说结论、再用数据佐证）：\n"
        "1) 一句话结论（该员工本期表现评价：好/一般/需关注）；\n"
        "2) 对比结论（vs 上月与全公司：哪里变好、哪里变差，先说结论后数据）；\n"
        "3) 归因判断（结合数据判断主要原因，明确支持哪个：是任务量/市场原因（店数与点数的同向变化）、"
        "还是重复巡店太多（重复数与重复率偏高）、还是投放质量（2点率偏低）、或奖金口径等变化；"
        "说明'数据显示…'推断依据）；\n"
        "4) 建议（1-3 条可执行建议）。\n"
        "只依据上面数据做判断，不得臆测数据外原因。")
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


def analyze_all_staff(db, month) -> dict:
    """算完工资后批量生成：所有有月绩效记录的员工，样本不足的跳过。"""
    from app.models import MonthPerfRecord
    codes = [r.person_code for r in db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month).all()]
    done, skipped, failed = 0, 0, 0
    for code in codes:
        if not staff_sample_ok(db, code, month):
            skipped += 1
            continue
        if ensure_staff_analysis(db, code, month):
            done += 1
        else:
            failed += 1
    return {"done": done, "skipped": skipped, "failed": failed}


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
