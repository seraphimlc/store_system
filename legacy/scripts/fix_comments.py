# -*- coding: utf-8 -*-
"""修复被 i18n 脚本破坏的 {# 注释：还原为原文。"""
import glob, re

FIXES = {
    "app/templates/perf.html": [
        ("{# {{ t('该期点数/金额映射 #') }}}", "{# 该期点数/金额映射 #}"),
    ],
    "app/templates/recon.html": [
        ("{# ---------- {{ t('员工 × 日 明细（问题行） ---------- #') }}}",
         "{# ---------- 员工 × 日 明细（问题行） ---------- #}"),
        ("{# ---------- {{ t('人月差异（找平入口） ---------- #') }}}",
         "{# ---------- 人月差异（找平入口） ---------- #}"),
    ],
}

for f, pairs in FIXES.items():
    s = open(f, encoding="utf-8").read()
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new)
            print("fixed", f, old[:30])
        else:
            print("NOT FOUND in", f, ":", old[:40])
    open(f, "w", encoding="utf-8").write(s)
