# 员工打卡（Staff Check-in）设计规格

> 状态：设计已确认（2026-09-27），已过一轮规格评审并修订（v2），待实现。
> 分支：`feat/staff-checkin`。目标环境：**本地开发验收**（本期不考虑上线）。
> 相关文档：`docs/谷歌地图API申请流程.md`、`docs/阿里云OSS开通清单.md`。
> 评审修订要点（v2）：对齐键统一为「主档实体 id」且两侧都走 head-master 解析；`unaligned` 判定前移；
> 点数取值范围与"不校验"的边界写清；补 `purged_at`/配置项/接口契约/删除级联/分页/阶段划分。
> 评审修订要点（v3，第二轮）：**主档链会变** → 存值读出来必须重解析 head（§5.1/§7/§8）；对比预过滤遇同名多主档
> 同样不绑（§8.2）；补 `PhotoPurged`、照片 `created_at` 索引、管理端取图路由、`lru_cache` 测试陷阱、无瓦片可用。

## 1. 目标与原则

员工每巡一家店打卡一次：**现场定位 + 手工录店名 + 手工录点数 + 至少一张门头照**。

- **打卡是独立支线**：只**读**结算主链（`formal_records` / `store_entities`）用于对比，**绝不写**它们。
- **不参与工资计算**：不进 `formal_records` / `person_daily_stats` / `payroll_*` 任何一张表。
- **存在的意义是"对比"**：将来与"有效记录"（上传文件跑出的正式表，结算唯一依据）逐店对比，发现偏差 → 漏报 / 虚报 / 记录没生效。
- **先上功能、轻管控**：管理制度尚未跟上，本期不做审批流、不做防作弊、不做业务合理性校验；**该留的痕迹全部留**（时间、坐标、精度、补录事实、作废留痕），将来制度跟上时数据还在。
- **外部依赖可缺席**：地图 key 与 OSS 凭据都由客户后续申请，**代码必须在这两样都没有的情况下跑完整流程**（降级 + 开关切换）。

## 2. 已确认决策（逐条对应沟通过程）

| # | 决策 | 说明 |
|---|---|---|
| D1 | 打卡粒度 | 一人 × 一店 × 一次，含定位、店名、点数、门头照 |
| D2 | 不参与薪资 | 不动现有表；仅新增 3 张打卡表 |
| D3 | 对比做到"逐店自动对齐" | 打卡时即尝试对齐店铺主档；后台生成差异清单（分类见 §8，共五类） |
| D4 | 真实性约束 = 轻约束 + 全留痕 | 不做防作弊；**必须拍照**；补录只记不判 |
| D5 | 地图 | Google Maps（Maps JavaScript API + Places API (New)），**客户自行申请 key**；本地优先 + 谷歌兜底 |
| D6 | 纠错规则 | 当天（JST）可自助编辑/删除；跨天只能新增补录；管理员可作废（留痕） |
| D7 | 补录窗口 | 默认最近 **31 天**内（`VISIT_CHECKIN_BACKFILL_DAYS`，0=不限）；**页面不做任何状态标记与提示** |
| D8 | 点数 | 员工填 1 或 2。**页面只给两个按钮、服务端只接受 {1,2}**（防脏数据/伪造请求）；**不做任何"该店该几点"的合理性校验、不提示、不阻止** |
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

**实现分两阶段**（各阶段可独立验收，便于分批评审）：

- **阶段 A｜采集**：模型 + 迁移 + `checkin.py` + `storage.py` + 员工端打卡页/历史 + 权限/i18n
- **阶段 B｜对齐与对比**：三层对齐 + 点位簿 + `checkin_compare.py` + 管理端列表/对比/导出/作废 + GC 脚本

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

新增/改动文件：

| 文件 | 职责 |
|---|---|
| `app/routers/checkin_r.py` | 全部路由（员工端 + 管理端） |
| `app/services/checkin.py` | 打卡采集/校验/编辑删除/对齐/点位簿/列表 |
| `app/services/checkin_compare.py` | 月度对比计算（纯读） |
| `app/services/storage.py` | 存储抽象 + 两个后端 + 照片业务封装 |
| `app/templates/my_checkin.html` | 打卡页（含地图与拍照 JS） |
| `app/templates/_checkin_today.html` | **当日列表片段**（初始渲染与 JSON 提交后复用） |
| `app/templates/my_checkins.html` | 我的打卡历史 |
| `app/templates/checkins.html` | 管理端列表 |
| `app/templates/checkins_compare.html` | 对比清单 |
| `app/models.py`（追加 3 个类） | `Checkin` / `CheckinPhoto` / `CheckinPoint` |
| `app/config.py`（追加设置项） | 见 §6.4 |
| `app/main.py`（注册路由 + 白名单） | 接线 |
| `app/templates/base.html`（导航） | 入口（员工端「打卡」/ 管理端「打卡记录」） |
| `app/i18n.py`（追加词条） | 日文文案 |
| `migrations/versions/*_staff_checkins.py` | 建 3 表（down_revision=`f7e8d9c0b1a2`） |
| `scripts/checkin_photo_gc.py` | 过期照片清理 + 孤儿对象扫描（dry-run 默认） |
| `requirements-web.txt` | **仅在启用 OSS 时**追加 `oss2`（代码为延迟导入） |
| `tests_web/test_checkin.py` | 主测试 |
| `AGENTS.md` + `docs/索引.md` | **按仓库约定同步更新**（改了代码必须同步这两份） |

