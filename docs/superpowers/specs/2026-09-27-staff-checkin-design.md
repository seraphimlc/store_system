# 员工打卡（Staff Check-in）设计规格

> 状态：设计已确认（2026-09-27），待实现。
> 分支：`feat/staff-checkin`。目标环境：**本地开发验收**（本期不考虑上线）。
> 相关文档：`docs/谷歌地图API申请流程.md`、`docs/阿里云OSS开通清单.md`。

## 1. 目标与原则

员工每巡一家店打卡一次：**现场定位 + 手工录店名 + 手工录点数 + 至少一张门头照**。

- **打卡是独立支线**：只**读**结算主链（`formal_records` / `store_entities`）用于对比，**绝不写**它们。
- **不参与工资计算**：不进 `formal_records` / `person_daily_stats` / `payroll_*` 任何一张表。
- **存在的意义是"对比"**：将来与"有效记录"（上传文件跑出的正式表，结算唯一依据）逐店对比，发现偏差 → 漏报 / 虚报 / 记录没生效。
- **先上功能、轻管控**：管理制度尚未跟上，本期不做审批流、不做防作弊、不做强制校验；**该留的痕迹全部留**（时间、坐标、精度、补录事实、作废留痕），将来制度跟上时数据还在。
- **外部依赖可缺席**：地图 key 与 OSS 凭据都由客户后续申请，**代码必须在这两样都没有的情况下跑完整流程**（降级 + 开关切换）。

## 2. 已确认决策（逐条对应沟通过程）

| # | 决策 | 说明 |
|---|---|---|
| D1 | 打卡粒度 | 一人 × 一店 × 一次，含定位、店名、点数、门头照 |
| D2 | 不参与薪资 | 不动现有表；仅新增 3 张打卡表 |
| D3 | 对比做到"逐店自动对齐" | 打卡时即尝试对齐店铺主档；后台跑三类差异清单 |
| D4 | 真实性约束 = 轻约束 + 全留痕 | 不做防作弊；**必须拍照**；补录只记不判 |
| D5 | 地图 | Google Maps（Maps JavaScript API + Places API (New)），**客户自行申请 key**；本地优先 + 谷歌兜底 |
| D6 | 纠错规则 | 当天（JST）可自助编辑/删除；跨天只能新增补录；管理员可作废（留痕） |
| D7 | 补录窗口 | 默认最近 **31 天**内（`CHECKIN_BACKFILL_DAYS`，0=不限）；**页面不做任何状态标记与提示** |
| D8 | 点数 | 员工填 1 或 2；**不做任何校验**（"多点少点都行"） |
| D9 | 照片存储 | **阿里云 OSS**（私有 bucket，服务端代理上传）；**生产不落本地盘**；保留 **24 个月** |
| D10 | 店铺实体 | 能对上就用；对不上**只记店名**（进打卡点位簿），**不写 `store_entities`**，**不做人工绑定 UI** |
| D11 | 管理范围 | 只读对比清单 + 列表 + 导出 + 作废；**不做审批流** |
| D12 | 不接 MCP | 打卡不暴露任何 MCP 工具（用户明确） |
| D13 | 本期不上线 | 先本地做扎实；生产上线推迟到 OSS 凭据到位之后 |
| D14 | 不考虑弱网 | 不做离线队列/断网续传；网络恢复后再提交 |

## 3. 范围

**本期做**
1. 员工端 H5 打卡页（地图定位 + 店名搜索/手输 + 点数 + 门头照 + 今日列表）
2. 员工端我的打卡历史（按月/按日）
3. 照片存储抽象（OSS / 本地两后端）与登录态鉴权取图
4. 店名三层对齐 + 打卡点位簿
5. 管理端：打卡列表、只读对比清单（五类差异）、Excel 导出、作废
6. 中日双语文案、H5 响应式、权限双层
7. 照片过期清理脚本（dry-run 默认）

**本期不做**（见 §17）

## 4. 架构与分层

