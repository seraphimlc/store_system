# -*- coding: utf-8 -*-
"""ORM 模型（spec v0.4 §4.1–4.8）。JSON 一律 sqlalchemy.JSON（SQLite/PG 兼容）。

阶段二对账仅空表骨架（recon_* / self_report_*），逻辑见对账设计文档。
"""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint, Index)
from sqlalchemy.orm import relationship

from app.db import Base


def _now():
    return datetime.utcnow()


# ---------- §4.1 ----------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(64), nullable=False, default="")
    role = Column(String(16), nullable=False, default="admin")  # admin / staff
    person_code = Column(String(32), nullable=True)  # staff 绑定人员编号
    is_active = Column(Boolean, nullable=False, default=True)
    # 员工状态：active 在岗 / leave 请假 / disabled 停用 / resigned 离职
    # 停用、离职 → is_active=False（禁登录）；数据与历史记录永不删除
    status = Column(String(16), nullable=False, default="active")
    # 首次登录/口令被管理员重置后必须改密（True=未改，除改密/退出外一律拦截）
    must_change_password = Column(Boolean, nullable=False, default=False)
    # 界面语言：zh / ja / 空=自动（URL→cookie→浏览器语言）；员工可账号级指定
    lang = Column(String(8), nullable=False, default="", server_default="")
    created_at = Column(DateTime, nullable=False, default=_now)

    @property
    def can_login(self) -> bool:
        """停用/离职不可登录；在岗/请假可登录。"""
        return self.status in ("active", "leave")


# ---------- §4.2 ----------
class ImportFile(Base):
    __tablename__ = "imports"
    id = Column(Integer, primary_key=True)
    file_name = Column(String(255), nullable=False)
    file_sha256 = Column(String(64), unique=True, nullable=False)
    file_size = Column(Integer, nullable=False, default=0)
    stored_path = Column(String(512), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, nullable=False, default=_now)
    month_label = Column(String(16), nullable=True)
    format = Column(String(16), nullable=True)
    header_row = Column(Integer, nullable=True)
    data_start_row = Column(Integer, nullable=True)
    # 解析布局（AI 识别 + 人工可纠正）：
    # {"header_row": 2, "cols": {...}, "value_map": {"visible": {...}, "deploy": {...}},
    #  "source": "ai"|"manual"|"rule"}
    layout = Column(JSON, nullable=True)
    parsed_sheets = Column(JSON, nullable=False, default=list)
    ignored_sheets = Column(JSON, nullable=False, default=list)
    total_rows = Column(Integer, nullable=False, default=0)
    parsed_rows = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="uploaded")  # uploaded/parsed/failed
    warnings = Column(JSON, nullable=False, default=list)
    errors = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=_now)


# ---------- §4.3 ----------
class RawRecord(Base):
    __tablename__ = "raw_records"
    __table_args__ = (
        UniqueConstraint("import_id", "sheet_name", "excel_row",
                         name="uq_raw_import_sheet_row"),
    )
    id = Column(Integer, primary_key=True)
    import_id = Column(Integer, ForeignKey("imports.id"), nullable=False)
    sheet_name = Column(String(128), nullable=False)
    excel_row = Column(Integer, nullable=False)
    store_id_raw = Column(Text, nullable=False, default="")
    store_name_local_raw = Column(Text, nullable=False, default="")
    store_name_en_raw = Column(Text, nullable=False, default="")
    modified_raw = Column(Text, nullable=False, default="")
    submitter_raw = Column(Text, nullable=False, default="")
    submitter_code = Column(String(32), ForeignKey("persons.code"), nullable=True)
    record_id_raw = Column(Text, nullable=False, default="")
    visible_raw = Column(String(8), nullable=True)      # YES/NO/空
    deploy_raw = Column(String(8), nullable=True)
    original_row = Column(JSON, nullable=False, default=list)
    # 追溯主锚（由全局去重回填）：判定状态 + 被哪条有效记录挤掉
    clean_status = Column(String(16), nullable=True)
    # final/dup_by_id/dup_by_name/visible_blank/manual_void
    filtered_by_raw_id = Column(Integer, nullable=True)
    # V3 过滤原因枚举：master_late/from_sub/cross_file_dup/no_ref（空=有效/空白）
    filter_reason = Column(String(20), nullable=True)
    # 员工确认：pending/approved/disputed/auto_ok（跨文件重复自动认可，不进任务）
    confirm_state = Column(String(24), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, default=_now)