## 5. 数据模型（3 张新表）

ORM 类名：`Checkin`（`staff_checkins`）/ `CheckinPhoto`（`staff_checkin_photos`）/ `CheckinPoint`（`checkin_stores`）。

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
| `lat` / `lng` | Float | 坐标（地图 pin 位置，可被拖拽/点选校正后覆盖） |
| `accuracy` | Float | 定位精度（米） |
| `store_entity_id` | Integer FK `store_entities.id` | 命中的店铺实体（对不上为空） |
| `store_master_id` | Integer | **对齐键 = head master 的实体 id**（`master_id == id` 那条的 `id`）。对不上为空 |
| `points` | Integer NOT NULL default 1 | 只接受 1 或 2（见 D8） |
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

> **对齐键的语义（v1 写错过，务必按此实现）**：`store_entities.master_id` 是 **Integer 实体 id**（NOT NULL、自指）；`store_entities.master_store_id` 是 **String，存的是主档的 `store_id_raw`**（从档冗余字段）。两者不可混用。本设计**统一用「head master 的实体 id」作对齐键**，`master_store_id` 不参与对比（实测有 7 行合并后仍是自己的 raw，**不要用它**）。
>
> **主档链会变**：上传入表时的 `auto_merge_exact` 会持续合并主档（实测库内已有 444 条 `store_merge_logs`）。因此**任何"存下来的主档 id"都可能已不是当前 head**——`staff_checkins.store_master_id`、`checkin_stores.store_master_id` 在读出来用于对比或搜索时（§7 层①、§8 的 C 侧），**必须再跑一次 `head_master_id()`**（按实体 id 缓存）。打卡侧对主链**只读不回写**。

### 5.2 `staff_checkin_photos` 照片

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `checkin_id` | Integer NOT NULL FK `staff_checkins.id`, Index | 删除由应用层显式处理（见 §13.5，不依赖 DB 级联） |
| `rel_path` | String(512) NOT NULL | 存储键（OSS key 或本地相对路径） |
| `thumb_path` | String(512) NOT NULL default "" | 缩略图键（前端生成后一并上传） |
| `bytes` | Integer NOT NULL default 0 | 原图字节数 |
| `sha256` | String(64) NOT NULL default "" | 内容摘要（去重/取证预留） |
| `captured_live` | Boolean NOT NULL default True | 是否摄像头直拍（false=设备降级为相册选择） |
| `purged_at` | DateTime | 过期清理留痕（非空＝对象已删，库里保留记录） |
| `created_at` | DateTime NOT NULL, **Index** | **GC 保留期以本字段为准**（不是 `ref_date`，也不是打卡提交时间）；建索引让 24 个月清理的扫描便宜 |

**一张打卡 1..N 张照片，服务端强制 ≥1**（0 张直接 400）。

### 5.3 `checkin_stores` 打卡点位簿（对齐资产）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `name_norm` | String(255) NOT NULL **UNIQUE** | 归一化店名（打卡点的唯一键） |
| `name_raw` | String(255) NOT NULL default "" | 最近一次写法 |
| `place_id` | String(255) | 最近一次谷歌 place_id |
| `address` / `lat` / `lng` | Text / Float / Float | 最近一次谷歌侧信息 |
| `store_entity_id` / `store_master_id` | Integer | 已对齐的实体 / head master 实体 id（对不上为空） |
| `hit_count` | Integer NOT NULL default 0 | 被打卡次数（本地搜索排序用） |
| `last_used_at` | DateTime | |
| `bound_by` | Integer FK `users.id` | 预留（本期无人工绑定；自动对齐时留空） |
| `created_at` / `updated_at` | DateTime NOT NULL | |

作用：①同一家店第二次打卡**直接命中已对齐结果**（稳定、0 外部调用）；②本地搜索的第一优先数据源。

