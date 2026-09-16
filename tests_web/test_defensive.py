# -*- coding: utf-8 -*-
"""防御性校验回归测试（QA 走查 3 处缺陷）：
1. POST /files/{fid}/finalize：fid 不存在 → 400（不再 303 报“已入正式表 0 条”）；
2. POST /files/{fid}/appeals/{appeal_id}/resolve：申诉不属于该文件 → 404 且状态不变；
3. POST /month/rebuild：month 非法（"" / "2026-13" / "202613"）→ 303 + err 提示，不 500。
"""
from urllib.parse import unquote

import app.db as appdb
from app.models import AppealRecord, FormalRecord, RawRecord, User
from app.services import v3_flow
from tests_web.test_v3_flow import (_csrf_of, _seed_admin, _up, db_fresh)


def _login_admin(client):
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)


# ---------------- 缺陷 1：finalize 不存在 fid ----------------
def test_finalize_nonexistent_file_rejected(client, tmp_path):
    """不存在的 fid → 拒绝（400），不得返回 303 成功入表提示、不得落正式表。"""
    _seed_admin(client)
    _login_admin(client)
    csrf = _csrf_of(client, "/confirm-admin")
    r = client.post("/files/999999/finalize", data={"csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 400                 # 非 303（不再假成功）
    assert r.status_code not in (301, 302, 303)
    assert "文件不存在" in unquote(r.text)
    db = appdb.SessionLocal()
    assert db.query(FormalRecord).count() == 0
    db.close()


def test_finalize_existing_file_still_works(client, tmp_path):
    """回归：存在的 fid 仍正常入正式表（校验未误伤正常路径）。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "def1.xlsx", [
        ["S-A", "甲店", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    v3_flow.process_import(db_fresh(), imp.id)
    _login_admin(client)
    csrf = _csrf_of(client, "/confirm-admin")
    r = client.post(f"/files/{imp.id}/finalize", data={"csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.query(FormalRecord).filter(
        FormalRecord.import_id == imp.id).count() == 1
    db.close()


# ---------------- 缺陷 2：申诉归属校验 ----------------
def test_resolve_appeal_wrong_file_rejected(client, tmp_path):
    """fid 与申诉所属文件不一致 → 404，且申诉与 raw 状态均不改变；
    用正确 fid 才处理成功（归属校验生效且不误伤）。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    # 文件A：仅一条有效
    fa = _up(db, admin, "own_a.xlsx", [
        ["S-A", "店A", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    # 文件B：同店跨日 → 8/2 那条 master_late（可申诉，属于文件B）
    fb = _up(db, admin, "own_b.xlsx", [
        ["S-B", "店B", "", "2026-08-01 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
        ["S-B", "店B", "", "2026-08-02 09:00:00", "甲(111)", "R3",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    v3_flow.process_import(db_fresh(), fa.id)
    v3_flow.process_import(db_fresh(), fb.id)
    db = appdb.SessionLocal()
    late = db.query(RawRecord).filter(RawRecord.import_id == fb.id,
                                      RawRecord.clean_status == "master_late").one()
    v3_flow.create_appeal(db, late.id, "111", "确实又去了")
    ap = db.query(AppealRecord).filter(
        AppealRecord.raw_record_id == late.id).one()
    late_id, ap_id = late.id, ap.id
    assert ap.status == "pending" and late.clean_status == "master_late"
    db.close()
    _login_admin(client)
    csrf = _csrf_of(client, "/confirm-admin")
    # 恶意/错误传 fid：用文件A 的 id 处理文件B 的申诉 → 404，无副作用
    r = client.post(f"/files/{fa.id}/appeals/{ap_id}/resolve",
                    data={"csrf_token": csrf, "decision": "accept"},
                    follow_redirects=False)
    assert r.status_code == 404
    assert "申诉不属于该文件" in unquote(r.text)
    db = appdb.SessionLocal()
    ap2 = db.get(AppealRecord, ap_id)
    rr2 = db.get(RawRecord, late_id)
    assert ap2.status == "pending" and ap2.handled_by is None
    assert rr2.clean_status == "master_late"
    assert rr2.confirm_state == "disputed"
    db.close()
    # 正确 fid（文件B）→ 正常处理
    r = client.post(f"/files/{fb.id}/appeals/{ap_id}/resolve",
                    data={"csrf_token": csrf, "decision": "accept"},
                    follow_redirects=False)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.get(AppealRecord, ap_id).status == "approved"
    rr3 = db.get(RawRecord, late_id)
    assert rr3.clean_status == "valid" and rr3.confirm_state == "approved"
    db.close()


def test_resolve_appeal_missing_still_404(client, tmp_path):
    """回归：申诉不存在仍 404（存在性校验保留）。"""
    _seed_admin(client)
    _login_admin(client)
    csrf = _csrf_of(client, "/confirm-admin")
    r = client.post("/files/1/appeals/888888/resolve",
                    data={"csrf_token": csrf, "decision": "accept"},
                    follow_redirects=False)
    assert r.status_code == 404


# ---------------- 缺陷 3：rebuild month 格式校验 ----------------
def test_month_rebuild_invalid_month_format(client, tmp_path):
    """非法月一律 303 + err 提示，绝不 500、也绝不静默假成功：
    空串 / 2026-13 / 202613 / 2026/08 / 中文 / 年份越界(0000-01, 9999-12) /
    尾随换行(2026-08\\n) / Unicode 数字月(٢٠٢٦-08) / 全角年份(２０２６-08)。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "def_m.xlsx", [
        ["S-M", "店M", "", "2026-08-06 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    v3_flow.process_import(db_fresh(), imp.id)
    _login_admin(client)
    csrf = _csrf_of(client, "/month")
    for bad in ("", "2026-13", "202613", "2026/08", "八月",
                "0000-01", "0000-12", "9999-12", "2026-08\n", "٢٠٢٦-08",
                "\uff12\uff10\uff12\uff16-08"):
        r = client.post("/month/rebuild",
                        data={"month": bad, "csrf_token": csrf},
                        follow_redirects=False)
        assert r.status_code == 303, f"{bad!r} → {r.status_code}"
        loc = unquote(r.headers.get("location", ""))
        assert "err=" in loc and "月份格式不正确" in loc, f"{bad!r} → {loc}"
        # 跟随跳转后的页面确实显示 err 提示（与现有 err 风格一致）
        page = client.get(r.headers["location"])
        assert page.status_code == 200
        assert "月份格式不正确（应为 YYYY-MM）" in page.text
    # 全程不得改动数据（非法月被入口守卫拦下，未进入重建）
    db = appdb.SessionLocal()
    assert db.query(FormalRecord).count() == 0
    assert db.query(RawRecord).filter(
        RawRecord.clean_status == "valid").count() == 1
    db.close()


def test_month_rebuild_fullwidth_month_does_not_wipe_formal(client, tmp_path):
    """全角年份「２０２６-08」（日语 IME 下粘贴很现实）必须被拒，
    且**整月正式表不得被清空**——这是 \\d(Unicode 语义) 放行时的破坏性路径：
    int() 解析出 2026 而 LIKE 匹配全角串 → 先删后填 0 行。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "fw.xlsx", [
        ["S-F1", "店F1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-F2", "店F2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],
    ], tmp_path)
    db.close()
    v3_flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    # 先正常入表：8 月正式表 2 行（作为「不得被清空」的基线）
    assert v3_flow.finalize_import(db, imp.id)["added"] == 2
    before_ids = {f.raw_record_id for f in db.query(FormalRecord).all()}
    assert len(before_ids) == 2
    db.close()
    _login_admin(client)
    csrf = _csrf_of(client, "/month")
    r = client.post("/month/rebuild",
                    data={"month": "\uff12\uff10\uff12\uff16-08",
                          "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "err=" in loc and "月份格式不正确" in loc, loc
    db = appdb.SessionLocal()
    after_ids = {f.raw_record_id for f in db.query(FormalRecord).all()}
    assert after_ids == before_ids, "全角月请求清空了/改动了正式表"
    assert db.query(RawRecord).filter(
        RawRecord.clean_status == "valid").count() == 2
    db.close()
    # 合法月仍照常重算（数据口径不变）
    r = client.post("/month/rebuild",
                    data={"month": "2026-08", "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303 and "err=" not in unquote(
        r.headers.get("location", ""))
    db = appdb.SessionLocal()
    assert {f.raw_record_id for f in db.query(FormalRecord).all()} == before_ids
    db.close()


def test_rebuild_month_canonical_key_even_if_guard_bypassed(client, tmp_path,
                                                            monkeypatch):
    """根治层：即使入口守卫被绕过（模拟 \\d 语义失效/守卫被删），
    日期区间与 LIKE 模式同源于一次解析出来的规范月串 ym，
    绝不允许「区间=2026-08、LIKE=全角串」→ 删空整月正式表。"""
    import re as _re_mod
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "fw2.xlsx", [
        ["S-G1", "店G1", "", "2026-08-03 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-G2", "店G2", "", "2026-08-04 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    v3_flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    assert v3_flow.finalize_import(db, imp.id)["added"] == 2
    # 绕过格式守卫（int("２０２６") == 2026，年份范围检查天然通过）
    monkeypatch.setattr(_re_mod, "fullmatch", lambda *a, **k: object())
    res = v3_flow.rebuild_month(db, "\uff12\uff10\uff12\uff16-08")
    assert res["ok"] is True, res
    # 解析后 ym = "2026-08"：删/建范围一致 → 2 行仍在（若 LIKE 用原始全角串则会是 0 行）
    assert res["formal_after"] == 2, res
    assert res["files"] == [imp.id]
    assert db.query(FormalRecord).count() == 2
    db.close()


def test_rebuild_month_service_guard_rejects_bad_month(client, tmp_path):
    """服务层为月份校验唯一关口：直接调 v3_flow.rebuild_month 亦须拒绝
    （含年份越界/尾随换行/Unicode 数字），返回 ok=False + 统一 err 文案。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    for bad in ("", "2026-13", "0000-01", "9999-12", "2026-08\n",
                "٢٠٢٦-08"):
        res = v3_flow.rebuild_month(db, bad)
        assert res["ok"] is False, f"{bad!r} → {res}"
        assert res["msg"] == "月份格式不正确（应为 YYYY-MM）", f"{bad!r}"
    # 合法月仍放行到正常分支（无数据 → ok=True，0 文件）
    ok = v3_flow.rebuild_month(db, "2026-08")
    assert ok["ok"] is True and ok["files"] == []
    # 年份边界内（9998-12 需 date(9999,1,1)）不越界
    edge = v3_flow.rebuild_month(db, "9998-12")
    assert edge["ok"] is True, edge
    db.close()


def test_month_rebuild_valid_month_still_works(client, tmp_path):
    """回归：合法 YYYY-MM 仍照常重算（校验未误伤）。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "def_m2.xlsx", [
        ["S-N", "店N", "", "2026-08-07 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    v3_flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    v3_flow.finalize_import(db, imp.id)
    db.close()
    _login_admin(client)
    csrf = _csrf_of(client, "/month")
    r = client.post("/month/rebuild",
                    data={"month": "2026-08", "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "msg=已重算" in loc
    assert "err=" not in loc


def test_month_rebuild_pending_appeal_err_branch_still_works(client, tmp_path):
    """回归：err 分支合并后仍正常——当月有 pending 申诉 → 303 + err（该月仍有
    N 条申诉未处理），数据不变（服务层业务门槛未被格式守卫影响）。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "def_m3.xlsx", [
        ["S-P", "店P", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],          # valid
        ["S-P", "店P", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],          # master_late → 申诉 pending
    ], tmp_path)
    db.close()
    v3_flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    late = db.query(RawRecord).filter(RawRecord.clean_status == "master_late").one()
    v3_flow.create_appeal(db, late.id, "111", "确实又去了")
    late_id = late.id
    db.close()
    _login_admin(client)
    csrf = _csrf_of(client, "/month")
    r = client.post("/month/rebuild",
                    data={"month": "2026-08", "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    loc = unquote(r.headers.get("location", ""))
    assert "err=" in loc and "该月仍有 1 条申诉未处理" in loc, loc
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "申诉未处理" in page.text
    db = appdb.SessionLocal()
    assert db.get(RawRecord, late_id).clean_status == "master_late"
    assert db.query(FormalRecord).count() == 0     # 未重建、未误入表
    db.close()

