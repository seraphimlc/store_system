# -*- coding: utf-8 -*-
"""本地看计划表结构：前若干行 + 表头行识别 + 有没有「负责队」列。只读。"""
import sys

from openpyxl import load_workbook

FILES = [
    "/Users/liuchang/Desktop/万总/陈嘉溢千叶B线执行计划_分级版.xlsx",
    "/Users/liuchang/Desktop/万总/陈嘉溢千叶C线执行计划_分级版.xlsx",
    "/Users/liuchang/Desktop/万总/甘子杰埼玉A线执行计划_大中小及郊外分级.xlsx",
]

for p in FILES:
    print("=" * 100)
    print("文件:", p.split("/")[-1])
    wb = load_workbook(p, read_only=True, data_only=True)
    print("sheets:", wb.sheetnames)
    ws = wb[wb.sheetnames[0]]
    print("维度: %d 行 x %d 列" % (ws.max_row or 0, ws.max_column or 0))
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=8,
                                         values_only=True), 1):
        cells = ["" if c is None else str(c)[:22] for c in row]
        print(f"  行{i}: {cells}")
    wb.close()
    sys.stdout.flush()
