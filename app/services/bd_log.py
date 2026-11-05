# -*- coding: utf-8 -*-
"""作业域变更日志（见 `docs/记录-作业域.md`）。

用户 2026-10-03：「每个任务的变化日志；团队变化、团队成员变化、队长变化等，要记录日志」。
设计：**一张通用追加表 `bd_log`**（domain 区分），写入只有一处、时间线一次查询出全。

**只写不改不删**（追加型）：所以"员工先报 40%、队长改成 60%"这种过程永远查得到
（`bd_task_progress` 是一天一条的快照，会被覆盖，看不到改了几次、谁改的）。
"""
from datetime import datetime, timedelta
import json
from typing import Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import BdLog, User

DOMAINS = ("task", "team", "member", "station", "zone", "grid",
           "grid_density", "message")


def actor_of(db: Session, user) -> Dict[str, str]:
    """操作人 → {"actor": 登录名或编号, "actor_name": 显示名}。"""
    if user is None:
        return {"actor": "", "actor_name": ""}
    login = getattr(user, "username", "") or ""
    code = getattr(user, "person_code", "") or ""
    name = getattr(user, "display_name", "") or ""
    if not name and code and not login:
        u = db.query(User).filter(User.person_code == code).first()
        name = (u.display_name if u else "") or ""
    return {"actor": login or code, "actor_name": name or (login or code)}


def log(db: Session, domain: str, action: str, ref_id: Optional[int] = None,
        ref_label: str = "", field: str = "", old: str = "", new: str = "",
        actor: str = "", actor_name: str = "", note: str = "",
        when: Optional[datetime] = None) -> BdLog:
    """追加一条日志（**调用方不要改/删**）。同值不写：`old == new` 且都是空则跳过。"""
    row = BdLog(domain=domain, action=action, ref_id=ref_id,
                ref_label=(ref_label or "")[:128], field=field,
                old_value=str(old if old is not None else ""),
                new_value=str(new if new is not None else ""),
                actor=actor or "", actor_name=actor_name or "",
                note=note or "", created_at=when or datetime.utcnow())
    db.add(row)
    return row


def log_op(db: Session, user, domain: str, action: str,
           ref_id: Optional[int] = None, ref_label: str = "",
           field: str = "", old: str = "", new: str = "",
           note: str = "") -> BdLog:
    """带操作人的便捷写入（路由/服务层优先用它）。"""
    a = actor_of(db, user)
    return log(db, domain, action, ref_id=ref_id, ref_label=ref_label,
               field=field, old=old, new=new, actor=a["actor"],
               actor_name=a["actor_name"], note=note)


def timeline(db: Session, domain: str, ref_id: int,
             limit: int = 200) -> List[BdLog]:
    """某个对象的时间线（新→旧）。"""
    return (db.query(BdLog)
            .filter(BdLog.domain == domain, BdLog.ref_id == ref_id)
            .order_by(BdLog.created_at.desc(), BdLog.id.desc())
            .limit(limit).all())


def _recent_q(db: Session, domain: str = "", kw: str = "", action: str = "",
              date_from: str = "", date_to: str = ""):
    """日志列表的**筛选条件**（`recent` 与 `recent_count` 共用，别各写一份）。

    筛选（2026-10-06 复盘：生产已 4300+ 条，没有筛选没法用）：
    `domain` 域 / `kw` 关键词（操作人·对象·备注）/ `action` 动作 /
    `date_from`~`date_to` **JST 日期**（页面显示的就是 JST，用户按 JST 想）。
    """
    q = db.query(BdLog)
    if domain in DOMAINS:
        q = q.filter(BdLog.domain == domain)
    if kw:
        like = "%" + kw.strip() + "%"
        from app.models import BdWorkGrid, BdRegion, BdTaskEvent
        from app.services import bd_grid_metadata as metadata
        event_ref=sa.func.coalesce(BdTaskEvent.store_task_id,BdTaskEvent.grid_task_id,
                                  BdTaskEvent.region_task_id,BdTaskEvent.main_task_id)
        matching_event=sa.exists().where(BdTaskEvent.event_type==BdLog.action,
            event_ref==BdLog.ref_id,metadata.event_match(db,kw.strip()))
        q = q.filter(sa.or_(BdLog.actor.like(like),BdLog.ref_label.like(like),BdLog.note.like(like),
            sa.and_(BdLog.domain=='grid',BdLog.ref_id.in_(db.query(BdWorkGrid.id).filter(metadata.grid_match(kw.strip())))),
            sa.and_(BdLog.domain=='zone',BdLog.ref_id.in_(db.query(BdRegion.id).filter(
                metadata.literal_match(BdRegion.name,kw.strip())))),
            sa.and_(BdLog.domain=='task',matching_event)))
    if action:
        q = q.filter(BdLog.action == action)
    # ⚠️ created_at 存的是 **UTC**，页面显示 JST（+9）→ 用户按 JST 填的日期要换算
    if date_from:
        dt = _parse_day(date_from)
        if dt:
            q = q.filter(BdLog.created_at >= dt - _JST_OFFSET)
    if date_to:
        dt = _parse_day(date_to)
        if dt:
            q = q.filter(BdLog.created_at < dt + _DAY - _JST_OFFSET)
    return q


