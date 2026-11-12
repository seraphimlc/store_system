# -*- coding: utf-8 -*-
"""看板：数据质量口径（SQL 聚合）+ AI 截断不许当答案。

两个都是 2026-10-17 线上问题：
- `quality_stats` 原来 `db.query(RawRecord).all()`（整表含大 JSON 水合）→ 单次
  **10.9 秒**，看板一页调多次 → **整页 26 秒**；
- 「总体分析」硬编码 `max_tokens=2500`，推理模型思考吃光预算 →
  `content` 为空 + `finish_reason=length` → `ai_chat` 把**没写完的思维链**当答案
  返回并永久缓存 → 页面长期显示英文推理稿。
"""
import json
import types
from datetime import datetime

import app.db as appdb
from app.models import RawRecord


_N = [0]


def _raw(db, imp_id, modified, status):
    """插一条原始记录（excel_row 递增，避开 (import,sheet,row) 唯一键）。"""
    _N[0] += 1
    db.add(RawRecord(import_id=imp_id, sheet_name="S1", excel_row=_N[0],
                     store_id_raw="ST", store_name_local_raw="店",
                     modified_raw=modified, visible_raw="YES",
                     original_row={"a": 1}, clean_status=status))
    db.commit()


def test_quality_stats_matches_original_python_semantics(client):
    """SQL 版必须与原「全表拉进 Python 再过滤」逐项一致（含 NULL / 跨月 / 未判定）。"""
    from app.services import dashboard as D

    db = appdb.SessionLocal()
    rows = [
        # 本月：4 条 valid（其中 2 条同一天）、1 条重复、1 条 未判定(NULL)
        ("2026-10-01 09:00:00", "valid"),
        ("2026-10-01 15:00:00", "valid"),
        ("2026-10-02 09:00:00", "valid"),
        ("2026-10-05 09:00:00", "valid"),
        ("2026-10-03 09:00:00", "cross_file_dup"),
        ("2026-10-04 09:00:00", None),
        # 别的月份：必须被排除
        ("2026-09-30 09:00:00", "valid"),
        ("2026-11-01 09:00:00", "valid"),
        # modified 为 NULL：必须被排除（原实现 (None or "")[:7] != month）
        (None, "valid"),
    ]
    for mod, st in rows:
        _raw(db, 1, mod, st)

    got = D.quality_stats(db, "2026-10")
    assert got["total"] == 6
    assert got["by_status"] == {"valid": 4, "cross_file_dup": 1, "?": 1}
    assert got["valid_days"] == 3          # 10-01/02/05（01 两条同一天只算一天）
    # 边界：没数据的月份 / 空 month
    assert D.quality_stats(db, "2026-08")["total"] == 0
    assert D.quality_stats(db, "2026-08")["valid_days"] == 0
    assert D.quality_stats(db, "")["total"] == 0
    # 与"口径参照"（老实现的写法）逐项对齐
    ref_cnt, ref_days = {}, set()
    for r in db.query(RawRecord).all():
        if (r.modified_raw or "")[:7] != "2026-10":
            continue
        ref_cnt[r.clean_status or "?"] = ref_cnt.get(r.clean_status or "?", 0) + 1
        if r.clean_status == "valid":
            ref_days.add((r.modified_raw or "")[:10])
    assert got["by_status"] == ref_cnt
    assert got["valid_days"] == len(ref_days)
    db.close()


def test_quality_stats_does_not_hydrate_all_rows(client, monkeypatch):
    """结构守门：不能再把整表 ORM 对象拉进 Python（那是 11 秒的原因）。"""
    from app.services import dashboard as D

    db = appdb.SessionLocal()
    for i in range(20):
        _raw(db, 1, "2026-10-0%d 09:00:00" % (i % 9 + 1), "valid")

    # 守门方式：抓真实执行的 SQL。11 秒的根因是**把 original_row（大 JSON）
    # 连同所有列一起水合 5 万行** —— 所以只要 SQL 里不出现它就安全。
    from sqlalchemy import event
    engine = db.get_bind()
    stmts = []

    def _rec(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        stmts.append(statement)

    event.listen(engine, "before_cursor_execute", _rec)
    try:
        got = D.quality_stats(db, "2026-10")
    finally:
        event.remove(engine, "before_cursor_execute", _rec)
    assert got["total"] == 20
    assert stmts, "没抓到 SQL，守门测试本身失效了"
    assert not any("original_row" in x for x in stmts), (
        "quality_stats 又把整表（含 original_row 大 JSON）拉出来了 —— 这是 11 秒的根因")
    assert all("raw_records" in x for x in stmts)
    assert any("group by" in x.lower() for x in stmts), (
        "分布统计应该在 SQL 端 group by，而不是拉回 Python 里数")
    assert any("count" in x.lower() for x in stmts)
    db.close()


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def _fake_client(payloads, calls):
    """每次 post 依次返回 payloads 里的一项，并记录 max_tokens。"""
    class _C:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):   # noqa: A002
            calls.append(json.get("max_tokens"))
            return _FakeResp(payloads[min(len(calls) - 1, len(payloads) - 1)])
    return _C


