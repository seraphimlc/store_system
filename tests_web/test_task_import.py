# -*- coding: utf-8 -*-
"""批量导入任务（线路执行计划表 → 车站任务）。

对应 2026-10-15/16 手工导的 3 次（小川队 A 线 / 汤静队 B 线 / 甘子杰 B 线）。
口径：已有任务一律不动、不新建车站、认不出队名整份拒收。
"""
import os
import re

import app.db as appdb
from app.models import BdLine, BdStation, BdTask, BdTeam, User
from app.services import bd_places, bd_task_import as ti
from tests.helpers import form_token, write_workbook
from tests_web.test_flow import _seed_admin

ADMIN_PW = "pw123456"
#: 表头行的列名（真实文件里 小川队_神奈川A线 就长这样）
HEAD = ["路线编号", "铁路线路", "车站／站群", "负责队", "任务来源", "状态", "备注"]


def _seed_ops(db, stations, team="小川队", with_line="テスト線"):
    """建线路 + 车站（重建物理车站层）+ 队伍。"""
    line = BdLine(name=with_line, name_norm=with_line, operator="テスト鉄道",
                  operator_short="テスト", kind="private", prefs="13",
                  n_station=len(stations))
    db.add(line)
    db.flush()
    for nm in stations:
        db.add(BdStation(name=nm, name_norm=bd_tasks_norm(nm), line=with_line,
                         line_id=line.id, operator="テスト鉄道", pref="13",
                         source="mlit"))
    db.flush()
    bd_places.rebuild_places(db)
    t = BdTeam(name=team, code="TJ1")
    db.add(t)
    db.commit()
    return line, t


def bd_tasks_norm(nm):
    from app.services import bd_tasks
    return bd_tasks.norm_name(nm)


def _plan_file(tmp_path, rows, name="plan.xlsx", header=None):
    """写一份「带标题/说明行 + 表头行」的执行计划表（真实文件就是这个形状）。"""
    path = str(tmp_path / name)
    write_workbook(path, [("计划", [
        ["某某队 线路执行计划"],                       # 第 1 行：大标题
        ["以下为本次新增站区"],                        # 第 2 行：说明
        header or HEAD,                                # 第 3 行：真正的表头
    ], rows)])
    return path


def _row(line, station, team="小川队", source="本次新增", status="未开始", note=""):
    return ["KN01", line, station, team, source, status, note]


def _login_admin(client):
    client.post("/login", data={"username": "admin", "password": ADMIN_PW},
                follow_redirects=False)


def _apply_form(client, html, csrf):
    """从预览页的「确认导入」表单里抠出隐藏字段（blob_sha / 表头行 / 列映射）。

    刻意不自己拼这些值：模板漏发隐藏字段时（真浏览器会 400/报错）这里就该红。
    """
    form = re.search(r'<form[^>]*action="/tasks/import/apply".*?</form>', html, re.S)
    assert form, "预览页里没有确认导入表单"
    data = {"_ft": form_token(client, "/tasks/import"), "csrf_token": csrf}
    for name, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form.group(0)):
        data[name] = value
    assert data.get("blob_sha"), "表单里没有 blob_sha（确认导入会找不到文件）"
    assert data.get("header_row"), "表单里没有表头行"
    assert "col_station" in data, "表单里没有列映射"
    return data


def _csrf(client, path):
    m = re.search(r'name="csrf_token" value="([^"]+)"',
                  client.get(path, follow_redirects=True).text)
    return m.group(1) if m else ""


def _post(client, path, data, from_path=None):
    d = dict(data)
    d["_ft"] = form_token(client, from_path or path)
    d["csrf_token"] = _csrf(client, from_path or path)
    return client.post(path, data=d, follow_redirects=False)


# ---------------------------------------------------------------- 服务层

def test_detect_layout_skips_title_rows(client, tmp_path):
    """表头不在第一行（真实文件在第 3/5 行）也要认出来。"""
    layout = ti.detect_layout(_plan_file(tmp_path, [_row("テスト線", "甲駅")]))
    assert layout["header_row"] == 3
    assert layout["cols"]["line"] == 1
    assert layout["cols"]["station"] == 2
    assert layout["cols"]["team"] == 3
    assert layout["cols"]["source"] == 4