# ---------- §4.4 ----------
class Person(Base):
    __tablename__ = "persons"
    code = Column(String(32), primary_key=True)
    display_name = Column(String(64), nullable=False, default="")
    first_seen_import_id = Column(Integer, ForeignKey("imports.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


# ---------- §4.5 ----------
class DashMetric(Base):
    """看板统计物化表：月份-统计项-数值（person 可空=全公司），生成后读表零聚合。"""
    __tablename__ = "dash_metrics"
    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, index=True)
    metric = Column(String(64), nullable=False)
    value = Column(Float, nullable=False, default=0)
    payload = Column(Text, nullable=True, default=None)   # 列表型指标(JSON字符串)
    person = Column(String(32), nullable=True, default=None)
    updated_at = Column(DateTime, nullable=False, default=_now)
    __table_args__ = (UniqueConstraint("month", "metric", "person",
                                       name="uq_dash_metric"),)


class StaffAnalysis(Base):
    """员工月度分析（算完工资后自动生成，存表供看板读取）。"""
    __tablename__ = "staff_analyses"
    id = Column(Integer, primary_key=True)
    person_code = Column(String(32), nullable=False, index=True)
    month = Column(String(7), nullable=False, index=True)
    content = Column(Text, nullable=False, default="")
    fingerprint = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now,
                        onupdate=_now)
    __table_args__ = (UniqueConstraint("person_code", "month",
                                       name="uq_staff_analysis_month"),)


class SysConfig(Base):
    """系统配置（每点金额/达标点数/达标奖金）：全局单值，最新一条生效（id 最大）。"""
    __tablename__ = "sys_configs"
    id = Column(Integer, primary_key=True)
    config_month = Column(String(7), nullable=False, default="",
                            server_default="")  # 空=无月份语义（仅记录，不参与匹配）
    per_point = Column(Integer, nullable=False, default=250)
    bonus_group = Column(Integer, nullable=False, default=68)
    bonus_amount = Column(Integer, nullable=False, default=3000)
    # 员工可见起始月（YYYY-MM）：员工端只显示该月及之后的数据；空=不限制
    staff_visible_from = Column(String(7), nullable=False, default="",
                                server_default="")
    updated_by = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=_now)


class ReconTask(Base):
    __tablename__ = "recon_tasks"
    id = Column(Integer, primary_key=True)
    kind = Column(String(16), nullable=False)   # person_points / daily_records
    source_import_id = Column(Integer, ForeignKey("imports.id"), nullable=True)
    status = Column(String(16), nullable=False, default="pending")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_now)
    finished_at = Column(DateTime, nullable=True)
    params = Column(JSON, nullable=False, default=dict)
    summary = Column(JSON, nullable=False, default=dict)


class ReconResult(Base):
    __tablename__ = "recon_results"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("recon_tasks.id"), nullable=False)
    japan_date = Column(Date, nullable=True)
    store_id = Column(Text, nullable=True)
    submitter_code = Column(String(32), nullable=True)
    system_value = Column(Integer, nullable=True)
    report_value = Column(Integer, nullable=True)
    diff = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False)
    family = Column(String(16), nullable=True)   # B-1/B-2/B-3
    confirmed = Column(Boolean, nullable=False, default=False)
    note = Column(Text, nullable=True)


class AiRun(Base):
    """B组 AI 批处理运行记录（供页面展示进度/结果）。"""
    __tablename__ = "ai_runs"
    id = Column(Integer, primary_key=True)
    total_pairs = Column(Integer, nullable=False, default=0)
    status = Column(String(12), nullable=False, default="running")  # running/done
    summary = Column(JSON, nullable=False, default=dict)
    started_at = Column(DateTime, nullable=False, default=_now)
    finished_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class StoreEntity(Base):
    """店铺实体档案：一个 store_id_raw 一行；master_id 自指=主档，否则指主档。

    V3：主档 = 一家真实店铺的代表实体（master_id 自指）；
    从档 = 同店不同编号，master_id 指向主档实体。
    raw_data_id = 该 store_id 在原始数据中最早出现的那条 raw；
    从档冗余 master_store_id / master_raw_data_id（少一次查询）。
    """
    __tablename__ = "store_entities"
    id = Column(Integer, primary_key=True)
    store_id_raw = Column(String(64), unique=True, nullable=False)
    name_local = Column(Text, nullable=False, default="")       # 最早出现写法
    name_norm = Column(Text, nullable=False, default="")        # 归一化（NFKC去空白小写）
    city = Column(String(64), nullable=True)
    address_local = Column(Text, nullable=True)   # 宽表 Store Address-Local（可空）
    first_seen_import_id = Column(Integer, ForeignKey("imports.id"), nullable=True)
    master_id = Column(Integer, ForeignKey("store_entities.id"), nullable=False)
    # V3：归属主档的 store_id（主档时等于自己）；从档冗余
    master_store_id = Column(String(64), nullable=True)
    # 该店最早出现的原始记录 id（主档/从档都维护各自 raw_data_id）
    raw_data_id = Column(Integer, ForeignKey("raw_records.id"), nullable=True)
    # 从档冗余：主档实体最早原始记录 id
    master_raw_data_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now)
    note = Column(Text, nullable=True)


