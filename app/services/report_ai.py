# -*- coding: utf-8 -*-
"""对比分析报告：结构化 JSON + 中/日双语 + 异步生成（规格 v7 §8）。

原则（写进代码而不是口号）：
1. **数字一律由 `daily_report.compare()` 算好**，模型只负责把数字写成评语；
2. 模型输出必须是**合法 JSON**，否则该语言判失败（`summary` 数字仍然可用）；
3. **同区间 + 同数据指纹 → 复用已有报告**，不重复消耗 token；
4. 一种语言失败不影响另一种；全部失败 → `failed`，页面提示「AI 评语暂不可用」；
5. **管理端的「追问清单」不下发给员工**（`person_block` 只返回 comment/off_days）。
"""
import hashlib
import json
import threading
from datetime import datetime, timedelta

from app.models import StaffReportAnalysis, StaffDailyReport  # noqa: F401


class ReportAIError(Exception):
    """报告生成失败。"""


def _s():
    from app.config import get_settings
    return get_settings()


def report_ai_enabled() -> bool:
    return bool(_s().report_ai_enabled)


def report_langs() -> list:
    raw = _s().report_ai_langs or "zh"
    return [x.strip() for x in raw.split(",") if x.strip()] or ["zh"]


PROMPT_VERSION = 2      # prompt/标签口径变更时 +1 → 旧报告不再被复用（否则会一直吃旧质量的缓存）


def data_fingerprint(res: dict) -> str:
    """对比数据的指纹（含区间、逐人数字、prompt 版本）：同数据同口径 → 复用报告。"""
    payload = {"v": PROMPT_VERSION,
               "start": str(res["start"]), "end": str(res["end"]),
               "counts": res.get("counts") or {},
               "persons": [[p["person_code"], p["sys_p1"], p["sys_p2"],
                            p["rep_p1"], p["rep_p2"]] for p in res["persons"]]}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


_L10N = {
    "zh": {"sys": "系统", "rep": "自报", "acc": "准确率", "filled": "已报天数",
           "total": "合计",
           "sdays": "系统天数", "gaps": "应填未填", "d": "Δ",
           "k_both": "两侧都有", "k_miss_rep": "系统有/未报", "k_miss_sys": "自报有/系统无",
           "rule": "全部文字用**简体中文**书写（不要混入日文或英文词汇）。"},
    "ja": {"sys": "システム", "rep": "自己申告", "acc": "正確率", "filled": "申告日数",
           "total": "合計",
           "sdays": "システム日数", "gaps": "要申告未申告", "d": "差異",
           "k_both": "両方あり", "k_miss_rep": "システムあり／未申告",
           "k_miss_sys": "自己申告あり／システムなし",
           "rule": "全文を**自然な日本語**で書くこと。中国語の語（准确率・系统・自报・漏填报・应填未填 など）を"
                   "混ぜないこと。用語は システム／自己申告／正確率／申告日数／要申告未申告 を使うこと。"},
}