def test_rejects_file_without_team_column(client, tmp_path):
    """没有「负责队」列 → 整份拒收（用户 2026-10-16 定稿：不猜队名）。"""
    path = _plan_file(tmp_path, [["東武 東上線", "和光市", "", "", "待确认", ""]],
                      header=["线路", "车站", "担当", "开始日", "状态", "备注"])
    db = appdb.SessionLocal()
    _seed_ops(db, ["和光市"])
    try:
        ti.build_plan(db, path)
        raise AssertionError("应当拒收")
    except ti.TaskImportError as e:
        assert "负责队" in str(e)
    finally:
        db.close()


def test_rejects_unresolvable_team(client, tmp_path):
    """有队列但值不是队名（如「待分配」）→ 同样拒收，并把读到的值报出来。"""
    path = _plan_file(tmp_path, [_row("テスト線", "甲駅", team="待分配")])
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅"])
    try:
        ti.build_plan(db, path)
        raise AssertionError("应当拒收")
    except ti.TaskImportError as e:
        assert "待分配" in str(e)
    finally:
        db.close()


def test_kana_fold_matches_ke_and_gake(client, tmp_path):
    """表上「保土ケ谷」（小写ケ）要能匹配库里的「保土ヶ谷」。"""
    path = _plan_file(tmp_path, [_row("テスト線", "保土ケ谷")])
    db = appdb.SessionLocal()
    _seed_ops(db, ["保土ヶ谷"])
    plan = ti.build_plan(db, path)
    db.close()
    assert plan["summary"]["bad"] == 0
    assert plan["summary"]["create_stations"] == 1


def test_build_plan_classifies_rows(client, tmp_path):
    """三种行：没有任务的 → 建；已有任务的 → 跳过不动；别的队 → 参考行跳过。"""
    path = _plan_file(tmp_path, [
        _row("テスト線", "甲駅"),                                   # 建
        _row("テスト線", "乙駅"),                                   # 已有任务 → 跳过
        _row("テスト線", "丙駅", team="查无此队"),                     # 队对不上 → 参考行
    ])
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅", "乙駅", "丙駅"])
    # 乙駅 先建一个任务并派给别的队（验证导入不会动它）
    from app.services import bd_tasks
    other = BdTeam(name="他队", code="TB1")
    db.add(other)
    db.commit()
    places = {p.name: p.id for p in db.query(
        __import__("app.models", fromlist=["BdStationPlace"]).BdStationPlace).all()}
    bd_tasks.create_tasks_for_places(db, [places["乙駅"]], team_id=other.id)
    db.commit()
    before = {t.id: t.team_id for t in db.query(BdTask).all()}

    plan = ti.build_plan(db, path)
    assert plan["summary"]["create_stations"] == 1
    assert plan["summary"]["existing"] == 1
    # 乙駅（已有任务）与 丙駅（队对不上）都算「跳过」，但原因不同
    assert plan["summary"]["skipped"] == 2
    reasons = [r.skip_reason for r in plan["rows"] if r.decision == "skip"]
    assert any("已有任务" in (x or "") for x in reasons)
    assert any("对不上" in (x or "") for x in reasons)
    assert plan["summary"]["bad"] == 0
    # 已有任务归属没变
    assert {t.id: t.team_id for t in db.query(BdTask).all()} == before
    db.close()


def test_apply_creates_only_missing_and_leaves_existing(client, tmp_path):
    """应用计划：只建没有任务的站，已有任务一个都不动。"""
    path = _plan_file(tmp_path, [
        _row("テスト線", "甲駅"),
        _row("テスト線", "乙駅"),
    ])
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅", "乙駅"])
    db.close()

    db = appdb.SessionLocal()
    res = ti.apply_plan(db, path, actor_name="test")
    assert res["created"] == 2
    assert res["existing_untouched"] == 0
    db.close()

    # 再导一次：全都已有任务 → 0 新建、0 改动
    db = appdb.SessionLocal()
    before = {t.id: t.team_id for t in db.query(BdTask).all()}
    res = ti.apply_plan(db, path, actor_name="test")
    assert res["created"] == 0
    assert res["existing_untouched"] == 2
    assert res["skipped_rows"] == 2
    assert {t.id: t.team_id for t in db.query(BdTask).all()} == before
    db.close()