## 6. 服务层接口

### 6.1 `app/services/checkin.py`

```python
JST = timezone(timedelta(hours=9))    # 日本无夏令时，固定 +09:00（不依赖 tzdata）

@dataclass(frozen=True)
class MatchResult:                     # match_store 的返回契约
    store_entity_id: Optional[int]     # 命中的实体
    store_master_id: Optional[int]     # head master 实体 id（对齐键）
    method: str                        # point_hit | name_exact | name_fuzzy | ambiguous | none
    score: int                         # 0..100（int(ratio()*100)，仓库既有口径）

def jst_now() -> datetime              # tz-aware
def jst_today() -> date
def head_master_id(db, entity_id) -> int          # 沿 master_id 链走到 head（防环）
def create_checkin(db, user, *, store_name, points, ref_date=None, lat=None, lng=None,
                   accuracy=None, place_id="", address="", client_ts="",
                   photos: list[tuple[bytes, bytes, bool]]) -> Checkin
    # photos = [(orig_bytes, thumb_bytes, captured_live), ...]，长度必须 ≥1
    # ref_date 为空 → jst_today()；越界（早于 backfill 窗口 / 晚于今天）→ raise ValueError
def update_checkin(db, user, cid, *, store_name=None, points=None, remove_photo_ids=(),
                   add_photos=()) -> Checkin
def delete_checkin(db, user, cid) -> None          # 先删存储对象，再删照片行与打卡行
def can_self_edit(ck, user) -> bool                # 本人 且 ref_date == jst_today()
def void_checkin(db, admin, cid, reason="") -> None
def unvoid_checkin(db, admin, cid) -> None
def match_store(db, name_raw) -> MatchResult       # 三层对齐（§7）
def search_local(db, q, limit=20) -> list[dict]    # 点位簿优先 → 主档；给前端候选
def list_checkins(db, *, person_code=None, month=None, date=None, only_backfill=False,
                  include_voided=False, page=1, per=50) -> dict   # {"rows": [...], "total": n, "page": p}
def touch_point(db, *, name_raw, place_id="", address="", lat=None, lng=None) -> CheckinPoint
```

### 6.2 `app/services/checkin_compare.py`

```python
def compare_month(db, month: str, person_code: str = "", page: int = 1, per: int = 100) -> dict
    # -> {"month": "2026-10", "summary": {...}, "rows": [...], "total": n, "page": p}
```
`rows[].kind ∈ {missing_formal, missing_checkin, points_mismatch, date_only, unaligned}`（见 §8）。

### 6.3 `app/services/storage.py`

```python
class StorageBackend:                  # put(key, data, content_type) / get(key) / delete(key) / exists(key) / list(prefix)
class LocalStorage(StorageBackend)     # 根目录 = settings.upload_dir / "checkins"（仅本地开发与单测）
class AliyunOssStorage(StorageBackend) # oss2，**延迟导入**（未装/未配不得影响启动）
def get_storage() -> StorageBackend    # 由 settings.checkin_storage 决定（local|oss）

class PhotoPurged(Exception):          # 对象已被 GC 删除（purged_at 非空）→ 路由层转 410

class CheckinPhotoStore:               # 业务封装
    def save(self, ck, orig_bytes, thumb_bytes, captured_live) -> CheckinPhoto
    def read(self, photo, thumb=False) -> bytes      # purged → raise PhotoPurged
    def delete(self, photo) -> None
    # 键规范：checkins/YYYY/MM/{person_code}/{uuid}.jpg 与 .../{uuid}_thumb.jpg（YYYY/MM 取 ref_date）
```

### 6.4 配置项（`app/config.py` 追加，沿用 `_env_int` / `os.environ.get` 风格）

| 设置属性 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `google_maps_key` | `VISIT_GOOGLE_MAPS_KEY` | `""` | 空 = 前端走降级路径 |
| `checkin_storage` | `VISIT_CHECKIN_STORAGE` | `local` | `local` / `oss` |
| `checkin_backfill_days` | `VISIT_CHECKIN_BACKFILL_DAYS` | `31` | 0 = 不限 |
| `checkin_match_threshold` | `VISIT_CHECKIN_MATCH_THRESHOLD` | `92` | `int(ratio()*100)` 口径 |
| `checkin_max_photos` | `VISIT_CHECKIN_MAX_PHOTOS` | `5` | 单次打卡张数上限 |
| `checkin_max_photo_mb` | `VISIT_CHECKIN_MAX_PHOTO_MB` | `5` | 单张大小上限 |
| `checkin_retention_months` | `VISIT_CHECKIN_RETENTION_MONTHS` | `24` | GC 保留期 |
| `oss_endpoint` | `VISIT_OSS_ENDPOINT` | `""` | 例 `oss-ap-northeast-1.aliyuncs.com` |
| `oss_bucket` | `VISIT_OSS_BUCKET` | `""` | 私有 bucket 名 |
| `oss_access_key_id` | `VISIT_OSS_AK` | `""` | RAM 子账号 |
| `oss_access_key_secret` | `VISIT_OSS_SK` | `""` | RAM 子账号 |
| `oss_prefix` | `VISIT_OSS_PREFIX` | `checkins/` | 对象前缀 |

