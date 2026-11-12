# -*- coding: utf-8 -*-
"""从「A3整线放射图及执行说明」PDF 抽「**线路 → 站名**」映射，落成 JSON 数据文件。

用户 2026-10-03：「如果有线路信息，比如日比谷线，加上线路信息，没有就算了」。
PDF 里确实有（页头形如 `XC01 · 井の頭線`，每页一条线 + 沿线站名 + 状态标记）。

**只跑一次**：产出 `scripts/bd_station_lines.json`，之后补线路用
`scripts/bd_backfill_lines.py`（不需要 pypdf）。

依赖 `pypdf`（项目 venv 没有；临时安装即可）：

    ./.venv/bin/python -m pip install --target /tmp/dsh_pdflib pypdf
    PYTHONPATH=/tmp/dsh_pdflib ./.venv/bin/python scripts/bd_extract_lines.py \
        --src "/Users/liuchang/Desktop/万总/task_plan" \
        --out scripts/bd_station_lines.json
"""
import argparse
import glob
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: PDF 里表示"这个站后面跟的是状态"的标记
STATUSES = {"已做", "进行中", "本队·新增", "参考·非新增", "县外·未开放",
            "原任务待核", "新增"}
#: 页头：`XC01 · 井の頭線`
LINE_RE = re.compile(r"^([A-Z]{2}\d{2}(?:\+[A-Z]{2}\d{2})?)\s*[·・]\s*(.+)$")
SKIP_RE = re.compile(r"^(?:\d+|[A-Z]|[A-Z]{2}\d{2}\b|站序|铁路资料|特别说明"
                     r"|完整来源|浅蓝底|完成按|每条铁路|线路索引|本队新增)")


def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).strip().replace(" ", "")


def read_pdf_lines(path):
    from pypdf import PdfReader
    out = []
    for pg in PdfReader(path).pages:
        txt = pg.extract_text() or ""
        out += [l.strip() for l in txt.splitlines()]
    return [l for l in out if l]


def extract_one(path):
    """→ (lines: {line_name: {"code":..., "stations":[...]}}, station2line)"""
    lines, cur = {}, None
    station2line = {}
    txt = read_pdf_lines(path)
    i = 0
    while i < len(txt):
        tok = txt[i]
        m = LINE_RE.match(tok)
        if m and not m.group(2).startswith("整线"):
            cur = {"code": m.group(1), "name": norm(m.group(2)),
                   "stations": []}
            lines.setdefault(cur["name"], cur)
            i += 1
            continue
        if tok in STATUSES:
            # 往回找最近的"站名"（跳过单个字母担当、页眉行、状态行）
            j = i - 1
            name = None
            while j >= 0 and j >= i - 3:
                cand = txt[j]
                if cand in STATUSES or re.fullmatch(r"[A-Z]", cand):
                    j -= 1
                    continue
                if (LINE_RE.match(cand) or SKIP_RE.match(cand)
                        or len(cand) > 12 or cand.startswith("小川队")
                        or cand.startswith("汤静队") or cand.startswith("甘子杰队")
                        or cand.startswith("罗子傑队") or cand.startswith("陈嘉溢队")
                        or cand.startswith("陳偉鋒队")):
                    break
                name = norm(cand)
                break
            if name and cur is not None:
                if name not in cur["stations"]:
                    cur["stations"].append(name)
                station2line.setdefault(name, cur["name"])
        i += 1
    return lines, station2line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="含 *执行说明.pdf 的目录")
    ap.add_argument("--out", default="scripts/bd_station_lines.json")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.src, "*执行说明.pdf")))
    if not files:
        print("✗ 没找到 PDF：%s" % args.src)
        return 2
    lines, s2l = {}, {}
    for f in files:
        team = os.path.basename(f).split("_")[0]
        try:
            ln, st = extract_one(f)
        except Exception as e:                      # noqa: BLE001
            print("⚠️ %s 解析失败：%s" % (team, e))
            continue
        for k, v in ln.items():
            lines.setdefault(k, v)
        for k, v in st.items():
            s2l.setdefault(k, v)
        print("%-10s 线路 %d 条 / 站 %d 个" % (team, len(ln), len(st)))

    data = {"source": os.path.abspath(args.src), "station_to_line": s2l,
            "lines": {k: v for k, v in sorted(lines.items())}}
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print("=" * 70)
    print("线路合计 %d 条，覆盖车站 %d 个 → %s" % (len(lines), len(s2l), out))
    print("样例:", list(s2l.items())[:5])
    return 0


if __name__ == "__main__":
    sys.exit(main())