def test_apply_refuses_unmatched_unless_skipped(client, tmp_path):
    """对不上的行没勾「跳过」→ 整批不写库；勾了才继续。"""
    path = _plan_file(tmp_path, [
        _row("テスト線", "甲駅"),
        _row("テスト線", "不存在的駅"),
    ])
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅"])
    db.close()

    db = appdb.SessionLocal()
    try:
        ti.apply_plan(db, path, actor_name="test")
        raise AssertionError("应当拒绝")
    except ti.TaskImportError as e:
        assert "对不上" in str(e)
    assert db.query(BdTask).count() == 0          # 一条都没写
    db.close()

    db = appdb.SessionLocal()
    plan = ti.build_plan(db, path)
    bad = [r for r in plan["rows"] if r.decision == "bad"]
    res = ti.apply_plan(db, path, skip_rows=[bad[0].row_no], actor_name="test")
    assert res["created"] == 1
    db.close()


def test_station_group_splits_into_two_tasks(client, tmp_path):
    """站群「A／B」要拆成两个站，各建一个任务。"""
    path = _plan_file(tmp_path, [_row("テスト線", "甲駅／乙駅")])
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅", "乙駅"])
    plan = ti.build_plan(db, path)
    assert plan["summary"]["create_stations"] == 2
    db.close()


def test_done_rows_are_not_created(client, tmp_path):
    """表上标为已完成的站不建任务（免得重复做）。"""
    path = _plan_file(tmp_path, [
        _row("テスト線", "甲駅", status="已完成"),
        _row("テスト線", "乙駅"),
    ])
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅", "乙駅"])
    plan = ti.build_plan(db, path)
    assert plan["summary"]["create_stations"] == 1
    skipped = [r for r in plan["rows"] if r.decision == "skip"]
    assert any("已完成" in (r.skip_reason or "") for r in skipped)
    db.close()


# ---------------------------------------------------------------- 页面

def test_import_page_renders(client):
    _seed_admin(client)
    _login_admin(client)
    page = client.get("/tasks/import")
    assert page.status_code == 200
    assert "批量导入任务" in page.text
    assert 'data-testid="task-import-upload"' in page.text