> OSS 变量名需同步补进 `docs/阿里云OSS开通清单.md`，地图变量名补进 `docs/谷歌地图API申请流程.md`。

## 7. 店名对齐（三层递进，只为"能对比"，不做管控）

| 层 | 条件 | 结果 |
|---|---|---|
| ① 点位簿命中 | `checkin_stores.name_norm` 全等 | 沿用该点位已存的 `store_entity_id`，主档 id 取 `head_master_id(该点位 store_master_id)`（**点位簿的值可能因后续合并而 stale，必须重解析**）（`method=point_hit`） |
| ② 主档全等 | `store_entities.name_norm` 全等，且该名字组内**恰好一个 head master** | 绑定之（`method=name_exact`） |
| ②′ 同名歧义 | 该名字组内 head master **多于一个**（实测 430 组） | **不绑定**（`method=ambiguous`），对比时归入 `unaligned` |
| ③ 模糊 | `difflib.SequenceMatcher`，候选 = SQL 子串检索 top-30 | `int(ratio()*100) ≥ VISIT_CHECKIN_MATCH_THRESHOLD`（默认 92）→ 取排序第一（`method=name_fuzzy`）；低于阈值 → 不绑定（`method=none`） |

- 命中实体后一律用 `head_master_id()` 解析到 head master 再存 `store_master_id`（实测库内有 2 条链式主档）。
- 三层都没中 → 只写 `checkin_stores` 一行（`store_master_id` 空），**绝不写 `store_entities`**。
- 模糊匹配**只用于自动对齐判定与搜索候选排序**，本期**没有人工绑定入口**（D10）。

## 8. 对比口径（按月 × 人，纯只读）

**对齐键 = head master 实体 id**，两侧用**同一条解析规则**：

- F 侧：`formal_records.store_id_raw` → `store_entities.store_id_raw` 命中实体 → `head_master_id()`。
- C 侧：`staff_checkins.store_master_id` → **读取时再跑一次 `head_master_id()`**（建卡时解析过一次，但此后主档可能被 `auto_merge_exact` 合并，存值会 stale；按实体 id 缓存避免重复走链）。
- `F` 侧的「当月」定义：`formal_records.japan_date ∈ [月初, 下月初)`（`formal_records` 无 month 列，且 `japan_date` 可空——**空日期的行不属于任何月份**，被自然排除）。
- 必须这样，否则同一家店的从档/多写法（空格、全角）会各算一家——即 8 月多算 4 条点数那个老坑。
- **不做 `store_id_raw` 兜底**：若某条 F 的 `store_id_raw` 在 `store_entities` 中查不到（实测 29755 条中 0 条），该行计入 `summary.data_gap` 并在页面上提示，**不静默合并**。

**算法（逐人逐月，顺序不可调换）**

1. 取 `F`（该人该月正式记录）与 `C`（该人该月打卡，**排除 `voided`**）。
2. **未对齐预过滤**（必须在第 5 步之前）：对 `store_master_id` 为空的 `C`，尝试与同人同月的 `F` 做「打卡店名 norm == 实体 name_norm」全等匹配：
   - **恰好命中一个 head master** → 视为已对齐（`align_method=name_eq`）；
   - **命中多个 head master**（实测 942 组重名、430 组含多个 head master）或未命中 → **不绑定**，归入 `unaligned`，**移出后续步骤**。
   （与 §7 ②′ 同一原则：歧义一律不绑，宁可标出来也不猜。）
3. **同日同主档配对**：
   - C 侧先按 `(ref_date, master_key)` 聚合（同人同日同店多次打卡 → 点数取**合计**，行内列出全部 `checkin_id`）；
   - F 侧同键多条同样取**合计**；
   - 配对成功者：点数一致 → 记入 `consistent_cnt`（不产出行）；点数不一致 → 产出 `points_mismatch` 行。