class StorePair(Base):
    """判重候选对：exact=归一化全等（程序必同）；fuzzy=高相似（人工/AI）。"""
    __tablename__ = "store_pairs"
    __table_args__ = (UniqueConstraint("entity_a", "entity_b",
                                       name="uq_storepair_ab"),)
    id = Column(Integer, primary_key=True)
    entity_a = Column(Integer, ForeignKey("store_entities.id"), nullable=False)
    entity_b = Column(Integer, ForeignKey("store_entities.id"), nullable=False)
    kind = Column(String(8), nullable=False)                    # exact / fuzzy
    sim = Column(Integer, nullable=True)                        # 相似度 x100
    status = Column(String(10), nullable=False, default="pending")  # pending/merged/skip
    note = Column(Text, nullable=True)
    ai_decision = Column(String(8), nullable=True)             # same/diff/unknown
    ai_confidence = Column(String(8), nullable=True)           # high/medium/low
    ai_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class StoreMergeLog(Base):
    """主档合并留痕：basis ∈ program_exact / manual / ai（预留）。"""
    __tablename__ = "store_merge_logs"
    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer, ForeignKey("store_entities.id"), nullable=False)
    from_master = Column(Integer, nullable=False)
    to_master = Column(Integer, nullable=False)
    basis = Column(String(16), nullable=False)
    note = Column(Text, nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class FormalRecord(Base):
    """V3 正式数据：文件判定后入此表（算绩效的唯一依据）。"""
    __tablename__ = "formal_records"
    # min/max(japan_date) 出现在每个报告/对比/导出请求里 → 必须有索引，
    # 否则 3 万行的正式表每次全表扫描（评审实测 SCAN formal_records）。
    __table_args__ = (Index("ix_formal_japan_date", "japan_date"),)
    id = Column(Integer, primary_key=True)
    import_id = Column(Integer, ForeignKey("imports.id"), nullable=False)
    raw_record_id = Column(Integer, ForeignKey("raw_records.id"),
                           nullable=False, unique=True)
    person_code = Column(String(32), ForeignKey("persons.code"), nullable=True)
    store_id_raw = Column(Text, nullable=False, default="")
    japan_date = Column(Date, nullable=True)
    points = Column(Integer, nullable=False, default=0)   # 1 或 2
    created_at = Column(DateTime, nullable=False, default=_now)


class AppealRecord(Base):
    """V3 绩效申诉：员工对被滤(master_late 等)记录逐条申诉，管理员处理。

    - 仅 master_late（同店更早、跨日疑似重复）可申诉——员工主张这条应算有效。
    - status: pending(待处理) / approved(申诉成立→改判有效) /
      rejected(申诉驳回→维持被滤)。
    - approve 后由 finalize 重算并入正式表；reject 后员工不可再申诉该条。
    """
    __tablename__ = "appeals"
    __table_args__ = (UniqueConstraint("raw_record_id",
                                       name="uq_appeal_raw"),)
    id = Column(Integer, primary_key=True)
    raw_record_id = Column(Integer, ForeignKey("raw_records.id"),
                           nullable=False, unique=True)
    import_id = Column(Integer, ForeignKey("imports.id"), nullable=False)
    submitter_code = Column(String(32), ForeignKey("persons.code"),
                            nullable=False)
    reason = Column(Text, nullable=False, default="")   # 员工申诉理由
    status = Column(String(12), nullable=False, default="pending")
    # pending / approved / rejected
    created_at = Column(DateTime, nullable=False, default=_now)
    handled_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    handled_at = Column(DateTime, nullable=True)


class AdjustRecord(Base):
    """V3 找平：对账确认的差异 → 下月工资加/减（留痕）。

    一个对账任务 × 一名员工最多一条确认（确认可取消=删除记录）。
    amount 为円：正=下月补发，负=下月扣回；applied_to_month = month 的下一月。
    """
    __tablename__ = "adjust_records"
    __table_args__ = (UniqueConstraint("source_task_id", "person_code",
                                       name="uq_adjust_task_person"),)
    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False)            # 差异所属月（被对账月）
    applied_to_month = Column(String(7), nullable=False)  # 生效月 = 下一月
    person_code = Column(String(32), nullable=False)
    amount = Column(Integer, nullable=False, default=0)   # 点数（正补/负扣，参考）
    per_point = Column(Integer, nullable=False, default=250)  # 发生时单价(円/点)，纠偏按此价
    amount_adj = Column(Integer, nullable=False, default=0)  # 金额增量(円，含奖金)：确认时写入薪资找平的金额
    reason = Column(Text, nullable=False, default="")
    source_task_id = Column(Integer, nullable=False)      # 来源对账任务
    source_points_diff = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class ReconDayRow(Base):
    """V3 日级对账：按 (员工 × 日) 的比对明细（只存"问题行"）。

    问题行 = 本地点数与对账点数不一致，或只有一侧有记录。
    diff = 本地点数 - 对账点数；side: both / local_only / report_only。
    """
    __tablename__ = "recon_day_rows"
    __table_args__ = (UniqueConstraint("task_id", "ref_date", "person_code",
                                       name="uq_recon_day_task_date_person"),
                      Index("ix_recon_day_task", "task_id"))
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=False)
    ref_date = Column(Date, nullable=False)              # 巡店日期（日本日期）
    person_code = Column(String(32), nullable=False)
    sys_points = Column(Integer, nullable=False, default=0)   # 本地（系统）
    rep_points = Column(Integer, nullable=False, default=0)   # 对账文件
    sys_count = Column(Integer, nullable=False, default=0)    # 点数来源行数
    rep_count = Column(Integer, nullable=False, default=0)
    diff = Column(Integer, nullable=False, default=0)
    side = Column(String(12), nullable=False, default="both")
    note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=_now)