def recent(db: Session, domain: str = "", limit: int = 200,
           offset: int = 0, kw: str = "", action: str = "",
           date_from: str = "", date_to: str = "") -> List[BdLog]:
    """全站最近日志——管理端"发生了什么"入口。"""
    return (_recent_q(db, domain=domain, kw=kw, action=action,
                      date_from=date_from, date_to=date_to)
            .order_by(BdLog.created_at.desc(), BdLog.id.desc())
            .limit(limit).offset(offset).all())


def recent_count(db: Session, domain: str = "", kw: str = "", action: str = "",
                 date_from: str = "", date_to: str = "") -> int:
    """同一筛选条件下的**总数**（给分页夹取页码用，2026-10-17 加）。"""
    return int(_recent_q(db, domain=domain, kw=kw, action=action,
                         date_from=date_from, date_to=date_to).count())


_JST_OFFSET = timedelta(hours=9)
_DAY = timedelta(days=1)


def _parse_day(s: str):
    """`YYYY-MM-DD` → datetime；非法输入返回 None（不筛，不报错）。"""
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d")
    except Exception:                             # noqa: BLE001
        return None


def ACTION_LABELS(lang: str = "zh") -> Dict[str, str]:
    """动作 → 可读标签（页面/导出共用）。"""
    zh = {"create": "创建", "update": "修改", "dispatch": "派给团队",
          "transfer": "转给其它队",
          "assign": "分派担当", "unassign": "取消担当", "role": "改角色",
          "state": "状态变化", "progress": "进展上报", "remove": "移出",
          "status": "状态", "rename": "改名",
          "wipe": "清空重建", "merge": "合并", "reject": "驳回",
          "return_pool": "退回任务池", "generate": "生成片区与网格",
          "recall": "收回派发", "carry_assignment": "承接原派发",
          "replace_release": "替换版本", "import_natural_grids": "导入自然网格",
          "regroup": "调整片区分组", "merge_grids": "合并网格",
          "absorb_enclosed": "归并内部网格",
          "regroup_by_merchant_density": "按商户密度重组",
          "refresh": "刷新网格密度", "create_batch": "创建巡店批次",
          "assign_region_task": "分配片区任务", "assign_grid_task": "分配网格任务",
          "complete_store_task": "完成门店任务", "update_store_result": "修改巡店结果",
          "grid_status_changed": "网格状态更新", "region_status_changed": "片区状态更新",
          "batch_status_changed": "批次状态更新",
          "grid_auto_completed_empty": "空网格自动完成",
          "region_auto_completed_empty": "空片区自动完成",
          "batch_auto_completed_empty": "空批次自动完成",
          "submit_store_proposal": "提交新店核实",
          "review_store_proposal": "审核新店核实"}
    ja = {"create": "作成", "update": "変更", "dispatch": "チーム割当",
          "transfer": "他チームへ移送",
          "assign": "担当割当", "unassign": "担当解除", "role": "役割変更",
          "state": "状態変更", "progress": "進捗提出", "remove": "退出",
          "status": "状態", "rename": "名称変更",
          "wipe": "クリア再構築", "merge": "統合", "reject": "差戻し",
          "return_pool": "タスクプールへ戻す", "generate": "エリアと区画を生成",
          "recall": "割当を回収", "carry_assignment": "既存割当を引継ぎ",
          "replace_release": "バージョンを置換", "import_natural_grids": "自然区画を取込",
          "regroup": "エリアを再編", "merge_grids": "区画を統合",
          "absorb_enclosed": "内側区画を統合",
          "regroup_by_merchant_density": "店舗密度で再編",
          "refresh": "区画密度を更新", "create_batch": "巡店バッチを作成",
          "assign_region_task": "エリアタスクを割当", "assign_grid_task": "区画タスクを割当",
          "complete_store_task": "店舗タスクを完了", "update_store_result": "巡店結果を変更",
          "grid_status_changed": "区画状態を更新", "region_status_changed": "エリア状態を更新",
          "batch_status_changed": "バッチ状態を更新",
          "grid_auto_completed_empty": "空の区画を自動完了",
          "region_auto_completed_empty": "空のエリアを自動完了",
          "batch_auto_completed_empty": "空のバッチを自動完了",
          "submit_store_proposal": "新店舗確認を申請",
          "review_store_proposal": "新店舗確認を審査"}
    return ja if lang == "ja" else zh


