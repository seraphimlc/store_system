# -*- coding: utf-8 -*-
"""V3 月度对账：导入对账 Excel，与正式数据(正式表/绩效)比对，生成差异。

对账文件可能有两种形态：
  A) 期望的"最终有效店"清单（store 维度）——与 formal_records.store 对比
  B) 期望的"每人点数"（人维度）——与 perf.month_perf 对比
解析器返回统一 dict，再由 compare_* 计算差异；差异写入 ReconResult。
"""
import datetime as _dt
from openpyxl import load_workbook

from app.db import get_db  # noqa: F401
from app.models import FormalRecord, Person, ReconResult, ReconTask, User

_MONTHS = {1: "2026-01", 2: "2026-02", 3: "2026-03", 4: "2026-04",
           5: "2026-05", 6: "2026-06", 7: "2026-07", 8: "2026-08",
           9: "2026-09", 10: "2026-10", 11: "2026-11", 12: "2026-12"}


def _cell(row, idx):
    return row[idx] if idx < len(row) else None


# 逐条明细对账：标准字段 → 候选列名（大小写不敏感子串匹配，自适应表头）
_DAILY_COL_KEYS = {
    "date": ("statement date", "modified time", "记录日期", "日期"),
    "store_id": ("store basic id", "store id", "店舗id", "店铺id",
                 "storecode"),
    "store_name": ("shop name", "store name-local", "store name", "店名"),
    "person_name": ("agent name", "submitter", "提交人", "员工姓名", "姓名"),
    "person_code": ("员工编号", "submitter code", "employee code",
                    "person code", "员工id"),
    "points": ("store total amount", "标准点数", "标准点", "点数",
               "total amount"),
    "visible": ("a+ posm visible", "visible"),
    "deploy": ("deploy new a+posm", "new a+ posm", "new a+posm", "deploy"),
}


def _match_daily_hdr(hdr):
    """自适应识别逐条明细表头：有 店标识 + 人员 + 日期 三类列即可。

    兼容：原始巡店表（Store ID/Modified Time/Submitter）与
    结算对账表（Store Basic ID/Statement Date/Agent Name）等变体。
    """
    j = "|".join(str(c or "").lower() for c in hdr)
    has_store = any(k in j for k in
                    ("store basic id", "store id", "店舗id", "店铺id"))
    has_person = any(k in j for k in
                     ("agent name", "submitter", "提交人", "员工姓名", "姓名"))
    has_date = any(k in j for k in
                   ("statement date", "modified time", "记录日期"))
    return has_store and has_person and has_date


def guess_kind(path):
    """猜对账文件格式（全 sheet 扫描表头，避免标题行/多表干扰）：
    daily_records(逐条巡店明细→可日级) / person_points(姓名+点数) /
    store_list / unknown。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    person = daily = store = False
    for sn in wb.sheetnames:
        ws = wb[sn]
        for r in ws.iter_rows(min_row=1, max_row=12, values_only=True):
            hdr = [str(c or "") for c in r]
            j = "|".join(hdr)
            if ("点数" in j or "总点数" in j) and "姓名" in j:
                person = True
            if _match_daily_hdr(hdr):
                daily = True
            if "Store ID" in j or "店舗ID" in j:
                store = True
    wb.close()
    if daily:
        return "daily_records"
    if person:
        return "person_points"
    if store:
        return "store_list"
    return "unknown"


def parse_person_points(path, month: str = ""):
    """读 person 维度对账：找含 姓名/编号 + 点数 的列。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    # 找到表头行
    hi = None
    for i, row in enumerate(rows[:12]):
        if any(c is not None and ("姓名" in str(c) or "点数" in str(c) or
                                  "总点数" in str(c)) for c in row):
            hi = i
            break
    if hi is None:
        return None
    header = [str(c) if c is not None else "" for c in rows[hi]]
    name_i = next((i for i, c in enumerate(header) if "姓名" in c), None)
    code_i = next((i for i, c in enumerate(header) if "编号" in c), None)
    pt_i = next((i for i, c in enumerate(header) if "总点" in c or
                 c.strip() == "点数"), None)
    if name_i is None or pt_i is None:
        return None
    out = {}
    for row in rows[hi + 1:]:
        name = str(_cell(row, name_i) or "").strip()
        if not name:
            continue
        pts = _cell(row, pt_i)
        if pts is None:
            continue
        code = ""
        if code_i is not None:
            code = str(_cell(row, code_i) or "").strip()
        # 兼容 “姓名(编号)” 在一格
        if not code and "(" in name and name.endswith(")"):
            base, cd = name.rsplit("(", 1)
            code = cd.rstrip(")")
        try:
            out[code or name] = int(pts)
        except (TypeError, ValueError):
            continue
    return out


def compare_person_points(db, report: dict, month: str):
    """report: {code/name: 期望点数}；与本系统 perf 对比 → 差异行。"""
    from app.services import perf
    mine = {x["code"]: x for x in perf.month_perf(db, month)}
    persons = {p.code: p.display_name for p in db.query(Person).all()}
    diffs = []
    for key, exp in report.items():
        # key 可能是 code 或姓名
        rec = mine.get(key)
        if rec is None:
            rec = next((v for v in mine.values() if v["name"] == key), None)
        sys_pts = rec["points"] if rec else 0
        rec_code = rec["code"] if rec else key
        rec_name = persons.get(rec_code, key)
        diffs.append({"code": rec_code, "name": rec_name,
                      "system_points": sys_pts,
                      "report_points": exp,
                      "diff": sys_pts - exp})
    return diffs


def system_only_codes(db, report: dict, month: str):
    """反向名单：系统当月有绩效记录、但对账文件未列出的人。

    对账文件通常=公司期望发薪名单；系统有而对账无 → 漏列/漏发风险，
    需要人工核对（对账本身只遍历对账文件里的人，不会报警）。
    """
    from app.services import perf
    mine = {x["code"]: x for x in perf.month_perf(db, month)}
    matched = set()
    for key in report:
        rec = mine.get(key)
        if rec is None:
            rec = next((v for v in mine.values() if v["name"] == key), None)
        if rec is not None:
            matched.add(rec["code"])
    return sorted(set(mine) - matched)


# ---------------- 日级对账：解析巡店明细并按 员工×日 比对 ----------------
_DEPLOY_KEYS = ("deploy new a+posm", "new a+ posm", "new a+posm")


