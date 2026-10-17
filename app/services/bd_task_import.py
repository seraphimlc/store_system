# -*- coding: utf-8 -*-
"""批量任务导入（作业域）：线路执行计划 Excel → 车站任务。

背景：用户 2026-10-15/16 让我手工导过 3 次（小川队 A 线 106 个 / 汤静队 B 线 63 个 /
甘子杰 B 线 31 个），每次都是写一次性脚本、dry-run 看过才写库。本模块把那套流程
产品化，网页端与 MCP 共用同一份口径。

**口径（与当时的手工脚本逐条对齐，不要自行放宽）**：
- **绝不改已有任务**：站上已有任务 → 跳过并回报（不改归属/状态/进度）。
- **不新建车站**：车站表里匹配不上 → 报出来让人处理，**不猜、不新建**。
- **dry-run 优先**：`build_plan` 只读；`apply_plan` 才写，且写前复核已有任务没被动。
- **必须能认出负责队**：文件里没有可识别的负责队列 → **整份拒收**
  （用户 2026-10-16 定稿：不猜队名，要求交文件方写清楚）。
- 站群「A／B」拆开分别匹配；一处匹配不上就整行算「对不上」。
"""
import collections
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 表头行探测：最多往下找几行
HEADER_SCAN_ROWS = 12

#: 列名同义词（表上写法 → 内部列）。命中即认，越靠前越优先。
COL_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "line": ("铁路线路", "路線", "路线", "线路", "线名"),
    "station": ("车站／站群", "车站/站群", "站区", "车站", "車站", "站群", "站名"),
    "team": ("负责队", "负责队伍", "既有归属", "归属", "担当队", "队"),
    "source": ("任务来源", "来源"),
    "status": ("状态", "进展状态"),
    "done_date": ("完成日", "完成日期"),
    "note": ("备注", "说明", "记事"),
    "grade": ("巡回分级", "分级", "级别"),
    "batch": ("建议批次", "批次"),
}
#: 认表头行至少要认出这两列，否则不算表头行
REQUIRED_COLS = ("station",)
#: 负责队列必须能认出来，否则整份拒收
TEAM_COL = "team"

#: 状态/完成日里出现这些词 → 认为表上标为已完成，不建任务
DONE_WORDS = ("已完成", "完了", "完成済")
#: 「任务来源」列里出现这些词 → 该行是「原任务保留」，只核对不新建
KEEP_WORDS = ("原任务保留", "原有任务", "已分配保留")


class TaskImportError(Exception):
    """导入被拒（参数/文件/口径问题）。网页与 MCP 都转为可读提示。"""


def save_blob(content: bytes) -> Tuple[str, str]:
    """把上传的计划文件存盘，返回 (sha256, 路径)。

    与巡店上传共用 `upload_dir`，但放 `task_plan/` 子目录：计划文件不进
    `imports` 表，不该混进「巡店文件列表」里造成误解。
    """
    import hashlib
    from app.config import get_settings
    sha = hashlib.sha256(content).hexdigest()
    d = os.path.join(get_settings().upload_dir, "task_plan")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, sha + ".xlsx")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(content)
    return sha, path


def blob_path(sha: str) -> str:
    """按 sha 还原文件路径。**只接受 64 位十六进制**，杜绝目录穿越。"""
    import re
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha or "")):
        raise TaskImportError("文件标识不正确，请重新上传")
    from app.config import get_settings
    return os.path.join(get_settings().upload_dir, "task_plan", sha + ".xlsx")


@dataclass
class PlanRow:
    """一行计划 = 一个站群行。"""
    row_no: int
    line: str = ""
    station_raw: str = ""
    team_raw: str = ""
    source: str = ""
    status: str = ""
    done_date: str = ""
    note: str = ""
    team_id: Optional[int] = None
    team_name: str = ""
    kind: str = "new"            # new / keep(原任务保留) / done / reference
    hits: List[Tuple[int, str, str]] = field(default_factory=list)  # (place_id, how, 用到的名字)
    missing: List[str] = field(default_factory=list)
    near: List[str] = field(default_factory=list)
    create: List[Tuple[int, str, str]] = field(default_factory=list)   # (place_id, 现名, 线路)
    existing: List[Tuple[int, str, str, str]] = field(default_factory=list)  # (place_id, 现名, 队, 线路)
    skip_reason: str = ""
    decision: str = ""           # create / skip / bad
    place_name: str = ""
    line_name: str = ""