def build_prompt(res: dict, lang: str) -> str:
    """只喂汇总与 Top 偏差（控 token）；标签按语言本地化，并附硬约束。"""
    top_n = _s().report_ai_top_n
    L = _L10N.get(lang, _L10N["zh"])
    s = res["summary"]
    persons = []
    for p in res["persons"][:top_n]:
        persons.append({
            "person_code": p["person_code"], "name": p["name"],
            L["acc"]: None if p["acc"] is None else round(p["acc"] * 100, 1),
            L["sys"] + L["total"]: p["sys_total"], L["rep"] + L["total"]: p["rep_total"],
            L["d"] + L["total"]: p["d_total"], L["d"] + "1点": p["d1"],
            L["d"] + "2点": p["d2"],
            L["filled"]: p["days_filled"], L["sdays"]: p["days_system"],
            L["gaps"]: p["gaps"]})
    kinds = {"both": L["k_both"], "missing_report": L["k_miss_rep"],
             "missing_system": L["k_miss_sys"]}
    anomalies = []
    for r in res["daily"]:
        if r["kind"] != "both":
            anomalies.append({"date": str(r["date"]),
                              "person": r.get("name") or r.get("person_code"),
                              "kind": kinds[r["kind"]]})
        elif r["dt"]:
            anomalies.append({"date": str(r["date"]),
                              "person": r.get("name") or r.get("person_code"),
                              "kind": kinds["both"], L["d"]: r["dt"]})
    anomalies = anomalies[:60]
    return (
        "你在给日本巡店业务做「%s vs %s」的数据分析。\n"
        "所有数字已经由程序算好（%s = %s − %s：正数=少报，负数=多报；"
        "%s = 1 − Σ|%s| ÷ Σ%s；漏填报不计入%s）。\n"
        "请**只使用下面给出的数字**，不要自己再算或推测新数字。\n\n"
        "区间：%s ~ %s\n"
        "汇总：%s %s 条 / %s %s 条 / %s %s 条 / 其中一致 %s 条 /"
        " %s %s / %s %s / 涉及员工 %s 人\n"
        "逐人（最多 %s 人）：%s\n"
        "异常日（最多 60 条）：%s\n\n"
        "请输出**严格 JSON**（不要解释、不要 markdown 代码块）：\n"
        "{\"overall_comment\": \"整体一致性与趋势（≤3 句）\",\n"
        " \"accuracy_notes\": \"%s分布与集中问题（≤2 句）\",\n"
        " \"per_person\": {\"<person_code>\": {\n"
        "   \"comment\": \"这个人的评语（≤2 句）\",\n"
        "   \"off_days\": [{\"date\": \"YYYY-MM-DD\", \"delta\": -7,\n"
        "                    \"note\": \"一句话说明\"}],\n"
        "   \"questions\": [\"给管理员核实的问题\"]}}}\n\n"
        "篇幅要求（很重要，太长会被截断导致格式出错）：overall_comment ≤3 句，"
        "accuracy_notes ≤2 句，每个 per_person 的 comment ≤2 句、off_days ≤5 条、"
        "questions ≤3 条，只对偏差最大的前 %s 人写详细评语。\n\n"
        "硬约束：\n"
        "1. 评语**只陈述数字与可能原因**（漏报/多报/口径不同/文件缺数据），"
        "**不做人身评价、不做定性指控**；\n"
        "2. `per_person` 的 key 必须用我给的 person_code，不要编造人员；\n"
        "3. `off_days` 只写我给出的异常日；\n"
        "4. `questions` 是给管理员看的核实清单；\n"
        "5. %s"
        % (L["sys"], L["rep"], L["d"], L["sys"], L["rep"], L["acc"], L["d"], L["sys"],
           L["acc"], res["start"], res["end"],
           L["rep"], s["checkin_cnt"], L["sys"], s["formal_cnt"],
           L["k_both"], s["matched_cnt"], s["consistent_cnt"],
           L["k_miss_rep"], res["counts"]["missing_report"],
           L["k_miss_sys"], res["counts"]["missing_system"], s["persons"],
           top_n, json.dumps(persons, ensure_ascii=False),
           json.dumps(anomalies, ensure_ascii=False), L["acc"], top_n, L["rule"]))