def _pick_daily_sheet_name(wb):
    """挑一个巡店明细 sheet：表头含 Store ID/Modified Time/Submitter；
    优先名含『最终有效/有效明细/有效数据』。"""
    best, best_score = None, -1
    for sn in wb.sheetnames:
        ws = wb[sn]
        ok = False
        for r in ws.iter_rows(min_row=1, max_row=8, values_only=True):
            if _match_daily_hdr([str(c or "") for c in r]):
                ok = True
                break
        if not ok:
            continue
        score = sum(10 for k in ("最终有效", "有效明细", "有效数据") if k in sn)
        if "原始" in sn:
            score += 1
        if score > best_score:
            best, best_score = sn, score
    return best


def _hdr_col(hdr, keys, exclude=()):
    for i, h in enumerate(hdr):
        hs = str(h or "").strip().lower()
        if any(e in hs for e in exclude):
            continue
        if any(k in hs for k in keys):
            return i
    return None


def parse_daily_records(path, month: str = "", db=None, ai_cols=None,
                        unmatched=None):
    """解析逐条巡店/结算明细 → {code: {name, days: {date: {pts, cnt}}}}。

    列定位优先用 ai_cols（模型解析的表头映射）；未提供/缺失字段回退规则
    （_DAILY_COL_KEYS 列名变体）。支持任意表头（每月对账文件表头可能不同）。
    - 日期列：Statement Date / Modified Time / 记录日期 …
    - 店标识：Store Basic ID / Store ID …
    - 人员：Agent Name（按姓名匹配 Person，需 db）或 员工编号 / Submitter「姓名(编号)」
    - 点数：Store Total Amount / 标准点数 直给；否则按 Deploy 规则
      （分界 2026-07-09 起 YES→2）。
    只计日期落在 month 的行；存在 Visible 列时空白行不算。
    """
    import re as _re
    wb0 = load_workbook(path, read_only=True, data_only=True)
    sn = _pick_daily_sheet_name(wb0)
    wb0.close()
    if sn is None:
        return None
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[sn]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    hi = hdr = None
    for i, r in enumerate(rows[:10]):
        h = [str(c or "") for c in r]
        if _match_daily_hdr(h):
            hi, hdr = i, h
            break
    if hi is None:
        return None
    cols = {}
    for key, keys in _DAILY_COL_KEYS.items():
        cols[key] = _hdr_col(hdr, keys,
                             exclude=("existing",) if key == "visible"
                             else ("existing", "visible") if key == "deploy"
                             else ())
    if ai_cols:
        # 模型解析优先：AI 给出的列号覆盖规则探测
        for k, v in ai_cols.items():
            if v is not None and 0 <= v < len(hdr):
                cols[k] = v
    i_store, i_date = cols["store_id"], cols["date"]
    i_name, i_code = cols["person_name"], cols["person_code"]
    i_vis, i_dep, i_pts = cols["visible"], cols["deploy"], cols["points"]
    if i_store is None or i_date is None or (i_name is None and i_code is None):
        return None
    # 员工姓名 → 编号（对账文件无编号时按姓名匹配；重名时优先当月有正式记录的）
    name2code = {}
    if db is not None:
        mc = set()
        if len(month) == 7:
            try:
                y, mo = int(month[:4]), int(month[5:7])
                from datetime import date as _d2
                st = _d2(y, mo, 1)
                en = _d2(y + 1, 1, 1) if mo == 12 else _d2(y, mo + 1, 1)
                mc = {c for (c,) in db.query(FormalRecord.person_code).filter(
                    FormalRecord.japan_date >= st,
                    FormalRecord.japan_date < en).all()}
            except ValueError:
                mc = set()
        by_name = {}
        for p in db.query(Person).all():
            by_name.setdefault(p.display_name, []).append(p.code)
        name2code = {n: (cs[0] if len(cs) == 1
                         else next((c for c in cs if c in mc), cs[0]))
                     for n, cs in by_name.items()}
    pat = _re.compile(r"^(.+)\((\d+)\)$")
    boundary = _dt.date(2026, 7, 9)
    out = {}
    for row in rows[hi + 1:]:
        sid = str(_cell(row, i_store) or "").strip()
        if sid == "":
            continue
        mod = str(_cell(row, i_date) or "")
        ds = mod[:10]
        if len(ds) < 10:
            continue
        if month and not ds.startswith(month):
            continue
        if i_vis is not None and str(_cell(row, i_vis) or "").strip().upper() \
                not in ("YES", "NO"):
            continue
        base = ""
        if i_name is not None:
            base = str(_cell(row, i_name) or "").strip()
        code = str(_cell(row, i_code) or "").strip() if i_code is not None else ""
        if not code:
            m = pat.match(base)
            if m:
                base, code = m.group(1).strip(), m.group(2)
        if not code and name2code and base:
            code = name2code.get(base, "")
        if not code:
            if unmatched is not None:
                unmatched.append((base, sid, ds))
            continue   # 未识别员工：跳过（页面/分析会提示）
        pts = None
        if i_pts is not None and _cell(row, i_pts) is not None and \
                str(_cell(row, i_pts)).strip() != "":
            try:
                pts = int(float(str(_cell(row, i_pts))))
            except (TypeError, ValueError):
                pts = None
        if pts is None:
            dep = (str(_cell(row, i_dep) or "").strip().upper()
                   if i_dep is not None else "")
            try:
                d = _dt.date.fromisoformat(ds)
            except ValueError:
                continue
            pts = 2 if (d >= boundary and dep == "YES") else 1
        rec = out.setdefault(code, {"name": base or code, "days": {}})
        dd = rec["days"].setdefault(ds, {"pts": 0, "cnt": 0})
        dd["pts"] += pts
        dd["cnt"] += 1
    return out or None


