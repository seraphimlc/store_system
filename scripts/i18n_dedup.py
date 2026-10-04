# -*- coding: utf-8 -*-
"""查 `app/i18n.py` 的**重复键**（同一个中文 key 出现多次）并报告译法冲突。

来源：多轮补日文时用同一个锚点插入会累积重复。Python dict **取最后一个值**，
所以功能上不出错，但重复键会掩盖"同一 key 两个不同译法"这种真问题。

⚠️ **默认只报告**。`--apply` 只清理"**整行只含一个 key**"的重复项（安全）；
一行里挤着多个 key 的不碰（删掉会连累别的译文）——那些要人工统一译法。

    ./.venv/bin/python scripts/i18n_dedup.py            # 报告
    ./.venv/bin/python scripts/i18n_dedup.py --apply    # 只删安全的重复行
"""
import argparse
import os
import re
import sys

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "app", "i18n.py")
KEY_RE = re.compile(r'^\s*"([^"]+)":')
ANY_KEY_RE = re.compile(r'"([^"]+)":')
#: 抓「键 → 值」对（同一行可能多个；值必须同行，跨行的另算）
PAIR_RE = re.compile(r'"([^"]+)":\s*"((?:[^"\\]|\\.)*)"')


def blocks(lines):
    """切「key 行 + 可选续行」块 → [(start, end, keys, text)]（end 不含）。"""
    out = []
    i = 0
    while i < len(lines):
        keys = ANY_KEY_RE.findall(lines[i]) if KEY_RE.match(lines[i]) else []
        if not keys:
            i += 1
            continue
        j = i + 1
        if lines[i].rstrip().endswith(":") or lines[i].count('"') % 2 == 1:
            while j < len(lines) and not lines[j].rstrip().endswith(","):
                j += 1
            j += 1
        j = max(j, i + 1)
        out.append((i, j, keys, "\n".join(lines[i:j])))
        i = j
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    lines = open(PATH, encoding="utf-8").read().splitlines()
    seen = {}
    dup_line = []          # 要删的**较早**重复块（整行单键才安全）
    values = {}            # key → [(值, 行号)]，**按值比较**才准
    order = []
    for start, end, keys, text in blocks(lines):
        pairs = PAIR_RE.findall(text)          # 一行可能挤着多个 key
        for key, val in pairs:
            if key in seen:
                # 保留**最后一次**出现 = 当前 dict 实际生效的值，清理不改译文
                if len(seen[key][2]) == 1:
                    dup_line.append((seen[key][0], seen[key][3], key))
            else:
                order.append(key)
            seen[key] = (start, text, keys, end)
            values.setdefault(key, []).append((val, start + 1))
    conflicts = [(k, values[k]) for k in order
                 if len({v for v, _ln in values[k]}) > 1]

    print("重复键 %d 个；其中**同 key 不同译法** %d 个："
          % (len(dup_line) + len(conflicts), len(conflicts)))
    for key, occ in conflicts:
        print("  ⚠️ %s" % key)
        for val, ln in occ:
            mark = "   ← **当前生效（dict 取最后一个）**" if (val, ln) == occ[-1] else ""
            print("      行%-5d %s%s" % (ln, val[:70], mark))
    print()
    print("可安全清理的单键重复行: %d 个" % len(dup_line))
    if not args.apply:
        print("（默认只报告；加 --apply 才删那些安全的重复行）")
        return 0
    kill = set()
    for start, end, _k in dup_line:
        kill.update(range(start, end))
    if not kill:
        print("没有可安全删除的行（都在多键行里，需人工统一译法）")
        return 0
    kept = [ln for i, ln in enumerate(lines) if i not in kill]
    open(PATH, "w", encoding="utf-8").write("\n".join(kept) + "\n")
    print("已删除 %d 行（保留**最后一次**出现的译法 = 当前生效值，译文不变）"
          % len(kill))
    return 0


if __name__ == "__main__":
    sys.exit(main())