def norm(s: Any) -> str:
    """NFKC + 去首尾空白（表上全角/半角混用很常见）。"""
    return unicodedata.normalize("NFKC", str(s if s is not None else "")).strip()


def _split_multi(raw: str) -> List[str]:
    """站群「A／B」「A/B」「A、B」拆成多段。"""
    t = norm(raw).replace("/", "／")
    out = []
    for chunk in t.replace("、", "／").replace("，", "／").split("／"):
        c = chunk.strip()
        if c and c not in out:
            out.append(c)
    return out


#: 站名里的「附注」括号对（旧名 / 别名 / 运营方）：文件常写
#: 「獨協大学前〈草加松原〉」而库里是「獨協大学前」（2026-10-17 实测，
#: 東武スカイツリーライン）。`（京急）` 那类运营方后缀本来就是同一机制，
#: 只是括号不同 → 统一成一张表。
#: ⚠️ NFKC 会把全角「（）」「＜＞」压成半角，所以半角那几个也要留着。
_ANNOT_PAIRS = (
    ("（", "）"), ("(", ")"), ("〈", "〉"), ("〔", "〕"), ("【", "】"),
    ("［", "］"), ("[", "]"), ("＜", "＞"), ("<", ">"),
)


def _name_variants(part: str) -> List[str]:
    """一个站名段的候选写法：**原名 → 去掉「附注」后缀**（括住的那部分）。

    ⚠️ **原名永远排第一**：只在原名匹配不上时才试去附注的写法 →
    不会改变任何现有匹配结果，只会让"带旧名/别名"的手工文件也能对上。
    """
    out = [part]
    for op, cl in _ANNOT_PAIRS:
        if op in part and part.endswith(cl):
            base = part[:part.find(op)].strip()
            if base and base not in out:
                out.append(base)
    return [x for x in out if x]


def _base_name(part: str) -> str:
    """去掉「附注」后的站名（没有附注就原样返回）——给"近似站名"提示用。"""
    vs = _name_variants(part)
    return vs[-1]


#: 日文站名里的小假名混写：表上写「保土ケ谷」而库里是「保土ヶ谷」（2026-10-16 实测）。
#: 归一到同一个键来匹配，省得每来一份文件都要人工加别名。
_KANA_FOLD = (("ヶ", "ケ"), ("ヵ", "カ"), ("ゖ", "ケ"))


def kana_key(s: str) -> str:
    """站名比对键：NFKC + 小假名归一（ケ/ヶ 视为同一个字）。"""
    t = norm(s)
    for a, b in _KANA_FOLD:
        t = t.replace(a, b)
    return t


# ---------------------------------------------------------------- 布局识别

def sheet_n_cols(path: str) -> int:
    """工作表有多少列（前 60 行里最宽的那行 + 声明宽度）——预览页列下拉要用。"""
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    return max([ws.max_column or 0] + [len(r) for r in rows[:60]])


def detect_layout(path: str) -> Dict[str, Any]:
    """自动识别表头行与列映射。认不出「车站」列 → 抛 TaskImportError。"""
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    n_cols = sheet_n_cols(path)
    best = None
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        # 逐列打分：**完全相同（3 分）> 包含（1 分）**。真实表里「路线编号」包含
        # 「路线」而「铁路线路」才是线路列 —— 不加这层优先级会把编号列认成线路
        # （2026-10-16 实测：小川队 A 线表因此把 line 认到第 0 列）。
        score_of: Dict[str, Tuple[int, int]] = {}
        for j, cell in enumerate(row):
            text = norm(cell)
            if not text:
                continue
            for key, syns in COL_SYNONYMS.items():
                s = 3 if text in syns else (1 if any(x in text for x in syns) else 0)
                if not s:
                    continue
                # 编号/序号/ID 列不是业务列（「路线编号」「駅ID」）
                if s == 1 and key in ("line", "station") and \
                        any(w in text for w in ("编号", "序号", "ID", "コード")):
                    continue
                cur = score_of.get(key)
                if cur is None or s > cur[0]:
                    score_of[key] = (s, j)
        cols = {k: v[1] for k, v in score_of.items()}
        if all(k in cols for k in REQUIRED_COLS):
            # 表头行评分：认出的列数优先，其次精确命中多者优先
            score = (len(cols), sum(1 for v in score_of.values() if v[0] == 3))
            if best is None or score > best[0]:
                best = (score, i + 1, cols)
    if best is None:
        raise TaskImportError(
            "认不出表头：前 %d 行里找不到「车站/站区」这类列名。"
            "请在文件里保留一行表头（含 线路、车站、负责队 等列名）再传"
            % HEADER_SCAN_ROWS)
    _score, header_row, cols = best
    return {"header_row": header_row, "cols": cols, "n_cols": n_cols,
            "source": "auto"}