```
员工手机(H5)
  │ 浏览器定位(navigator.geolocation) + Google Maps JS + Places(本地优先)
  ▼
app/routers/checkin_r.py        员工端 /my/checkin* + 管理端 /checkins*
  ▼
app/services/checkin.py         打卡采集 · 店名对齐 · 点位簿 · 列表/编辑/作废
app/services/checkin_compare.py 月度对比（只读 formal_records + store_entities）
app/services/storage.py         照片存储抽象（AliyunOssStorage / LocalStorage）
  ▼
3 张新表 + OSS(私有 bucket)
```

新增文件：

| 文件 | 职责 |
|---|---|
| `app/routers/checkin_r.py` | 全部路由（员工端 + 管理端） |
| `app/services/checkin.py` | 打卡采集/校验/编辑删除/对齐/点位簿/列表 |
| `app/services/checkin_compare.py` | 月度对比计算（纯读） |
| `app/services/storage.py` | 存储抽象 + 两个后端 + 照片业务封装 |
| `app/templates/my_checkin.html` | 打卡页（含地图与拍照 JS） |
| `app/templates/my_checkins.html` | 我的打卡历史 |
| `app/templates/checkins.html` | 管理端列表 |
| `app/templates/checkins_compare.html` | 对比清单 |
| `app/models.py`（追加 3 个类） | 数据模型 |
| `migrations/versions/*_staff_checkins.py` | 建 3 表（down_revision=`f7e8d9c0b1a2`） |
| `scripts/checkin_photo_gc.py` | 过期照片清理（dry-run 默认） |
| `tests_web/test_checkin.py` | 主测试 |
| `app/i18n.py`（追加词条） | 日文文案 |
| `app/main.py`（注册路由 + 白名单） | 接线 |
| `app/templates/base.html`（导航） | 入口 |

## 5. 数据模型（3 张新表）

### 5.1 `staff_checkins` 打卡主表

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `person_code` | String(32) NOT NULL, FK `persons.code` | 归属员工（与正式表同口径，才能对齐） |
| `user_id` | Integer FK `users.id` | 提交账号（留痕） |
| `ref_date` | Date NOT NULL | **业务日 = JST 日期**（服务端计算，客户端不可信） |
| `store_name_raw` | String(255) NOT NULL default "" | 员工输入/谷歌返回的店名原文 |
| `store_name_norm` | String(255) NOT NULL default "" | 归一化（`store_master.norm_name`：NFKC + 去空白 + 小写） |
| `place_id` | String(255) | Google place_id（降级时为空） |
| `address` | Text | 谷歌返回地址（降级时为空） |
| `lat` / `lng` | Float | 坐标（地图 pin 位置，可被拖拽校正后覆盖） |
| `accuracy` | Float | 定位精度（米） |
| `store_entity_id` | Integer FK `store_entities.id` | 命中的店铺实体（对不上为空） |
| `store_master_id` | Integer | **对齐键**：归一到主档的实体 id（冗余存，便于查询/对比） |
| `points` | Integer NOT NULL default 1 | 1 或 2（不校验） |
| `note` | Text | 预留（本期页面不提供输入框） |
| `is_backfill` | Boolean NOT NULL default False | 提交时 JST 日期 ≠ `ref_date` 即置位；**只记不判** |
| `submitted_at` | DateTime NOT NULL | 服务端 UTC 提交时刻 |
| `client_ts` | String(40) | 客户端时间字符串（留痕用，可为空） |
| `voided` | Boolean NOT NULL default False | 作废标记（不物理删） |
| `voided_by` / `voided_at` / `void_reason` | Integer FK `users.id` / DateTime / String(255) | 作废留痕 |
| `source` | String(16) NOT NULL default "web" | 预留（未来 MCP 等） |
| `created_at` / `updated_at` | DateTime NOT NULL | |

索引：`(person_code, ref_date)`、`(ref_date)`、`(store_master_id)`。
**不做唯一约束**（允许同人同日同店重复，页面只软提示）——轻管控原则。