def compare_daily(db, parsed: dict, month: str):
    """本地正式表(按 员工×日 点数) vs 对账明细 → 问题行列表。

    问题行：仅一侧有记录，或两侧点数不一致。返回 dict 列表。
    """
    local = {}
    for f in db.query(FormalRecord).all():
        d = f.japan_date
        if d is None or (str(d))[:7] != month:
            continue
        ds = str(d)
        m = local.setdefault(f.person_code or "", {}).setdefault(
            ds, {"cnt": 0, "pts": 0})
        m["cnt"] += 1
        m["pts"] += f.points or 0
    names = {p.code: p.display_name for p in db.query(Person).all()}
    rows = []
    for code in sorted(set(local) | set(parsed)):
        pdays = parsed.get(code, {}).get("days", {})
        for ds in sorted(set(local.get(code, {})) | set(pdays)):
            s = local.get(code, {}).get(ds)
            r = pdays.get(ds)
            sys_pts = s["pts"] if s else 0
            rep_pts = r["pts"] if r else 0
            side = "both" if (s and r) else ("local_only" if s
                                             else "report_only")
            diff = sys_pts - rep_pts
            if side == "both" and diff == 0:
                continue
            rows.append({
                "date": ds, "code": code,
                "name": names.get(code, parsed.get(code, {}).get("name", code)),
                "sys": sys_pts, "rep": rep_pts,
                "cnt_sys": s["cnt"] if s else 0,
                "cnt_rep": r["cnt"] if r else 0,
                "diff": diff, "side": side})
    return rows


def create_recon(db, month: str, filename: str, content: bytes,
                 user_id: int) -> ReconTask:
    """同步完整跑一个对账任务（测试/工具用）；线上走 start_task+launch。"""
    t = start_task(db, month, filename, content, user_id)
    return run_task(db, t.id)


def start_task(db, month: str, filename: str, content: bytes,
               user_id: int) -> ReconTask:
    """保存对账文件并登记任务(pending)，不计算。"""
    import hashlib, os
    from app.config import get_settings
    d = get_settings().upload_dir
    os.makedirs(d, exist_ok=True)
    sha = hashlib.sha256(content).hexdigest()
    path = os.path.join(d, "recon_" + sha + ".xlsx")
    with open(path, "wb") as f:
        f.write(content)
    kind = guess_kind(path)
    task = ReconTask(kind="monthly_v3", status="pending",
                     created_by=user_id,
                     params={"month": month, "file": filename, "kind": kind,
                             "path": path})
    db.add(task)
    db.commit()
    return task


def _local_daily_from_stats(db, month: str):
    """先查统计表 person_daily_stats（空则先刷）→ {code: {date: {cnt,pts}}}。"""
    from app.services import perf as _vp
    from app.models import PersonDailyStat
    if not month or len(month) != 7:
        return {}
    _vp.ensure_month_stats(db, month)
    lo, hi = _vp._month_edges(month)
    out = {}
    for st in db.query(PersonDailyStat).filter(
            PersonDailyStat.ref_date >= lo,
            PersonDailyStat.ref_date < hi).all():
        out.setdefault(st.person_code, {})[str(st.ref_date)] = {
            "cnt": st.records or 0, "pts": st.points or 0}
    return out


def run_task(db, task_id: int):
    """离线执行对账任务：
    解析文件→对账数据表(recon_data_rows)；查统计表(person_daily_stats)与
    对账表按 人+日期 比对；写入结果行；生成对账 Excel 产物；状态 done。"""
    from app.models import ReconDataRow, ReconDayRow, ReconResult
    import os as _os
    from app.config import get_settings
    from app.services import perf as _vp
    t = db.get(ReconTask, task_id)
    if t is None:
        return None
    params = dict(t.params or {})
    kind = params.get("kind", "")
    month = params.get("month", "")
    path = params.get("path", "")
    db.query(ReconDayRow).filter(ReconDayRow.task_id == task_id).delete()
    db.query(ReconResult).filter(ReconResult.task_id == task_id).delete()
    db.query(ReconDataRow).filter(ReconDataRow.task_id == task_id).delete()
    t.status = "running"
    db.commit()
    try:
        report = {}
        parsed = None
        if kind == "person_points":
            report = parse_person_points(path, month) or {}
        elif kind == "daily_records":
            # 表头解析优先交给模型（每月对账文件表头可能不同）；失败回退规则
            ai_cols = ai_parse_daily_cols(path) if ai_configured() else {}
            params["parser"] = "ai" if ai_cols else "rules"
            unmatched = []
            parsed = parse_daily_records(path, month, db=db, ai_cols=ai_cols,
                                         unmatched=unmatched)
            if unmatched:
                # 对账文件里员工姓名未匹配到系统员工 → 记录，供页面/分析提示
                params["unmatched"] = unmatched[:30]
            if parsed:
                report = {c: sum(dd["pts"] for dd in v["days"].values())
                          for c, v in parsed.items()}
                # 账单日文件（如 Statement Date 固定）无逐日信息 → 标记，日级仅参考
                _all_days = set()
                for _v in parsed.values():
                    _all_days.update(_v["days"])
                if len(_all_days) == 1:
                    _sd = next(iter(_all_days))
                    params["single_date"] = True
                    params["single_date_val"] = _sd
                # 对账数据表：全量 人×日 落库
                for c, v in parsed.items():
                    for ds, dd in v["days"].items():
                        db.add(ReconDataRow(
                            task_id=task_id,
                            ref_date=_dt.date.fromisoformat(ds),
                            person_code=c, person_name=v["name"],
                            points=dd["pts"], cnt=dd["cnt"]))
        saved = 0
        sys_only = []
        if report:
            # 人月汇总对账：写全量人月行（_current_recon 依据），仅 person_points；再写差异
            if kind == "person_points" and month:
                from datetime import date as _d9
                for c, pts in report.items():
                    db.add(ReconDataRow(
                        task_id=task_id, ref_date=_d9.fromisoformat(month + "-01"),
                        person_code=c, person_name=str(c), points=int(pts), cnt=1))
            for d_ in compare_person_points(db, report, month):
                if d_["diff"] == 0:
                    continue
                db.add(ReconResult(
                    task_id=task_id, submitter_code=d_["code"],
                    system_value=d_["system_points"],
                    report_value=d_["report_points"],
                    diff=d_["diff"], status="diff",
                    family="person_points", confirmed=False,
                    note=f"{d_['name']} 点数差异"))
                saved += 1
            sys_only = system_only_codes(db, report, month)
        day_rows = 0
        if kind == "daily_records" and parsed:
            local = _local_daily_from_stats(db, month)
            for code in sorted(set(local) | set(parsed)):
                pdays = parsed.get(code, {}).get("days", {})
                for ds in sorted(set(local.get(code, {})) | set(pdays)):
                    s = local.get(code, {}).get(ds)
                    r = pdays.get(ds)
                    sys_pts = s["pts"] if s else 0
                    rep_pts = r["pts"] if r else 0
                    side = "both" if (s and r) else (
                        "local_only" if s else "report_only")
                    diff = sys_pts - rep_pts
                    if side == "both" and diff == 0:
                        continue
                    db.add(ReconDayRow(
                        task_id=task_id,
                        ref_date=_dt.date.fromisoformat(ds),
                        person_code=code,
                        sys_points=sys_pts, rep_points=rep_pts,
                        sys_count=s["cnt"] if s else 0,
                        rep_count=r["cnt"] if r else 0,
                        diff=diff, side=side,
                        note=(parsed.get(code, {}).get("name", "")
                              if side != "both" else "")))
                    day_rows += 1
        if month:
            local_codes = {m["code"] for m in _vp.month_perf(db, month)}
        else:
            local_codes = set()
        compared = len(set(report) | local_codes)
        attribution = {}
        if kind == "daily_records" and parsed:
            attribution = attribute_daily(db, path, month)
        t.summary = {
            "kind": kind, "compared": compared,
            "diff_count": day_rows if kind == "daily_records" else saved,
            "monthly_diff": saved, "sys_only": sys_only,
            "attribution": attribution}
        wb = build_report(db, task_id, "系统")
        if wb is not None:
            wdir = get_settings().upload_dir
            _os.makedirs(wdir, exist_ok=True)
            fp = _os.path.join(wdir, f"recon_result_{task_id}.xlsx")
            wb.save(fp)
            params["result_path"] = fp
        t.params = params
        t.status = "done"
        t.finished_at = _dt.datetime.utcnow()
        db.commit()
        # 任务完成 → 自动生成 AI 解读（配置了模型且开关开启时；失败不影响任务）
        if _ai_auto_enabled():
            try:
                interpret_task(db, task_id)
            except Exception:  # noqa: BLE001
                db.rollback()
        # 任务完成 → 自动生成该月分期对账偏差表（失败不影响任务）
        if month:
            try:
                from app.services import period
                period.sync_period_table(db, month)
            except Exception:  # noqa: BLE001
                db.rollback()
        return t
    except Exception as e:  # noqa: BLE001
        db.rollback()
        t = db.get(ReconTask, task_id)
        if t is not None:
            t.status = "failed"
            sm = dict(t.summary or {})
            sm["error"] = str(e)
            t.summary = sm
            db.commit()
        raise


