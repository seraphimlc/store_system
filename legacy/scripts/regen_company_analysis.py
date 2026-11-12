# -*- coding: utf-8 -*-
"""重新生成看板「总体分析（全公司）」。

背景：2026-10-17 线上那条内容是**没写完的思维链**（提示词 max_tokens=2500 太小
→ 思考吃满预算 → content 为空 → 旧 ai_chat 回退 reasoning_content），而且指纹只含
事实数据、不含提示词版本 → **永久缓存**。修复后指纹包含 COMPANY_PROMPT_VERSION，
旧行自动失效；本脚本把好的内容直接生成好写回去，省掉用户等一次页面加载。

写的是 `staff_analyses`（**AI 结果缓存表**，非结算域四表）。生成前先把旧内容
打印出来备查。
"""
import hashlib                                       # noqa: F401  (留作对照)
from app.db import SessionLocal
from app.models import StaffAnalysis
from app.services import dashboard as D
from app.services.ai_chat import chat, configured

db = SessionLocal()
row = db.query(StaffAnalysis).filter(
    StaffAnalysis.person_code == "COMPANY", StaffAnalysis.month == "ALL").first()

print("=== 旧内容（前 300 字，将被替换）===")
if row:
    print("旧指纹 =", (row.fingerprint or "")[:12], " 长度 =", len(row.content or ""))
    print((row.content or "")[:300].replace("\n", " ") + " …")
else:
    print("（无旧行）")

facts = D.fact_text(db, None)
fp = D.company_fingerprint(facts)
print("\n新指纹 =", fp[:12], " （事实", len(facts), "字 + 提示词版本 "
      + D.COMPANY_PROMPT_VERSION + "）")

if not configured():
    raise SystemExit("未配置 AI，跳过")
print("\n=== 调模型（不传 max_tokens，用 AI_MAX_TOKENS）… ===")
text = chat(D.COMPANY_PROMPT.format(facts=facts), timeout=300)
print("新内容长度 =", len(text))
print("--- 新内容 ---")
print(text)
print("--- 检查 ---")
low = text.lower()
print("含英文思考痕迹('we need'/'let us'/'let\\'s') =",
      any(k in low for k in ("we need", "let us", "let's")))
print("含加粗要点('**') =", "**" in text)
print("结尾是否完整（不以逗号/and/或 收尾）= ",
      not text.rstrip().endswith(("，", ",", "and", "或", "…")))

if row is None:
    db.add(StaffAnalysis(person_code="COMPANY", month="ALL",
                         fingerprint=fp, content=text))
else:
    row.fingerprint, row.content = fp, text
db.commit()
print("\n✓ 已写回 staff_analyses（COMPANY/ALL）")
db.close()