### 5.2 `staff_checkin_photos` 照片

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `checkin_id` | Integer NOT NULL FK `staff_checkins.id`, Index | |
| `rel_path` | String(512) NOT NULL | 存储键（OSS key 或本地相对路径） |
| `thumb_path` | String(512) NOT NULL default "" | 缩略图键（前端生成后一并上传） |
| `bytes` | Integer NOT NULL default 0 | 原图字节数 |
| `sha256` | String(64) NOT NULL default "" | 内容摘要（去重/取证预留） |
| `captured_live` | Boolean NOT NULL default True | 是否摄像头直拍（false=设备降级为相册选择） |
| `created_at` | DateTime NOT NULL | |

**一张打卡 1..N 张照片，服务端强制 ≥1**（0 张直接 400）。

### 5.3 `checkin_stores` 打卡点位簿（对齐资产）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `name_norm` | String(255) NOT NULL **UNIQUE** | 归一化店名（打卡点的唯一键） |
| `name_raw` | String(255) NOT NULL default "" | 最近一次写法 |
| `place_id` | String(255) | 最近一次谷歌 place_id |
| `address` / `lat` / `lng` | Text / Float / Float | 最近一次谷歌侧信息 |
| `store_entity_id` / `store_master_id` | Integer | 已对齐的实体/主档（对不上为空） |
| `hit_count` | Integer NOT NULL default 0 | 被打卡次数（本地搜索排序用） |
| `last_used_at` | DateTime | |
| `bound_by` | Integer FK `users.id` | 预留（本期无人工绑定；自动对齐时留空） |
| `created_at` / `updated_at` | DateTime NOT NULL | |

作用：①同一家店第二次打卡**直接命中已对齐结果**（稳定、0 外部调用）；②本地搜索的第一优先数据源。

## 6. 服务层接口

### 6.1 `app/services/checkin.py`

```python
JST = timezone(timedelta(hours=9))          # 日本无夏令时，固定 +09:00（不依赖 tzdata）

def jst_now() -> datetime                    # tz-aware
def jst_today() -> date
def create_checkin(db, user, *, store_name, points, ref_date=None, lat=None, lng=None,
                   accuracy=None, place_id="", address="", client_ts="",
                   photos: list[tuple[bytes, bytes, bool]]) -> Checkin
    # photos = [(orig_bytes, thumb_bytes, captured_live), ...]，长度必须 ≥1
    # ref_date 为空 → jst_today()；越界（早于 backfill 窗口/未来）→ raise ValueError
def update_checkin(db, user, cid, *, store_name=None, points=None, remove_photo_ids=(),
                   add_photos=()) -> Checkin
def delete_checkin(db, user, cid) -> None
def can_self_edit(ck, user) -> bool          # 本人 且 ref_date == jst_today()
def void_checkin(db, admin, cid, reason="") -> None
def unvoid_checkin(db, admin, cid) -> None
def match_store(db, name_raw) -> MatchResult # (entity_id, master_id, method, score)
def search_local(db, q, limit=20) -> list[dict]   # 点位簿优先 → 主档；给前端候选
def list_checkins(db, *, person_code=None, month=None, date=None,
                  only_backfill=False, include_voided=False) -> list[dict]
def touch_point(db, *, name_raw, place_id="", address="", lat=None, lng=None) -> None
```

### 6.2 `app/services/checkin_compare.py`

```python
def compare_month(db, month: str, person_code: str = "") -> dict
    # -> {"month": "2026-10", "summary": {...}, "rows": [ {...}, ... ]}
```
`rows[].kind ∈ {missing_formal, missing_checkin, points_mismatch, date_only, unaligned}`（见 §8）。

### 6.3 `app/services/storage.py`

```python
class StorageBackend:                 # 接口：put(key, data, content_type) / get(key) / delete(key) / exists(key)
class LocalStorage(StorageBackend):   # 根目录 settings.upload_dir/checkins（仅本地开发与单测）
class AliyunOssStorage(StorageBackend)  # oss2，**延迟导入**（未装/未配也能跑其他路径）
def get_storage() -> StorageBackend   # 由 settings.checkin_storage 决定（local|oss）

class CheckinPhotoStore:              # 业务封装
    def save(self, ck, orig_bytes, thumb_bytes, captured_live) -> CheckinPhoto
    def read(self, photo) -> bytes
    def delete(self, photo) -> None
    # 键规范：checkins/YYYY/MM/{person_code}/{uuid}.jpg 与 .../{uuid}_thumb.jpg（YYYY/MM 取 ref_date）
```