4. 剩余 `C` 尝试 ±1 天内同主档的 `F`（窗口内多条时取**日期最近且未被占用**者）→ `date_only` 行；该类行内的点数差异**只展示在该行 `points_*` 列**，不重复计入 `points_mismatch`。
5. 剩余 `C` → `missing_formal`（打卡有、有效记录无）。
6. 剩余 `F` → `missing_checkin`（有效记录有、打卡无）。
7. 每个已配对的 `F` 只能被占用一次（含第 4 步）。

**汇总**

| 字段 | 定义 |
|---|---|
| `checkin_cnt` | 原始打卡条数（排除作废） |
| `formal_cnt` | 原始正式记录条数 |
| `matched_cnt` | 同日同主档配对成功的**聚合行数** |
| `consistent_cnt` | 其中点数一致的聚合行数 |
| `consistent_rate` | `consistent_cnt / matched_cnt`；`matched_cnt == 0` 时显示 `—` |
| `missing_formal` / `missing_checkin` / `points_mismatch` / `date_only` / `unaligned` | 五类各自计数 |
| `data_gap` | `store_id_raw` 查不到实体的 F 条数（正常为 0） |

**月份边界按 JST**。打卡数据从启用日开始，之前月份只有 `F` 侧 → 页面提示"该月无打卡数据"，不报错。

## 9. 员工端（H5）

路由：

| 路由 | 返回 | 说明 |
|---|---|---|
| `GET /my/checkin` | HTML | 打卡页（含今日已打卡列表，用 `_checkin_today.html` 渲染） |
| `POST /my/checkin` | **JSON** | 提交（multipart） |
| `POST /my/checkin/{id}/edit` | **JSON** | 当天编辑（店名/点数/坐标；可删图、可加图） |
| `POST /my/checkin/{id}/delete` | **JSON** | 当天删除 |
| `GET /my/checkin/search?q=` | JSON | 店名候选（本地；`source=local`） |
| `GET /my/checkin/photo/{pid}` | 二进制 | 鉴权取图（本人；`?thumb=1` 取缩略图） |
| `GET /my/checkins` | HTML | 我的打卡历史（月份 + 按日分组） |

**提交契约（v1 未定义，此处钉死）**：打卡页强依赖 JS（地图、canvas 压缩），因此**提交走 `fetch` + JSON**，不做 no-JS 兜底（明确 YAGNI）。

- 请求：`multipart/form-data`，字段 `store_name / points / ref_date? / lat / lng / accuracy / place_id / address / client_ts / photos[] / thumbs[] / captured_live[] / csrf_token`
- 成功 → `200 {"ok": true, "count": N, "today_html": "<li>…</li>"}`；前端替换当日列表并清空表单
- 校验失败 → `400 {"ok": false, "error": "…", "field": "photos"}`；**前端不清空表单**（保留已填内容与照片选择）
- 未登录 → `401`（前端跳登录）；无权限（非本人）→ `403`
- `edit` / `delete` 同契约（成功 `200 {"ok": true, "today_html": ...}`）

页面结构（自上而下）：**地图卡 → 店名卡 → 点数卡 → 照片卡 → 提交**，下方「今日已打卡 N 家」列表。

1. **定位**：进页面请求 `navigator.geolocation.getCurrentPosition`（`enableHighAccuracy: true`，8s 超时）。
   成功 → 地图落 pin + 显示 `±Xm`；pin **可拖拽**、**可点击地图落点**（拖/点后显示「已手动校正」）。
   失败/被拒 → 地图退到该员工最近一次打卡坐标（无则东京默认视图），提示「未取到定位，可在地图上手动选点」，**不阻塞提交**。
   **地图瓦片加载不出来（离线/内网/CDN 不可达）时，点击落点与拖拽仍必须可用**——降级渲染不得依赖瓦片，底图失败只影响观感。
2. **店名**：输入 ≥2 字符、300ms 防抖 → **先请求本地** `/my/checkin/search`；
   本地无结果且存在 `mapsKey` → 调 Google Places（Autocomplete + Place Details 取 `place_id/地址/坐标`）→ 选中回填店名/地址/place_id/坐标 + 「用这个位置校正地图」按钮。
   也支持**完全不搜、直接手输店名**。
3. **点数**：1 / 2 两个大按钮，默认 1；不做任何提示与校验。
4. **照片**：`<input type="file" accept="image/*" capture="environment" multiple>`；
   前端 canvas 压原图（长边 1280、JPEG q0.8）+ 生成缩略图（长边 320、q0.7）；
   缩略图即时预览、可删；提交前校验 ≥1 张。设备不支持直接拍摄时降级为相册选择并置 `captured_live=false`。
