# 员工打卡（Staff Check-in）设计规格

> 状态：v4（2026-09-27）——按用户 2026-09-27 的口径**大幅收窄**：**不做对比、不做纠错/补录**，只做「看得见员工每天去了哪、走访了多少店」+ 内部赛马。
> 分支：`feat/staff-checkin`。本期不做上线。
> 相关文档：`docs/谷歌地图API申请流程.md`、`docs/阿里云OSS开通清单.md`。

## 1. 要解决的问题

管理上要能看见：**每个员工每天去了哪些店、走访了多少家**；再做一个**内部赛马**（按走访店数排名）作为激励。没有其它目的。

因此本功能是**一条完全独立的记录线**：

- 员工每巡一家店打卡一次：**现场定位 + 店名 + 点数 + 至少一张门头照**。
- 打卡数据**不参与工资计算**，也**不参与任何绩效判定**；不进 `formal_records` / `person_daily_stats` / `payroll_*` 任何一张表。
- 打卡数据**不与上传文件跑出的"有效记录"做对比**（用户明确：对比怎么做还没想好，本期不做）。
- **不改动任何现有表、不写 `store_entities`**，只新增 2 张打卡表。

## 2. 已确认决策

| # | 决策 | 说明 |
|---|---|---|
| D1 | 打卡粒度 | 一人 × 一店 × 一次：定位、店名、点数、门头照 |
| D2 | 不参与薪资、不做对比 | 纯记录 + 统计 + 排名；对比与对齐逻辑本期一律不做 |
| D3 | 不做纠错、不做补录 | **只能提交"今天"的打卡；提交后不能改、不能删**（连管理员也不做改删） |
| D4 | 点数 | 员工填 1 或 2（页面两个按钮）；不做任何合理性校验 |
| D5 | 照片 | **至少 1 张门头照**；前端调后摄拍摄；存**阿里云 OSS（私有 bucket）**；生产不落本地盘 |
| D6 | 地图/搜索 | Google Maps（Maps JS API + Places API (New)），**客户自行申请 key**；无 key 时降级仍可打卡 |
| D7 | 店名来源优先级 | **① Google 搜到 → 用 Google 的店名 ② Google 搜不到 → 员工自己填 ③ 能命中我们实体库 → 用实体库的规范店名**（命中即记录实体 id） |
| D8 | 点数/店数口径 | 店数 = 打卡条数；另给"去重店数"（同一家店当天打两次算 1） |
| D9 | 管理端 | 打卡列表 + 汇总/赛马榜 + 导出 Excel；不做审批、不做作废 |
| D10 | 不接 MCP | 打卡不暴露任何 MCP 工具 |
| D11 | 本期不上线 | 先在本地把功能做出来；上线等 OSS 凭据到位后再说 |
| D12 | 不管弱网 | 不做离线队列/断网续传；网好了再提交 |

## 3. 范围

**本期做**
1. 员工端打卡页：定位 + 谷歌地图 + 店名搜索/手输 + 点数 + 门头照 + 「今日已打卡 N 家」
2. 员工端我的打卡历史（按月/按日看自己去过哪）
3. 管理端：打卡列表（按月/按人）、**赛马榜（日榜/月榜）**、Excel 导出
4. 照片走 OSS（私有 bucket），取图走登录态鉴权
5. 中文/日文文案（现场是日本员工，日文要做）

**本期不做**（见 §10）：与有效记录对比、店铺主档对齐体系、纠错/删除/补录、审批流、防作弊、作废、照片过期清理脚本、MCP 工具、生产上线。

## 4. 数据模型（2 张新表，不动现有表）

### 4.1 `staff_checkins`（ORM 类 `Checkin`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `person_code` | String(32) NOT NULL, FK `persons.code` | 归属员工 |
| `user_id` | Integer FK `users.id` | 提交账号 |
| `ref_date` | Date NOT NULL | **业务日 = JST 日期**（服务端算，客户端不可信） |
| `store_name` | String(255) NOT NULL | **落库店名**（Google 名 / 实体库规范名 / 员工手输，见 §6） |
| `store_name_norm` | String(255) NOT NULL default "" | 归一化名（复用 `store_master.norm_name`：NFKC + 去空白 + 小写）；用于"去重店数"与历史联想 |
| `name_source` | String(16) NOT NULL default "manual" | 名字来源：`google` / `entity` / `manual`（留痕，也便于将来切换口径） |
| `place_id` | String(255) | Google place_id（**唯一允许长期保存的 Google 数据**，见 §6） |
| `store_entity_id` | Integer | 命中我们实体库时记下（命中不上为空；**不写 `store_entities`**） |
| `lat` / `lng` | Float | **设备定位坐标**（我们自己的数据） |
| `accuracy` | Float | 定位精度（米） |
| `points` | Integer NOT NULL default 1 | 1 或 2 |
| `submitted_at` | DateTime NOT NULL | 服务端 UTC 提交时刻 |
| `client_ts` | String(40) | 客户端时间字符串（留痕） |
| `source` | String(16) NOT NULL default "web" | 预留 |
| `created_at` | DateTime NOT NULL | |