def _normalize(data: dict, res: dict) -> dict:
    """校验并裁剪模型输出：只保留已知人员、只留需要的字段。"""
    if not isinstance(data, dict):
        raise ReportAIError("模型输出不是 JSON 对象")
    known = {p["person_code"]: p["name"] for p in res["persons"]}
    out = {"overall_comment": str(data.get("overall_comment") or "")[:4000],
           "accuracy_notes": str(data.get("accuracy_notes") or "")[:4000],
           "per_person": {}}
    src = data.get("per_person")
    if not isinstance(src, dict):
        src = {}
    for code, blk in src.items():
        if code not in known or not isinstance(blk, dict):
            continue                                  # 编造的人员直接丢弃
        off = []
        for o in (blk.get("off_days") or [])[:30]:
            if isinstance(o, dict) and o.get("date"):
                off.append({"date": str(o.get("date")),
                            "delta": o.get("delta"),
                            "note": str(o.get("note") or "")[:300]})
        out["per_person"][code] = {
            "name": known[code],
            "comment": str(blk.get("comment") or "")[:4000],
            "off_days": off,
            "questions": [str(q)[:300] for q in (blk.get("questions") or [])[:10]]}
    if not out["overall_comment"] and not out["per_person"]:
        raise ReportAIError("模型输出缺少可用内容")
    return out