5. **日期**：默认今天（JST 由服务端最终裁定）；「补录」展开日期选择，范围 = `[jst_today - VISIT_CHECKIN_BACKFILL_DAYS, jst_today]`（默认 31 天）；**页面不做任何"补录"字样标记**。
6. **重复软提示**：同人同日同店已存在 → 提示「今天已在这家店打过卡（可继续提交）」，**不阻止**。
7. **失败处理**：展示 `error`，**保留表单内容与照片选择**，可原地重试——绝不产生"有打卡记录、没照片"的幽灵数据（写入顺序：先存储、后落库；落库失败回滚已写对象）。

## 10. 照片与存储

- **上传路径 = 服务端代理**（手机 → web → OSS）。理由：保证"≥1 张门头照"与打卡记录同事务；不引入 STS/客户端直传与 CORS。
- **Bucket 私有**：取图必须经过登录态鉴权路由；**没有任何公开 URL**。
- **取图**：`GET /my/checkin/photo/{pid}`（本人）与 `GET /checkins/photo/{pid}`（管理员）→ 校验归属 → 从存储后端读出并回传（`Cache-Control: private, max-age=300`）；列表页只加载 `thumb_path`。
  **`purged_at` 非空**（对象已被 GC 删除）→ 返回 `410 Gone`，页面上显示「照片已过保留期清理」占位，不报错。
- **校验**：张数 1..`VISIT_CHECKIN_MAX_PHOTOS`(5)；单张 ≤ `VISIT_CHECKIN_MAX_PHOTO_MB`(5)MB；请求总大小 ≤ `MAX_UPLOAD_MB`(50)；类型按**文件头 magic bytes** 判定（JPEG/PNG/WebP），不信任 `Content-Type`；`thumbs` 数量必须等于 `photos` 数量，否则 400。
- **清理**：`scripts/checkin_photo_gc.py`
  - 主逻辑：`created_at` 早于 `now - VISIT_CHECKIN_RETENTION_MONTHS` 的照片 → 删对象 + 写 `purged_at`（**打卡记录与照片行都不删**）
  - **孤儿扫描**（`--orphans`）：列出存储前缀下无对应 `staff_checkin_photos` 行的对象（应用层删除失败或中断的残留）→ 默认**只报告**，`--apply --orphans` 才删
  - 默认 `dry-run`，`--apply` 才真正删除；输出统计（扫描数/待删数/已删数）
- **OSS 未配置/不可用**：上传直接失败并提示重试，**不降级写本地盘**（避免照片散落在服务器上）。
- `oss2` 为延迟导入；仅当 `VISIT_CHECKIN_STORAGE=oss` 时才会 import，故本地开发与单测**无需安装**（启用 OSS 时补进 `requirements-web.txt` 并重建镜像）。

## 11. 管理端

| 路由 | 说明 |
|---|---|
| `GET /checkins` | 列表：月 / 人 / 仅补录 筛选，`page`/`per`(默认 50) 分页；列 日期·员工·店名·点数·坐标·缩略图·提交时刻·补录·作废；动作 作废/恢复 |
| `POST /checkins/{id}/void` | 作废（reason，留痕）→ 303 重定向（沿用现有表单风格） |
| `POST /checkins/{id}/unvoid` | 恢复 |
| `GET /checkins/photo/{pid}` | 鉴权取图（管理员，`?thumb=1`；`purged_at` 非空 → 410） |
| `GET /checkins/compare` | 对比清单（月 / 人筛选，五类差异 + 汇总卡，分页） |
| `GET /checkins/export?kind=list\|compare` | Excel 导出（openpyxl，复用现有 `StreamingResponse` 写法） |

- 对比清单**纯只读**：无任何写动作（作废只在列表页）。
- 导出照片列给**登录后可点开的链接**（本站鉴权路由），不放 OSS 直链。
- 权限：中间件 + 路由 `role != admin` 双层（沿用现有约定）。
- 关键元素带 `data-testid`（沿用 `/perf` 等页面的既有约定），便于后续自动化验收。

## 12. 权限与 i18n

- `app/main.py` 的 `STAFF_ALLOWED` 增加 `/my/checkin` 一项即可：中间件用 `path.startswith(STAFF_ALLOWED)` 前缀匹配，`/my/checkins` 已被覆盖（**不要**当成两条独立条目理解）。
- 员工端所有路由校验 `user.role == "staff"` 且有 `person_code`；管理端 `role == "admin"`。
- 取图与编辑/删除均校验**归属**（本人）。
- 注意 `must_change_password` 拦截：未改密员工会被重定向到 `/my/password`，验收走查需用已改密账号（见 §15）。
- 界面文案全部进 `app/i18n.py`（key=中文原文，ja=日文）；现场是日本员工，**日文是必做项**。

## 13. 集成陷阱（实现时必须处理）