索引：`(person_code, ref_date)`、`(ref_date)`。
**不加唯一约束**（同店同日重复打卡允许，只做软提示）。

### 4.2 `staff_checkin_photos`（ORM 类 `CheckinPhoto`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `checkin_id` | Integer NOT NULL, Index | 归属打卡 |
| `rel_path` | String(512) NOT NULL | 存储键（OSS 对象键） |
| `thumb_path` | String(512) NOT NULL default "" | 缩略图键（前端生成后一起上传） |
| `bytes` | Integer NOT NULL default 0 | 原图字节数 |
| `captured_live` | Boolean NOT NULL default True | 是否摄像头直拍（false=降级为相册选择） |
| `created_at` | DateTime NOT NULL | |

**一次打卡 1..N 张，服务端强制 ≥1**。

> **去掉了 v3 的 `checkin_stores` 点位簿**：本期不需要对齐体系，本地搜索直接查「实体库 + 该员工自己的历史打卡店名」即可，少一张表、少一套维护逻辑。将来若要恢复对比功能，再加。

## 5. 员工端（H5）

| 路由 | 返回 | 说明 |
|---|---|---|
| `GET /my/checkin` | HTML | 打卡页 + 「今日已打卡 N 家」列表 |
| `POST /my/checkin` | JSON | 提交（multipart） |
| `GET /my/checkin/search?q=` | JSON | 店名候选（本地优先，见 §6） |
| `GET /my/checkin/photo/{pid}` | 二进制 | 鉴权取图（本人） |
| `GET /my/checkins` | HTML | 我的打卡历史（按月/按日） |

页面自上而下：**地图卡 → 店名卡 → 点数卡 → 照片卡 → 提交**，下方今日列表。

1. **定位**：进页面请求 `navigator.geolocation`（`enableHighAccuracy`，8s 超时）→ 地图落 pin、显示 `±Xm`；**pin 可拖拽、可点击地图落点**。定位失败只提示、**不阻塞**；**地图瓦片加载不出来时选点仍可用**（退化为无底图选点器即可）。
2. **店名**：输入 ≥2 字、300ms 防抖 → 先请求本地 `/my/checkin/search`（实体库 + 本人历史）→ 本地没结果且**有 key** 时走 Google Places（单选确认）→ 选中后自动填店名。
   也可以**完全不搜、直接手输**。
3. **点数**：1 / 2 两个按钮，默认 1。
4. **照片**：`<input type="file" accept="image/*" capture="environment" multiple>`；前端用 canvas 压原图（长边 1280 / q0.8）+ 生成缩略图（长边 320），提交前校验 ≥1 张；缩略图可预览。
5. **日期**：固定**今天**（服务端按 JST 裁定）；页面上**没有**日期选择、没有补录入口。
6. **重复软提示**：今天已在这家店打过卡 → 提示「今天已打过（可继续提交）」，不阻止。
7. **提交**：`fetch` + JSON。成功 → `{ok:true, count:N, today_html}`，前端替换今日列表并清空表单；失败 → `{ok:false,error}`，**保留已填内容与照片**，可原地重试。
8. **今日列表**：倒序显示缩略图 / 店名 / 点数 / 时间，**只读**（不做编辑、不做删除）。
9. **我的历史**（`/my/checkins`）：按月选、按日分组，看自己每天去过哪几家。

## 6. 店名与 Google 内容（按 D7 + 合规约束）

**取值顺序**：
1. Google Places 搜到 → **店名用 Google 返回的名字**（`name_source=google`，同时存 `place_id`）；
2. Google 搜不到 → **员工自己填**（`name_source=manual`）；
3. 以上两种情况，都**再拿这个名字去我们实体库匹配一次**：命中 → **店名改用实体库的规范名**（`name_source=entity`，记 `store_entity_id`）；没命中 → 保留原本的名字。

**合规约束（Google 条款原文：Places 内容不得长期缓存，`place_id` 除外）**：
- **长期存库的只有 `place_id`**（唯一豁免项）+ **我们自己的数据**（员工输入、实体库名、设备坐标）。
- Google 返回的**地址不落库**；**坐标一律用设备定位，不写 Google 的坐标**。
- 地图与搜索结果**显示时带 Google 标识**（正常用 Google 地图组件即满足）。
- 若后续客户要求更严格口径：`name_source` 字段已把来源分开，把"不落 Google 名"变成一行判断即可，不需要改表。