## 7. 店名对齐（三层递进，只为"能对比"，不做管控）

| 层 | 条件 | 结果 |
|---|---|---|
| ① 点位簿命中 | `checkin_stores.name_norm` 全等 | 直接沿用该点位已存的 `store_entity_id/master_id` |
| ② 主档全等 | `store_entities.name_norm` 全等 | 自动绑定该实体（并写主档 id） |
| ③ 模糊 | `difflib.SequenceMatcher`，候选 = SQL 子串检索 top-30 | 相似度 ≥ `CHECKIN_MATCH_THRESHOLD`（默认 92）→ 用候选排序第一；**低于阈值不绑**，`store_master_id` 留空，对比时归入 `unaligned` |

- 三层都没中 → 只写 `checkin_stores` 一行（`store_master_id` 空），**绝不写 `store_entities`**。
- 模糊匹配**只用于自动对齐判定与搜索候选排序**，本期**没有人工绑定入口**（D10）。
- 命中方式写入对比行（`align_method`）便于事后追因。

## 8. 对比口径（按月 × 人，纯只读）

**对齐键 = 主档实体 id**：打卡侧 `store_master_id` ↔ 有效记录侧 `formal_records.store_id_raw` 经 `store_entities` 归一到 `master_store_id`（主档 id）。必须这样，否则同一家店的从档/多写法（空格、全角）会各算一家——即 8 月多算 4 条点数那个老坑。

**算法（逐人逐月）**
1. `F` = 该人该月正式记录，每条映射为 `(master_key, date, points)`；`master_key` = 主档 id，主档缺失时退化为 `store_id_raw`。
2. `C` = 该人该月打卡（**排除 `voided`**）。
3. **同日同主档配对** → 配对成功：点数一致 = 一致（只计数）；点数不同 → `points_mismatch`。
4. 剩余 `C` 中，尝试 ±1 天内同主档的 `F` → `date_only`（仅日期不同）。
5. 剩余 `C` → `missing_formal`（打卡有、有效记录无）。
6. 剩余 `F` → `missing_checkin`（有效记录有、打卡无）。
7. 打卡侧未对齐（`store_master_id` 为空且店名无法与 `F` 全等匹配）→ `unaligned`（无法判定，单列）。
8. 同日同主档存在多条 `F` 时，`points` 取**合计**参与比较，行内展示全部店名。

**汇总**：`checkin_cnt / formal_cnt / matched_cnt / consistent_cnt / consistent_rate / 五类各自计数`（一致率 = 一致数 ÷ max(两侧计数)，分母为 0 时显示 `—`）。

**月份边界按 JST**。打卡数据从启用日开始，之前月份只有 `F` 侧 → 页面提示"该月无打卡数据"，不报错。

## 9. 员工端（H5）

路由：

| 路由 | 说明 |
|---|---|
| `GET /my/checkin` | 打卡页（含今日已打卡列表） |
| `POST /my/checkin` | 提交（multipart：`store_name`/`points`/`ref_date`/`lat`/`lng`/`accuracy`/`place_id`/`address`/`client_ts`/`photos[]`/`thumbs[]`/`captured_live[]`/`csrf_token`） |
| `POST /my/checkin/{id}/edit` | 当天编辑（店名/点数/坐标；可删图、可加图） |
| `POST /my/checkin/{id}/delete` | 当天删除 |
| `GET /my/checkin/search?q=` | 店名候选 JSON（本地；`source=local`） |
| `GET /my/checkin/photo/{pid}` | 鉴权取图（本人） |
| `GET /my/checkins` | 我的打卡历史（月份/按日分组） |