def test_import_page_upload_preview_and_apply(client, tmp_path):
    """端到端：上传 → 预览（能看到将新建/已有/对不上）→ 确认导入 → 任务真的建了。"""
    _seed_admin(client)
    _login_admin(client)
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅", "乙駅"])
    db.close()

    path = _plan_file(tmp_path, [
        _row("テスト線", "甲駅"),
        _row("テスト線", "乙駅"),
    ])
    with open(path, "rb") as f:
        blob = f.read()

    # ① 上传 → 预览
    csrf = _csrf(client, "/tasks/import")
    data = {"_ft": form_token(client, "/tasks/import"), "csrf_token": csrf}
    r = client.post("/tasks/import", data=data,
                    files={"file": ("plan.xlsx", blob,
                                    "application/vnd.openxmlformats-officedocument"
                                    ".spreadsheetml.sheet")},
                    follow_redirects=False)
    assert r.status_code == 200
    assert 'data-testid="task-import-apply"' in r.text
    assert "将新建" in r.text
    # 预览阶段绝不能写库
    db = appdb.SessionLocal()
    assert db.query(BdTask).count() == 0
    db.close()

    # ② 像浏览器那样，从预览页的表单里把隐藏字段读出来再提交
    data = _apply_form(client, r.text, csrf)
    r = client.post("/tasks/import/apply", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert "msg=" in r.headers["location"]
    db = appdb.SessionLocal()
    assert db.query(BdTask).count() == 2
    db.close()


def test_reparse_keeps_all_columns_in_dropdown(client, tmp_path):
    """人工改列映射后「重新解析」，列下拉仍要有工作表里**全部**列。

    踩过的坑：n_cols 若按「已选中的最大列号 +1」算，改一次映射后下拉就缩水，
    想再选更靠右的列就选不到了（列数必须从工作表本身数）。
    """
    _seed_admin(client)
    _login_admin(client)
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅"])
    db.close()
    path = _plan_file(tmp_path, [_row("テスト線", "甲駅")])   # 7 列
    with open(path, "rb") as f:
        blob = f.read()
    csrf = _csrf(client, "/tasks/import")
    r = client.post("/tasks/import",
                    data={"_ft": form_token(client, "/tasks/import"),
                          "csrf_token": csrf},
                    files={"file": ("plan.xlsx", blob, "application/vnd.ms-excel")},
                    follow_redirects=False)
    blob_sha = re.search(r'name="blob_sha" value="([^"]*)"', r.text).group(1)

    # 只指定「车站」列（其它一律不指定）→ 重新解析
    r = client.post("/tasks/import",
                    data={"_ft": form_token(client, "/tasks/import"),
                          "csrf_token": csrf, "blob_sha": blob_sha,
                          "header_row": "3", "col_line": "1", "col_station": "2",
                          "col_team": "3"},
                    follow_redirects=False)
    assert r.status_code == 200
    sel = re.search(r'data-testid="layout-col-note"[^>]*>(.*?)</select>', r.text, re.S)
    assert sel, "预览页没有列下拉"
    opts = re.findall(r'<option value="(\d+)"', sel.group(1))
    assert opts == [str(i) for i in range(7)], opts      # 7 列一个都不能少

    # 把「车站」列指到一个空白列 → 解析被拒，但**列编辑器必须还在**（否则人就改不回来了）
    r = client.post("/tasks/import",
                    data={"_ft": form_token(client, "/tasks/import"),
                          "csrf_token": csrf, "blob_sha": blob_sha,
                          "header_row": "3", "col_line": "6", "col_station": "9",
                          "col_team": "3"},
                    follow_redirects=False)
    assert r.status_code == 200
    assert 'data-testid="task-import-layout"' in r.text
    assert 'data-testid="import-err"' in r.text


def test_import_apply_reports_unmatched(client, tmp_path):
    """预览里有对不上的行且没勾跳过 → 页面回显错误，仍然不写库。"""
    _seed_admin(client)
    _login_admin(client)
    db = appdb.SessionLocal()
    _seed_ops(db, ["甲駅"])
    db.close()
    path = _plan_file(tmp_path, [
        _row("テスト線", "甲駅"),
        _row("テスト線", "不存在的駅"),
    ])
    with open(path, "rb") as f:
        blob = f.read()
    csrf = _csrf(client, "/tasks/import")
    r = client.post("/tasks/import",
                    data={"_ft": form_token(client, "/tasks/import"),
                          "csrf_token": csrf},
                    files={"file": ("plan.xlsx", blob, "application/vnd.ms-excel")},
                    follow_redirects=False)
    assert "对不上的站" in r.text                      # 预览里必须把对不上的行标出来
    r = client.post("/tasks/import/apply",
                    data=_apply_form(client, r.text, csrf), follow_redirects=False)
    assert r.status_code == 200
    assert "对不上" in r.text
    db = appdb.SessionLocal()
    assert db.query(BdTask).count() == 0
    db.close()


def test_import_requires_admin(client):
    """非管理员进不来（作业域管理页）。"""
    db = appdb.SessionLocal()
    db.add(User(username="emp9", password_hash="x", display_name="员工",
                role="staff", is_active=True))
    db.commit()
    db.close()
    client.post("/login", data={"username": "emp9", "password": ""},
                follow_redirects=False)
    r = client.get("/tasks/import", follow_redirects=False)
    assert r.status_code in (302, 403)


# ---------------------------------------------------------------- 附注站名（2026-10-17）

def test_name_variants_strips_annotation_but_keeps_original_first():
    """「独協大学前〈草加松原〉」→ 候选 = [原名, 去附注]。

    ⚠️ 原名**必须排第一**：只在原名匹配不上时才用去附注的写法 →
    绝不改变任何现有匹配结果（真库里 0 个站名带附注，实测过）。
    """
    assert ti._name_variants("獨協大学前〈草加松原〉") == [
        "獨協大学前〈草加松原〉", "獨協大学前"]
    assert ti._name_variants(ti.norm("北千住（東京）")) == ["北千住(東京)", "北千住"]
    assert ti._name_variants("A[B]") == ["A[B]", "A"]
    assert ti._name_variants("新宿") == ["新宿"]
    # 括号不在结尾 → 不许动（官方名里带括号的情况别被误伤）
    assert ti._name_variants("A〈B〉C") == ["A〈B〉C"]
    assert ti._name_variants("") == []
    # 近似站名提示也走同一层（否则对不上时给的"近似"会带上附注）
    assert ti._base_name("獨協大学前〈草加松原〉") == "獨協大学前"


def test_import_matches_station_written_with_old_name_annotation(client, tmp_path):
    """生产掉行实例：文件写「獨協大学前〈草加松原〉」，库里是「獨協大学前」。"""
    db = appdb.SessionLocal()
    _seed_ops(db, ["獨協大学前", "松原"], team="甘子杰队",
              with_line="東武スカイツリーライン(伊勢崎線)")
    path = _plan_file(tmp_path, [
        _row("東武スカイツリーライン(伊勢崎線)", "獨協大学前〈草加松原〉",
             team="甘子杰队")])
    plan = ti.build_plan(db, path)
    assert plan["summary"]["bad"] == 0, [r.station_raw for r in plan["rows"]
                                         if r.decision == "bad"]
    assert plan["summary"]["create_stations"] == 1
    # 落到**去附注后**的那个站（物理车站层）上
    from app.models import BdStationPlace
    pid = plan["rows"][0].create[0][0]
    assert db.get(BdStationPlace, pid).name == "獨協大学前"
    db.close()


def test_annotated_name_still_prefers_the_literal_station(client, tmp_path):
    """库里**真有一行**叫「獨協大学前〈草加松原〉」时，必须命中它而不是基名那行。"""
    db = appdb.SessionLocal()
    _seed_ops(db, ["獨協大学前", "獨協大学前〈草加松原〉"], team="甘子杰队",
              with_line="東武スカイツリーライン(伊勢崎線)")
    path = _plan_file(tmp_path, [
        _row("東武スカイツリーライン(伊勢崎線)", "獨協大学前〈草加松原〉",
             team="甘子杰队")])
    plan = ti.build_plan(db, path)
    assert plan["summary"]["bad"] == 0
    assert [x[1] for x in plan["rows"][0].create] == ["獨協大学前〈草加松原〉"]
    db.close()


def test_create_station_accepts_pref_and_coords_for_n02_missing_station(client):
    """补 N02 里没有的站（幕張豊砂 2023 开站）：县/坐标要能落进车站与物理车站层。

    故意**不给 ekicode/group_code** → `source="manual"`、物理车站层按「站名+县」归组，
    这样资产体检的「数据源对齐」（数 `group_code != ''` 的 N02 派生行）不会被污染。
    """
    from app.models import BdStationPlace
    from app.services import bd_tasks

    db = appdb.SessionLocal()
    line = BdLine(name="京葉線", name_norm="京葉線", operator="東日本旅客鉄道",
                  operator_short="JR", kind="jr", prefs="12")
    db.add(line)
    db.flush()
    db.commit()
    st = bd_tasks.create_station(db, "幕張豊砂", line_id=line.id, pref="12",
                                 lon=140.02741, lat=35.65793)
    db.commit()
    assert (st.pref, st.source, st.ekicode, st.group_code) == ("12", "manual", "", "")
    assert abs(st.lon - 140.02741) < 1e-9 and abs(st.lat - 35.65793) < 1e-9
    pl = db.get(BdStationPlace, st.place_id)
    assert pl is not None and pl.name == "幕張豊砂" and pl.pref == "12"
    assert abs(pl.lon - 140.02741) < 1e-9 and abs(pl.lat - 35.65793) < 1e-9
    assert pl.group_code == "" and pl.source == "manual"
    db.close()
