# -*- coding: utf-8 -*-
"""V3 文件处理流程测试：店铺主从档 + 判定 + 有效自动入绩效 + 绩效申诉 + 入正式表。"""
import pytest
import re

import app.db as appdb
from app.auth import hash_password
from app.models import (AppealRecord, FormalRecord, ImportFile, Person,
                        RawRecord, StoreEntity, User)
from app.services.importer import parse_file, upload_and_store
from app.services import flow, period
from tests.helpers import write_workbook

H = ["Store ID", "Store Name-Local", "Store Name-English", "Modified Time",
     "Submitter", "Record ID", "A+ POSM Visible", "Existing A+ POSM", "NEW A+ POSM"]


def _up(db, admin, name, rows, tmp_path):
    p = str(tmp_path / name)
    write_workbook(p, [("STORE_TASK_EXCEL_SHEET ", [H], rows)])
    with open(p, "rb") as f:
        imp = upload_and_store(name, f.read(), admin.id, db)
    parse_file(imp, db)
    return imp


def _seed_admin(client):
    db = appdb.SessionLocal()
    db.add(User(username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True))
    db.commit()
    db.close()


def test_v3_basic_flow(client, tmp_path):
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    # 文件1：甲 巡 两家店（A店首次, B店同日在两文件都出现）
    imp1 = _up(db, admin, "f1.xlsx", [
        ["S-A", "甲店", "", "2026-07-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-B", "乙店", "", "2026-07-01 10:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    r = flow.process_import(db, imp1.id)
    assert r["judge"]["valid"] == 2 and r["appealable"] == 0
    # 店铺都建了主档（第一家）
    ents = db.query(StoreEntity).all()
    assert {e.store_id_raw for e in ents} == {"S-A", "S-B"}
    assert all(e.master_id == e.id for e in ents)
    db.close()


def test_v3_sub_entity_and_crossfile(client, tmp_path):
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    # 文件1：S-OLD（“松屋”）07-01 首次
    imp1 = _up(db, admin, "g1.xlsx", [
        ["S-OLD", "松屋", "", "2026-07-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    flow.process_import(db, imp1.id)
    # 文件2：新编号 S-NEW 店名松屋（同店新编号→从档）+ 同日同店 S-OLD 重复
    imp2 = _up(db, admin, "g2.xlsx", [
        ["S-NEW", "松屋", "", "2026-07-01 11:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
        ["S-OLD", "松屋", "", "2026-07-01 12:00:00", "甲(111)", "R3",
         "YES", "YES", "NO"],
    ], tmp_path)
    r = flow.process_import(db, imp2.id)
    # 同名归并应自动发生：S-NEW 成为 S-OLD 的从档（松屋同一家店）
    old = db.query(StoreEntity).filter(StoreEntity.store_id_raw == "S-OLD").one()
    new = db.query(StoreEntity).filter(StoreEntity.store_id_raw == "S-NEW").one()
    assert new.master_id == old.id and new.master_store_id == "S-OLD"
    raw = {x.store_id_raw: x for x in db.query(RawRecord)
           .filter(RawRecord.import_id == imp2.id).all()}
    # S-NEW 从档 → from_sub（默认已认可滤除，可申诉改判）
    assert raw["S-NEW"].clean_status == "from_sub"
    assert raw["S-NEW"].confirm_state == "auto_ok"
    # S-OLD 同日重复 → cross_file_dup（重复导入自动滤，员工端不出现）
    assert raw["S-OLD"].clean_status == "cross_file_dup"
    assert raw["S-OLD"].confirm_state == "auto_ok"
    assert r["appealable"] == 0      # 全自动，无可申诉
    db.close()


def test_v3_valid_auto_finalize(client, tmp_path):
    """有效数据自动入正式表（无需员工确认）：判定后即可 finalize。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "h1.xlsx", [
        ["S-A", "甲店", "", "2026-07-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-B", "乙店", "", "2026-07-01 10:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    flow.process_import(db, imp.id)
    res = flow.finalize_import(db, imp.id)
    assert res["ok"] is True and res["added"] == 2
    assert db.query(FormalRecord).filter(
        FormalRecord.import_id == imp.id).count() == 2
    db.close()


def _seed_staff(client, code="111", name="甲", login="emp1"):
    db = appdb.SessionLocal()
    db.add(User(username=login, password_hash=hash_password("pw123456"),
                display_name=name, role="staff", person_code=code,
                is_active=True, status="active"))
    db.commit()
    db.close()


def _upload_and_v3(client, tmp_path, rows, fname):
    db = appdb.SessionLocal()
    admin = db.query(User).filter(User.username == "admin").one()
    imp = _up(db, admin, fname, rows, tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    return imp.id


def db_fresh():
    return appdb.SessionLocal()



def _csrf_of(client, path):
    import re
    page = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else ""



def test_perf_salary(client, tmp_path):
    """入正式表后，绩效/工资按68规则正确。"""
    from app.services import perf
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "perf1.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],   # 8/1≥7/9, Deploy NO → 1点
        ["S2", "店2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],  # 8/2≥7/9, Deploy YES → 2点
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    res = flow.finalize_import(db, imp.id)
    assert res["ok"] is True and res["added"] == 2
    mp = perf.month_perf(db, "2026-08")
    me = next(x for x in mp if x["code"] == "111")
    assert me["points"] == 3 and me["amount"] == 750  # 3×250
    s = perf.company_summary(db, "2026-08")
    assert s["total_amount"] == 750
    db.close()
    # 路由渲染：发薪表（新设计：总金额/找平金额/应该付金额）
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    page = client.get("/perf?month=2026-08&period=half1").text
    assert "上半月(1-15) 发薪表" in page
    assert "总金额(円)" in page and "找平金额(円)" in page \
        and "应该付金额(円)" in page
    page2 = client.get("/perf?month=2026-08&period=half2").text
    assert "下半月(16-月末) 发薪表" in page2


def test_v3_blank_row_not_anchor(client, tmp_path):
    """visible_blank 行不占主档最早锚位：同店同日先 blank 后可见 → 可见行须 valid。
    （8 月真实数据回归：曾因空白行占锚把有效巡店误判 cross_file_dup。）"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "blank1.xlsx", [
        ["S-B", "blank店", "", "2026-08-05 11:03:22", "甲(111)", "R1",
         "", "YES", "NO"],
        ["S-B", "blank店", "", "2026-08-05 11:04:37", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    rows = {r.modified_raw: r for r in db.query(RawRecord).filter(
        RawRecord.import_id == imp.id).all()}
    assert rows["2026-08-05 11:03:22"].clean_status == "visible_blank"
    assert rows["2026-08-05 11:04:37"].clean_status == "valid"
    db.close()


def test_v3_same_day_two_visible_only_first_valid(client, tmp_path):
    """同店同日两条可见：仅完整时间戳最早一条 valid，另一条 cross_file_dup。
    （曾按日期比较取错锚，导致晚行 valid、早行被滤。）"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "two1.xlsx", [
        ["S-T", "同日店", "", "2026-08-05 10:11:47", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-T", "同日店", "", "2026-08-05 10:09:55", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    rows = {r.modified_raw: r for r in db.query(RawRecord).filter(
        RawRecord.import_id == imp.id).all()}
    assert rows["2026-08-05 10:09:55"].clean_status == "valid"   # 时间戳更早
    assert rows["2026-08-05 10:11:47"].clean_status == "cross_file_dup"
    db.close()


def test_v3_space_diff_name_not_merged(client, tmp_path):
    """空格差异的店名视为不同店，不得归并主/从档（8 月基准：
    ざくろ銀座店 vs ざくろ 銀座店 是两家店，各自有效）。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "sp1.xlsx", [
        ["S-Z1", "ざくろ銀座店", "", "2026-08-05 10:44:46", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-Z2", "ざくろ 銀座店", "", "2026-08-05 10:49:33", "乙(222)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    rows = {r.store_id_raw: r for r in db.query(RawRecord).filter(
        RawRecord.import_id == imp.id).all()}
    assert rows["S-Z1"].clean_status == "valid"
    assert rows["S-Z2"].clean_status == "valid"
    ents = {e.store_id_raw: e for e in db.query(StoreEntity).all()}
    assert ents["S-Z1"].master_id == ents["S-Z1"].id
    assert ents["S-Z2"].master_id == ents["S-Z2"].id
    db.close()


def test_v3_cross_month_visit_not_suppressed(client, tmp_path):
    """同店跨月两巡（7月已巡、8月再巡）→ 8 月是新结算记录，应 valid。
    （8 月基准口径：判重按 店名×结算月 窗口，跨月不互相压制；
    曾因全局最早锚把 8 月行误判 master_late/from_sub。）"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    # 同一文件含 7 月末与 8 月头两巡（模拟 0726-0805 跨月导出）
    imp = _up(db, admin, "crossm.xlsx", [
        ["S-X", "跨月店", "", "2026-07-29 09:31:24", "甲(111)", "R1",
         "NO", "YES", "NO"],
        ["S-X", "跨月店", "", "2026-08-05 12:31:08", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    rows = {r.modified_raw: r for r in db.query(RawRecord).filter(
        RawRecord.import_id == imp.id).all()}
    assert rows["2026-07-29 09:31:24"].clean_status == "valid"   # 7月结算有效
    assert rows["2026-08-05 12:31:08"].clean_status == "valid"   # 8月新巡不受7月压制
    db.close()


def test_recon_person_points(client, tmp_path):
    """对账：导入人员点数表，与系统正式数据对比出差异。"""
    from app.services import recon
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    # 造系统正式数据：甲 2 店 3 点（8/1,8/2）
    imp = _up(db, admin, "r1.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S2", "店2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],
    ], tmp_path)
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    res = flow.finalize_import(db, imp.id)
    assert res["ok"] is True and res["added"] == 2
    db.close()
    # 对账文件：甲期望 5 点（与系统 3 点差 2）
    from tests.helpers import write_workbook
    p = str(tmp_path / "recon.xlsx")
    write_workbook(p, [("对账", [["姓名", "编号", "总点数"]],
                        [["甲", "111", 5]])])
    content = open(p, "rb").read()
    db = appdb.SessionLocal()
    tk = recon.create_recon(db, "2026-08", "recon.xlsx", content, admin.id)
    assert tk.summary.get("kind") == "person_points"
    from app.models import ReconResult
    diffs = db.query(ReconResult).filter(ReconResult.task_id == tk.id).all()
    assert len(diffs) == 1
    d = diffs[0]
    assert d.system_value == 3 and d.report_value == 5 and d.diff == -2
    db.close()


def test_recon_all_match_zero_diff_rows(client, tmp_path):
    """对账两侧点数全一致时：差异数=0，不落任何 ReconResult 行
    （回归：曾把比对行数当差异数，全对齐仍显示“差异 34 条”）。"""
    from app.services import recon
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "rq.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],    # 1点
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp.id)
    db.close()
    # 外部对账与系统一致：甲 1 点
    from tests.helpers import write_workbook
    from app.models import ReconResult
    p = str(tmp_path / "recon_ok.xlsx")
    write_workbook(p, [("对账", [["姓名", "编号", "总点数"]],
                        [["甲", "111", 1]])])
    db = appdb.SessionLocal()
    tk = recon.create_recon(db, "2026-08", "recon_ok.xlsx",
                               open(p, "rb").read(), admin.id)
    assert tk.summary.get("compared") == 1
    assert tk.summary.get("diff_count") == 0     # 全对齐 → 差异 0
    assert db.query(ReconResult).filter(
        ReconResult.task_id == tk.id).count() == 0   # 0 差异行不落库
    db.close()



def test_appeal_grouped_by_day_and_dup_hidden(client, tmp_path):
    """员工「我的申诉」按天倒序组织：重复导入(cross_file_dup)自动滤、
    不进入可申诉列表；仅 master_late/from_sub（未申诉过的）按天出现。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    # 文件1: 8/1 首次(valid)
    imp1 = _up(db, admin, "d1.xlsx", [
        ["S-X", "店X", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    # 文件2: 8/2 S-Z首次 valid + 8/2 S-X跨日(master_late) + 8/4 S-Z(master_late)
    imp2 = _up(db, admin, "d2.xlsx", [
        ["S-Z", "店Z", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
        ["S-X", "店X", "", "2026-08-02 09:00:00", "甲(111)", "R3",
         "YES", "YES", "NO"],          # 8/2 与文件1(8/1)跨日 → master_late
        ["S-Z", "店Z", "", "2026-08-04 09:00:00", "甲(111)", "R5",
         "YES", "YES", "NO"],          # 与 R2 跨日 → master_late
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp1.id)
    flow.process_import(db_fresh(), imp2.id)
    db = appdb.SessionLocal()
    days = flow.appeal_list(db, "111")
    # 8/4、8/2 各 1 条 master_late 可申诉（倒序）；8/1(valid)不出现
    assert [(d, len(v)) for d, v in days] == [("2026-08-04", 1),
                                              ("2026-08-02", 1)]
    db.close()


def test_cross_day_dup_auto_hidden(client, tmp_path):
    """同日同店跨文件重复(cross_file_dup) = 重复导入 → auto_ok 且不产生可申诉。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp1 = _up(db, admin, "e1.xlsx", [
        ["S-X", "店X", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    imp2 = _up(db, admin, "e2.xlsx", [
        ["S-X", "店X", "", "2026-08-01 10:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],          # 同日同店 → cross_file_dup
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp1.id)
    flow.process_import(db_fresh(), imp2.id)
    db = appdb.SessionLocal()
    dup = db.query(RawRecord).filter(
        RawRecord.clean_status == "cross_file_dup").one()
    assert dup.confirm_state == "auto_ok"
    assert flow.appeal_list(db, "111") == []   # 无可申诉
    db.close()


def test_my_perf_page_staff_own_report(client, tmp_path):
    """员工端 /my/perf：只显示本人日明细与月汇总（正式表有效店）。"""
    from app.services import perf
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "mp.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],    # 1点
        ["S2", "店2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],   # 2点
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    res = flow.finalize_import(db, imp.id)
    assert res["ok"] and res["added"] == 2
    db.close()
    _seed_staff(client)
    client.post("/login", data={"username": "emp1", "password": "pw123456"},
                follow_redirects=False)
    page = client.get("/my/perf").text
    assert "我的绩效" in page and "2026-08-01" in page and "2026-08-02" in page
    # 汇总：2 店、3 点（1+2）、750円
    assert "总点数" in page and "750" in page
    # 员工看不到管理员页（越权仍被拦）
    r = client.get("/perf", follow_redirects=False)
    assert r.status_code == 302


def test_rebuild_month_no_double_count_on_backfill(client, tmp_path):
    """同月补传含更早记录的文件：按文件入表会双算；rebuild_month 重判+重建后
    该店该月只保留最早一条，旧行被正确改判 master_late。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    # 文件1：店X 8/5（先入表）
    f1 = _up(db, admin, "m1.xlsx", [
        ["S-X", "店X", "", "2026-08-05 10:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), f1.id)
    db = appdb.SessionLocal()
    assert flow.finalize_import(db, f1.id)["ok"] is True
    db.close()
    # 补传文件2：店X 8/3（更早）→ 单独 finalize 会双算
    f2 = _up(db, admin, "m2.xlsx", [
        ["S-X", "店X", "", "2026-08-03 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), f2.id)
    db = appdb.SessionLocal()
    assert flow.finalize_import(db, f2.id)["ok"] is True
    assert db.query(FormalRecord).count() == 2      # 双算：8/3 + 8/5 都在
    db.close()
    # 按结算月重算：只保留当月最早(8/3)
    db = appdb.SessionLocal()
    res = flow.rebuild_month(db, "2026-08")
    assert res["ok"] is True
    assert res["formal_before"] == 2 and res["formal_after"] == 1
    assert res["points_after"] == 1
    dates = {str(f.japan_date) for f in db.query(FormalRecord).all()}
    assert dates == {"2026-08-03"}
    old = db.query(RawRecord).filter(
        RawRecord.modified_raw == "2026-08-05 10:00:00").one()
    assert old.clean_status == "master_late"        # 旧行被重判为被滤
    db.close()


def test_rebuild_month_keeps_approved_appeal(client, tmp_path):
    """rebuild_month 不得推翻「申诉经管理员认可」改判有效的记录。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "m3.xlsx", [
        ["S-A", "店A", "", "2026-07-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],                     # valid
        ["S-A", "店A", "", "2026-07-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],                     # master_late → 申诉认可
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    late = db.query(RawRecord).filter(
        RawRecord.clean_status == "master_late").one()
    flow.create_appeal(db, late.id, "111", "确实又去了")
    ap = db.query(AppealRecord).filter(
        AppealRecord.raw_record_id == late.id).one()
    assert flow.resolve_appeal(db, ap.id, "accept", admin.id)["ok"]
    assert flow.finalize_import(db, imp.id)["added"] == 2
    db.close()
    # 重算 7 月：申诉认可的那条必须仍在
    db = appdb.SessionLocal()
    res = flow.rebuild_month(db, "2026-07")
    assert res["ok"] is True
    assert res["formal_after"] == 2
    rr = db.get(RawRecord, late.id)
    assert rr.clean_status == "valid" and rr.confirm_state == "approved"
    ids = {f.raw_record_id for f in db.query(FormalRecord).all()}
    assert late.id in ids
    db.close()



def test_recon_sys_only_and_export(client, tmp_path):
    """对账反向名单 + 差异导出：
    系统有记录而对账文件没列的人进 summary.sys_only，并在页面/导出中出现。"""
    from app.services import recon
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "so.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],                      # 甲 1点
        ["S2", "店2", "", "2026-08-02 09:00:00", "乙(222)", "R2",
         "YES", "YES", "YES"],                     # 乙 2点
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp.id)
    # 对账文件只列甲 → 乙出现在反向名单
    from tests.helpers import write_workbook
    p = str(tmp_path / "so2.xlsx")
    write_workbook(p, [("对账", [["姓名", "编号", "总点数"]],
                        [["甲", "111", 1]])])
    tk = recon.create_recon(db, "2026-08", "so2.xlsx",
                               open(p, "rb").read(), admin.id)
    assert tk.summary["diff_count"] == 0
    assert set(tk.summary["sys_only"]) == {"222"}
    db.close()
    # 页面提示反向名单 + 导出 Excel
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    page = client.get(f"/recon?task_id={tk.id}").text
    assert "系统当月有正式记录、但对账文件未列出" in page
    r = client.get(f"/recon/export?task_id={tk.id}")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"                 # xlsx(zip) 魔数
    assert "spreadsheetml" in r.headers.get("content-type", "")


def test_recon_adjust_flow(client, tmp_path):
    """找平闭环：对账差异确认 → 生成下月调差记录（幂等/可取消），页面展示。"""
    from app.models import AdjustRecord
    from app.services import perf, recon
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "ad.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],    # 1点
        ["S2", "店2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],   # 2点 → 共 3 点（工资 750）
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp.id)
    # 对账文件报 5 点 → 系统差 -2 → 找平 +2 点（金额=+2×250 自动）
    from tests.helpers import write_workbook
    p = str(tmp_path / "ad2.xlsx")
    write_workbook(p, [("对账", [["姓名", "编号", "总点数"]],
                        [["甲", "111", 5]])])
    tk = recon.create_recon(db, "2026-08", "ad2.xlsx",
                               open(p, "rb").read(), admin.id)
    assert tk.summary["diff_count"] == 1
    db.close()
    # 页面：找平自动（无确认按钮），显示金额差
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    page = client.get(f"/recon?task_id={tk.id}").text
    assert "确认找平" not in page
    assert "应找平金额(円)" in page
    assert "自动" in page
    # 自动找平：sync 后 payroll 找平金额 = 金额差（含奖金）
    db = appdb.SessionLocal()
    period.sync_period_table(db, "2026-08")
    rows = {r["code"]: r for r in period.period_rows(db, "2026-08")}
    assert rows["111"]["adj_amt"] == rows["111"]["diff_amt"]
    db.close()


def test_recon_report_generation(client, tmp_path):
    """一键月度对账报告：build_report 生成 4-sheet 工作簿；路由可下载。"""
    from app.services import recon
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "rp.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],    # 1点
        ["S2", "店2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],   # 2点 → 3 点
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp.id)
    from tests.helpers import write_workbook
    p = str(tmp_path / "rp2.xlsx")
    write_workbook(p, [("对账", [["姓名", "编号", "总点数"]],
                        [["甲", "111", 5]])])   # 期望 5 → 差异 -2
    tk = recon.create_recon(db, "2026-08", "rp2.xlsx",
                               open(p, "rb").read(), admin.id)
    wb = recon.build_report(db, tk.id, "管理员甲")
    assert wb is not None
    names = wb.sheetnames
    assert names == ["对账报告摘要", "差异明细", "系统有而对账文件无", "找平确认"]
    ws = wb["对账报告摘要"]
    vals = "|".join(str(c or "") for row in ws.iter_rows(values_only=True)
                    for c in row)
    assert "对账月份" in vals and "差异条数" in vals and "系统正式口径" in vals
    assert "管理员甲" in vals
    ws2 = wb["差异明细"]
    cells = [str(c) for row in ws2.iter_rows(values_only=True) for c in row]
    assert "甲" in cells and "-2" in cells
    # 路由下载
    db.close()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    r = client.get(f"/recon/report?task_id={tk.id}")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert "spreadsheetml" in r.headers.get("content-type", "")


def test_recon_daily_records(client, tmp_path):
    """日级对账：上传逐条巡店明细 → 员工×日 问题行 + 人月差异 + 报告日级页签。"""
    from app.models import ReconDayRow
    from app.services import recon
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "dy.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],     # 1点
        ["S2", "店2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],    # 2点 → 系统月 3 点
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp.id)
    db.close()
    # 对账文件：逐条巡店明细（08-02 那笔 deploy=NO → 对账侧 1 点）
    HD = ["Store ID", "Store Name-Local", "Modified Time", "Submitter",
          "A+ POSM Visible", "NEW A+ POSM"]
    from tests.helpers import write_workbook
    p = str(tmp_path / "dy2.xlsx")
    write_workbook(p, [("有效明细", [HD], [
        ["S1", "店1", "2026-08-01 09:00:00", "甲(111)", "YES", "NO"],
        ["S2", "店2", "2026-08-02 09:00:00", "甲(111)", "YES", "NO"],
    ])])
    db = appdb.SessionLocal()
    tk = recon.create_recon(db, "2026-08", "dy2.xlsx",
                               open(p, "rb").read(), admin.id)
    s = tk.summary or {}
    assert s["kind"] == "daily_records"
    assert s["diff_count"] == 1            # 日级问题行 1（08-02：系统2 vs 对账1）
    assert s["monthly_diff"] == 1          # 人月差异 +1
    drs = db.query(ReconDayRow).filter(ReconDayRow.task_id == tk.id).all()
    assert len(drs) == 1
    assert str(drs[0].ref_date) == "2026-08-02"
    assert drs[0].sys_points == 2 and drs[0].rep_points == 1
    assert drs[0].diff == 1 and drs[0].side == "both"
    db.close()
    # 报告含日级页签
    db = appdb.SessionLocal()
    wb = recon.build_report(db, tk.id, "管理员")
    assert "员工×日对账明细" in wb.sheetnames
    ws = wb["员工×日对账明细"]
    cells = [str(c) for row in ws.iter_rows(values_only=True) for c in row]
    assert "2026-08-02" in cells and "甲" in cells and "1" in cells
    db.close()
    # 页面显示日级表与列头
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    page = client.get(f"/recon?task_id={tk.id}").text
    assert "员工×日 对账明细" in page
    assert "日期" in page and "员工姓名" in page and "2026-08-02" in page
    assert "点数不一致" in page


def test_recon_task_stats_and_product(client, tmp_path):
    """任务化：状态 done + 统计表(person_daily_stats) + 对账数据表全量落库
    + 下载对账结果 Excel。"""
    from app.models import PersonDailyStat, ReconDataRow
    from app.services import recon
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "tp.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S2", "店2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp.id)
    # finalize 钩子应已写统计表
    stats = db.query(PersonDailyStat).filter(
        PersonDailyStat.person_code == "111").all()
    assert len(stats) == 2 and sum(s.points for s in stats) == 3
    from tests.helpers import write_workbook
    HD = ["Store ID", "Store Name-Local", "Modified Time", "Submitter",
          "A+ POSM Visible", "NEW A+ POSM"]
    p = str(tmp_path / "tp2.xlsx")
    write_workbook(p, [("有效明细", [HD], [
        ["S1", "店1", "2026-08-01 09:00:00", "甲(111)", "YES", "NO"],
        ["S2", "店2", "2026-08-02 09:00:00", "甲(111)", "YES", "NO"],
    ])])
    tk = recon.create_recon(db, "2026-08", "tp2.xlsx",
                               open(p, "rb").read(), admin.id)
    assert tk.status == "done"
    assert tk.summary.get("kind") == "daily_records"
    assert db.query(ReconDataRow).filter(
        ReconDataRow.task_id == tk.id).count() == 2     # 对账数据表全量
    assert (tk.params or {}).get("result_path")          # 产物已落盘
    db.close()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    r = client.get(f"/recon/result?task_id={tk.id}")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    page = client.get(f"/recon?task_id={tk.id}").text
    assert "已完成" in page and "下载对账结果" in page


def test_recon_reupload_same_month_rebuilds(client, tmp_path):
    """同月重传 → 旧任务标记「上一版」保留，新任务为当前（重新对账）。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "ru.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp.id)
    from tests.helpers import write_workbook
    HD = ["Store ID", "Store Name-Local", "Modified Time", "Submitter",
          "A+ POSM Visible", "NEW A+ POSM"]
    p = str(tmp_path / "ru2.xlsx")
    write_workbook(p, [("有效明细", [HD], [
        ["S1", "店1", "2026-08-01 09:00:00", "甲(111)", "YES", "NO"],
    ])])
    content = open(p, "rb").read()
    from app.services import recon
    t1, older1 = recon.submit_task(db, "2026-08", "第一版.xlsx",
                                      content, admin.id, sync=True)
    assert older1 == []
    t2, older2 = recon.submit_task(db, "2026-08", "第二版.xlsx",
                                      content, admin.id, sync=True)
    assert older2 == [t1.id]                    # 旧任务被标记上一版
    from app.models import ReconTask
    ts = [t for t in db.query(ReconTask).filter(
        ReconTask.kind == "monthly_v3").all()
        if (t.params or {}).get("month") == "2026-08"]
    assert len(ts) == 2                         # 历史版保留 + 新当前版
    by_id = {t.id: t for t in ts}
    assert (by_id[t1.id].params or {}).get("replaced_by") == t2.id
    assert (by_id[t2.id].params or {}).get("replaced_by") is None
    assert (by_id[t2.id].params or {}).get("version") == 2
    assert t2.status == "done"
    db.close()
    # 页面徽标：新任务=当前；旧任务=上一版
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    page = client.get(f"/recon?task_id={t2.id}").text
    assert "下载对账结果" in page and "已完成" in page

def test_recon_attribution_and_ai_placeholder(client, tmp_path):
    """对账自动归因写入 summary；AI 解读在未配置模型时给出友好提示。"""
    from app.services import recon
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "at.xlsx", [
        ["S1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S2", "店2", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "YES"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp.id)
    from tests.helpers import write_workbook
    HD = ["Store ID", "Store Name-Local", "Modified Time", "Submitter",
          "A+ POSM Visible", "NEW A+ POSM"]
    p = str(tmp_path / "at2.xlsx")
    write_workbook(p, [("有效明细", [HD], [
        ["S1", "店1", "2026-08-01 09:00:00", "甲(111)", "YES", "NO"],
        ["S2", "店2", "2026-08-02 09:00:00", "甲(111)", "YES", "NO"],
    ])])
    tk = recon.create_recon(db, "2026-08", "at2.xlsx",
                               open(p, "rb").read(), admin.id)
    att = (tk.summary or {}).get("attribution") or {}
    assert att.get("consistent") == 2 and att.get("only_system") == 0
    assert att.get("only_report") == 0 and att.get("net") == 0
    wb = recon.build_report(db, tk.id, "管理员")
    assert "差异归因" in wb.sheetnames
    db.close()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    page = client.get(f"/recon?task_id={tk.id}").text
    assert "差异归因（系统自动）" in page and "AI 对账分析" in page
    # 未配置模型 → 提示而非崩溃
    csrf = _csrf_of(client, f"/recon?task_id={tk.id}")
    r = client.post(f"/recon/{tk.id}/interpret",
                    data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    assert "未配置" in unquote(r.headers.get("location", ""))


