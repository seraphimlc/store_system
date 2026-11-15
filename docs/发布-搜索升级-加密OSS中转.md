# 搜索升级发布：加密 ZIP 经现有 OSS 中转

更新：2026-11-15。**本文是已完成发布的证据记录，不是另一份操作手册；唯一生产部署入口为[生产部署与回滚](../deploy-next/生产部署与回滚.md)。** 用户要求 ZIP 使用现有 OSS 中转，避免本地大归档直传；已按此路径完成生产发布，不修改桶权限，也不把公开桶伪称私有桶。日常步骤见[生产部署与回滚](../deploy-next/生产部署与回滚.md) §4，完整验收见[搜索计划](计划-商户搜索升级.md) §10.50。

## 已部署事实

- release：`prod-20261115-031440`，源码 `bc3cfd0d70c9226cb13f85691c04c914bb6d5182`；2026-11-15 03:52:30 UTC+8 切换完成。
- 更新 API、员工端、Gateway；管理端仍 `prod-20261113-011447`，Worker 仍 `store-next-api:prod-20261112`，认证模块保持生产基线。不是把整个当前工作区上线。
- API 1.5 CPU / 1024MiB，Worker 1 CPU / 512MiB。稳定项目 `store-next-prod-20261112`，配置 `/opt/store-next/runtime/compose.json` 与 `runtime.env`；状态 `/opt/store-next/production-state.json`。
- 全量商户搜索使用 620,280 条商户语料，按网格/片区返回归属、计数、分页，不设任务/批次语料边界；任务/团队历史和写入仍走原权限。
- 本地业务库未随本次迁移，本地默认 Docker 未更新；不得混称为生产状态。

## 已验证传输与密钥边界

1. 使用已有五项配置：`STORE_FILES_OSS_ENDPOINT`、`STORE_FILES_OSS_BUCKET`、`STORE_FILES_CDN_BASE`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`，不新增必需凭证；不提供私有桶/gcloud、明文镜像或其他中转备选方案。端点为空时适配器默认 `https://storage.googleapis.com`；桶 `visitworld-assets` 为公开读取，CDN `https://cdn.visitworld.me`。
2. 本地使用随机 256 位密钥、随机 96 位 IV 的 AES-256-GCM；release ID 和原归档摘要作为 AAD。ZIP 白名单仅 `images.tar.gz.enc`、`manifest.json`、`SHA256SUMS`，不含密钥、配置、数据库备份或明文镜像。
3. 运行 `node scripts/encrypted-release-bundle.mjs pack "$RELEASE"`，私下加载已有配置后执行同工具 `upload "$RELEASE"`；公开前端资源用 `node scripts/release-bundle.mjs assets --release "$RELEASE"`。禁止用通用明文 pack/upload 上传至公开桶。
4. 密钥独立写入本地 600 文件，经 SSH 传至服务器 700 release 目录；不进 OSS/CDN、命令参数、日志或 Git。服务器从实际上传结果下载大 ZIP，不经本地 SSH 直传大包。
5. 验证 ZIP 大小/SHA256、白名单/CRC、密文摘要，认证解密至临时文件；GCM 和明文归档摘要都通过后才导入镜像。镜像核对 amd64、config 摘要、OCI manifest 引用与 rootfs/config；不同 Docker 后端 `.Id` 不能机械相比。
6. 前端七个资源已校验摘要、MIME、CORS。两端密钥、临时恢复库/容器/env 已删除；归档和备份受控保留。保密性来自认证加密，不是随机 URL 或 Cache-Control。历史公开包安全处置另行跟踪。

| 产物 | 已验证摘要 |
|---|---|
| 加密 ZIP | 234,703,570 bytes；SHA256 `5d8f021fe470219ea3b67d2b52dc005a4f1b0491eaa21703953e9394d029921e` |
| API config digest | `sha256:ee5f53039ce01b9980cdd923b7352a34d8de6c637d5ddf1cb29d636971406b2a` |
| Gateway config digest | `sha256:2ae6dde23a3203696f9d31fe7ac456784e95cc016c9ef763c7b2af7f83a461a0` |

## 搜索迁移与构建

生产实际执行 **007–009**：可重建索引、独立商户日志/反馈、地理归属版本；不执行历史任务搜索 003–006、不执行独立认证 001、不清洗商户源记录或改结算表。索引构建成功 COMMIT 后执行 `VACUUM ANALYZE bd_merchant_search_name_posting`，含 VACUUM 共 840,120ms；构建期间旧 API 继续服务，切换存在短维护窗口。

当前 generation `ef78f7b9-1937-4b45-bed0-400749409031`，策略 `merchant-guarded-v3`；文档 620,280、名称组 470,517、postings 4,118,535；源摘要 `c706d89d00b726d928efdd7d4a909ee70c76b5b0d4b745f1b09dd8cf45289e06`。API 监听前预热，代际/归属版本变化后重载，不承诺无等待。

源码维护脚本 `scripts/merchant-search-index.mts` 的 schema/rebuild 要求 `--apply --confirm-database=store_settle`，status 只读；API/dev 不自动执行。生产宿主机无 Node，编译运行镜像不含完整源码/tsx，不能直接照抄源码命令；本次用 Docker 内编译版维护 helper，复用目标私有 env/network，显式事务及提交后 VACUUM。操作步骤与前置审计见部署手册。

## 验收、回退与限制

- 同配额恢复库不可变候选：5 并发 200 请求，0 错误，P95 958ms/P99 1235ms；固定名称二级样本候选覆盖 58,391/113,774＝51.3219%，高于原归一化 32.05%。这不等于同店准确率或真实用户成功率。
- 生产八类 smoke 均 200、有搜索日志；393×852 公网验收默认网格、片区筛选、实际命中高亮、只读详情和抽屉上方 46px 反馈条，无横向溢出/pageerror。禁用地图供应商调用并拦截反馈 POST，未伪造反馈。
- API/Gateway/Worker 健康且无 OOM；原外层 Nginx 字节保持。初始生产验证日志不是一级真实用户样本，需后续真实采集衡量收益。
- 回退点 `prod-20261113-105556`；备份 `/opt/store-next/backups/prod-20261115-031440`，不可变产物 `/opt/store-next/releases/prod-20261115-031440`。恢复原 Compose/API/Gateway及配置；保留新增搜索 schema/日志和任务卷，不倒灌数据库、不运行旧 Python cutover rollback、不解暂停旧服务。
- 禁发失败候选 `prod-20261114-175857`、`prod-20261114-223406`、`prod-20261115-014028`。