class PersonDailyStat(Base):
    """本地绩效统计表（人 × 日）：由正式表刷写，供月度对账任务直接查询。"""
    __tablename__ = "person_daily_stats"
    __table_args__ = (UniqueConstraint("person_code", "ref_date",
                                       name="uq_pdstat_person_date"),
                      Index("ix_pdstat_date", "ref_date"))
    id = Column(Integer, primary_key=True)
    person_code = Column(String(32), nullable=False)
    ref_date = Column(Date, nullable=False)
    records = Column(Integer, nullable=False, default=0)   # 当日有效店数
    p1 = Column(Integer, nullable=False, default=0)
    p2 = Column(Integer, nullable=False, default=0)
    points = Column(Integer, nullable=False, default=0)    # p1 + p2*2


class ReconDataRow(Base):
    """对账数据表：外部对账文件解析后按 (任务 × 日期 × 员工) 全量落库。"""
    __tablename__ = "recon_data_rows"
    __table_args__ = (UniqueConstraint("task_id", "ref_date", "person_code",
                                       name="uq_recon_data_task_date_person"),
                      Index("ix_recon_data_task", "task_id"))
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=False)
    ref_date = Column(Date, nullable=False)
    person_code = Column(String(32), nullable=False)
    person_name = Column(String(64), nullable=False, default="")
    points = Column(Integer, nullable=False, default=0)
    cnt = Column(Integer, nullable=False, default=0)       # 对账侧行数（店数）
    created_at = Column(DateTime, nullable=False, default=_now)


class PayrollPeriodRow(Base):
    """月度分期对账偏差表：月 × 员工。

    上半月(1-15)/下半月(16-月末) = 系统正式表该段点数（已分期发给现场）；
    对账点数 = 该月对账文件点数（期末终算）；上月修正 = 上月该员工对账偏差；
    本月对账偏差 = 对账 − (上半月+下半月) + 上月修正（偏差>0=少付需补，<0=多付需扣；
    生成后管理员可手改，手改值在重新生成时保留）。
    """
    __tablename__ = "payroll_period_rows"
    __table_args__ = (UniqueConstraint("month", "person_code",
                                        name="uq_period_month_code"),)
    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False)          # YYYY-MM
    person_code = Column(String(32), nullable=False)
    half1_points = Column(Integer, nullable=False, default=0)   # 1-15 点数
    half2_points = Column(Integer, nullable=False, default=0)   # 16-月末 点数
    # 分期店数快照（同步时间点固化；发薪单据不随人×日表后续变化漂移）
    half1_records = Column(Integer, nullable=False, default=0)  # 上半月有效店
    half1_p1 = Column(Integer, nullable=False, default=0)       # 上半月1点店
    half1_p2 = Column(Integer, nullable=False, default=0)       # 上半月2点店
    half2_records = Column(Integer, nullable=False, default=0)  # 下半月有效店
    half2_p1 = Column(Integer, nullable=False, default=0)       # 下半月1点店
    half2_p2 = Column(Integer, nullable=False, default=0)       # 下半月2点店
    settle_points = Column(Integer, nullable=False, default=0)  # 对账点数
    prev_adjust_points = Column(Integer, nullable=False, default=0)  # 上月修正点数(递延上月找平)
    diff_points = Column(Integer, nullable=False, default=0)    # 对账偏差(点,系统参考,自动刷)
    # 金额(円)
    half1_bonus = Column(Integer, nullable=False, default=0)   # 上半月奖金(满68=3000)
    half2_bonus = Column(Integer, nullable=False, default=0)   # 下半月奖金(满68=3000)
    half1_amount = Column(Integer, nullable=False, default=0)   # 上半月实发=点数×250+奖金
    half2_amount = Column(Integer, nullable=False, default=0)   # 下半月实发=点数×250+奖金
    settle_amount = Column(Integer, nullable=False, default=0)  # 对账金额(自动=工资规则×对账点数)
    prev_adjust_amount = Column(Integer, nullable=False, default=0)  # 上月修正金额(递延)
    diff_amount = Column(Integer, nullable=False, default=0)    # 对账偏差金额(系统参考,自动刷)
    adjust_points = Column(Integer, nullable=False, default=0)  # 找平点数(人工执行,默认0)
    adjust_amount = Column(Integer, nullable=False, default=0)  # 找平金额(=点数×该月单价)
    updated_by = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=_now)