def _payload(finish, content=None, reasoning=None):
    msg = {"role": "assistant"}
    if content is not None:
        msg["content"] = content
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"finish_reason": finish, "message": msg}],
            "usage": {"total_tokens": 1}}


def test_chat_rejects_truncated_output(monkeypatch):
    """`finish_reason=length` + content 为空 → **绝不能**把思维链当答案返回。"""
    import httpx

    from app.services import ai_chat
    monkeypatch.setenv("AI_API_KEY", "k")
    monkeypatch.setenv("AI_BASE_URL", "https://example.invalid/v1")
    calls = []
    # 每次都是"思考吃满预算、content 空"（就是线上那次）
    monkeypatch.setattr(httpx, "Client", _fake_client(
        [_payload("length", reasoning="We need answer in Chinese... let's sum")],
        calls))
    try:
        ai_chat.chat("x", max_tokens=2500, retries=0)
    except RuntimeError as e:
        assert "截断" in str(e)
    else:
        raise AssertionError("截断的思维链被当成答案返回了（本次线上事故）")
    # 必须自动放大预算重试过（2500 → 5000 → 10000 → 20000 会停在上限附近）
    assert len(calls) >= 2 and max(calls) > 2500, calls
    assert max(calls) <= 16000, calls


def test_chat_escalates_then_succeeds(monkeypatch):
    """第一次截断、放大预算后正常收尾 → 返回真正的答案。"""
    import httpx

    from app.services import ai_chat
    monkeypatch.setenv("AI_API_KEY", "k")
    monkeypatch.setenv("AI_BASE_URL", "https://example.invalid/v1")
    calls = []
    monkeypatch.setattr(httpx, "Client", _fake_client(
        [_payload("length", reasoning="thinking..."),
         _payload("stop", content="**结论**：正常了")],
        calls))
    out = ai_chat.chat("x", max_tokens=2500, retries=0)
    assert out == "**结论**：正常了"
    assert calls[0] == 2500 and calls[1] > 2500


def test_chat_still_falls_back_to_reasoning_only_when_finished(monkeypatch):
    """模型**正常收尾**却把答案写在 reasoning_content → 仍要回退取它（JSON 调用方依赖）。"""
    import httpx

    from app.services import ai_chat
    monkeypatch.setenv("AI_API_KEY", "k")
    monkeypatch.setenv("AI_BASE_URL", "https://example.invalid/v1")
    calls = []
    monkeypatch.setattr(httpx, "Client", _fake_client(
        [_payload("stop", content="", reasoning='{"a": 1}')], calls))
    assert ai_chat.chat("x") == '{"a": 1}'
    assert ai_chat.extract_json('{"a": 1}') == {"a": 1}


def test_company_fingerprint_changes_with_prompt_version(client, monkeypatch):
    """提示词版本一改，指纹必须变 → 旧缓存（可能是垃圾）自动作废。"""
    from app.services import dashboard as D

    facts = "2026-10: 员工56人 总点数22749"
    fp1 = D.company_fingerprint(facts)
    monkeypatch.setattr(D, "COMPANY_PROMPT_VERSION", D.COMPANY_PROMPT_VERSION + ".x")
    assert D.company_fingerprint(facts) != fp1
    assert D.company_fingerprint(facts + " 多一行") != fp1
    assert D.company_fingerprint(facts) == D.company_fingerprint(facts)
    # 提示词里必须明确禁止输出思考过程（否则推理稿又会被贴到页面上）
    assert "不要输出任何思考过程" in D.COMPANY_PROMPT
    assert "{facts}" in D.COMPANY_PROMPT


def test_company_prompt_has_no_stray_braces(client):
    """提示词要用 .format(facts=...) —— 除 {facts} 外不能有别的花括号（会 KeyError）。"""
    from app.services import dashboard as D

    assert D.COMPANY_PROMPT.format(facts="F") .count("F") == 1


def test_no_ai_call_site_passes_tiny_max_tokens(client):
    """⚠️ 全局守门：不许再给推理模型传**小 max_tokens**。

    `deepseek-v4.1-flash` 这类模型的思考**同样占输出预算**（实测：一句"1+1=?"
    就花 62 个 reasoning token）。给小了 → 思考吃满 → `content` 为空 +
    `finish_reason=length` → 拿不到答案，还可能把思维链当结果。
    2026-10-17 两处踩到（看板总体分析 2500 / 员工分析 1800）。阈值 4000：
    要点式答案留足余量，也不至于浪费。

    用 AST 取**真实的调用关键字**（不看注释/文档串，否则说明文字里的数字会误报）。
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    bad = []
    for p in sorted((root / "app").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                v = kw.value
                if (kw.arg == "max_tokens"
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, int)
                        and v.value < 4000):
                    bad.append(f"{p.relative_to(root)}:{v.lineno}  "
                               f"max_tokens={v.value}")
    assert not bad, (
        "这些 AI 调用传了过小的 max_tokens（思考会吃满预算、答案拿不到）：\n"
        + "\n".join(bad))
