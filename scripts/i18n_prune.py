# -*- coding: utf-8 -*-
"""清理 i18n 死键：只删「在代码/模板里精确找不到该字符串」的键（默认 dry-run）。

判据：键字符串不得出现在 app/、store_settle/、mcp_service/ 的代码与模板里。
测试与脚本不算"使用"（测试里写死的中文不构成界面文案）。
用法：`./.venv/bin/python scripts/i18n_prune.py`（dry-run）/ `--apply`（真删）
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(ROOT, "app", "i18n.py")
SCAN_DIRS = ("app", "store_settle", "mcp_service", "deploy")

sys.path.insert(0, ROOT)
from scripts.i18n_runtime_keys import RUNTIME_KEYS  # noqa: E402


def _blob():
    parts = []
    for d in SCAN_DIRS:
        for root, dirs, files in os.walk(os.path.join(ROOT, d)):
            dirs[:] = [x for x in dirs if x not in (".venv", "__pycache__")]
            for f in files:
                if f.endswith((".py", ".html", ".js", ".sh", ".yml", ".yaml")):
                    p = os.path.join(root, f)
                    if p == I18N:
                        continue
                    try:
                        parts.append(io.open(p, encoding="utf-8").read())
                    except Exception:
                        pass
    return "\n".join(parts)


def main():
    apply = "--apply" in sys.argv
    text = io.open(I18N, encoding="utf-8").read()
    blob = _blob()
    keys = re.findall(r'"([^"]+)":\s*"', text)
    dead = []
    for k in keys:
        if k in blob:
            continue
        # 太短的键容易误判（如 "或"）：即使命中率低也保守跳过
        if len(k) < 3 or k in RUNTIME_KEYS:
            continue
        dead.append(k)
    print("拟删除死键 %d 个：" % len(dead))
    for k in sorted(dead):
        print("   -", k)
    if not apply:
        print("\n（dry-run；加 --apply 才真删）")
        return 0
    for k in dead:
        text = re.sub(r'\n?\s*"%s": "[^"]*",' % re.escape(k), "", text, count=1)
    io.open(I18N, "w", encoding="utf-8").write(text)
    print("\n已删除 %d 个死键" % len(dead))
    return 0


if __name__ == "__main__":
    sys.exit(main())