class MonthPerfRecord(Base):
    """月绩效工资记录（入统计表/重算时物化，每员工每月一条）。

    打开绩效工资页只查本表，不再实时聚合正式表；
    明细经页面「详细」按钮查正式表/统计表。
    """
    __tablename__ = "month_perf_records"
    __table_args__ = (UniqueConstraint("month", "person_code",
                                        name="uq_mpf_month_code"),)
    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False)
    person_code = Column(String(32), nullable=False)
    records = Column(Integer, nullable=False, default=0)   # 有效店数
    p1 = Column(Integer, nullable=False, default=0)
    p2 = Column(Integer, nullable=False, default=0)
    points = Column(Integer, nullable=False, default=0)    # p1 + p2*2
    salary = Column(Integer, nullable=False, default=0)    # 工资 = 点数×单价 + 满68奖3000
    per_point = Column(Integer, nullable=False, default=250)  # 该月单价(円/点)=当前点数金额
    # 月度对账完成后更新（来自薪资找平表）
    settle_points = Column(Integer, nullable=False, default=0)   # 对账点数
    settle_amount = Column(Integer, nullable=False, default=0)   # 对账金额(円)
    diff_points = Column(Integer, nullable=False, default=0)     # 本月对账偏差(点)
    diff_amount = Column(Integer, nullable=False, default=0)     # 本月对账偏差金额(円)
    rate37 = Column(Float, nullable=False, default=0.0)
    pass37 = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_now)


# ---------------------------------------------------------------------------
# P1（WorkBuddy 接入）：Token / 审计 / 封账 / 重算快照
# 设计见 docs/superpowers/specs/2026-09-22-workbuddy-p1-write-tools-design.md
# ---------------------------------------------------------------------------

class ApiToken(Base):
    """MCP Access Token（绑定到人，可吊销）。spec §5.1。"""
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False, default="")
    token_prefix = Column(String(16), nullable=False, unique=True, index=True)
    token_hash = Column(String(64), nullable=False)          # sha256 十六进制
    scopes = Column(String(64), nullable=False, default="read")   # read / read,write
    created_at = Column(DateTime, nullable=False, default=_now)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    # 有效期：默认签发 90 天；管理员可签永久（NULL=永久）。spec §三
    expires_at = Column(DateTime, nullable=True)


class McpAuditLog(Base):
    """MCP 调用审计（两阶段写入：先插行 ok=NULL，返回前回填）。spec §8。"""
    __tablename__ = "mcp_audit_log"

    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, nullable=True)
    user_id = Column(Integer, nullable=True)
    tool = Column(String(64), nullable=True)
    params_json = Column(Text, nullable=True)                # 脱敏，不含 Token 明文
    ok = Column(Boolean, nullable=True)                      # 执行中为 NULL
    error_code = Column(String(32), nullable=True)
    detail = Column(Text, nullable=True)
    client_info = Column(String(255), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class OAuthClient(Base):
    """MCP OAuth 动态注册客户端（specs-mcp-oauth.md §三）。

    `client_secret_hash` 为 NULL = 公共客户端（PKCE 强制，WorkBuddy 即此类）；
    库内只存哈希，明文只在注册响应中出现一次。
    """
    __tablename__ = "oauth_clients"

    id = Column(Integer, primary_key=True)
    client_id = Column(String(64), unique=True, nullable=False, index=True)
    client_secret_hash = Column(String(64), nullable=True)
    client_name = Column(String(128), nullable=False, default="")
    redirect_uris = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=_now)
    last_used_at = Column(DateTime, nullable=True)


