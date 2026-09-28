# -*- coding: utf-8 -*-
"""自报趋势图：数据序列 + 内联 SVG 几何（纯计算，不碰 DB 之外的业务）。

从 daily_report 拆出：这里只有"把逐日数字画成图"的数学，与填报/对比无关。
"""
from datetime import timedelta

from app.models import StaffDailyReport
from app.services.daily_report import jst_today


CHART_DAYS = 30          # 趋势窗口


CHART_MIN_FILLED = 3     # 少于这么多天就不给图（规格：太少了不给）


CHART_MIN_SPAN = 7       # 横轴最少铺开的天数（数据太少时避免一条陡线）


def chart_series(db, person_code: str, *, days: int = CHART_DAYS,
                 today=None, min_span: int = CHART_MIN_SPAN) -> dict:
    """最近 N 天（含今天）的自报序列：缺的天标 filled=False。

    **横轴自适应**：固定 30 天会把"刚开始填报的人"全挤到右边，所以窗口右端固定为今天、
    左端取「第一个有填报的日子」与「今天-(min_span-1)」中更早者，且不超过 N 天上限。
    这样稀疏数据也能铺满图宽；中间的缺口仍然断开显示。
    """
    today = today or jst_today()
    hard_start = today - timedelta(days=days - 1)
    rows = {r.report_date: r for r in db.query(StaffDailyReport).filter(
        StaffDailyReport.person_code == person_code,
        StaffDailyReport.report_date >= hard_start,
        StaffDailyReport.report_date <= today).all()}
    if rows:
        start = min(min(rows), today - timedelta(days=min_span - 1))
        start = max(start, hard_start)
    else:
        start = max(today - timedelta(days=min_span - 1), hard_start)
    pts, d = [], start
    while d <= today:
        r = rows.get(d)
        pts.append({"date": d, "filled": r is not None,
                    "p1": r.p1_cnt if r else 0, "p2": r.p2_cnt if r else 0,
                    "total": r.total_cnt if r else 0})
        d += timedelta(days=1)
    filled = sum(1 for x in pts if x["filled"])
    vals = [x[k] for x in pts for k in ("p1", "p2")]
    return {"days": pts, "filled": filled, "start": start, "end": today,
            "span_days": len(pts), "window_days": days,
            "p1": sum(x["p1"] for x in pts), "p2": sum(x["p2"] for x in pts),
            "total": sum(x["total"] for x in pts),
            "max": max(vals) if vals else 0,
            "min_filled": CHART_MIN_FILLED,
            "show": filled >= CHART_MIN_FILLED}


def _nice_ceiling(v: int) -> int:
    """把最大值抬到"好看的整数刻度上限"（例：7→8、9→10、30→30）。"""
    v = max(1, int(v or 0))
    for c in _Y_STEPS:
        if c >= v:
            return c
    return v


def _smooth(pts, tension: float = 0.32):
    """把折线点转成平滑曲线路径（Cardinal 样条 → 三次贝塞尔），控制点夹在绘图区内。"""
    if len(pts) < 2:
        return ""
    d = "M%.1f,%.1f" % pts[0]
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) * tension / 2,
              p1[1] + (p2[1] - p0[1]) * tension / 2)
        c2 = (p2[0] - (p3[0] - p1[0]) * tension / 2,
              p2[1] - (p3[1] - p1[1]) * tension / 2)
        d += " C%.1f,%.1f %.1f,%.1f %.1f,%.1f" % (c1[0], c1[1], c2[0], c2[1],
                                                  p2[0], p2[1])
    return d


def _area(d: str, run: list, base_y: float) -> str:
    """把曲线路径闭合成面积路径；**单点/空路径直接丢弃**（否则会产出没有 M 的非法 d）。"""
    if not d or not d.startswith("M") or len(run) < 2:
        return ""
    return d + " L%.1f,%.1f L%.1f,%.1f Z" % (run[-1][0], base_y, run[0][0], base_y)


def chart_geometry(series: dict, *, width: int = 360, height: int = 176,
                   pad_l: int = 30, pad_r: int = 12, pad_t: int = 14,
                   pad_b: int = 26) -> dict:
    """把序列转成 SVG 几何（供模板直接渲染）：

    - 纵轴：好看的上限 + 3 条刻度线（0 / 中 / 顶）与数值标签
    - 横轴：最多 4 个日期标签（MM-DD）
    - 两条曲线：**按连续段**平滑连线（缺数据处断开）+ 段内面积填充
    - 每个有数据的日子一个圆点（`<title>` 里带数值，鼠标悬停可见）
    - "今天"竖虚线
    """
    days = series["days"]
    n = len(days)
    if not n:
        return {"width": width, "height": height, "empty": True}
    top = _nice_ceiling(series["max"])
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    step_x = plot_w / max(n - 1, 1)

    def x_of(i):
        return pad_l + i * step_x

    def y_of(v):
        return pad_t + plot_h - (min(max(v, 0), top) / top) * plot_h

    geo = {"width": width, "height": height, "empty": False,
           "pad_l": pad_l, "pad_r": pad_r, "pad_t": pad_t, "pad_b": pad_b,
           "plot_w": plot_w, "plot_h": plot_h, "top": top,
           "top_y": round(y_of(top), 1),
           "mid_y": round(y_of(top / 2), 1), "mid_v": top // 2,
           "base_y": round(y_of(0), 1), "span_days": n,
           "today_x": round(x_of(n - 1), 1),
           "ticks": [{"y": round(y_of(top), 1), "v": top},
                     {"y": round(y_of(top / 2), 1), "v": top // 2},
                     {"y": round(y_of(0), 1), "v": 0}],
           "x_ticks": []}
    idxs = sorted({0, n // 3, (2 * n) // 3, n - 1})
    for i in idxs:
        geo["x_ticks"].append({"x": round(x_of(i), 1),
                               "label": str(days[i]["date"])[5:]})
    for key in ("p1", "p2"):
        paths, areas, dots = [], [], []
        run = []
        for i, d in enumerate(days):
            if d["filled"]:
                run.append((round(x_of(i), 1), round(y_of(d[key]), 1)))
                dots.append({"x": round(x_of(i), 1), "y": round(y_of(d[key]), 1),
                             "v": d[key], "date": str(d["date"])})
            else:
                if run:
                    _d = _smooth(run)
                    paths.append(_d)
                    areas.append(_area(_d, run, geo["base_y"]))
                run = []
        if run:
            _d = _smooth(run)
            paths.append(_d)
            areas.append(_area(_d, run, geo["base_y"]))
        # 硬校验：只保留合法路径（必须以 M 开头）。单点段（孤立的一天）只画点，不连线、不填面积。
        geo["paths_" + key] = [p for p in paths if p.startswith("M")]
        geo["areas_" + key] = [a for a in areas if a.startswith("M")]
        geo["dots_" + key] = dots
    return geo


_Y_STEPS = (2, 4, 6, 8, 10, 12, 16, 20, 24, 30, 40, 50, 60, 80,
            100, 150, 200, 300, 500, 1000)