def read_rows(path: str, layout: Dict[str, Any]) -> List[PlanRow]:
    """按布局读数据行（跳过表头行本身与其上方的标题/说明行）。"""
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True).active
    cols = layout.get("cols") or {}
    header_row = int(layout.get("header_row") or 1)
    if not cols.get("station"):
        raise TaskImportError("列映射里缺少「车站」列，请在预览页指定")

    def cell(row, key):
        j = cols.get(key)
        if j is None or j >= len(row):
            return ""
        return norm(row[j])

    out = []
    for i, row in enumerate(ws.iter_rows(values_only=True), 1):
        if i <= header_row:
            continue
        station = cell(row, "station")
        if not station:
            continue
        out.append(PlanRow(
            row_no=i, line=cell(row, "line"), station_raw=station,
            team_raw=cell(row, "team"), source=cell(row, "source"),
            status=cell(row, "status"), done_date=cell(row, "done_date"),
            note=cell(row, "note")))
    if not out:
        raise TaskImportError("表头行下面没有数据行（车站列为空）")
    return out


# ---------------------------------------------------------------- 车站索引

def build_index(db) -> Tuple[Dict[str, Dict[str, int]], Dict[str, set], Dict[int, str],
                             Dict[int, str]]:
    """(线路→{站名: place_id}, 站名→{place_id}, place→站名, place→线路名)。"""
    from sqlalchemy import text
    from app.services import bd_lines
    by_line: Dict[str, Dict[str, int]] = {}
    by_name: Dict[str, set] = collections.defaultdict(set)
    names: Dict[int, str] = {}
    lines_seen: Dict[int, List[str]] = collections.defaultdict(list)
    q = db.execute(text("""SELECT s.place_id, s.name, l.name, l.operator_short
            FROM bd_station s JOIN bd_line l ON l.id = s.line_id
            WHERE s.place_id IS NOT NULL""")).all()
    for pid, nm, lname, lshort in q:
        if not pid or not nm:
            continue
        names[pid] = nm
        if lname and lname not in lines_seen[pid]:
            lines_seen[pid].append(lname)
        key = kana_key(nm)
        by_line.setdefault(bd_lines.norm_line(lname), {})[key] = pid
        by_line.setdefault(bd_lines.norm_line((lshort or "") + (lname or "")), {})[key] = pid
        by_name[key].add(pid)
    # 一个物理车站可能属于多条线路（如巢鴨 = 山手線 + 三田線）→ 全列出来，
    # 免得预览页拿最后一条盖住文件里的那条，看着像匹配错了（2026-10-16 实测）。
    lines_of: Dict[int, str] = {}
    for pid, ls in lines_seen.items():
        lines_of[pid] = "／".join(ls[:3]) + ("…" if len(ls) > 3 else "")
    return by_line, by_name, names, lines_of


def _match_one(by_line, by_name, line_raw: str, name: str) -> Tuple[Optional[int], str]:
    """一个站名 → place_id。strict（线路精确）→ loose（线路弱匹配）→ name-only（全库唯一同名）。"""
    from app.services import bd_lines
    key = kana_key(name)
    parts = [norm(p) for p in
             (line_raw or "").replace("，", "、").replace("・", "、").split("、") if norm(p)]
    for base in parts:
        hit = by_line.get(bd_lines.norm_line(base), {}).get(key)
        if hit:
            return hit, "strict"
    for base in parts:
        core = bd_lines.norm_line(base).lstrip("0123456789").replace("号線", "")
        found = None
        for k, m in by_line.items():
            kk = k.lstrip("0123456789").replace("号線", "")
            if key in m and core and (kk == core or (len(core) >= 3 and (core in kk or kk in core))):
                if found and found != m[key]:
                    return None, "宽松匹配命中多个线路"
                found = m[key]
        if found:
            return found, "loose"
    cand = by_name.get(key) or set()
    if len(cand) == 1:
        return next(iter(cand)), "name-only"
    if len(cand) > 1:
        return None, "同名多站(%d)" % len(cand)
    return None, "车站表里找不到"


