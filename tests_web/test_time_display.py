# -*- coding: utf-8 -*-
"""时间显示口径：库里存 **UTC**，页面按 **JST** 显示。

用户 2026-10-16 反馈：刚上传完，页面「上传时间」显示的比日本当地早 9 小时。
库里是 `datetime.utcnow()`（UTC），`files.html` / `recon.html` 当时直接 strftime，
没走 `jst()` → 日本用户必然看出时间不对。本用例钉住这两处。
"""
from datetime import datetime, timezone

import app.db as appdb
from app.models import ImportFile, ReconTask, User
from tests_web.test_flow import _seed_admin

UTC = datetime(2026, 10, 16, 13, 39, 34)      # 客户实际上传时刻（UTC）
JST_TEXT = "10-16 22:39"                       # 日本当地 = UTC+9
UTC_TEXT = "10-16 13:39"


def _login_admin(client):
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)


def _admin_id():
    db = appdb.SessionLocal()
    uid = db.query(User).filter(User.username == "admin").one().id
    db.close()
    return uid


def test_jst_str_converts_utc_to_japan_time():
    """jst() 助手：naive 时间按 UTC 解释、aware 时间照转、坏数据不炸页面。"""
    from app.templating import jst_str
    assert jst_str(UTC, "%m-%d %H:%M") == JST_TEXT
    assert jst_str(UTC.replace(tzinfo=timezone.utc), "%m-%d %H:%M") == JST_TEXT
    assert jst_str(None) == "—"
    assert jst_str("坏数据") == "—"                 # 坏数据给占位，不 500


def test_files_page_shows_upload_time_in_jst(client):
    """/files 的「上传时间」必须是日本时间（不能直接显示 UTC）。"""
    _seed_admin(client)
    _login_admin(client)
    db = appdb.SessionLocal()
    db.add(ImportFile(file_name="oct.xlsx", file_sha256="sha-jst-test",
                      file_size=1, stored_path="/tmp/x.xlsx",
                      uploaded_by=_admin_id(), uploaded_at=UTC,
                      status="parsed", parsed_rows=1,
                      parsed_sheets=[], ignored_sheets=[], warnings=[],
                      errors=[]))
    db.commit()
    db.close()
    page = client.get("/files").text
    assert JST_TEXT in page
    assert UTC_TEXT not in page


def test_recon_page_shows_created_time_in_jst(client):
    """/recon 的任务创建时间同样按 JST 显示。"""
    _seed_admin(client)
    _login_admin(client)
    db = appdb.SessionLocal()
    db.add(ReconTask(kind="monthly_v3", status="done", created_by=_admin_id(),
                     created_at=UTC, params={"month": "2026-10"},
                     summary={}))
    db.commit()
    db.close()
    page = client.get("/recon").text
    assert JST_TEXT in page
    assert UTC_TEXT not in page