def _task_worker(task_id: int):
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        run_task(db, task_id)
    except Exception:  # noqa: BLE001
        pass
    finally:
        db.close()



def submit_task(db, month: str, filename: str, content: bytes,
                user_id: int, sync: bool = False):
    """登记新对账任务；同月已有版本则标记为「上一版」(保留历史)。
    sync=True 时同步执行（测试/工具）；否则后台线程执行。返回 (task, older_ids)。"""
    from app.models import ReconTask
    t = start_task(db, month, filename, content, user_id)
    older = []
    for e in db.query(ReconTask).filter(
            ReconTask.kind == "monthly_v3").all():
        if e.id == t.id:
            continue
        if (e.params or {}).get("month") != month:
            continue
        ep = dict(e.params or {})
        if ep.get("replaced_by"):
            continue
        ep["replaced"] = True
        ep["replaced_by"] = t.id
        e.params = ep
        older.append(e.id)
    if older:
        db.commit()
    tp = dict(t.params or {})
    tp["version"] = len(older) + 1
    tp["current"] = True
    t.params = tp
    db.commit()
    if sync:
        run_task(db, t.id)
    else:
        launch_task(t.id)
    return t, older

def launch_task(task_id: int):
    """后台线程离线跑对账任务（daemon；进程重启未完成任务显示 pending 可重跑）。"""
    import threading
    threading.Thread(target=_task_worker, args=(task_id,),
                     daemon=True).start()


# ---------------- 差异归因（行级，确定性规则） ----------------
def attribute_daily(db, path: str, month: str):
    """行级归因统计：对账文件每一行 vs 系统正式/判定。

    返回：
      consistent   两侧(店+时间戳)一致的店行数
      only_system  本地正式表有、对账文件没有
      only_report  对账文件有、本地没有（按系统判定原因拆分）
      net          only_system - only_report 总数
    """
    from app.models import FormalRecord, RawRecord
    from openpyxl import load_workbook
    import re as _re
    # 先扫对账文件：定位列并判断是否含时间戳（无时间戳→按店匹配归因）
    wb = load_workbook(path, read_only=True, data_only=True)
    sheet_keys2 = []
    has_ts = False
    for sn in wb.sheetnames:
        ws = wb[sn]
        hdr = None
        for r in ws.iter_rows(min_row=1, max_row=10, values_only=True):
            h = [str(c or "") for c in r]
            if _match_daily_hdr(h):
                hdr = h
                break
        if hdr is None:
            continue
        i_id = _hdr_col(hdr, ("store basic id", "store id", "店舗id",
                              "店铺id"))
        i_date = _hdr_col(hdr, ("statement date", "modified time", "记录日期"))
        if i_id is None or i_date is None:
            continue
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r is None:
                continue
            sid = str(r[i_id] or "") if i_id < len(r) else ""
            if not sid.strip():
                continue
            mod = str(r[i_date] or "") if i_date < len(r) else ""
            if mod.startswith(month):
                sheet_keys2.append((sid, mod))
                if len(mod) >= 16:
                    has_ts = True
    wb.close()

    def key(store, mod):
        return (store, mod) if has_ts else (store,)

    local = set()
    for f in db.query(FormalRecord).all():
        if str(f.japan_date or "")[:7] != month:
            continue
        rr = db.get(RawRecord, f.raw_record_id)
        if rr is not None:
            local.add(key(rr.store_id_raw, rr.modified_raw))
    rawmap = {}
    for rr in db.query(RawRecord).all():
        if rr.clean_status != "visible_blank":
            rawmap.setdefault(key(rr.store_id_raw, rr.modified_raw), rr)
    sheet_set = {key(s, m) for s, m in sheet_keys2}
    consistent = sum(1 for k in local if k in sheet_set)
    only_system = len(local) - consistent
    by_reason = {"from_sub": 0, "master_late": 0, "cross_file_dup": 0,
                 "no_ref": 0, "not_found": 0}
    for k in sheet_set - local:
        rr = rawmap.get(k)
        if rr is None:
            by_reason["not_found"] += 1
        elif rr.clean_status in by_reason:
            by_reason[rr.clean_status] += 1
        else:
            by_reason.setdefault(rr.clean_status, 0)
            by_reason[rr.clean_status] += 1
    only_report = sum(by_reason.values())
    return {"consistent": consistent, "only_system": only_system,
            "only_report": only_report,
            "only_report_reason": by_reason,
            "net": only_system - only_report}