class OAuthCode(Base):
    """授权码（5 分钟有效，一次性）。

    `access_token_id`：该 code 换出的 access token 行 id —— code 被重放时据此
    吊销已换出的 token（specs-mcp-oauth.md §五.2 / 验收 4）。
    """
    __tablename__ = "oauth_codes"

    id = Column(Integer, primary_key=True)
    code_hash = Column(String(64), unique=True, nullable=False, index=True)
    client_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    redirect_uri = Column(String(512), nullable=False)
    code_challenge = Column(String(128), nullable=False)
    code_challenge_method = Column(String(16), nullable=False, default="S256")
    scope = Column(String(64), nullable=False, default="read")
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    access_token_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class OAuthRefreshToken(Base):
    """Refresh token（90 天，每次刷新轮换：旧的置 revoked_at）。"""
    __tablename__ = "oauth_refresh_tokens"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    client_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    scope = Column(String(64), nullable=False, default="read")
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    last_used_at = Column(DateTime, nullable=True)


class SealedMonth(Base):
    """封账月份：表内月份一律禁止写入（闸门 3）。spec §7。"""
    __tablename__ = "sealed_months"

    month = Column(String(7), primary_key=True)              # YYYY-MM
    note = Column(String(255), nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class RebuildSnapshot(Base):
    """重算前正式表全量快照（只能回灌正式表，非整月可逆）。spec §9 改动 B。"""
    __tablename__ = "rebuild_snapshots"

    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, index=True)
    audit_id = Column(Integer, nullable=True)                # 不加 FK：HTML 路径无审计行
    payload_json = Column(Text, nullable=False)
    row_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=_now)


class PayrollPaidMark(Base):
    """发薪标记：某月某期（上半月/下半月）是否已实际发薪。

    为什么需要：找平的「上月结转」要在本月两期工资里扣/补，但**已发薪的期改不了**。
    系统没有发薪动作（只导出发薪表、线下发），所以必须由管理员显式标记；
    `period.sync_period_table` 只把**未标记的期**算作可吸收额度，
    否则会出现「上半月已发、下半月为 0 → 结转被判定已吸收，实际漏扣」。
    """
    __tablename__ = "payroll_paid_marks"
    __table_args__ = (UniqueConstraint("month", "half", name="uq_paid_mark"),)

    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, index=True)     # YYYY-MM
    half = Column(Integer, nullable=False)                    # 1=上半月 2=下半月
    marked_by = Column(Integer, nullable=True)
    marked_at = Column(DateTime, nullable=False, default=_now)


class PayrollPayment(Base):
    """**发放台账（不可变历史事实）**：某月某人第 seq 期实际发了多少。

    为什么要独立成表：`payroll_period_rows` 是**计算结果**（会随数据/对账/规则重算），
    而"已经发了 375,000"是**历史事实**，不能再被重算改写。两者混在一张表里会导致：
    8月对账晚上传 → 9月整行重算 → 已发的上半月数字也被改；且吸收逻辑不知道哪期已发
    → 高估吸收能力 → **漏扣**（实测 -59,000 被误判为已吸收）。

    - seq 不固定：一月发 2 次就 seq=1,2；发 3 次就 1,2,3（不写死两期）
    - amount 是**发放时快照**，之后重算不影响它
    - adjust_applied 记该期实际抵扣/补发的找平额（负=扣）
    """
    __tablename__ = "payroll_payments"
    __table_args__ = (UniqueConstraint("month", "person_code", "seq",
                                       name="uq_payment_inst"),)

    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, index=True)      # 归属结算月 YYYY-MM
    person_code = Column(String(32), nullable=False, index=True)
    seq = Column(Integer, nullable=False)                      # 第几期（1,2,3…）
    points = Column(Integer, nullable=False, default=0)        # 该期点数（快照）
    amount = Column(Integer, nullable=False, default=0)        # 实发金额 円（快照）
    bonus = Column(Integer, nullable=False, default=0)         # 其中奖金 円
    adjust_applied = Column(Integer, nullable=False, default=0)  # 该期实际抵扣/补发 円
    note = Column(String(255), nullable=True)
    paid_at = Column(DateTime, nullable=False, default=_now)
    paid_by = Column(Integer, nullable=True)
    # ---- 找平抵扣溯源（否则只有一个金额 = 空穴来风，出 bug 没法查）----
    adjust_source_type = Column(String(12), nullable=True)     # carry | diff
    adjust_source_month = Column(String(7), nullable=True)     # 产生结转的月份（上月）
    adjust_source_row_id = Column(Integer, nullable=True)      # 上月 payroll_period_rows.id
    adjust_source_task_id = Column(Integer, nullable=True)     # 上月当前对账任务 id（可空）
    adjust_leftover = Column(Integer, nullable=True)           # 扣完后仍需递延的金额（负）