## 7. 照片与 OSS

- **服务端代理上传**（手机 → 我们服务器 → OSS）：保证"≥1 张照片"和打卡记录同事务，不引入客户端直传与 CORS。
- **Bucket 私有**，取图只经登录态鉴权路由（本人 / 管理员），**没有任何公开 URL**；列表页只取缩略图。
- 校验：张数 1..5；单张 ≤5MB；按**文件头 magic bytes** 判类型（JPEG/PNG/WebP），不信 `Content-Type`。
- 存储后端做成抽象（`AliyunOssStorage` / `LocalStorage`），**延迟导入 `oss2`**：本地开发与无 OSS 凭据时用 `LocalStorage`，OSS 到位后改一个环境变量即切换。
- **OSS 不可用 → 上传失败并提示重试，不降级写本地盘**。
- 保留期：本期**不写清理脚本**，需要时用 OSS 生命周期规则（客户文档已给）即可；表结构不预留额外字段。

## 8. 管理端

| 路由 | 说明 |
|---|---|
| `GET /checkins` | 打卡列表：按月 / 按人筛选；列 日期·员工·店名·点数·坐标·缩略图·提交时刻；分页 |
| `GET /checkins/board` | **赛马榜**：日榜 / 月榜；按走访店数排名，列 排名·员工·打卡店数·去重店数·点数合计 |
| `GET /checkins/photo/{pid}` | 鉴权取图（管理员） |
| `GET /checkins/export?kind=list\|board` | Excel 导出 |

- 权限：中间件 + 路由 `role != admin` 双层（沿用现有约定）；关键元素带 `data-testid`（沿用 `/perf` 既有约定）。
- 不做作废、不做改删（D3）。

## 9. 权限与文案

- `app/main.py` 的 `STAFF_ALLOWED` 加 `/my/checkin` **一项即可**（中间件是 `path.startswith` 前缀匹配，`/my/checkins` 已覆盖）。
- 员工端校验 `role == "staff"` 且有 `person_code`；取图校验归属（本人 / 管理员）。
- 注意未改密员工会被重定向到 `/my/password`（`must_change_password`）。
- 界面文案全部走 `app/i18n.py`（key=中文原文，ja=日文）。

## 10. 明确不做

- 与"有效记录"的对比、店铺主档对齐体系、`checkin_stores` 点位簿
- 员工纠错（编辑/删除）、补录、日期选择
- 管理员作废/改删、审批流
- 防作弊（设备指纹、速度异常、多账号告警）
- 离线队列 / 断网续传
- 员工端备注输入、POI/地址反查（地图**拖拽与点击落点**是本期功能，别误删）
- 照片过期清理脚本（改用 OSS 生命周期规则）
- 任何 MCP 工具、生产上线

## 11. 集成注意

1. **时区**：现有库全用 naive UTC。业务日必须显式 JST，用固定 `timedelta(hours=9)`（**不用 `zoneinfo`**，避免 slim 镜像缺 tzdata）。
2. **模板循环变量不可命名为 `t`**（会覆盖 i18n 全局函数）：用 `ck` / `ph`。
3. **`{# #}` 与 `<script>` 内中文不会被自动翻译**：地图/拍照 JS 里的可见文案要手工处理，并且日文词条要补。
4. **`down_revision = "f7e8d9c0b1a2"`**（当前唯一 head）。
5. **SQLite/PG 双兼容**：Text 默认值用 `sa.text("('')")`；`lat/lng` 用 `Float`。
6. **fetch 提交同样要 `csrf_token`**（复用 `csrf_ok`）。
7. **无 key 时不得请求任何谷歌域**（地图与 Places 都不加载）。
8. 新增环境变量：`VISIT_GOOGLE_MAPS_KEY`、`VISIT_CHECKIN_STORAGE`(local|oss)、`VISIT_OSS_ENDPOINT/BUCKET/AK/SK/PREFIX`、`VISIT_CHECKIN_MAX_PHOTOS`(5)、`VISIT_CHECKIN_MAX_PHOTO_MB`(5)。

## 12. 待确认（两点）

1. **赛马榜给谁看**：只放管理端；还是员工端也能看到"自己在团队里的排名/今日店数"（激励效果更强，但会引入互相比较的压力）。
2. **"提交后完全不能改、不能删"确认**：真的连"选错店/照片拍糊"都不留后路吗？（我按你说的先做成不可改；如果需要，最小后路是"允许删掉自己今天最后一条"。）

实现按用户口径推进：**不设"必须跑通/测试全绿"的交付门槛**，我写完自己会看一眼，但不拿这个卡进度。