1. **时区**：现有库全用 naive UTC（`datetime.utcnow`）。打卡的业务日必须显式 JST，固定 `timedelta(hours=9)` 偏移，**不用 `zoneinfo`**（避免 slim 镜像缺 tzdata）。
2. **模板循环变量不可命名为 `t`**：会覆盖 i18n 全局函数（历史坑）。本期循环变量用 `ck`、`ph`。
3. **`{# #}` 注释与 `<script>` 内中文不会被自动翻译**（i18n 脚本跳过 script/style）——地图/拍照 JS 里的用户可见文案需手工 `t()` 化或改用模板变量传入，**且**日文字典要补全。
4. **OSS 后端延迟导入** `oss2`：未配/未装时不得影响其他路径启动。
5. **删除与级联**：`delete_checkin` 必须**显式**先删存储对象、再删 `staff_checkin_photos` 行、再删打卡行——**不依赖 DB 级联**（本地 SQLite 未开 `PRAGMA foreign_keys`，行为与 PG 不一致）。存储对象删除失败时记录 warning，交由 GC 的孤儿扫描兜底。
6. **照片写入顺序**：先写存储 → 再落库；落库失败回滚已写对象；多张按序号配对 `photos[i] ↔ thumbs[i]`。
7. **`is_backfill` 只记不判**：不得在页面上显示补录状态、不得阻止提交、不得用于任何准入判断。
8. **不改现有表**：任何 `ALTER` 现有表的冲动都要驳回（对比是只读）。`purged_at` 属于新建表的一部分。
9. **迁移 head**：`down_revision = "f7e8d9c0b1a2"`（当前唯一 head）。
10. **SQLite/PG 双兼容**：Text 默认值用 `sa.text("('')")`；`lat/lng` 用 `Float`（PG→double precision）。
11. **CSRF**：multipart/fetch 提交同样需要 `csrf_token`（复用 `csrf_ok`）。
12. **地图 JS 只在有 key 时加载**：无 key 时不得请求任何谷歌域；Leaflet 降级也按需动态注入（避免无谓的 CDN 依赖）。
13. **测试隔离**：单测必须把 `UPLOAD_DIR` 指向 `tmp_path`，否则 `LocalStorage` 会把图片写进仓库 `./data/uploads`。注意 `get_settings()` 带 `@lru_cache`，**必须在首次取 Settings 之前设置环境变量**（或先 `get_settings.cache_clear()`），否则改了 env 也不生效。

## 14. 测试策略

`tests_web/test_checkin.py`（存储用 `LocalStorage` + `tmp_path`，**不连外网**）：

| 组 | 用例 |
|---|---|
| 打卡 CRUD | 提交成功（含 1 张照片）；0 张照片 → 400 且 `field=photos`；店名空 → 400；`points ∈ {0,3,"x"}` → 400 |
| 纠错规则 | 当天可编辑/删除；跨天（`ref_date != jst_today`）编辑/删除被拒 |
| 补录窗口 | `ref_date` 超出 31 天 → 400；未来日期 → 400；`VISIT_CHECKIN_BACKFILL_DAYS=0` 时不限；`is_backfill` 正确置位 |
| 照片 | 伪造扩展名（内容非图片）被 magic bytes 拒绝；超大被拒；`thumbs` 数量不匹配 → 400 |
| 鉴权 | 本人可取图；他人 403；管理员可取图；未登录跳登录；`purged_at` 非空 → 410 |
| 越权 | 员工访问 `/checkins`、`/checkins/compare` 被拦（中间件 + 路由双层） |
| 对齐 | 点位簿命中；主档全等（组内唯一主档）；**同名多主档 → 不绑定（ambiguous）且未写 `store_entities`**；模糊 ≥ 阈值自动绑；低于阈值不绑；链式主档解析到 head；**先建打卡 → 再合并主档 → 对比仍能配对成功（stale 值读时重解析）** |
| 搜索 | 本地搜索命中点位簿与主档；空/无结果返回空数组 |
| 对比 | 五类差异各自可构造并断言（**含 `unaligned` 非零**）；`voided` 打卡被排除；±1 天归入 `date_only`；同人同日同店重复打卡按合计比对；一致率计算（`matched_cnt=0` → `—`） |
| 删除 | 删除打卡后其照片行与存储对象一并消失（无孤儿） |
| 作废 | 作废后不出现在默认列表与对比中；恢复后回归；留痕字段写入 |
| 导出 | 列表/对比 Excel 可生成且表头正确 |
| 事务 | 存储写入成功但落库失败 → 已写对象被回滚删除 |
| GC | `dry-run` 不删任何对象；`--apply` 只删过期对象并写 `purged_at`；孤儿扫描只报告 |