# ---------------- AI 对账解读（可选，需配置模型 API） ----------------
def _ai_cfg():
    import os
    return {"key": os.environ.get("AI_API_KEY", ""),
            "base": os.environ.get("AI_BASE_URL", "").rstrip("/"),
            "model": os.environ.get("AI_MODEL", "gpt-4o-mini")}


def ai_configured() -> bool:
    cfg = _ai_cfg()
    return bool(cfg["key"] and cfg["base"])


def _ai_auto_enabled() -> bool:
    """任务完成是否自动生成 AI 解读：默认开（配了模型即自动）；AI_AUTO_INTERPRET=0 关闭。"""
    import os
    return (os.environ.get("AI_AUTO_INTERPRET", "1") != "0"
            and ai_configured())


# AI 表头解析：让模型理解任意对账文件表头 → 标准列映射。
# 每个月的对账文件表头都可能不同，规则列名兜底，模型为主。
_AI_COL_FIELDS = ("date", "store_id", "store_name", "person_name",
                  "person_code", "points", "visible", "deploy")


def _sheet_preview(path: str, max_rows: int = 14, max_cols: int = 12):
    """把对账文件前几行文本化，供模型理解表头与样例。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    lines = []
    try:
        for sn in wb.sheetnames[:3]:
            ws = wb[sn]
            rows = list(ws.iter_rows(min_row=1, max_row=max_rows,
                                     values_only=True))
            if not rows:
                continue
            lines.append(f"# sheet: {sn}")
            for ri, r in enumerate(rows):
                cells = [str(c)[:24] if c is not None else ""
                         for c in r[:max_cols]]
                lines.append(f"R{ri}: " + " | ".join(
                    f"[{ci}]{v}" for ci, v in enumerate(cells)))
    finally:
        wb.close()
    return "\n".join(lines)


def ai_parse_daily_cols(path: str) -> dict:
    """模型解析表头 → {字段: 列号}；未配置/失败/不可信 → {}（回退规则）。"""
    if not ai_configured():
        return {}
    preview = _sheet_preview(path)
    if not preview:
        return {}
    prompt = (
        "你是表格解析助手。下面是一个对账 Excel 前几行（R0 通常是表头，列号从0开始，"
        "每格形如 [列号]值；可能有标题行/多sheet）。请识别这些列的语义，只返回 JSON：\n"
        '{"date": 列号或null, "store_id": 列号或null, "store_name": 列号或null, '
        '"person_name": 列号或null, "person_code": 列号或null, "points": 列号或null, '
        '"visible": 列号或null, "deploy": 列号或null}\n'
        "字段含义：date=记录/账单日期；store_id=店铺标识（如 Store Basic ID/Store ID）；"
        "store_name=店铺名称；person_name=员工姓名；person_code=员工编号；"
        "points=每家店结算点数（值常为 1/2）；visible=巡店可见标记（YES/NO）；"
        "deploy=投放标记（YES/NO）。无法确定的给 null。除 JSON 外不要输出任何文字。\n\n"
        + preview)
    try:
        text = _chat(prompt)
        import json as _json
        data = _json.loads(text)
        if not isinstance(data, dict):
            return {}
        ncols = None
        # 从预览推断最大列数
        m = __import__("re").findall(r"\[(\d+)\]", preview)
        if m:
            ncols = max(int(x) for x in m) + 1
        out = {}
        for f in _AI_COL_FIELDS:
            v = data.get(f)
            if isinstance(v, bool) or not isinstance(v, int):
                continue
            if v < 0 or (ncols is not None and v >= ncols):
                continue
            out[f] = v
        # 至少要能定位 店 + 人员 + 日期 才可信
        if not (out.get("date") is not None and
                out.get("store_id") is not None and
                (out.get("person_name") is not None or
                 out.get("person_code") is not None)):
            return {}
        return out
    except Exception:  # noqa: BLE001
        return {}


def build_interpret_prompt(db, task_id: int) -> str:
    """构造「对账结果 AI 分析」提示词。

    设计要点：
    - 只给模型可引用的事实（比对/归因/合计/问题行样例/反向名单/特殊标记）；
    - 要求结构化输出（总体结论/差异构成/风险核对/建议动作 四节）；
    - 约束：不得编造数字、不得臆测文件外原因、中文、限字数。
    """
    from app.models import Person, ReconDayRow, ReconResult, ReconTask
    from app.services import perf
    t = db.get(ReconTask, task_id)
    month = (t.params or {}).get("month", "")
    s = (t.summary or {})
    att = s.get("attribution") or {}
    p = t.params or {}
    lines = []
    lines.append(f"- 对账月份：{month}；来源文件：{p.get('file','')}")
    lines.append(f"- 比对人数：{s.get('compared',0)}；日级问题行：{s.get('diff_count',0)}；"
                 f"人月差异人数：{s.get('monthly_diff',0)}")
    if att:
        lines.append(f"- 行级归因：两侧一致 {att.get('consistent')}，仅系统有 "
                     f"{att.get('only_system')}，仅对账有 {att.get('only_report')}"
                     f"（原因拆分 {att.get('only_report_reason')}），净差 {att.get('net')}")
    # 系统合计 与 对账侧合计（人月点数）
    comp = perf.company_summary(db, month) if month else {}
    if comp:
        lines.append(f"- 系统当月合计：有效店 {comp.get('records')}，1点/2点 "
                     f"{comp.get('p1')}/{comp.get('p2')}，总点 {comp.get('total_points')}，"
                     f"工资 {comp.get('total_amount')} 円")
    res = db.query(ReconResult).filter(ReconResult.task_id == task_id).all()
    if res:
        rep_total = sum(r.report_value or 0 for r in res)
        sys_total = sum(r.system_value or 0 for r in res)
        lines.append(f"- 仅差异人员的人月点数合计（注意：只含人月差异≠0 的人员，"
                     f"不是全月合计）：系统 {sys_total} vs 对账 {rep_total}，"
                     f"差 {sys_total - rep_total:+d}")
    names = {p.code: p.display_name for p in db.query(Person).all()}
    dr = db.query(ReconDayRow).filter(ReconDayRow.task_id == task_id).order_by(
        ReconDayRow.ref_date, ReconDayRow.person_code).limit(15).all()
    if dr:
        lines.append("- 问题行样例（日期 员工 本地/对账 差 归属）：")
        for r in dr:
            lines.append(f"  {r.ref_date} {names.get(r.person_code,r.person_code)} "
                         f"{r.sys_points}/{r.rep_points} 差{r.diff:+d} {r.side}")
    sys_only = s.get("sys_only") or []
    if sys_only:
        lines.append(f"- 系统有而对账文件无（反向名单）：{len(sys_only)} 人 "
                     f"（{', '.join(names.get(c,c) for c in sys_only[:8])}"
                     f"{'…' if len(sys_only)>8 else ''}）")
    if p.get("single_date"):
        lines.append(f"- 注意：对账文件为账单日文件（日期列固定 {p.get('single_date_val')}），"
                     "无逐日信息，日级明细仅参考，应以人月差异为准")
    if p.get("parser"):
        lines.append(f"- 表头解析方式：{p.get('parser')}（ai=模型解析，rules=规则兜底）")
    if p.get("unmatched"):
        lines.append(f"- 对账文件有 {len(p.get('unmatched'))} 行员工姓名未匹配到系统员工"
                     "（已跳过）：" +
                     "、".join(f"{x[0]}" for x in p.get('unmatched')[:10]))
    prompt = (
        "你是巡店结算系统的对账分析师，只依据下面给出的数据做分析，不得编造或臆测。\n"
        "请用简体中文输出 Markdown 分节，结构固定如下：\n"
        "## 总体结论\n一句话说明差异规模与结论（一致/存在差异需处理/对账文件覆盖不足等）。\n"
        "## 差异构成\n结合归因与人月差解释差异来自哪里（仅系统多/仅对账多/原因类别），引用具体数字。\n"
        "## 风险与需人工核对\n只列数据能支撑的点（如反向名单=漏列风险、仅对账含从档/重复=口径差异、"
        "账单日文件=无逐日信息），逐条列出，不得臆测文件外的原因。\n"
        "## 建议动作\n给出 2-3 条可执行建议（如核对反向名单、对齐对账口径、确认找平、重传新版文件）。\n"
        "约束：不编造数字；不用给定数据之外的信息下结论；总字数≤350字。\n\n"
        "对账数据：\n" + "\n".join(lines))
    return prompt


def _chat(prompt: str) -> str:
    """调用模型（公共实现见 app.services.ai_chat）。"""
    from app.services import ai_chat
    return ai_chat.chat(prompt)


def interpret_task(db, task_id: int) -> dict:
    """AI 解读对账任务；未配置模型或失败时返回 ok=False。"""
    if not ai_configured():
        return {"ok": False, "msg": "未配置 AI 模型（AI_API_KEY / AI_BASE_URL）"}
    t = db.get(ReconTask, task_id)
    if t is None:
        return {"ok": False, "msg": "对账任务不存在"}
    try:
        text = _chat(build_interpret_prompt(db, task_id))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"AI 调用失败：{type(e).__name__}: {e}"}
    from datetime import datetime as _dt2
    sm = dict(t.summary or {})
    sm["ai_interpret"] = {"text": text, "model": _ai_cfg()["model"],
                          "at": _dt2.now().strftime("%Y-%m-%d %H:%M")}
    t.summary = sm
    db.commit()
    return {"ok": True, "text": text}

# ---------------- 找平：对账差异 → 下月工资加/减 ----------------
def _next_month(month: str) -> str:
    if not month:
        return ""
    y, m0 = int(month[:4]), int(month[5:7])
    if m0 == 12:
        return f"{y + 1}-01"
    return f"{y}-{m0 + 1:02d}"


def _diff_amount(system_pts, report_pts, month: str = None) -> int:
    """差异应补金额（円）：按工资规则（对应月份门槛）分别折算后相减。"""
    from app.services import perf
    return perf.salary_for(report_pts or 0, month=month) \
        - perf.salary_for(system_pts or 0, month=month)


def ai_employee_notes(db, task_id: int) -> dict:
    """批量让模型为「有差异员工」生成一行简短解读 → {code: note}。

    用于对账报告 Excel 的「说明」列（人月差异明细 / 员工×日明细）。
    结果缓存到 task.summary.ai_employee_notes（只调一次模型）；未配置/失败 → {}。
    """
    from app.models import Person, ReconResult, ReconTask
    t = db.get(ReconTask, task_id)
    if t is None:
        return {}
    sm = dict(t.summary or {})
    if sm.get("ai_employee_notes"):
        return sm["ai_employee_notes"]
    if not ai_configured():
        return {}
    rows = (db.query(ReconResult).filter(
        ReconResult.task_id == task_id, ReconResult.diff != 0)
        .order_by(ReconResult.submitter_code).all())
    if not rows:
        return {}
    names = {p.code: p.display_name for p in db.query(Person).all()}
    p = t.params or {}
    lines = [f"对账月份：{p.get('month','')}；来源文件：{p.get('file','')}"]
    att = sm.get("attribution") or {}
    if att:
        lines.append(f"行级归因：一致 {att.get('consistent')}，仅系统 "
                     f"{att.get('only_system')}，仅对账 {att.get('only_report')}"
                     f"（原因 {att.get('only_report_reason')}）")
    if p.get("single_date"):
        lines.append("注意：对账文件为账单日文件（日期列固定），无逐日信息，"
                     "日级仅参考，以人月差异为准。")
    lines.append("差异员工（编号 姓名 系统点数 对账点数 差）：")
    for r in rows:
        lines.append(f"{r.submitter_code} {names.get(r.submitter_code, '')} "
                     f"{r.system_value or 0} {r.report_value or 0} "
                     f"{r.diff or 0:+d}")
    prompt = (
        "你是对账差异解释助手，读者是【不懂技术的管理员】，请用大白话解释。"
        "为每个差异员工写一句中文解读（≤70字）：①先说清谁多谁少、差多少点"
        "（如『系统比对账文件多 197 点』）；②可能原因只能从数据判断，用通俗"
        "说法（如『对账文件只给了整月总数、没有每天明细，所以按天对比的数字"
        "会很大，不用看，按整月核对就行』『对账文件可能漏了部分店』）；"
        "③一句能直接执行的处理建议。严禁出现『账单日聚合/日级错位/以人月差异"
        "为准/行级归因』等术语。不得臆测数据外的原因。"
        "只返回 JSON：{\"员工编号\": \"解读\"}。\n\n" + "\n".join(lines))
    try:
        import json as _json
        text = _chat(prompt)
        data = _json.loads(text)
        if not isinstance(data, dict):
            return {}
        notes = {str(k): str(v)[:140] for k, v in data.items() if v}
        sm["ai_employee_notes"] = notes
        t.summary = sm
        db.commit()
        return notes
    except Exception:  # noqa: BLE001
        return {}


def confirm_adjust(db, task_id: int, person_code: str,
                   actor_id=None) -> dict:
    """把对账任务某员工差异确认成找平记录（下月工资加/减，幂等）。

    一个任务 × 一名员工只有一条；重复确认返回已有记录（不重复加钱）。
    """
    from app.models import AdjustRecord, ReconResult, ReconTask
    t = db.get(ReconTask, task_id)
    rr = (db.query(ReconResult)
          .filter(ReconResult.task_id == task_id,
                  ReconResult.submitter_code == person_code).first())
    if t is None or rr is None:
        return {"ok": False, "msg": "对账任务或该员工差异行不存在"}
    month = (t.params or {}).get("month", "")
    nxt = _next_month(month)
    old = (db.query(AdjustRecord)
           .filter(AdjustRecord.source_task_id == task_id,
                   AdjustRecord.person_code == person_code).first())
    if old is not None:
        return {"ok": True, "msg": "已确认过（幂等）", "amount": old.amount,
                "record": old}
    # 找平按点数记录（对账比系统多 → 下月补正数；系统比对账多 → 负数扣回）
    # 锁存发生当月的点数单价：下月纠偏金额=点数×当时单价，不随本月单价变化
    from app.services import perf
    amount = (rr.report_value or 0) - (rr.system_value or 0)
    pp = perf.month_per_point(db, month)
    rec = AdjustRecord(
        month=month, applied_to_month=nxt, person_code=person_code,
        amount=amount, per_point=pp,
        reason=(f"对账#{task_id} {month} 差异 {rr.diff:+d} 点"
                f"（系统 {rr.system_value} / 对账 {rr.report_value}）"
                f"· 当时单价 {pp}円/点"),
        source_task_id=task_id, source_points_diff=rr.diff or 0,
        created_by=actor_id)
    db.add(rec)
    # 同步写入薪资找平执行值（同一张表 payroll_period_rows）：
    # 对账页确认 = 一键把该员工当月的金额差全部找平（增量=金额差−已找平金额；金额含奖金）
    from app.models import PayrollPeriodRow
    prow = (db.query(PayrollPeriodRow)
            .filter(PayrollPeriodRow.month == month,
                    PayrollPeriodRow.person_code == person_code).first())
    if prow is not None:
        delta = (prow.diff_amount or 0) - (prow.adjust_amount or 0)
        prow.adjust_amount = (prow.adjust_amount or 0) + delta
    else:
        delta = 0
    db.commit()
    if prow is not None:            # 记录金额增量（供取消时精确回滚）
        rec.amount_adj = delta
        db.commit()
    return {"ok": True, "amount": amount, "per_point": pp, "record": rec}


def cancel_adjust(db, task_id: int, person_code: str) -> bool:
    """取消已确认的找平记录（同时撤销写入薪资找平表的执行值，按金额）。"""
    from app.models import AdjustRecord, PayrollPeriodRow, ReconTask
    old = (db.query(AdjustRecord)
           .filter(AdjustRecord.source_task_id == task_id,
                   AdjustRecord.person_code == person_code).first())
    if old is not None:
        prow = (db.query(PayrollPeriodRow)
                .filter(PayrollPeriodRow.month == old.month,
                        PayrollPeriodRow.person_code == person_code)
                .first())
        if prow is not None:
            # 回滚当时写入的金额增量（确认时按金额差写入）
            adj_amt = getattr(old, "amount_adj", 0) or 0
            if adj_amt:
                prow.adjust_amount = (prow.adjust_amount or 0) - adj_amt
    n = (db.query(AdjustRecord)
         .filter(AdjustRecord.source_task_id == task_id,
                 AdjustRecord.person_code == person_code).delete())
    db.commit()
    return n > 0


def task_adjust_map(db, task_id: int):
    """某对账任务已确认的找平：{person_code: AdjustRecord}。"""
    from app.models import AdjustRecord
    return {a.person_code: a
            for a in db.query(AdjustRecord).filter(
                AdjustRecord.source_task_id == task_id).all()}


# ---------------- 月度对账报告（一键生成） ----------------
def build_report(db, task_id: int, author_name: str = ""):
    """生成《月度对账报告.xlsx》：
    Sheet1 报告摘要（月份/来源文件/公司正式口径合计/比对与差异/找平/结论）
    Sheet2 员工×日对账明细(问题行，若日级) · Sheet3 差异明细 ·
    Sheet4 系统有而对账文件无 · Sheet5 找平确认留痕。"""
    from app.models import (AdjustRecord, Person, ReconDayRow, ReconResult,
                            ReconTask)
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from app.services import perf
    from datetime import datetime

    cur = db.get(ReconTask, task_id)
    if cur is None:
        return None
    month = (cur.params or {}).get("month", "")
    fname = (cur.params or {}).get("file", "")
    summary = cur.summary or {}
    rows = (db.query(ReconResult).filter(ReconResult.task_id == task_id)
            .order_by(ReconResult.submitter_code).all())
    day_rows = (db.query(ReconDayRow).filter(ReconDayRow.task_id == task_id)
                .order_by(ReconDayRow.ref_date,
                          ReconDayRow.person_code).all())
    persons = {p.code: p.display_name for p in db.query(Person).all()}
    sys_codes = summary.get("sys_only") or []
    adj = sorted(task_adjust_map(db, task_id).values(),
                 key=lambda a: a.person_code)
    adj_total = sum(a.amount or 0 for a in adj)

    # 系统正式口径合计（该月）
    comp = perf.company_summary(db, month) if month else {}
    adjust_map = perf.adjust_map(db, month)
    payroll_total = comp.get("total_amount", 0) + sum(adjust_map.values()) * 250

    bold = Font(bold=True)
    wb = Workbook()

    # Sheet1 摘要
    ws = wb.active
    ws.title = "对账报告摘要"
    ws.append(["月度对账报告"])
    ws.cell(1, 1).font = Font(bold=True, size=14)
    ws.append([])
    ws.append(["对账月份", month])
    ws.append(["对账来源文件", fname])
    ws.append(["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M")])
    ws.append(["生成人", author_name or ""])
    ws.append([])
    ws.append(["—— 系统正式口径（公司合计）——"])
    ws.cell(ws.max_row, 1).font = bold
    if comp:
        ws.append(["有效店数", comp.get("records", 0)])
        ws.append(["1点店 / 2点店", f"{comp.get('p1',0)} / {comp.get('p2',0)}"])
        ws.append(["总点数", comp.get("total_points", 0)])
        ws.append(["点数工资合计(円)", comp.get("total_amount", 0)])
        ws.append(["上月找平合计(点)", sum(adjust_map.values())])
        ws.append(["应付合计(円)", payroll_total])
    ws.append([])
    ws.append(["—— 对账比对 ——"])
    ws.cell(ws.max_row, 1).font = bold
    ws.append(["比对人数", summary.get("compared", 0)])
    ws.append(["差异条数", summary.get("diff_count", 0)])
    ws.append(["系统有而对账文件无(人)", len(sys_codes)])
    if day_rows:
        ws.append(["员工×日 问题行", len(day_rows)])
    ws.append(["本任务确认找平(点)", adj_total])
    ws.append([])
    if (summary.get("diff_count") or 0) == 0 and not sys_codes:
        ws.append(["结论：系统与对账文件完全一致（0 差异，无漏列）。"])
    else:
        ws.append(["结论：存在差异/漏列，见员工×日明细/差异明细与反向名单 "
                   "sheet；确认后的差异已按下月找平处理。"])

    # Sheet2 员工×日对账明细（问题行）
    notes = ai_employee_notes(db, task_id) or {}
    if day_rows:
        wsd = wb.create_sheet("员工×日对账明细")
        wsd.append(["日期", "员工编号", "员工姓名", "本地(系统)点数",
                    "对账点数", "差异(本地-对账)", "说明"])
        for c in range(1, 8):
            wsd.cell(1, c).font = bold
        side_text = {"both": "点数不一致", "local_only": "仅本地有",
                     "report_only": "仅对账有"}
        for dr in day_rows:
            note = side_text.get(dr.side, "")
            ai_n = notes.get(dr.person_code)
            if ai_n:
                note = f"{note}｜AI:{ai_n}"
            wsd.append([str(dr.ref_date), dr.person_code,
                        persons.get(dr.person_code, dr.person_code),
                        dr.sys_points or 0, dr.rep_points or 0,
                        dr.diff or 0, note])

    # Sheet2 差异明细
    ws2 = wb.create_sheet("差异明细")
    ws2.append(["人员", "编号", "系统点数", "对账点数",
                "差异(系统-对账)", "应找平(円)", "AI说明"])
    for c in range(1, 8):
        ws2.cell(1, c).font = bold
    if not rows:
        ws2.append(["无差异：两侧点数一致"])
    else:
        for r in rows:
            name = (r.note or "").replace(" 点数差异", "") or r.submitter_code
            ws2.append([name, r.submitter_code, r.system_value or 0,
                        r.report_value or 0, r.diff or 0,
                        _diff_amount(r.system_value or 0, r.report_value or 0, month),
                        notes.get(r.submitter_code, "")])

    # Sheet3 反向名单
    ws3 = wb.create_sheet("系统有而对账文件无")
    if not sys_codes:
        ws3.append(["无（对账文件已覆盖系统全部有记录员工）"])
    else:
        ws3.append(["编号", "姓名"])
        ws3.cell(1, 1).font = bold
        ws3.cell(1, 2).font = bold
        for code in sys_codes:
            ws3.append([code, persons.get(code, code)])

    # Sheet4 找平确认留痕
    ws4 = wb.create_sheet("找平确认")
    ws4.append(["编号", "姓名", "点数", "生效月份", "原因", "确认时间"])
    for c in range(1, 7):
        ws4.cell(1, c).font = bold
    if not adj:
        ws4.append(["无（本任务未确认找平）"])
    else:
        for a in adj:
            ws4.append([a.person_code, persons.get(a.person_code,
                                                   a.person_code),
                        a.amount or 0, a.applied_to_month,
                        a.reason or "",
                        a.created_at.strftime("%Y-%m-%d %H:%M")
                        if a.created_at else ""])
    wsA = wsI = None
    att = summary.get("attribution") or {}
    if att:
        wsA = wb.create_sheet("差异归因")
        wsA.append(["类别", "数值"])
        wsA.append(["两侧一致的店行", att.get("consistent", 0)])
        wsA.append(["仅系统有（对账表没有）", att.get("only_system", 0)])
        wsA.append(["仅对账有（系统判滤）", att.get("only_report", 0)])
        for k, v in (att.get("only_report_reason") or {}).items():
            wsA.append([f"  其中: {k}", v])
        wsA.append(["净差（系统-对账）", att.get("net", 0)])
        wsA.append([])
        wsA.append(["注：行级按 店+时间戳 一致判定；仅对账有多为系统规则滤除"
                    "（从档归并/重复导入/跨日），存疑需人工核对。"])

    ai = summary.get("ai_interpret")
    if ai:
        wsI = wb.create_sheet("AI解读")
        wsI.append(["模型", ai.get("model", "")])
        wsI.append(["生成时间", ai.get("at", "")])
        wsI.append([])
        for line in (ai.get("text", "") or "").splitlines():
            wsI.append([line])

    sheets_all = [ws, ws2, ws3, ws4]
    if day_rows:
        sheets_all.insert(1, wsd)
    if wsA:
        sheets_all.append(wsA)
    if wsI:
        sheets_all.append(wsI)
    for wsx in sheets_all:
        for col in wsx.columns:
            w = max(len(str(c.value or "")) for c in col) + 2
            wsx.column_dimensions[col[0].column_letter].width = min(w, 48)
    return wb
