# -*- coding: utf-8 -*-
"""**AI 派工建议**（2026-10-06 用户："你想做哪个就做哪个" → 选这个，价值最高）。

用途：管理端「未分配」tab 的车站池 → 让模型给出"**哪几站派给哪个队 + 为什么**"的建议，
管理员**勾选采纳**后走既有的 `/tasks/assign` 完成派队。

## 边界（沿用本项目已立下的规矩）
- **数字一律由程序算**：各队负担（未完成/停滞/主要线路）与车站清单都是代码查出来的，
  模型只做"**分组 + 写理由**"。
- **只建议、不写库**：本模块**一个写操作都没有**（不建任务、不派队、不落库）；
  采纳 = 管理员在页面上勾选 → 走 `bd_tasks.assign_team_to_places`。
- **输出必须可核对**：模型给的站名/队伍名**逐个对照真实数据校验**，
  编造的站名、不存在的队伍、重复的车站一律丢弃并回报（`dropped`），
  宁可少给建议也不能给假的。
- **AI 不可用不影响主流程**：未配置/调用失败 → 抛 `SuggestError`，页面显示一句话，手动分配照常。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import func, or_

from app.models import BdLine, BdStation, BdStationPlace, BdTask, BdTeam, BdTeamMember

PROMPT_VERSION = 2        # prompt/口径变更时 +1（建议不落库，仅用于排查与日志）
#: 一次最多喂多少个车站（太多会稀释注意力，也会烧 token）；超了提示先按线路筛
POOL_LIMIT = 150


class SuggestError(Exception):
    """建议生成失败（页面显示这句话，不影响手动分配）。"""


def pool_digest(db, kw: str = "", line_id: Optional[int] = None,
                limit: int = POOL_LIMIT) -> Dict:
    """**未分配车站**的确定性摘要（按线路+顺序排，给模型看）。

    复用车站池的同一口径（`bd_tasks.list_unassigned_places`）——
    绝不自己另写一套"未分配"判据（否则建议与页面会对不上）。
    """
    from app.services import bd_tasks
    d = bd_tasks.list_unassigned_places(db, kw=kw, line_id=line_id, all_rows=True)
    rows = d["rows"]
    out = []
    for r in rows[:limit]:
        st = (db.query(BdStation, BdLine)
              .outerjoin(BdLine, BdLine.id == BdStation.line_id)
              .filter(BdStation.place_id == r["place"].id)
              .order_by(BdStation.seq.is_(None), BdStation.seq.asc()).first())
        pref = st[0].pref if st else ""
        line_name = (st[1].operator_short + " " + st[1].name) if st and st[1] else \
            (st[0].line if st else "")
        out.append({"place_id": r["place"].id, "name": r["name"],
                    "lines": r["lines_text"], "line": line_name, "pref": pref})
    return {"total": d["total"], "rows": out, "truncated": max(0, d["total"] - len(out))}


def team_digest(db, stale_days: int = 2) -> List[dict]:
    """**各队当前负担**（确定性）：成员/在岗/未完成/停滞/已完成 + 主要做过的线路。"""
    from app.services import bd_tasks, bd_teams
    from app.services.date_plan import jst_today
    summary = {s["team_id"]: s for s in bd_tasks.team_board_summary(db, stale_days)}
    # 各队"主要线路"：按 该队任务 → 物理车站 → 线路 计数（取前 3）
    pl = db.query(BdStation.place_id, BdLine.operator_short, BdLine.name).join(
        BdLine, BdLine.id == BdStation.line_id).subquery()
    line_rows = (db.query(BdTask.team_id, pl.c.operator_short, pl.c.name,
                          func.count(BdTask.id))
                 .join(pl, pl.c.place_id == BdTask.place_id)
                 .filter(BdTask.team_id.isnot(None))
                 .group_by(BdTask.team_id, pl.c.operator_short, pl.c.name).all())
    top: Dict[int, List[str]] = {}
    for tid, op, nm, n in sorted(line_rows, key=lambda x: -x[3]):
        top.setdefault(tid, [])
        if len(top[tid]) < 3:
            top[tid].append("%s %s×%d" % (op or "", nm, n))
    out = []
    for t in db.query(BdTeam).order_by(BdTeam.id).all():
        ms = bd_teams.team_members(db, t.id, active_only=True, today=jst_today())
        on_duty = [m for m in ms if m.get("status") in ("active", "", None)]
        s = summary.get(t.id, {})
        out.append({
            "team_id": t.id, "name": t.name, "members": len(ms),
            "on_duty": len(on_duty),
            "open_tasks": (s.get("doing", 0) + s.get("unassigned", 0)),
            "doing": s.get("doing", 0), "done": s.get("done", 0),
            "stale": s.get("stale", 0), "total": s.get("total", 0),
            "top_lines": top.get(t.id, []),
        })
    return out


def build_prompt(pool: Dict, teams: List[dict]) -> str:
    """拼 prompt：**规则写死**（不许编站名、只输出 JSON），数字全部来自上面的确定性摘要。"""
    lines = ["你是巡店外勤的派工助手。请把下面「还没有派给任何队伍」的车站，",
             "分配给合适的队伍，并说明理由。", "",
             "规则（必须遵守）：",
             "1. **站名只能从下面的车站清单里选，一字不差照抄**；不许编造、不许改写。",
             "2. **队伍只能用下面列出的队伍名**。",
             "3. 一个车站只能出现在一个队伍里（别重复给）。",
             "4. 尽量**就近成片**：同一条线路、同一个都道府県的车站分给同一个队。",
             "5. **均衡负担**：现在未完成任务多的队少分一些；已经做过该线路/该县的队优先。",
             "6. 每队 5～20 个站；队伍不够就少分，**不要硬凑**。",
             "7. **只输出 JSON**，不要任何解释文字、不要 markdown 代码块。",
             "8. summary 与 reason **一律用简体中文**（不要繁体、不要日文汉字）。", "",
             "输出格式：",
             '{"summary": "一句话总览（简体中文，40字内）",',
             ' "groups": [{"team": "队伍名", "stations": ["站名1", "站名2"],',
             '             "reason": "为什么给这个队（简体中文，40字内，说明依据：线路/县/现有负担）"}]}',
             "", "## 未分配车站（共 %d 个" % pool["total"]
             + ("，本次只列前 %d 个，请只在这些里选）" % len(pool["rows"])
                if pool["truncated"] else "）")]
    for r in pool["rows"]:
        lines.append("- %s · %s · %s" % (r["line"] or r["lines"], r["pref"], r["name"]))
    lines += ["", "## 各队当前负担"]
    for t in teams:
        lines.append("- %s：成员 %d（在岗 %d），未完成 %d（进行中 %d），停滞 %d，"
                     "已完成 %d，主要线路：%s"
                     % (t["name"], t["members"], t["on_duty"], t["open_tasks"],
                        t["doing"], t["stale"], t["done"],
                        "、".join(t["top_lines"]) or "暂无"))
    return "\n".join(lines)


def _clean(v) -> str:
    return str(v or "").strip()


def suggest(db, kw: str = "", line_id: Optional[int] = None,
            timeout: int = 300) -> Dict:
    """生成建议：**模型只分组 + 写理由**，其余全部由代码校验。

    返回 `{"summary", "groups":[{team_id, team_name, stations:[{place_id,name}], reason}],
    "dropped": {...}, "usage", "v"}`。任何调用/解析失败 → `SuggestError`。
    """
    from app.services import ai_chat

    if not ai_chat.configured():
        raise SuggestError("AI 未配置（.env 里没有 AI_API_KEY / AI_BASE_URL）")
    pool = pool_digest(db, kw=kw, line_id=line_id)
    if not pool["rows"]:
        raise SuggestError("当前筛选下没有未分配的车站")
    teams = team_digest(db)
    if not teams:
        raise SuggestError("还没有队伍，先建队再派工")
    prompt = build_prompt(pool, teams)

    try:
        res = ai_chat.chat(prompt, timeout=timeout, return_usage=True)
    except Exception as e:                                  # noqa: BLE001
        raise SuggestError("AI 调用失败：%s" % e)
    if isinstance(res, tuple):           # return_usage=True → (text, usage)
        text, usage = res
    else:                                 # 兜底：万一实现变了
        text, usage = res, {}
    data = ai_chat.extract_json(text) if text else None
    if not isinstance(data, dict):
        raise SuggestError("模型没返回可解析的 JSON")

    # ---- 校验层（**这一步是重点**：模型可能会编站名/编队伍/重复分站）----
    by_name = {}
    for r in pool["rows"]:
        by_name.setdefault(_clean(r["name"]), r)
    team_by_name = {_clean(t["name"]): t for t in teams}
    used, groups, dropped = set(), [], {"station": 0, "team": 0, "dup": 0}
    for g in (data.get("groups") or []):
        if not isinstance(g, dict):
            continue
        tname = _clean(g.get("team"))
        t = team_by_name.get(tname)
        if t is None:
            # 允许"队伍名里带/不带「队」"这种小差异（实测模型常这么写）
            cand = [x for k, x in team_by_name.items()
                    if k and (k in tname or tname in k)]
            t = cand[0] if len(cand) == 1 else None
        if t is None:
            dropped["team"] += 1
            continue
        sts = []
        for nm in (g.get("stations") or []):
            nm = _clean(nm)
            r = by_name.get(nm)
            if r is None:
                dropped["station"] += 1
                continue
            if r["place_id"] in used:
                dropped["dup"] += 1
                continue
            used.add(r["place_id"])
            sts.append({"place_id": r["place_id"], "name": r["name"],
                        "line": r["line"], "pref": r["pref"]})
        if not sts:
            continue
        groups.append({"team_id": t["team_id"], "team_name": t["name"],
                       "stations": sts, "reason": _clean(g.get("reason"))[:120]})
    if not groups:
        raise SuggestError("模型给的建议一条都没通过校验（站名/队伍对不上），"
                           "请再试一次或手动分配")
    groups.sort(key=lambda x: -len(x["stations"]))
    return {"summary": _clean(data.get("summary"))[:120], "groups": groups,
            "dropped": dropped, "usage": usage, "v": PROMPT_VERSION,
            "pool_total": pool["total"], "n_pool_shown": len(pool["rows"])}