另：`tests_web/test_permissions.py` 补一条员工越权访问 `/checkins` 的断言。

## 15. 本地验收（本期交付标准）

1. `./.venv/bin/python -m pytest tests_web/test_checkin.py -q` 全绿；相关权限测试全绿。
2. `./scripts/dev_server.sh` 起服务 → **员工账号登录**（用未触发改密的真实账号，如 `chenjiayi`，口令同演示口令；本库 55 个 staff 账号中 20 个 `must_change_password=1`，会先被重定向到 `/my/password`）→ `/my/checkin`：
   - **无 `VISIT_GOOGLE_MAPS_KEY`**：地图走降级（Leaflet/OSM 或点击落点/拖拽），店名本地搜索可用，定位可用，拍照可用，**能完整提交**；
   - 有 key（可选）：谷歌地图加载、Places 候选、拖拽纠偏。
3. 今日列表可编辑/删除；跨天的只读；补录 32 天前被拒。
4. 管理端 `/checkins` 看到记录与缩略图、可作废；`/checkins/compare` 显示五类差异与一致率；两个导出可下载。
5. 日文界面（`?lang=ja`）走查打卡页与管理页无中文残留（业务数据除外）。
6. `scripts/checkin_photo_gc.py` 默认 dry-run 不删任何东西。
7. 线上零影响：本期不在线上部署（D13）。

## 16. 风险与未验证假设

| 风险 | 影响 | 对策 |
|---|---|---|
| 店名对不上主档（员工写法与文件写法差异大） | 对比清单里大量 `unaligned`，等于没对比 | 点位簿 + 三层对齐；对齐键与数据源解耦，将来换 Alipay 主档可重跑对齐 |
| **`name_norm` 同名多主档**（实测 942 组重复、430 组含多个 head master） | 自动绑定可能绑错店 → 把真差异掩盖成"一致" | 歧义组**一律不绑**（归入 `unaligned`），宁可标出来也不绑错 |
| 谷歌 key 是浏览器可见的 | 被人薅用量 | 客户的 key 必须做 referrer + API 限制 + 每日配额上限（文档已给）；超额自动回落本地 |
| OSS 凭据未到位导致无法上线 | 功能只能本地验收 | 存储抽象 + 开关；本地 `local` 后端跑通，凭据到达后切换（**生产不发本地存储**） |
| 24 个月保留依赖宿主 cron | 脚本故障 → 无限累积 | OSS 侧生命周期 900 天兜底（客户文档已给）；GC 孤儿扫描可兜应用层删除失败 |
| 定位精度差（室内/地下） | 坐标不可用 | 只作留痕与展示，**不参与任何准入判断**；可拖拽/点选校正 |
| 打卡表增长（~15k 行/月） | 列表页变慢 | 列表与对比均分页；索引已覆盖 `(person_code, ref_date)` 与 `(ref_date)` |
| **主档持续合并**（实测 444 条合并日志） | 打卡存的主档 id 会 stale → 明明同一家店却报成"两边都没有" | 读时统一重解析到 head（§5.1/§8）；测试覆盖"先打卡后合并"场景 |

**未验证假设**（实现后用真实数据检验）：①打卡店名与文件店名归一化后的全等率；②±1 天窗口是否够（跨零点打卡占比）；③真实打卡点数与正式表点数的一致率；④`unaligned` 的实际占比。

## 17. 明确不做（YAGNI）

- 防作弊：设备指纹、速度异常检测、同设备多账号告警、复核队列
- 补卡审批流、补卡状态标记与提示
- 人工绑定主档的 UI（本期只有自动对齐；含"绑错了要解绑"的场景）
- 离线队列 / 断网续传 / 后台补传（D14）
- 员工端备注输入、**POI/地址反查**（注意：地图上**拖拽与点击落点**属于本期功能，见 §9.1，不要误删）
- 打卡数据进入任何薪资/绩效计算路径
- 任何 MCP 工具（D12）
- 生产上线与线上迁移（D13）

## 18. 未来扩展（本期只留接口，不实现）

- **店铺主档换成 Alipay 数据源**：对齐目标换数据源，打卡侧字段与逻辑不变（`store_master_id` 语义保持"对齐到的 head master 实体 id"）。
- **MCP 工具**（如 `visit_checkin` / `visit_my_checkins` / `visit_checkin_report`）：`source` 字段与 `staff_checkins` 结构已预留。
- **离线队列**：`client_ts` 与 `is_backfill` 已预留判定依据。
- **打卡点位簿升级为店铺别名库**：将来若主档以 Alipay 为准，本表可作为"现场叫法 → 主档"的映射资产。