def DOMAIN_LABELS(lang: str = "zh") -> Dict[str, str]:
    zh = {"task": "任务", "team": "团队", "member": "成员", "station": "车站",
          "zone": "片区", "grid": "网格", "grid_density": "网格密度", "message": "站内消息"}
    ja = {"task": "タスク", "team": "チーム", "member": "メンバー",
          "station": "駅", "zone": "担当エリア", "grid": "作業区画",
          "grid_density": "区画密度", "message": "メッセージ"}
    return ja if lang == "ja" else zh


_ENUM_LABELS = {
    "zh": {
        "role": {"leader": "队长", "member": "队员", "admin": "管理员",
                 "staff": "员工"},
        "state": {"unassigned": "未分派", "doing": "进行中", "done": "已完成"},
        "task_status": {"active": "进行中", "completed": "已完成",
                         "pending": "待开始", "assigned": "已分派",
                         "in_progress": "进行中"},
        "review_status": {"pending": "待确认", "confirmed": "已确认",
                           "adjusted": "已调整", "rejected": "已驳回"},
        "team_status": {"active": "启用", "closed": "停用"},
        "member_status": {"active": "在岗", "leave": "休假",
                           "resigned": "离职", "disabled": "停用"},
        "station_status": {"active": "启用", "inactive": "停用"},
        "business_status": {"normal": "正常", "abnormal": "异常",
                             "uncertain": "存疑", "closed": "关店"},
    },
    "ja": {
        "role": {"leader": "リーダー", "member": "メンバー", "admin": "管理者",
                 "staff": "スタッフ"},
        "state": {"unassigned": "未割当", "doing": "進行中", "done": "完了"},
        "task_status": {"active": "進行中", "completed": "完了",
                         "pending": "未開始", "assigned": "割当済み",
                         "in_progress": "進行中"},
        "review_status": {"pending": "確認待ち", "confirmed": "確認済み",
                           "adjusted": "調整済み", "rejected": "差戻し済み"},
        "team_status": {"active": "有効", "closed": "停止"},
        "member_status": {"active": "在職", "leave": "休暇",
                           "resigned": "退職", "disabled": "停止"},
        "station_status": {"active": "有効", "inactive": "無効"},
        "business_status": {"normal": "正常", "abnormal": "異常",
                             "uncertain": "要確認", "closed": "閉店"},
    },
}


def display_value(value, field: str = "", domain: str = "", lang: str = "zh") -> str:
    """把日志值中的已知枚举转为界面文案；未知值和持久化值保持不变。"""
    if value is None or value == "":
        return ""
    labels = _ENUM_LABELS["ja" if lang == "ja" else "zh"]
    field_key = (field or "").strip().lower()
    domain_key = (domain or "").strip().lower()

    def enum_label(key, raw):
        if not isinstance(raw, str):
            return raw
        key = str(key or "").strip().lower()
        group = {
            "role": "role",
            "state": "state",
            "status": {"team": "team_status", "member": "member_status",
                       "station": "station_status", "task": "task_status",
                       "zone": "task_status", "grid": "task_status"}.get(domain_key),
            "business_status": "business_status",
            "review_status": "review_status",
        }.get(key)
        return labels.get(group, {}).get(raw, raw) if group else raw

    text = str(value)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        group = {"role": "role", "state": "state", "status": {
            "team": "team_status", "member": "member_status",
            "station": "station_status", "task": "task_status",
            "zone": "task_status", "grid": "task_status",
        }.get(domain_key), "business_status": "business_status",
            "review_status": "review_status"}.get(field_key)
        return labels.get(group, {}).get(text, text) if group else text

    def translate(item):
        if isinstance(item, dict):
            return {key: translate(enum_label(key, nested))
                    if isinstance(nested, str) else translate(nested)
                    for key, nested in item.items()}
        if isinstance(item, list):
            return [translate(nested) for nested in item]
        return item

    return json.dumps(translate(parsed), ensure_ascii=False, separators=(",", ":"))
