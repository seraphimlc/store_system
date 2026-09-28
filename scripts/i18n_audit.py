# -*- coding: utf-8 -*-
"""i18n 审计：找出「模板用了但字典里没有」和「字典里有但全仓库没人用」的键。

用法：`./.venv/bin/python scripts/i18n_audit.py [--fix-missing]`
- 模板/代码里 `t('...')` 用到的中文 → 必须在 `app/i18n.py` 的字典里有日文
- 字典里的键若在整个仓库（除 i18n.py 外）都找不到该字符串 → 死键
  （注意：`?msg=` / `?err=` 这类**动态传值**的文案在代码里以字符串字面量出现，不会被误判）
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(ROOT, "app", "i18n.py")


def _sources():
    out = {}
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (
            ".git", ".venv", "__pycache__", "node_modules", "data",
            ".pytest_cache", ".agents")]
        for f in files:
            if f.endswith((".py", ".html", ".js")):
                p = os.path.join(root, f)
                if p == I18N:
                    continue
                try:
                    out[p] = io.open(p, encoding="utf-8").read()
                except Exception:
                    pass
    # 测试与脚本里写死的中文不算"界面文案"，否则会漏报死键
    return {p: v for p, v in out.items()
            if not any(seg in p for seg in ("tests_web", "tests/", "scripts/"))}


def _changed_files():
    """本分支相对 main 改过的文件（配合 --changed 只看自己负责的范围）。"""
    import subprocess
    try:
        out = subprocess.run(["git", "diff", "--name-only", "main...HEAD"],
                             cwd=ROOT, capture_output=True, text=True).stdout
    except Exception:
        return set()
    return {os.path.join(ROOT, p) for p in out.split() if p}


def _added_keys():
    """本分支 diff 里**新增**的 t('...') 文案。"""
    import subprocess
    diff = subprocess.run(["git", "diff", "-U0", "main...HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout
    out = set()
    for line in diff.split("\n"):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        out |= set(re.findall(r"\bt\(\s*'([^']+)'\s*\)", line))
        out |= set(re.findall(r'\bt\(\s*"([^"]+)"\s*\)', line))
    return out


def main():
    only_changed = "--changed" in sys.argv
    only_added = "--added" in sys.argv
    i18n = io.open(I18N, encoding="utf-8").read()
    # 一行里可能有多个键，必须全取（原先只取行首，导致误报"缺失"）
    keys = set(re.findall(r'"([^"]+)":\s*"', i18n))
    src = _sources()
    if only_changed:
        keep = _changed_files()
        src = {p: v for p, v in src.items() if p in keep}
    blob = "\n".join(src.values())

    # 模板/代码里 t('中文') 用到的键
    used = set()
    for text in src.values():
        used |= set(re.findall(r"\bt\(\s*'([^']+)'\s*\)", text))
        used |= set(re.findall(r'\bt\(\s*"([^"]+)"\s*\)', text))

    if only_added:
        used = used & _added_keys()
    missing = sorted(k for k in used if k not in keys)
    dead = sorted(k for k in keys if k not in blob)

    print("模板/代码用到但字典缺失（日文界面会显示中文）: %d%s"
          % (len(missing), "（仅本分支改过的文件）" if only_changed else ""))
    for k in missing:
        print("   -", k)
    print()
    if only_added:
        dead = []          # 新增范围下不看死键
    print("字典里有但全仓库没人用（死键）: %d" % len(dead))
    for k in dead:
        print("   -", k)
    print()
    print("合计字典键: %d / 被使用: %d" % (len(keys), len(used & keys)))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
