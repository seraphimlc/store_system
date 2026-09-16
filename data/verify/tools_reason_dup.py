# -*- coding: utf-8 -*-
"""逐行归因基准重复表 533 行：判定它被滤的原因类别，并核对总数。
输出: 每类原因计数；供 judge_import 校准。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3, json, unicodedata
from collections import defaultdict, Counter

def norm(s):
    if not s: return ""
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(s.casefold().split())

con = sqlite3.connect("store_settle_aug5.db")
cur = con.cursor()
cur.execute("SELECT id, format FROM imports")
fmt = {r[0]: r[1] for r in cur.fetchall()}
cur.execute("SELECT import_id, store_id_raw, modified_raw, visible_raw, record_id_raw, original_row FROM raw_records")
rows = cur.fetchall()
raw_all = []
for r in rows:
    if (r[2] or "").startswith("2026-08") and (r[3] or "").strip() in ("YES", "NO"):
        o = json.loads(r[5])
        def gv(i):
            try:
                return str(o[i]).strip() if len(o) > i and o[i] is not None else ""
            except Exception:
                return ""
        f = fmt.get(r[0], "flat9")
        fvi = 7 if f == "wide50" else 5
        raw_all.append(dict(
            store=r[1].strip(), mod=r[2].strip(), rec=(r[4] or "").strip(),
            name=gv(1), fv=gv(fvi), imp=r[0]))
print("8月可见行:", len(raw_all))

d = json.load(open("data/verify/recon_cache.json", encoding="utf-8"))
H = dict(zip(d["最终有效数据"][0], range(len(d["最终有效数据"][0]))))
def g(r, c): return str(r[H[c]]).strip() if r[H[c]] is not None else ""
bv, br = [], []
for r in d["最终有效数据"][1:]:
    bv.append(dict(store=g(r, "Store ID"), mod=g(r, "Modified Time"), name=g(r, "Store Name-Local"),
                   fv=g(r, "First Visit Time"), rec=g(r, "Record ID")))
for r in d["全部重复数据"][1:]:
    br.append(dict(store=g(r, "Store ID"), mod=g(r, "Modified Time"), name=g(r, "Store Name-Local"),
                   fv=g(r, "First Visit Time"), rec=g(r, "Record ID")))
print("基准 V:", len(bv), "D:", len(br))

bvs = {(x["store"], x["mod"]): x for x in bv}
brs = {(x["store"], x["mod"]): x for x in br}
vstore = {x["store"] for x in bv}
# raw store 是否在有效表有店（同店名或同store）
vn = defaultdict(list)
for x in bv: vn[norm(x["name"])].append(x)
vs = defaultdict(list)
for x in bv: vs[x["store"]].append(x)

reasons = Counter()
for x in br:  # 533
    s, m = x["store"], x["mod"]
    same_store_in_v = s in vs          # 同 store 在有效表
    nm = norm(x["name"])
    name_in_v = nm in vn
    if same_store_in_v:
        # 同 store: 与有效行的 FV/日期比较
        vx = vs[s][0]
        if x["fv"] == vx["fv"] and x["fv"]:
            reasons["A_same_store_same_FV"] += 1
        elif (x["mod"] or "")[:10] == (vx["mod"] or "")[:10]:
            reasons["B_same_store_same_day_diff_FV"] += 1
        else:
            reasons["C_same_store_diff_day_diff_FV"] += 1
    else:
        if name_in_v:
            reasons["D_cross_store_same_name"] += 1
        else:
            reasons["E_orphan"] += 1
print("\n重复行归因:", dict(reasons), "合计", sum(reasons.values()))

# 也归因 V 行: 检查同一 store 在 D 表有行(组) 与 V 自身最早性
print("\n有效行中: 其 store 也在重复表出现的:", sum(1 for s in vstore if s in brs) if False else "")
dup_store = {x["store"] for x in br}
print("有效表store在重复表也出现(重叠):", len(vstore & dup_store))
# V行 modified 是否 = 同store组最早
group_mod_min = 0
for s in vstore & dup_store:
    allm = [x["mod"] for x in raw_all if x["store"] == s]
    vmod = vs[s][0]["mod"]
    if vmod == min(allm): group_mod_min += 1
print("重叠店中 V=组内modified最早:", group_mod_min, "/", len(vstore & dup_store))