页面结构（自上而下）：**地图卡 → 店名卡 → 点数卡 → 照片卡 → 提交**，下方「今日已打卡 N 家」列表。

1. **定位**：进页面请求 `navigator.geolocation.getCurrentPosition`（`enableHighAccuracy: true`，8s 超时）。
   成功 → 地图落 pin + 显示 `±Xm`；pin **可拖拽**，拖过后显示「已手动校正」。
   失败/被拒 → 地图退到该员工最近一次打卡坐标（无则东京默认视图），提示「未取到定位，可在地图上手动选点」，**不阻塞提交**。
2. **店名**：输入 ≥2 字符、300ms 防抖 → **先请求本地** `/my/checkin/search`；
   本地无结果且存在 `mapsKey` → 调 Google Places（Autocomplete + Place Details 取 `place_id/地址/坐标`）→ 选中回填店名/地址/place_id/坐标 + 「用这个位置校正地图」按钮。
   也支持**完全不搜、直接手输店名**。
3. **点数**：1 / 2 两个大按钮，默认 1，不校验、不提示。
4. **照片**：`<input type="file" accept="image/*" capture="environment" multiple>`；
   前端 canvas 压原图（长边 1280、JPEG q0.8）+ 生成缩略图（长边 320、q0.7）；
   缩略图即时预览、可删；提交前校验 ≥1 张。设备不支持直接拍摄时降级为相册选择并置 `captured_live=false`。
5. **日期**：默认今天（JST 由服务端最终裁定）；「补录」展开日期选择，范围 = `[jst_today - CHECKIN_BACKFILL_DAYS, jst_today]`（默认 31 天）；**页面不做任何"补录"字样标记**。
6. **重复软提示**：同人同日同店已存在 → 提示「今天已在这家店打过卡（可继续提交）」，**不阻止**。
7. **提交成功**：清空表单（便于连续打卡）+ 提示「今日已打卡 N 家」；今日列表即时刷新（htmx 局部替换）。
8. **提交失败**（含照片上传失败）：**保留已填内容与照片选择**，提示重试——绝不产生"有打卡记录、没照片"的幽灵数据（照片先写存储、后落库；任一步失败则回滚已写入的对象）。

## 10. 照片与存储

- **上传路径 = 服务端代理**（手机 → web → OSS）。理由：保证"≥1 张门头照"与打卡记录同事务；不引入 STS/客户端直传与 CORS。
- **Bucket 私有**：取图必须经过登录态鉴权路由；**没有任何公开 URL**。
- **取图**：`GET /my/checkin/photo/{pid}`（本人）与 `GET /checkins/photo/{pid}`（管理员）→ 校验归属 → 从存储后端读出并回传（`Cache-Control: private, max-age=300`）；列表页只加载 `thumb_path`。
- **校验**：张数 1..`CHECKIN_MAX_PHOTOS`(5)；单张 ≤ `CHECKIN_MAX_PHOTO_MB`(5)MB；请求总大小 ≤ `MAX_UPLOAD_MB`；类型按**文件头 magic bytes** 判定（JPEG/PNG/WebP），不信任 `Content-Type`；`thumbs` 数量必须等于 `photos` 数量，否则 400。
- **清理**：`scripts/checkin_photo_gc.py`（默认 dry-run，`--apply` 删除；保留 `CHECKIN_RETENTION_MONTHS`=24）。删除对象后在 `staff_checkin_photos` 上留痕（新增列 `purged_at`，实现时随迁移一起建），记录本身与打卡记录都不删。
- **OSS 未配置/不可用**：上传直接失败并提示重试，**不降级写本地盘**（避免照片散落在服务器上）。

## 11. 管理端

| 路由 | 说明 |
|---|---|
| `GET /checkins` | 列表：月 / 人 / 仅补录 筛选；列 日期·员工·店名·点数·坐标·缩略图·提交时刻·补录·作废；动作 作废/恢复 |
| `POST /checkins/{id}/void` | 作废（reason，留痕） |
| `POST /checkins/{id}/unvoid` | 恢复 |
| `GET /checkins/compare` | 对比清单（月 / 人筛选，五类差异 + 汇总卡） |
| `GET /checkins/export?kind=list\|compare` | Excel 导出（openpyxl，复用现有导出写法） |