class PayrollAdjust(Base):
    """**找平表**：一行 = 一笔找平（某月差异 × 某人），进度是**存下来的事实**。

    为什么独立成表（用户要求）：
    - 找平进度（已找平/剩余/是否结清）应是可直接查询的一等数据，
      而不是每次查询靠找平链（prev_adjust_amount）反推；
    - 多月差异交错时链条反推会失真（旧实现只能给"近似回收额"）；
    - 可记录"结清时间/结清于哪一期"，便于对账与 bug 排查；
    - 这张表就是找平的账：谁欠谁、欠多少、还了多少、何时还清。

    约定：
    - adjust_amount：原始找平金额（负=应扣/正=应补，含奖金口径）
    - settled_amount：已找平金额（逐步累加，与 adjust_amount 同号）
    - remaining：剩余（= adjust_amount − settled_amount；0 = 已结清）
    - 回收顺序：**FIFO**（先欠的先还），一笔发放吸收的金额优先冲最早的未结清找平
    """
    __tablename__ = "payroll_adjusts"
    __table_args__ = (UniqueConstraint("source_month", "person_code",
                                       name="uq_adjust_person"),)

    id = Column(Integer, primary_key=True)
    source_month = Column(String(7), nullable=False, index=True)   # 产生差异的月份
    person_code = Column(String(32), nullable=False, index=True)
    source_task_id = Column(Integer, nullable=True)                # 对账任务 id
    source_row_id = Column(Integer, nullable=True)                 # 该月找平行 id
    adjust_amount = Column(Integer, nullable=False, default=0)     # 原始找平金额
    settled_amount = Column(Integer, nullable=False, default=0)    # 已找平
    remaining = Column(Integer, nullable=False, default=0)         # 剩余
    status = Column(String(12), nullable=False, default="in_progress")
    settled_at = Column(DateTime, nullable=True)                   # 结清时间
    updated_at = Column(DateTime, nullable=False, default=_now)


class PayrollSettlementLink(Base):
    """**找平回收明细**：发放（payroll_payments）↔ 找平（payroll_adjusts）多对多。

    用户要求双向可查：
    - 一笔薪资（某月某期）→ 冲了哪几笔找平（FIFO 可能同时冲多个月）
    - 一笔找平 → 被哪几期薪资回收的（可能跨多期/多月）
    单一来源字段（payroll_payments.adjust_source_*）只能表达一对一，故独立成表。
    """
    __tablename__ = "payroll_settlement_links"
    __table_args__ = (UniqueConstraint("adjust_id", "payment_id",
                                       name="uq_link_adjust_payment"),)

    id = Column(Integer, primary_key=True)
    adjust_id = Column(Integer, ForeignKey("payroll_adjusts.id"),
                       nullable=False, index=True)      # 哪笔找平
    payment_id = Column(Integer, ForeignKey("payroll_payments.id"),
                        nullable=False, index=True)     # 哪笔发放冲的
    month = Column(String(7), nullable=False, index=True)   # 发放月
    person_code = Column(String(32), nullable=False, index=True)
    amount = Column(Integer, nullable=False, default=0)     # 本次冲抵（负=扣回）
    created_at = Column(DateTime, nullable=False, default=_now)