def _resolve_group(by_line, by_name, line_raw: str,
                   station_raw: str) -> Tuple[List[Tuple[int, str, str]], List[str]]:
    """一个站群行 → (命中列表, 对不上的段)。"""
    hits: List[Tuple[int, str, str]] = []
    missing: List[str] = []
    for part in _split_multi(station_raw):
        got = None
        for cand in _name_variants(part):
            pid, how = _match_one(by_line, by_name, line_raw, cand)
            if pid:
                got = (pid, how, cand)
                break
        if got is None:
            missing.append(part)
        elif all(got[0] != h[0] for h in hits):
            hits.append(got)
    return hits, missing


def _near_names(by_name, text: str, limit: int = 8) -> List[str]:
    """近似站名（给人工处理时参考）。键已做过小假名归一。"""
    key = _split_multi(text)[0] if _split_multi(text) else norm(text)
    key = kana_key(_base_name(key))
    if not key:
        return []
    found = [n for n in by_name if key in n or n in key]
    return sorted(found)[:limit]


# ---------------------------------------------------------------- 负责队

def resolve_team(db, raw: str) -> Optional[Tuple[int, str]]:
    """表上队名 → (team_id, 库里队名)。

    精确 → 双向包含（「小川队」↔「A小川队」）。**不猜**：认不出返回 None。
    """
    from app.models import BdTeam
    val = norm(raw)
    if not val:
        return None
    teams = db.query(BdTeam).all()
    for t in teams:
        if norm(t.name) == val:
            return t.id, t.name
    contains = [t for t in teams if val in norm(t.name) or norm(t.name) in val]
    if len(contains) == 1:
        return contains[0].id, contains[0].name
    return None


# ---------------------------------------------------------------- 生成计划

