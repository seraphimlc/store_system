# -*- coding: utf-8 -*-
"""自动生成员工登录名：姓名 → 拼音小写（风格如 ganzijie/ganzijie），
冲突自动加编号/序号；无法拼音化的名字回退到编号尾段。"""
import re

from pypinyin import Style, lazy_pinyin

_ALNUM = re.compile(r"[^a-z0-9]+")
_CODE_TAIL = 6  # 兜底取编号后 6 位


def _from_name(name: str) -> str:
    """汉字转拼音（无音调小写）；其余字符保留并清洗为 a-z0-9。"""
    parts = lazy_pinyin(name or "", style=Style.NORMAL, errors="default")
    raw = _ALNUM.sub("", "".join(parts).lower())
    return raw


def _from_code(person_code: str) -> str:
    digits = re.sub(r"\D", "", person_code or "")
    return "emp" + (digits[-_CODE_TAIL:] or "0")


def suggest_username(name: str, person_code: str) -> str:
    """基础登录名（不含唯一性）。"""
    base = _from_name(name)
    if len(base) >= 3:
        return base
    return _from_code(person_code)


def unique_username(db, name: str, person_code: str,
                    taken_extra=None, base=None) -> str:
    """生成未被占用的登录名。taken_extra: 本次会话内已占用的集合。
    base: 可选，指定基础登录名（如模型生成的拼音/罗马字）；默认按姓名生成。"""
    from app.models import User
    taken = set(taken_extra or ())
    taken.update(u.username for u in db.query(User.username).all())
    base = base or suggest_username(name, person_code)
    if base not in taken:
        return base
    # 追加编号尾 4 位
    digits = re.sub(r"\D", "", person_code or "")
    cand = (base + digits[-4:]) if digits else (base + "x")
    if cand not in taken:
        return cand
    # 再冲突则加序号
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


# ---------- 模型生成登录名（中文拼音 / 日文罗马字 / 英文原名） ----------
_CACHE: dict = {}


def _clean_cand(s: str) -> str:
    # 登录名候选：小写 a-z0-9 与连字符（罗马字如 ogawa-itsuru 保留连字符）
    return re.sub(r"[^a-z0-9-]+", "", (s or "").lower()).strip("-")


def ai_login_candidates(names) -> dict:
    """批量让模型为姓名生成登录名候选：
    中文 → 拼音；日文（汉字/假名）→ 罗马字；英文 → 原名清洗。
    返回 {name: candidate}（只含模型给出且可清洗的）；失败/未配置 → {}。
    结果按姓名缓存（进程内），避免重复调用。
    """
    names = [n for n in names if n]
    if not names:
        return {}
    from app.services import ai_chat
    if not ai_chat.configured():
        return {}
    todo = [n for n in names if n not in _CACHE]
    if todo:
        prompt = (
            "你是登录名生成助手。下面是一些员工的姓名（可能是中文、日文、"
            "英文），请为每个姓名给出一个适合做系统登录名的小写候选："
            "中文名→汉语拼音；日本名（汉字/平假名/片假名）→日文罗马字；"
            "英文名→原样小写。只保留 a-z0-9 与小写，可含连字符，去掉空格。"
            "只返回 JSON，如 {\"小川逸\":\"ogawa-itsuru\", ...}：\n"
            + "\n".join(names))
        try:
            import json as _json
            text = ai_chat.chat(prompt)
            data = _json.loads(text)
            if isinstance(data, dict):
                for n, c in data.items():
                    clean = _clean_cand(c)
                    if clean:
                        _CACHE[n] = clean
        except Exception:  # noqa: BLE001
            pass
    return {n: _CACHE[n] for n in names if n in _CACHE}


def login_name_for(db, display_name: str, person_code: str) -> str:
    """员工登录名：模型候选优先（拼音/罗马字），否则规则拼音/编号；再唯一化。"""
    cand = ai_login_candidates([display_name]).get(display_name)
    return unique_username(db, display_name, person_code, base=cand)