- 对比清单**纯只读**：无任何写动作（除列表页的作废）。
- 导出照片列给**登录后可点开的链接**（本站鉴权路由），不放 OSS 直链。
- 权限：中间件 + 路由 `role != admin` 双层（沿用现有约定）。

## 12. 权限与 i18n

- `app/main.py` 的 `STAFF_ALLOWED` 增加 `/my/checkin`、`/my/checkins`（**否则员工被中间件拦掉**——历史老坑）。
- 员工端所有路由校验 `user.role == "staff"` 且有 `person_code`；管理端 `role == "admin"`。
- 取图与编辑/删除均校验**归属**（本人）。
- 界面文案全部进 `app/i18n.py`（key=中文原文，ja=日文）；现场是日本员工，**日文是必做项**。

## 13. 集成陷阱（实现时必须处理）

1. **时区**：现有库全用 naive UTC（`datetime.utcnow`）。打卡的业务日必须显式 JST，固定 `timedelta(hours=9)` 偏移，**不用 `zoneinfo`**（避免 slim 镜像缺 tzdata）。
2. **模板循环变量不可命名为 `t`**：会覆盖 i18n 全局函数（历史坑）。本期循环变量用 `ck`、`ph`。
3. **`{# #}` 注释与 `<script>` 内中文不会被自动翻译**（i18n 脚本跳过 script/style）——地图/拍照 JS 里的用户可见文案需手工 `t()` 化或改用模板变量传入，**且** 多语言字典要补日文。
4. **OSS 后端延迟导入** `oss2`：未配/未装时不得影响其他路径启动（本地开发与单测不连外网）。
5. **照片写入顺序**：先写存储 → 再落库；落库失败要回滚已写对象；多张时按序号配对 `photos[i] ↔ thumbs[i]`。
6. **`is_backfill` 只记不判**：不得在页面上显示补录状态、不得阻止提交、不得用于任何准入判断。
7. **不改现有表**：任何 `ALTER` 现有表的冲动都要驳回（对比是只读）。
8. **迁移 head**：`down_revision = "f7e8d9c0b1a2"`（当前 head）。
9. **SQLite/PG 双兼容**：Text 默认值用 `sa.text("('')")`；`lat/lng` 用 `Float`（PG→double precision）。
10. **CSRF**：multipart 提交同样需要 `csrf_token`（现有 `csrf_ok` 复用）。
11. **地图 JS 只在有 key 时加载**：无 key 时不得请求任何谷歌域（含 Leaflet 降级也按需动态注入，避免无谓的 CDN 依赖）。

## 14. 测试策略

`tests_web/test_checkin.py`（存储用 `LocalStorage`，**不连外网**）：

| 组 | 用例 |
|---|---|
| 打卡 CRUD | 提交成功（含 1 张照片）；0 张照片 → 400；店名空 → 400；点数非 1/2 → 400 |
| 纠错规则 | 当天可编辑/删除；跨天（`ref_date != jst_today`）编辑/删除被拒 |
| 补录窗口 | `ref_date` 超出 31 天 → 400；`CHECKIN_BACKFILL_DAYS=0` 时不限；`is_backfill` 正确置位 |
| 照片 | 类型按 magic bytes 拒绝伪造扩展名；超大被拒；`thumbs` 数量不匹配 → 400 |
| 鉴权 | 本人可取图；他人 403；管理员可取图；未登录跳登录 |
| 越权 | 员工访问 `/checkins`、`/checkins/compare` 被拦（中间件 + 路由双层） |
| 对齐 | 点位簿命中；主档全等；模糊 ≥ 阈值自动绑；低于阈值 → `store_master_id` 为空且**未写 `store_entities`** |
| 搜索 | 本地搜索命中点位簿与主档；空/无结果返回空数组 |
| 对比 | 五类差异各自可构造并断言；`voided` 打卡被排除；±1 天归入 `date_only`；一致率计算（分母 0 时 `—`） |
| 作废 | 作废后不出现在默认列表与对比中；恢复后回归；留痕字段写入 |
| 导出 | 列表/对比 Excel 可生成且表头正确 |
| 事务 | 存储写入成功但落库失败 → 已写对象被回滚删除 |

