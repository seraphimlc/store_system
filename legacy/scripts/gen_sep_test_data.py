# -*- coding: utf-8 -*-
"""生成 9 月上下半月巡店测试数据（wide50 格式，可直接上传到系统走全流程）。

设计目的：验证「两期发薪 + 金额找平扣减 + 跨月递延」（8 月金额差作为 9 月要扣的余额）：
  A 上半月够扣  : 江田卓(8月余额63,000) 上半月260点=74,000 → 上半月实发 11,000
  B 上半不够扣  : 陳偉鋒(余额71,000) 上半月1点=250 → 上半月0、剩70,750转下半月；
                  下半月300点=87,000(含奖12,000) → 下半月实发 16,250
  C 两期都不够  : 汤静(余额77,750) 上下各1点(共500) → 两期均0、递延77,250到10月
  D 上半月无店  : 楚艦文(余额250) 上半月0店 → 余额全转下半月；下半月1点250 → 实发0
  E 正常扣减    : 小川逸(余额58,250) 上下各200点 → 正常扣
  F 正常无余额  : 全思瑜(8月余额0) 上下各100点 → 正常发薪
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openpyxl import Workbook

W1 = ["Store Information"] + [""] * 13
W2 = ["Store ID", "Store Name-Local", "Store Name-English", "Country/Region",
      "city", "Create Time", "Modified Time", "First Visit Time",
      "Review Completion Time", "Submitter", "ISO Company", "Record ID",
      "A+ POSM Visible", "Existing A+ POSM Visible", "Deploy New A+POSM"]

# (员工, 上半月店数[2点], 上半月店数[1点], 下半月[2点], 下半月[1点])
PLAN = [
    ("江田　卓(2188240630166753)", 130, 0, 0, 0),      # A 上半够扣
    ("陳偉鋒(2188240606730380)", 0, 1, 150, 0),        # B 上半不够→下半继续
    ("汤静(2188240607339564)", 0, 1, 0, 1),            # C 两期不够→递延
    ("楚艦文(2188240627065270)", 0, 0, 0, 1),          # D 上半无店
    ("小川逸(2188240606634082)", 100, 0, 100, 0),      # E 正常扣
    ("全思瑜(2188240630173395)", 50, 0, 50, 0),        # F 无余额正常
]
HERE = os.path.dirname(os.path.abspath(__file__))


def rows_for(half: str):
    """half=upper(9/1-9/15) / lower(9/16-9/30)"""
    out = []
    for who, up2, up1, lo2, lo1 in PLAN:
        n2, n1 = (up2, up1) if half == "upper" else (lo2, lo1)
        days = ["2026-09-02", "2026-09-05", "2026-09-10"] if half == "upper" \
            else ["2026-09-17", "2026-09-22", "2026-09-28"]
        idx = 0
        for _ in range(n2):     # 2点店（Deploy=YES）
            d = days[idx % len(days)]
            idx += 1
            out.append([f"{half.upper()}2-{who[:4]}-{idx:04d}",
                        f"テスト店2点-{half}-{who[:4]}-{idx:04d}", "",
                        "Japan", "Tokyo", f"{d} 08:00:00", f"{d} 09:{idx%60:02d}:00",
                        f"{d} 08:30:00", f"{d} 10:00:00", who, "MarsNavi Co., Ltd.",
                        f"R-{half}-2-{idx}", "YES", "YES", "YES"])
        for _ in range(n1):     # 1点店（Deploy=NO）
            d = days[idx % len(days)]
            idx += 1
            out.append([f"{half.upper()}1-{who[:4]}-{idx:04d}",
                        f"テスト店1点-{half}-{who[:4]}-{idx:04d}", "",
                        "Japan", "Tokyo", f"{d} 08:00:00", f"{d} 09:{idx%60:02d}:00",
                        f"{d} 08:30:00", f"{d} 10:00:00", who, "MarsNavi Co., Ltd.",
                        f"R-{half}-1-{idx}", "YES", "NO", "NO"])
    return out


for half, fname in (("upper", "2026-09_上半月巡店.xlsx"),
                    ("lower", "2026-09_下半月巡店.xlsx")):
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("STORE_TASK_EXCEL_SHEET ")
    ws.append(W1)
    ws.append(W2)
    data = rows_for(half)
    for r in data:
        ws.append(r)
    path = os.path.join(HERE, fname)
    wb.save(path)
    print(f"wrote {path}  行数={len(data)}")
