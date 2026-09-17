# -*- coding: utf-8 -*-
"""文件上传与解析入库服务（spec §4.2/§4.3；引擎 loader 复用）。

upload_and_store: 校验 + sha256 防重 + 落盘
parse_file: loader 解析 → 单事务写 imports 诊断 + raw_records + persons 补全
"""
import hashlib
import os
import re

from app.config import get_settings
from app.models import ImportFile, Person, RawRecord, User
from app.services.login_names import unique_username
from store_settle import loader as engine_loader


class UploadError(Exception):
    pass


class DuplicateUpload(UploadError):
    pass


def _save_blob(content: bytes) -> str:
    sha = hashlib.sha256(content).hexdigest()
    d = get_settings().upload_dir
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, sha + ".xlsx")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(content)
    return sha, path


def _name_of(submitter_raw: str) -> str:
    m = re.match(r"^(.+)\(\d+\)\s*$", submitter_raw.strip())
    return (m.group(1).strip() if m else submitter_raw) or "未识别"


def ensure_staff_user(db, person_code: str, display_name: str) -> User:
    """员工账号幂等创建：导入发现新人员编号时自动开户。

    登录名由姓名自动生成（模型优先：中文→拼音 / 日文→罗马字 / 英文→原名；
    模型不可用时回退规则拼音/编号）；口令为系统默认初始口令，首登须改密。
    已存在该 person_code 的账号（含早期手工账号）则不重复创建。
    """
    from app.auth import hash_password
    from app.services.login_names import login_name_for
    existing = (db.query(User)
                .filter(User.role == "staff", User.person_code == person_code)
                .first())
    if existing is not None:
        return existing
    username = login_name_for(db, display_name, person_code)
    u = User(username=username,
             password_hash=hash_password(get_settings().default_staff_password),
             display_name=display_name, role="staff", person_code=person_code,
             is_active=True, status="active", must_change_password=True)
    db.add(u)
    return u


def upload_and_store(filename: str, content: bytes, user_id: int, db) -> ImportFile:
    if not filename.lower().endswith(".xlsx"):
        raise UploadError("仅支持 .xlsx 文件")
    if len(content) > get_settings().max_upload_mb * 1024 * 1024:
        raise UploadError(f"超过 {get_settings().max_upload_mb}MB 上限")
    sha, path = _save_blob(content)
    exists = db.query(ImportFile).filter(ImportFile.file_sha256 == sha).first()
    if exists:
        raise DuplicateUpload("该文件已存在（同内容 sha256）")
    imp = ImportFile(file_name=filename, file_sha256=sha, file_size=len(content),
                     stored_path=path, uploaded_by=user_id,
                     status="uploaded", parsed_sheets=[], ignored_sheets=[],
                     warnings=[], errors=[])
    db.add(imp)
    db.commit()
    return imp


def parse_file(imp: ImportFile, db) -> None:
    """loader 解析并把行落库；失败 → status=failed + errors（单文件事务）。

    表头识别：优先系统内调用模型（AI 解析列语义），失败/未配置回退规则匹配。
    """
    col_override = None
    ai_note = ""
    try:
        from app.services.ai_visit import ai_parse_visit_layout
        layout = ai_parse_visit_layout(imp.stored_path)
        import sys as _sys
        print(f"[importer] AI 布局解析 {imp.file_name}: {layout!r}",
              file=_sys.stderr)
        if layout:
            col_override = layout
            ai_note = (f"AI 布局解析: 表头行{layout['header_row']} "
                       f"列{layout['cols']}")
        else:
            ai_note = "AI 布局解析: 无结果（未配置/失败/不可信），回退规则解析"
    except Exception as e:  # noqa: BLE001
        col_override = None
        ai_note = f"AI 布局解析异常: {type(e).__name__}: {e}，回退规则解析"
        import sys as _sys
        print(f"[importer] AI 布局解析异常: {type(e).__name__}: {e}",
              file=_sys.stderr)
    try:
        res = engine_loader.load_workbook(
            imp.stored_path, filename=imp.file_name, col_override=col_override)
    except Exception as e:  # 损坏/非 zip 等：绝不猜测，标 failed
        imp.status = "failed"
        imp.errors = [f"文件无法解析: {type(e).__name__}: {e}"]
        db.commit()
        return
    if res.failed:
        imp.status = "failed"
        imp.errors = res.errors
        imp.warnings = res.warnings
        db.commit()
        return
    if ai_note:
        imp.warnings = (imp.warnings or []) + [ai_note]

    persons_cache = {}  # code -> name seen this file（首见写入）
    for row in res.rows:
        code = row.submitter_code
        if code and code not in persons_cache:
            existing = db.get(Person, code)
            if existing is None:
                name = _name_of(row.submitter_raw)
                db.add(Person(code=code, display_name=name,
                              first_seen_import_id=imp.id))
            persons_cache[code] = True
        db.add(RawRecord(
            import_id=imp.id, sheet_name=row.sheet_name, excel_row=row.excel_row,
            store_id_raw=row.store_id_raw, store_name_local_raw=row.store_name_local_raw,
            store_name_en_raw=row.store_name_en_raw, modified_raw=row.modified_raw,
            submitter_raw=row.submitter_raw, submitter_code=row.submitter_code,
            record_id_raw=row.record_id_raw, visible_raw=row.visible_raw or None,
            deploy_raw=row.deploy_raw or None, original_row=row.original_row,
        ))

    imp.format = res.format
    imp.header_row = res.header_row
    imp.data_start_row = res.data_start_row
    imp.parsed_sheets = res.parsed_sheets
    imp.ignored_sheets = res.ignored_sheets
    imp.total_rows = res.total_rows
    imp.parsed_rows = res.parsed_rows
    imp.status = "parsed"
    imp.warnings = (res.warnings or []) + ([ai_note] if ai_note else [])
    imp.errors = []
    db.commit()



def delete_file(imp: ImportFile, db) -> None:
    _ = imp.id  # 旧 run 引用检查已下线
    db.query(RawRecord).filter(RawRecord.import_id == imp.id).delete()
    db.delete(imp)
    db.commit()
    # 物理 blob：有同名 sha 其它 import 时保留；简化：删除失败不阻断
    try:
        os.remove(imp.stored_path)
    except OSError:
        pass
