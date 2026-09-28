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
from datetime import datetime

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


def run_analysis(analysis_id: int) -> None:
    """后台执行：running → 逐语言调模型 → done/failed（线程内自建 DB 会话）。"""
    import app.db as appdb
    from app.services import ai_chat, daily_report
    db = appdb.SessionLocal()
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
    """失败报告重跑（同一条记录，状态回 pending）。"""
    a = db.get(StaffReportAnalysis, analysis_id)
    if a is None:
        return None, "报告不存在"
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


def latest_for(db, start, end):
    """该区间最近一条报告（任意状态，用于页面展示进度/失败）。"""
    return (db.query(StaffReportAnalysis)
            .filter(StaffReportAnalysis.period_start == start,
                    StaffReportAnalysis.period_end == end)
            .order_by(StaffReportAnalysis.id.desc()).first())


def person_block(analysis, person_code: str, lang: str = "") -> dict:
    """员工端取**本人**段落：只含 comment / off_days，**不含 questions**（管理工具）。"""
    if analysis is None:
        return {}
    by = (analysis.payload or {}).get("by_lang") or {}
    langs = [lang] if lang else list(by.keys())
    out = {}
    for lg in langs:
        pp = ((by.get(lg) or {}).get("per_person") or {}).get(person_code)
        if pp:
            out[lg] = {"comment": pp.get("comment") or "",
                       "off_days": pp.get("off_days") or []}
    return out