def _estimate_tokens(*texts) -> int:
    """无 usage 时的粗估（CJK 约 3 字符/token）——只用于留痕，不参与任何判断。"""
    return max(1, sum(len(t or "") for t in texts) // 3)


STALE_MINUTES = 15      # pending/running 超过这个时长视为卡死（线程被重启掐掉等）


def is_stale(a) -> bool:
    """pending/running 是否已卡死（超时）。"""
    if a is None or a.status not in ("pending", "running"):
        return False
    created = a.created_at
    if created is None:
        return False
    return created < datetime.utcnow() - timedelta(minutes=STALE_MINUTES)


def can_retry(a) -> bool:
    """能否重试：失败、或卡死的 pending/running；done 与在跑中的都不行。"""
    if a is None:
        return False
    return a.status == "failed" or is_stale(a)


def run_analysis(analysis_id: int) -> None:
    """后台执行：running → 逐语言调模型 → done/failed（线程内自建 DB 会话）。"""
    import app.db as appdb
    from app.services import ai_chat, daily_report
    db = appdb.SessionLocal()
    a = None
    try:
        a = db.get(StaffReportAnalysis, analysis_id)
        if a is None:
            return
        a.status = "running"
        db.commit()
        res = daily_report.compare(db, a.period_start, a.period_end)
        by_lang, errors, tokens, model = {}, [], 0, _s().ai_model
        for lang in report_langs():
            prompt = build_prompt(res, lang)
            try:
                if not ai_chat.configured():
                    raise ReportAIError("AI 未配置")
                text, usage = ai_chat.chat(prompt, timeout=_s().report_ai_timeout,
                                           retries=1, max_tokens=_s().report_ai_max_tokens,
                                           return_usage=True)
                data = ai_chat.extract_json(text)
                if data is None:
                    # 把原始输出片段留在错误里：多半是 max_tokens 不够导致 JSON 被截断
                    raise ReportAIError("模型输出不是合法 JSON（片段：%s）"
                                        % (text or "")[:200].replace("\n", " "))
                by_lang[lang] = _normalize(data, res)
                tokens += int((usage or {}).get("total_tokens") or 0) or _estimate_tokens(prompt, text)
            except Exception as e:  # noqa: BLE001  单语言失败不影响另一种
                errors.append("%s: %s" % (lang, e))
        # 指纹必须保留（start_analysis 已写入）：它决定"同数据复用"是否生效。
        # 直接拿 res["summary"] 覆盖会把指纹冲成空 → 每次都重新烧 token。
        fp = (a.summary or {}).get("fingerprint") or data_fingerprint(res)
        a.summary = dict(res["summary"], fingerprint=fp)
        a.payload = {"by_lang": by_lang, "failed_langs": [e.split(":")[0] for e in errors],
                     "start": str(res["start"]), "end": str(res["end"])}
        a.ai_model = model
        a.ai_tokens = tokens
        a.ai_error = "; ".join(errors)[:2000] or None
        a.status = "done" if by_lang else "failed"
        a.finished_at = datetime.utcnow()
        db.commit()
        # 落物化表（员工端只读它；失败不影响报告本身）
        try:
            materialize(db, analysis_id, res)
        except Exception:  # noqa: BLE001  best-effort
            db.rollback()
    except Exception as e:  # noqa: BLE001  **兜底：任何意外都不允许把状态卡在 running**
        db.rollback()
        try:
            row = db.get(StaffReportAnalysis, analysis_id)
            if row is not None:
                row.status = "failed"
                row.ai_error = ("生成过程异常：%s" % e)[:2000]
                row.finished_at = datetime.utcnow()
                db.commit()
        except Exception:  # noqa: BLE001  连兜底都失败就只能放弃
            db.rollback()
    finally:
        db.close()


def start_analysis(db, user, start, end):
    """建 pending 记录并起后台线程；同数据已有 done 报告 → 直接复用。

    返回 (analysis|None, msg)。
    """
    from app.services import daily_report
    if not report_ai_enabled():
        return None, "AI 报告已关闭（VISIT_REPORT_AI=0）"
    res = daily_report.compare(db, start, end)
    if not res["summary"]["checkin_cnt"] and not res["summary"]["formal_cnt"]:
        return None, "该区间没有可对比的数据"
    fp = data_fingerprint(res)
    # 同区间已有"正在生成"的记录 → 复用它（避免重复线程 + 重复烧 token）
    for p in (db.query(StaffReportAnalysis)
              .filter(StaffReportAnalysis.period_start == start,
                      StaffReportAnalysis.period_end == end,
                      StaffReportAnalysis.status.in_(("pending", "running")))
              .order_by(StaffReportAnalysis.id.desc()).all()):
        if not is_stale(p):
            return p, "同区间报告正在生成中，未重复触发"
    prev = (db.query(StaffReportAnalysis)
            .filter(StaffReportAnalysis.period_start == start,
                    StaffReportAnalysis.period_end == end,
                    StaffReportAnalysis.status == "done")
            .order_by(StaffReportAnalysis.id.desc()).all())
    need = set(report_langs())
    for p in prev:
        have = set(((p.payload or {}).get("by_lang") or {}).keys())
        # 必须"语言的都在"才算完整：否则中文成功、日文失败时，半成品会被永久复用
        if (p.summary or {}).get("fingerprint") == fp and need <= have:
            return p, "已复用同数据的既有报告（未重复消耗）"
    a = StaffReportAnalysis(period_start=start, period_end=end, status="pending",
                            summary=dict(res["summary"], fingerprint=fp),
                            payload={}, created_by=getattr(user, "id", None))
    db.add(a)
    db.commit()
    threading.Thread(target=run_analysis, args=(a.id,), daemon=True).start()
    return a, "已开始生成"


def retry(db, analysis_id: int):
    """失败/卡死报告重跑（同一条记录）。**在跑中的不允许重复触发**。"""
    a = db.get(StaffReportAnalysis, analysis_id)
    if a is None:
        return None, "报告不存在"
    if not report_ai_enabled():
        return a, "AI 报告已关闭（VISIT_REPORT_AI=0）"
    if not can_retry(a):
        return a, ("报告已完成，无需重试" if a.status == "done"
                   else "报告正在生成中，请稍候")
    a.status = "pending"
    a.ai_error = None
    db.commit()
    threading.Thread(target=run_analysis, args=(a.id,), daemon=True).start()
    return a, "已重新开始生成"


def latest_done(db, start=None, end=None):
    q = db.query(StaffReportAnalysis).filter(StaffReportAnalysis.status == "done")
    if start:
        q = q.filter(StaffReportAnalysis.period_start == start)
    if end:
        q = q.filter(StaffReportAnalysis.period_end == end)
    return q.order_by(StaffReportAnalysis.id.desc()).first()


def materialize(db, analysis_id: int, res: dict) -> int:
    """把对比结果落成物化表（幂等：先删后插）。返回写入行数。

    员工端核对页从此**只读物化表**，不再实时跑 compare()。
    """
    from sqlalchemy.exc import IntegrityError

    from app.models import StaffReportCompareDay as _D
    from app.models import StaffReportComparePerson as _P
    db.query(_D).filter(_D.analysis_id == analysis_id).delete(
        synchronize_session=False)
    db.query(_P).filter(_P.analysis_id == analysis_id).delete(
        synchronize_session=False)
    for p in res["persons"]:
        db.add(_P(analysis_id=analysis_id, person_code=p["person_code"],
                  name=p["name"] or "", sys_p1=p["sys_p1"], sys_p2=p["sys_p2"],
                  sys_total=p["sys_total"], rep_p1=p["rep_p1"],
                  rep_p2=p["rep_p2"], rep_total=p["rep_total"], d1=p["d1"],
                  d2=p["d2"], d_total=p["d_total"], acc=p["acc"],
                  days_filled=p["days_filled"],
                  days_system=p["days_system"], days_both=p["days_both"],
                  gaps=p["gaps"], abs_dt=p["abs_dt"]))
    for r in res["daily"]:
        db.add(_D(analysis_id=analysis_id, person_code=r["person_code"],
                  ref_date=r["date"], kind=r["kind"], sys_p1=r["sys_p1"],
                  sys_p2=r["sys_p2"], sys_total=r["sys_total"],
                  rep_p1=r["rep_p1"], rep_p2=r["rep_p2"],
                  rep_total=r["rep_total"], d1=r["d1"], d2=r["d2"], dt=r["dt"]))
    try:
        db.commit()
    except IntegrityError:      # 并发重复落表 → 不是错误（唯一键就是干这个的）
        db.rollback()
        return 0
    return len(res["persons"]) + len(res["daily"])


def refresh_for_date(db, ref_date) -> int:
    """**数据变化后刷新物化行**：覆盖该日期的已完成报告重算并落表。

    触发点：员工填报/修改（`daily_report`）、系统侧统计重算（`perf.sync_month_stats`）。
    不刷新的话，员工端核对页会一直显示报告生成那一刻的旧数字（与管理端实时对比不一致）。
    返回刷新了几份报告。
    """
    from app.services import daily_report
    rows = (db.query(StaffReportAnalysis)
            .filter(StaffReportAnalysis.status == "done",
                    StaffReportAnalysis.period_start <= ref_date,
                    StaffReportAnalysis.period_end >= ref_date).all())
    for a in rows:
        res = daily_report.compare(db, a.period_start, a.period_end)
        if res["persons"]:
            materialize(db, a.id, res)
    return len(rows)


def _person_row(p) -> dict:
    return {"person_code": p.person_code, "name": p.name, "sys_p1": p.sys_p1,
            "sys_p2": p.sys_p2, "sys_total": p.sys_total, "rep_p1": p.rep_p1,
            "rep_p2": p.rep_p2, "rep_total": p.rep_total, "d1": p.d1,
            "d2": p.d2, "d_total": p.d_total, "acc": p.acc,
            "days_filled": p.days_filled,
            "days_system": p.days_system, "days_both": p.days_both,
            "gaps": p.gaps, "abs_dt": p.abs_dt}


def employee_snapshot(db, analysis, person_code: str) -> dict:
    """员工端**只读**物化结果：{person, days}。

    物化行在报告生成时写入（`run_analysis`）或由 `scripts/backfill_compare.py` 补写；
    这里**绝不写库**（读接口不能有副作用，并发下会撞唯一键）。
    """
    from app.models import StaffReportCompareDay as _D
    from app.models import StaffReportComparePerson as _P
    if analysis is None:
        return {"person": None, "days": []}
    p = (db.query(_P).filter(_P.analysis_id == analysis.id,
                             _P.person_code == person_code).first())
    days = (db.query(_D).filter(_D.analysis_id == analysis.id,
                                _D.person_code == person_code)
            .order_by(_D.ref_date).all())
    return {"person": _person_row(p) if p is not None else None,
            "days": [{"date": d.ref_date, "kind": d.kind, "sys_total": d.sys_total,
                      "sys_p1": d.sys_p1, "sys_p2": d.sys_p2,
                      "rep_total": d.rep_total, "rep_p1": d.rep_p1,
                      "rep_p2": d.rep_p2, "dt": d.dt} for d in days]}


def file_coverage(db):
    """已导入文件在正式表里的日期覆盖范围 (min, max)；没有任何正式记录 → (None, None)。

    这是"离线数据"的边界：区间超出它就没有系统侧数字可比。
    """
    from sqlalchemy import func

    from app.models import FormalRecord
    row = (db.query(func.min(FormalRecord.japan_date),
                    func.max(FormalRecord.japan_date)).first())
    return (row[0], row[1]) if row and row[0] and row[1] else (None, None)


def available_periods(db, person_code: str = "", *, limit: int = 12) -> list:
    """员工端可看的核对区间：同区间取最新一份，且**整段落在已导入文件的覆盖范围内**。

    数据源 = 物化表（`staff_report_compare_person`，报告生成时写入）。
    **只读**：老报告若没有物化行，用 `scripts/backfill_compare.py` 补（不在读路径里写库）。
    """
    from app.models import StaffReportAnalysis as _A
    from app.models import StaffReportComparePerson as _P
    cov_start, cov_end = file_coverage(db)
    if cov_start is None:
        return []
    q = (db.query(_A).join(_P, _P.analysis_id == _A.id)
         .filter(_A.status == "done"))
    if person_code:
        q = q.filter(_P.person_code == person_code)
    seen, out = set(), []
    for a in q.order_by(_A.id.desc()).all():
        if a.period_start < cov_start or a.period_end > cov_end:
            continue                      # 跨出文件覆盖范围 → 不列（没有离线数据可比）
        key = (a.period_start, a.period_end)
        if key in seen:                   # 同区间重复生成过 → 只留最新
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= limit:
            break
    return out


def latest_for(db, start, end):
    """该区间最近一条报告（任意状态，用于页面展示进度/失败）。"""
    return (db.query(StaffReportAnalysis)
            .filter(StaffReportAnalysis.period_start == start,
                    StaffReportAnalysis.period_end == end)
            .order_by(StaffReportAnalysis.id.desc()).first())


def import_period(db, import_id: int):
    """**该文件**在正式表里的日期范围；没有正式记录时返回 (None, None)。

    刻意不回退全局覆盖范围：入表 0 条的文件不该触发生成报告。
    """
    from sqlalchemy import func

    from app.models import FormalRecord
    row = (db.query(func.min(FormalRecord.japan_date),
                    func.max(FormalRecord.japan_date))
           .filter(FormalRecord.import_id == import_id).first())
    if row and row[0] and row[1]:
        return row[0], row[1]
    return None, None


def auto_for_import(db, import_id: int) -> str:
    """**文件上传并入表后**调用（`flow.auto_finalize_pipeline`）→ 自动生成该区间的对比报告。

    理由（2026-09-28 用户明确）：自报在时间上**先于**系统数据（员工当天就报，
    文件是事后才上传），所以对比分析的天然触发点就是"文件入表完成"。
    best-effort：失败不影响上传；同数据已有完整报告则直接复用，不重复消耗。
    """
    if not report_ai_enabled():
        return "AI 报告已关闭（VISIT_REPORT_AI=0）"
    from app.services import ai_chat
    if not ai_chat.configured():
        # AI 未配置：不要建空报告、更不要起后台线程（否则测试/无 key 环境会起无用线程）
        return "AI 未配置，跳过"
    start, end = import_period(db, import_id)
    if not start or not end:
        # 该文件没有进正式表（全被判重/全失败）→ 不生成；**不回退到全局范围**，
        # 否则会拿整个库的数据范围生成一份没人要的报告（白烧 token）。
        return "该文件没有正式记录，跳过"
    _, msg = start_analysis(db, None, start, end)
    return "%s ~ %s：%s" % (start, end, msg)