另：`tests_web/test_permissions.py` 补一条员工越权访问 `/checkins` 的断言。

## 15. 本地验收（本期交付标准）

1. `./.venv/bin/python -m pytest tests_web/test_checkin.py -q` 全绿；相关权限测试全绿。
2. `./scripts/dev_server.sh` 起服务 → 员工账号 `demo123` 登录 → `/my/checkin`：
   - **无 `VISIT_GOOGLE_MAPS_KEY`**：地图走降级（Leaflet/OSM 或手动选点），店名本地搜索可用，定位可用，拍照可用，**能完整提交**；
   - 有 key（可选）：谷歌地图加载、Places 候选、拖拽纠偏。
3. 今日列表可编辑/删除；跨天的只读；补录 32 天前被拒。
4. 管理端 `/checkins` 看到记录与缩略图、可作废；`/checkins/compare` 显示五类差异与一致率；两个导出可下载。
5. 日文界面（`?lang=ja`）走查打卡页与管理页无中文残留（业务数据除外）。
6. 线上零影响：本期不在线上部署；发布前需另行确认（见 D13）。

## 16. 风险与未验证假设

| 风险 | 影响 | 对策 |
|---|---|---|
| 店名对不上主档（员工写法与文件写法差异大） | 对比清单里大量 `unaligned`，等于没对比 | 点位簿 + 三层对齐；对齐键与数据源解耦，将来换 Alipay 主档可重跑对齐 |
| 谷歌 key 是浏览器可见的 | 被人薅用量 | 客户的 key 必须做 referrer + API 限制 + 每日配额上限（文档已给）；超额自动回落本地 |
| OSS 凭据未到位导致无法上线 | 功能只能本地验收 | 存储抽象 + 开关；本地 `local` 后端跑通，凭据到达后切换（**生产不发本地存储**） |
| 24 个月保留依赖宿主 cron | 脚本故障 → 无限累积 | OSS 侧生命周期 900 天兜底（文档已给客户） |
| 定位精度差（室内/地下） | 坐标不可用 | 只作留痕与展示，**不参与任何准入判断**；可拖拽校正 |
| 假设 `store_entities.name_norm` 覆盖率高 | 对齐命中率低 | 已验证：42140 条 100% 已填充 |

**未验证假设**（实现后需用真实数据检验）：①打卡店名与文件店名的归一化后全等率；②±1 天窗口是否够（跨零点打卡占比）；③真实打卡点数与正式表点数的一致率。

## 17. 明确不做（YAGNI）

- 防作弊：设备指纹、速度异常检测、同设备多账号告警、复核队列
- 补卡审批流、补卡状态标记与提示
- 人工绑定主档的 UI（本期只有自动对齐；含"绑错了要解绑"的场景）
- 离线队列 / 断网续传 / 后台补传（D14）
- 员工端备注输入、打卡地点地图选点（POI 反查）
- 打卡数据进入任何薪资/绩效计算路径
- 任何 MCP 工具（D12）
- 生产上线与线上迁移（D13）

## 18. 未来扩展（本期只留接口，不实现）

- **店铺主档换成 Alipay 数据源**：对齐目标换数据源，打卡侧字段与逻辑不变（`store_master_id` 字段语义保持"对齐到的实体 id"）。
- **MCP 工具**（如 `visit_checkin` / `visit_my_checkins` / `visit_checkin_report`）：`source` 字段与 `staff_checkins` 结构已预留。
- **离线队列**：`client_ts` 与 `is_backfill` 已预留判定依据。
- **打卡点位簿升级为店铺别名库**：将来若主档以 Alipay 为准，本表可作为"现场叫法 → 主档"的映射资产。
