# 8 月真实数据对账验证（本地隔离库，不入库）

对账基准：`~/Desktop/万总/8月全月巡回最终结算.xlsx`（月度总览：
原始 26498 / Visible 有效 13044 / 最终有效店 12511 / 重复 533 /
1点 8231 / 2点 4280 / 总点 16791 / 金额 4896750）。
本目录 recon_cache.json 为该文件关键 sheet 的一次性缓存。

## 验证流程（最终口径：判重键 = 店名 trim × 结算月窗口）

1. 全量导入 4 份 8 月文件（含 0726-0805 里的 7 月行，不剔除）+ 跑 V3 判定：
   `DATABASE_URL=sqlite:///./store_settle_aug8.db python tools_rebuild_aug8.py`
   期望 8 月 clean_status: valid=12511, cross_file_dup=309,
   from_sub=99, master_late=125（重复合计 533，与基准逐行全等）。

2. 模拟全部任务超时默认认可 → 逐文件 finalize → 绩效/工资对账：
   `DATABASE_URL=sqlite:///./store_settle_aug8.db python tools_finalize_aug.py`
   （脚本内 db 名若不同可先 sed 替换）期望：8 月 formal 12511、
   1点 8231、2点 4280、总点 16791、8 月工资合计 4896750 —— 与基准完全一致。

## 判定口径要点（2026-09 反推 + 万总裁决确认）

- **判重窗口 = 结算月**（万总以「8 月月度总览」口径裁决）：同名店内
  当月只保留最早一次巡店；同店跨月（7 月巡过、8 月再巡）是新的结算记录，
  互不压制——即使 0726-0805 这类跨月文件全量导入也正确。
- 判重键 = (行原始店名 trim, modified 月)，组内 modified 完整时间戳最早
  一条 → valid，与它挂在主档还是从档编号下无关（主档当月未巡、从档巡了
  → 从档行即该店当月首次，算有效）。
- ⚠ 店名不得做 NFKC/去空白归一：ざくろ銀座店 与 ざくろ 銀座店
  （空格差异）是两家店，各自有效。norm_name 只作展示/候选，不作判重键。
- 与组内最早行同 store：同日 → cross_file_dup（数据重复，自动滤不进任务）；
  异日 → master_late（员工确认，可申诉）；组内其它编号(从档候选)更早
  → from_sub（自动滤）。
- visible_blank 不是有效巡店，不参与判重；比较用 modified 完整时间戳
  （同日多条时取真正最早，非文件序）。
- loader 表头映射同名列取最左首现：wide50 右侧镜像表头
  （第二组 Store Name-Local/English）不得覆盖主数据区列。

## store 实体主从档说明

promote/anchor 仍维护 store_entities 主/从档与 raw_data_id（文档要求的
实体层，供展示、人工调档、追溯）；judge 判重不依赖它（以上述行级键为准）。