# ---------- 员工每日填报 + 对比分析报告 ----------
class StaffDailyReport(Base):
    """员工每天提交一次：担当区域 + 1点店铺数 + 2点店铺数。

    填报数据**不参与工资计算**，只用于与文件跑出的 person_daily_stats 对比（规格 D1–D7）。
    一天一条：`(person_code, report_date)` 唯一；`report_date` 为 JST 业务日。
    """
    __tablename__ = "staff_daily_reports"
    __table_args__ = (UniqueConstraint("person_code", "report_date",
                                       name="uq_sdr_person_date"),
                      Index("ix_sdr_date", "report_date"))
    id = Column(Integer, primary_key=True)
    person_code = Column(String(32), ForeignKey("persons.code"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    report_date = Column(Date, nullable=False)              # JST 业务日
    area = Column(String(64), nullable=False, default="", server_default="")
    p1_cnt = Column(Integer, nullable=False, default=0)     # 1 点店铺数
    p2_cnt = Column(Integer, nullable=False, default=0)     # 2 点店铺数
    total_cnt = Column(Integer, nullable=False, default=0)  # = p1_cnt + p2_cnt
    submitted_at = Column(DateTime, nullable=False, default=_now)
    client_ts = Column(String(40), nullable=False, default="", server_default="")
    source = Column(String(16), nullable=False, default="web", server_default="web")
    created_at = Column(DateTime, nullable=False, default=_now)


class StaffReportAnalysis(Base):
    """区间对比分析报告。

    `summary` = **程序算出的**对比结果（数字唯一可信来源）；
    `payload` = **模型产出的**结构化评语（`by_lang.zh/ja`，逐人段落）。
    管理端渲染全部；员工端只渲染 `per_person[自己]`（服务端裁剪，规格 D13/D14/D15）。
    """
    __tablename__ = "staff_report_analyses"
    __table_args__ = (Index("ix_sra_period", "period_start", "period_end"),)
    id = Column(Integer, primary_key=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    status = Column(String(12), nullable=False, default="pending",
                    server_default="pending")        # pending/running/done/failed
    summary = Column(JSON, nullable=False, default=dict)
    payload = Column(JSON, nullable=False, default=dict)
    ai_model = Column(String(64), nullable=False, default="", server_default="")
    ai_tokens = Column(Integer, nullable=False, default=0)
    ai_error = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    finished_at = Column(DateTime, nullable=True)


# ---------- 核对结果物化（员工端只读；报告生成时落表） ----------
class StaffReportComparePerson(Base):
    """对比结果·人×区间汇总（物化）。

    报告生成时写一次；**员工端核对页直接读本表**，不再实时跑 compare()。
    `acc/acc1/acc2` 存 0..1 的准确率（NULL=没有可对照的日子）。
    """
    __tablename__ = "staff_report_compare_person"
    __table_args__ = (UniqueConstraint("analysis_id", "person_code",
                                       name="uq_srcp_analysis_person"),
                      Index("ix_srcp_person", "person_code"))
    id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey("staff_report_analyses.id"),
                         nullable=False)
    person_code = Column(String(32), nullable=False)
    name = Column(String(64), nullable=False, default="", server_default="")
    sys_p1 = Column(Integer, nullable=False, default=0)
    sys_p2 = Column(Integer, nullable=False, default=0)
    sys_total = Column(Integer, nullable=False, default=0)
    rep_p1 = Column(Integer, nullable=False, default=0)
    rep_p2 = Column(Integer, nullable=False, default=0)
    rep_total = Column(Integer, nullable=False, default=0)
    d1 = Column(Integer, nullable=False, default=0)
    d2 = Column(Integer, nullable=False, default=0)
    d_total = Column(Integer, nullable=False, default=0)
    acc = Column(Float, nullable=True)      # 0..1；NULL=没有可对照的日子
    days_filled = Column(Integer, nullable=False, default=0)
    days_system = Column(Integer, nullable=False, default=0)
    days_both = Column(Integer, nullable=False, default=0)
    gaps = Column(Integer, nullable=False, default=0)      # 应填未填
    abs_dt = Column(Integer, nullable=False, default=0)    # Σ|Δ|（准确率分子）
    created_at = Column(DateTime, nullable=False, default=_now)


class StaffReportCompareDay(Base):
    """对比结果·人×日明细（物化）。`kind` ∈ both / missing_report / missing_system。"""
    __tablename__ = "staff_report_compare_day"
    __table_args__ = (UniqueConstraint("analysis_id", "person_code", "ref_date",
                                       name="uq_srcd_analysis_person_date"),
                      Index("ix_srcd_person", "person_code"))
    id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey("staff_report_analyses.id"),
                         nullable=False)
    person_code = Column(String(32), nullable=False)
    ref_date = Column(Date, nullable=False)
    kind = Column(String(16), nullable=False, default="", server_default="")
    sys_p1 = Column(Integer, nullable=True)
    sys_p2 = Column(Integer, nullable=True)
    sys_total = Column(Integer, nullable=True)
    rep_p1 = Column(Integer, nullable=True)
    rep_p2 = Column(Integer, nullable=True)
    rep_total = Column(Integer, nullable=True)
    d1 = Column(Integer, nullable=True)
    d2 = Column(Integer, nullable=True)
    dt = Column(Integer, nullable=True)
    acc = Column(Float, nullable=True)      # 单日准确率（仅 both 日有值）
    created_at = Column(DateTime, nullable=False, default=_now)


class FormToken(Base):
    """**一次性提交令牌**（防重复提交）。

    渲染表单时发放（`{{ form_token() }}`），提交时校验并**立即作废**：
    双击、返回再提交、网络重试、脚本重放都会在第二次被拒。
    `user_id` 为空 = 未登录时发放（登录页豁免，用不到）。
    """
    __tablename__ = "form_tokens"
    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=_now, index=True)
    used_at = Column(DateTime, nullable=True)