def build_plan(db, path: str, layout: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
    """只读：解析文件 + 匹配车站 + 分类。**不写库。**

    返回 {layout, rows, summary, team_names, rejects}；行上有 decision：
    - create  该建任务
    - skip    不该建（原任务保留且已有 / 表上已完成 / 参考行）
    - bad     对不上，需人工处理
    """
    layout = layout or detect_layout(path)
    if not layout.get("n_cols"):       # 人工改过列映射时也要有列数（预览页下拉要用）
        layout = dict(layout, n_cols=sheet_n_cols(path))
    rows = read_rows(path, layout)

    # 负责队：整份都认不出 → 拒收（用户 2026-10-16 定稿）
    resolved_cache: Dict[str, Optional[Tuple[int, str]]] = {}
    for r in rows:
        key = norm(r.team_raw)
        if key not in resolved_cache:
            resolved_cache[key] = resolve_team(db, key) if key else None
        got = resolved_cache[key]
        if got:
            r.team_id, r.team_name = got

    teams_found = sorted({norm(r.team_raw) for r in rows if norm(r.team_raw)})
    if not any(r.team_id for r in rows):
        raise TaskImportError(
            "文件里认不出「负责队」：需要有一列写明每行归哪个队，且队名能和系统里的队对上。"
            "本表读到的队名是 %s。请让对方把负责队列写清楚再传。"
            % ("、".join(teams_found) if teams_found else "（没有这一列）"))

    by_line, by_name, place_names, line_names = build_index(db)
    from app.models import BdTask
    task_by_place = {t.place_id: t for t in db.query(BdTask).all() if t.place_id}
    team_by_id = {}
    from app.models import BdTeam
    for t in db.query(BdTeam).all():
        team_by_id[t.id] = t.name

    for r in rows:
        if not r.team_id:
            r.decision = "skip"
            r.skip_reason = ("负责队「%s」在系统里对不上" % r.team_raw) if r.team_raw \
                else "这一行没写负责队"
            continue
        # 表上标为已完成 → 不建（先确认勿重复做）
        if r.done_date or any(w in r.status for w in DONE_WORDS):
            r.decision = "skip"
            r.skip_reason = "表上标为已完成（%s）" % (r.done_date or r.status)
            continue
        r.hits, r.missing = _resolve_group(by_line, by_name, r.line, r.station_raw)
        if r.missing or not r.hits:
            r.decision = "bad"
            r.near = _near_names(by_name, r.station_raw)
            continue
        r.kind = "keep" if any(w in r.source for w in KEEP_WORDS) else "new"
        r.decision = "skip"
        for pid, how, used in r.hits:
            nm = place_names.get(pid, "")
            ln = line_names.get(pid, "")
            t = task_by_place.get(pid)
            if t is not None:
                r.existing.append((pid, nm, team_by_id.get(t.team_id) or "未分配", ln))
            elif r.kind == "new":
                r.create.append((pid, nm, ln))
            else:
                r.skip_reason = "标为「原任务保留」但库里没有这个任务"
        if r.create:
            r.decision = "create"
        elif r.existing:
            r.decision = "skip"
            if not r.skip_reason:
                r.skip_reason = "已有任务，不改归属"
        r.place_name = "、".join(x[1] for x in (r.create or r.existing))
        r.line_name = (r.create or r.existing)[0][2] if (r.create or r.existing) else ""

    creates = [x for r in rows if r.decision == "create" for x in r.create]
    bad_rows = [r for r in rows if r.decision == "bad"]
    return {
        "layout": layout,
        "rows": rows,
        "summary": {
            "rows": len(rows),
            "create_rows": sum(1 for r in rows if r.decision == "create"),
            "create_stations": len({pid for pid, _n, _l in creates}),
            "existing": sum(len(r.existing) for r in rows),
            "skipped": sum(1 for r in rows if r.decision == "skip"),
            "bad": len(bad_rows),
            "teams": sorted({r.team_name for r in rows if r.team_id}),
        },
        "team_names_found": teams_found,
    }


# ---------------------------------------------------------------- 落库

def apply_plan(db, path: str, layout: Optional[Dict[str, Any]] = None,
               skip_rows: Sequence[int] = (), actor_user=None,
               actor_name: str = "") -> Dict[str, Any]:
    """按计划建任务。**只建没有任务的站**，已有任务一律不动。

    skip_rows：预览页勾了「跳过这些行」的行号（对不上的行必须被勾掉才允许继续）。
    """
    plan = build_plan(db, path, layout)
    rows = plan["rows"]
    skip = {int(x) for x in (skip_rows or [])}

    unresolved = [r.row_no for r in rows if r.decision == "bad" and r.row_no not in skip]
    if unresolved:
        raise TaskImportError(
            "还有 %d 行对不上车站（行 %s），请勾选「跳过这些行」或先在车站表里补站"
            % (len(unresolved), "、".join(str(x) for x in unresolved[:12])))

    from app.models import BdTask
    from app.services import bd_tasks
    from app.services.date_plan import jst_today

    by_team: Dict[int, List[int]] = collections.defaultdict(list)
    for r in rows:
        if r.decision != "create" or r.row_no in skip or not r.team_id:
            continue
        for pid, _nm, _ln in r.create:
            if pid not in by_team[r.team_id]:      # 同一个站被两行提到 → 只建一个
                by_team[r.team_id].append(pid)

    # 写前留证据：现有任务归属快照（写后复核没被动）
    before_snapshot = {t.id: t.team_id for t in db.query(BdTask).all()}
    before_counts = collections.Counter(before_snapshot.values())

    created_total, created_skipped, per_team = 0, 0, []
    for tid, pids in sorted(by_team.items()):
        res = bd_tasks.create_tasks_for_places(
            db, pids, by=(actor_name or "import:task-file"), team_id=tid,
            assign_date=jst_today(), actor_user=actor_user)
        db.commit()
        created_total += res["created"]
        created_skipped += res["skipped"]
        per_team.append({"team_id": tid, "team": res["team_name"],
                         "created": res["created"], "skipped": res["skipped"]})

    # 复核：已有任务的归属一个都不许变。
    # ⚠️ 这里**不能**说"已回滚" —— `create_tasks_for_places` 内部已经 commit 过，
    # 到这一步新建的任务是落定的；只能如实报错，让管理员去查（正常路径不该触发）。
    changed = []
    for t in db.query(BdTask).all():
        old = before_snapshot.get(t.id)
        if old is not None and old != t.team_id:
            changed.append((t.id, old, t.team_id))
    if changed:
        raise TaskImportError(
            "导入后发现已有任务的归属被改动了（%d 条，例如 task#%s：%s → %s）。"
            "新建的 %d 个任务已入库，请立即核对任务列表与操作日志"
            % (len(changed), changed[0][0], changed[0][1], changed[0][2],
               created_total))

    return {
        "created": created_total,
        "skipped": created_skipped,          # 建任务时又发现已有任务而跳过的（正常应为 0）
        "skipped_rows": plan["summary"]["skipped"],
        "by_team": per_team,
        "rows": plan["summary"]["rows"],
        "existing_untouched": plan["summary"]["existing"],
        "before_counts": {str(k): v for k, v in before_counts.items()},
    }
