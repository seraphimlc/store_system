> **当前日常发布手册（2026-11-13）**：[生产部署与回滚](生产部署与回滚.md)。镜像统一打为单个 ZIP，通过 Google Cloud Storage 上传；下文集团云分卷与首次旧系统迁移仅作历史记录。

## 镜像集团云归档（2026-11-12）

通过用户指定的frontend-tools / front-demo / aliyun_default（集团云）上传。完整ZIP为231,917,744字节（约221.2MiB），单次上传失败后分为14卷，均已上传并核对公开HTTP状态和大小。

- [下载清单与还原脚本ZIP](https://static.xyb2b.com/files/f2e97b4c6150a2c259a533b4e7f06d46.zip)（这是下载工具包，镜像由脚本从14个分卷还原）。
- 解压后运行`python3 restore-images.py`；逐卷SHA256、整体ZIP SHA256及CRC验证完成后生成`images.tar.gz`。已用本地已校验分卷完整演练还原；未重新下载全部221MiB。
- 导入：`docker load -i images.tar.gz`；镜像为`store-next-api:prod-20261112`与`store-next-gateway:prod-20261112`，linux/amd64。下载脚本不会执行Docker或修改数据库。
- 完整镜像ZIP SHA256：`e1217be2748e34d600c03e7b3c4da6de2efa94fa57686739a4438ab4c2c43f25`。
- 下载工具包SHA256：`ca2213bbb17b364957fbf812e303d8aabbf257ef8c722a743d10de2d7d6683d7`。
- 归档只含镜像、公开资源及manifest，不含运行凭证或数据库备份。分卷URL及校验清单位于工具包`cloud-download.json`。

## 生产目标：visitworld（2026-11-12）

生产使用独立的 `deploy-next/production-release.py` 编排器，不复用本地数据库白名单或HTTP豁免。执行方式为通过SSH将该文件安装到 `/opt/store-next/production-release.py`，所有生产操作固定经 `ssh visitworld`。现有 `scripts/release.mjs` 仍为本地目标，`--env production` 继续拒绝，不能删除guard来发布。

生产目录：

- `/opt/store-next/releases/<release>/`：amd64镜像归档、manifest、Compose、私有运行配置和验收证明。
- `/opt/store-next/backups/<release>/`：数据库备份、旧容器配置、Nginx原配置、原件归档；仅root可读。
- `/opt/store-next/production-state.json`：正式运行状态。
- `/opt/store-next/legacy-static/`：保留旧页面静态资源。

分阶段命令（prepare不可盲目重跑，先status核对）：

```bash
ssh visitworld 'python3 /opt/store-next/production-release.py status <release>'
# prepare：备份并恢复到临时库；candidate：只读恢复库，生产入口不变。
ssh visitworld 'python3 /opt/store-next/production-release.py prepare <release>'
ssh visitworld 'python3 /opt/store-next/production-release.py candidate <release>'
# 切流前完成候选浏览器和同库回滚演练，再执行授权的cutover。
ssh visitworld 'python3 /opt/store-next/production-release.py cutover <release>'
# 应用回滚保留同一现库数据，不恢复旧数据库备份。
ssh visitworld 'python3 /opt/store-next/production-release.py rollback <release>'
```

生产API使用Secure Cookie、现有`deploy_default`网络的生产`store_settle`，不启用本地HTTP例外。切换窗口有全站维护、事务排空、旧应用暂停、最终备份及只读验证；新Worker使用独立`store-next-prod-jobs`，历史补投关闭。旧Web/MCP暂停并禁止自动重启，DB持续运行；回滚恢复旧策略并unpause，避免旧entrypoint执行迁移。不要手动unpause旧应用造成双写。

原件卷只读挂载，后续新文件写新任务卷；回滚前必须保留/核对新卷里的合法文件及新任务，不能删卷或全库回灌。回滚只对本次首次Python→Node切换实现；后续生产新栈版本升级应另行扩展带当前release校验、任务兼容检查和相同数据卷交接的流程。


> **生产迁移计划**：[生产迁移与回滚](../docs/计划-生产迁移与回滚.md)。本手册现有CLI只支持本地；生产切换须先完成该计划P0。应用回滚保留同库数据，全库恢复属于单独事故决策。

> **2026-11-12端口更新**：本地Docker统一入口改为 http://localhost:8000/admin/ （管理端）和 http://localhost:8000/ （员工端）。开发态仍使用8001/8002，API3000，Docker基准库store_settle与开发临时库store_settle_refactor_p1不变。只重新创建edge端口映射，不重建或切换业务release。下文8080为历史记录。

## 当前本地验收连接（2026-11-12）

Docker 与日常开发配置分开：Docker 使用未跟踪的 `.env.next.docker.local`，通过 `NEXT_DOCKER_DATABASE_URL` 显式指向本地 `store_settle`；`.env.next.local` 的 `NEXT_DATABASE_URL` 继续使用 `store_settle_refactor_p1`。部署不会迁移、清库或覆盖开发配置。

```bash
pnpm release:build -- --env local --assets cdn --release <新release-id> --config .env.next.docker.local
pnpm release:deploy -- --env local --release <新release-id> --config .env.next.docker.local
```

基准库Docker队列独立使用 `store-next-jobs-store-settle`，不复用旧临时库的待办任务卷；基准库验收设置 `NEXT_WORKER_RECONCILE=false`，避免启动自动补投历史任务，正常API新提交任务仍由Worker处理。旧release回滚仍使用它保存的数据库和任务卷，因此跨数据库回滚会切回该旧release原连接，不能把它当成基准库版本回滚。

以下为历史发布说明；与上述连接说明冲突时以上述为准。

# 新系统本地构建与 Docker 发布

## 日常开发：一个命令启动（2026-11-11）

```bash
pnpm dev
```

- 管理端：`http://127.0.0.1:8001/admin/`
- 员工端：`http://127.0.0.1:8002/`
- API：`http://127.0.0.1:3000/api/health/ready`（可用NEXT_PORT改变；前端代理同步）
- Worker：无独立HTTP端口；私有目录`data/refactor/dev-jobs`，带心跳/单Worker锁。

配置统一按shell > `.env.next.local` > `.env`加载；Vite不接收数据库/云凭证。API/Worker共享开发任务/上传目录，历史原件读取使用已配置的只读目录。开发Worker直接加载TS，并监听API/domain修改；不恢复数据库中的历史任务，避免与旧Docker Worker交叉处理。业务操作会写当前本地测试库，请勿把真实数据测试当作可随意清空的夹具；启动命令不建库、不覆盖数据、不运行迁移。

Ctrl+C统一停止四服务；遇端口冲突退出，不强停他人服务。修改配置后重启命令。前端HMR与后端/Worker源码监听用于日常迭代；下文Docker构建仅用于阶段验收/发布，8080仍可能是旧镜像，不能与8001/8002混淆。


> **2026-11-11 当前交付范围已收敛，现已恢复执行。** 以[当前重构计划](../docs/计划-React-NestJS重构与部署.md)的R1～R5为准：保留现有网格/片区管理，恢复原件、完成GCS/CDN、本地Docker与切换准备；算法生成、502根因与性能/50会话验收不再阻塞本次交付。原件87文件已同步，22条标准引用可用；另1条已确认为无独立原件的历史保留标记。候选b业务验收与隔离发布恢复演练已通过，GCS资源上传/公开读取已验证，CORS与最终8080切换仍待完成。下文历史“待提供OSS配置/必须完成算法或性能”等描述不再代表当前门槛。


当前仅开放本地目标；旧系统仍按`deploy/README.md`运行。生产部署在P7验收前由命令拒绝。

## 配置

复制根目录`.env.next.example`到未跟踪的`.env.next.local`。`NEXT_DATABASE_URL`沿用当前本地独立副本`store_settle_refactor_p1`，该库是保留数据的本地基准，**不可丢弃或自动覆盖**。自动化写验收由隔离运行器创建随机schema-only临时库，不能直接对该基准库运行破坏性fixture。命令不创建表、不迁移数据库，也不自动恢复数据。

需要本地Node22.12+、pnpm12.18、Docker（Compose/buildx）。镜像在本地构建，目标不重新编译。首次拉取基础镜像和依赖耗时单独计算，不能当作应用重启耗时。

```bash
pnpm release:build -- --env local --assets local
# 使用上一条返回的release ID
pnpm release:deploy -- --env local --release <release-id>
pnpm release:rollback -- --env local --release <previous-release-id>
```

入口：`http://localhost:8080/admin/`管理端，`http://localhost:8080/`员工端。当前已提供P3主要页面、P4巡店/对账上传、发薪导出登记与判定/入表/重算子流程，但整个业务迁移未完成，不能替代完整旧系统。

## 产物与切换

产物在忽略目录`.release/<id>/`：manifest、资源、HTML、Docker构建上下文、镜像归档、目标运行配置。`runtime.env`包含运行凭证，权限600；不能分享该目录。前端公开资源不含这些配置。

先校验资源/HTML/镜像，再启动候选API和gateway、检查就绪，再切换统一入口。候选Worker默认不启动，切换前停止旧Worker，切换后启动新Worker；后续失败恢复旧上游。成功后旧版本容器停止以释放内存，但镜像与配置保留，可回滚重启。

本地静态资源保留在`.release/public/`并只读挂载给各gateway，保证旧页面chunk在切换/回滚后仍可读取。HTML不长期缓存，哈希资源长期缓存。不得清除正在使用的release资源。

入口容器共享独立Docker网络`store-next-entry`；每个release有自己的API私有网络。只公开127.0.0.1:8080，不占用旧系统端口或修改其数据库/容器。

Worker持久卷`store-next-jobs`保存queued/running/done/failed，稳定ID拒绝重复入队；独占Worker由部署顺序保证，容器重启恢复已登记的幂等任务，超过重试上限或未知任务失败留痕。当前注册`system.check`、`report.analysis`、`dashboard.analysis`及`settlement.judge/finalize/rebuild`、`import.visit/delete`、`reconciliation.monthly`、`store.master`与`reconciliation.insight`，API与Worker共享任务卷；AI报告逐语言检查点和数据版本保护已验证。其他业务任务的副作用幂等与进度写入在P4/P5逐一实现，不能把基础恢复测试当作所有业务恢复保证。

## OSS/CDN（等待配置与真实验收）

额外变量：`NEXT_CDN_BASE_URL`、`NEXT_OSS_ENDPOINT`、`NEXT_OSS_REGION`、`NEXT_OSS_BUCKET`、`NEXT_OSS_PREFIX`、`NEXT_OSS_ACCESS_KEY_ID`、`NEXT_OSS_ACCESS_KEY_SECRET`，临时凭证另设`NEXT_OSS_STS_TOKEN`；`NEXT_PUBLIC_ORIGIN`用于CDN CORS验证。

```bash
pnpm release:build -- --env local --assets cdn
pnpm release:deploy -- --env local --release <release-id>
```

远程模式先上传版本独立目录；已存在对象须哈希/大小一致，不覆盖不同内容，部分成功可安全重试。CDN读取验证内容哈希、MIME及允许应用Origin的CORS，全部通过才进入容器切换。没有配置时不能声称CDN发布已完成；也不会自动切换成其他bucket。

前端CDN模式与local模式构建出的HTML不同，必须当作不同release验收。正式部署使用独立目标配置，后续P7再开放。

## 验证

```bash
# 需要两个已本地构建版本；第一版本已部署
node scripts/verify-release.mjs <previous-release> <candidate-release>
pnpm test:browser # schema-only 临时库 + 独立 API/Worker，十组源码浏览器回归；不验证当前 Docker release
```

第一个脚本验证HTML篡改拒绝切换、版本切换、旧chunk保留、缓存、回滚、回滚后chunk与再次部署。第二个脚本只在隔离库插入/清理合成账号，检查真实Docker入口两端闭环。

## AI 报告运行配置

API与Worker共享runtime.env和任务卷，额外运行变量为`AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`、`VISIT_REPORT_AI`、`VISIT_REPORT_LANGS`、`NEXT_REPORT_AI_TIMEOUT_MS`、`NEXT_REPORT_AI_MAX_TOKENS`。凭证只在运行时注入，不进入前端或镜像。未配置模型时任务明确失败、数字仍可查看；`VISIT_REPORT_AI=0`拒绝新生成并禁止Provider调用。

AI外部调用不在数据库事务中；结果保存带分析版本校验。任务至少一次恢复，供应商是否支持幂等键需要实际供应商契约验证，不能将mock结果当成“不重复计费”的证明。

发布清单声明支持的任务类型。切换前及停旧Worker之后，均检查queued/running任务；回滚到不支持report.analysis的旧版本时若仍有该任务，命令拒绝切换。旧Worker不会被默认为支持未知任务。该本地闸门不等于生产停写/事务排空已验收。

新增本地验证：`node --env-file=.env.next.local scripts/verify-task-compatibility.mjs`检查未知任务阻止切换、入口不变及Worker恢复。只在本地测试环境运行，会短暂停止Worker，探针无业务副作用；不可当成生产验证命令使用。

P4本地资金子流程验证：`node --env-file=.env.next.local scripts/verify-p4-payments-browser.mjs`。仅允许独立验收库，创建/清理合成应发、登记和债务记录；不向银行发起转账，不用于生产。

结算Worker验收：`node --env-file=.env.next.local scripts/verify-p4-settlement-browser.mjs`。仅独立库，创建解析文件/原始行的合成fixture验证判定与重算并清理；尚未替代真实上传解析验收。结算任务执行前校验管理员权限，源文件/申诉门槛在HTTP预检查和Worker事务内均执行。

## 私有巡店原件

新上传使用运行时NEXT_UPLOAD_DIRECTORY=/data/jobs/uploads，API/Worker共享私有jobs卷；与公开前端OSS/CDN资源严格分离。单文件最多50MB，入口允许multipart开销；应用在鉴权/CSRF通过后才接收文件。上传HTTP不等待解析，后台import.visit完成标准表头或显式布局解析、建档、判定和月度结果同步。

`node --env-file=.env.next.local scripts/verify-p4-upload-browser.mjs`验证真实文件上传与布局纠正，只能在独立验收库运行，且发现真实AI Key时拒绝，避免自动分析产生付费调用。脚本结束按fixture范围清理原件和任务，不触及其他文件。

Worker配额现为512MiB，为流式Excel解析留出空间；尚未证明可在生产3.5GiB主机以全量文件/50人混合负载稳定运行，P6必须按真实规模复测。原数据库stored_path指向旧目录的历史文件需要另行兼容挂载和核对，当前不会自动搬迁或修改路径。

月度对账/发薪导出验收：`node --env-file=.env.next.local scripts/verify-p4-reconciliation-browser.mjs`。使用独立验收库真实上传合成巡店和对账文件，验证版本失败保护、差异、下载和导出即登记，结束按fixture ID清理。脚本要求Docker中没有真实AI Key，不能对生产运行。私有发薪产物存于任务卷payroll-exports，下载每次管理员鉴权，不放CDN。

当前对账失败/pending版本不进入工资来源，只有成功版本才能替代旧有效任务；这是避免失败上传影响应发的保护。对账AI/逐店归因和完整生产数据规模仍未验收。

配置/封账/留痕本地验收：`node --env-file=.env.next.local scripts/verify-p4-controls-browser.mjs`。要求独立库且sys_configs为空，防止覆盖用户已有设置；脚本真实上传、登记、保存参数、显式重算和封账/解封，结束按fixture范围清理。原实发金额与核销进度在重算/确认中保持不变。配置保存本身不自动重算历史。

文件删除本地验证：`node --env-file=.env.next.local scripts/verify-p4-deletion-browser.mjs`。真实上传两份合成文件，预览/取消/确认，验证剩余同店转有效、实发/人员/主档保留、封账拒绝；仅独立验收库，按fixture范围清理。删除不撤销实际支付，私有原件有其他导入/对账引用时保留。

主档本地验收：`node --env-file=.env.next.local scripts/verify-p4-stores-browser.mjs`。仅独立库，真实上传近似店铺，验证合并/拆分、封账跳过与台账保留，按fixture ID清理。`STORE_AUTO_MERGE_EXACT=0`可显式关闭上传自动exact合并，但不关闭候选生成。后台任务需要store.master能力，回滚旧版会被待处理任务类型闸门阻止。

绩效日明细验收：`node --env-file=.env.next.local scripts/verify-p4-performance-browser.mjs`（独立库、真实上传合成Excel）。管理员最新日/日期/员工筛选及员工整月/切月日统计，均只读且保留旧两个不同来源。

自动布局：`VISIT_LAYOUT_AI=0`关闭；否则只有标准表头识别失败、没有部分输出且有AI_API_KEY才使用模型。VISIT_REPORT_AI控制报告，不控制布局。无效坐标/模型失败明确报错，人工layout可修复。当前成功模型路径mock验收，真实供应商识别效果未做付费调用，不能视为已验证。显式手工布局不会调用模型。

对账解读：AI_RECON_INTERPRET=0关闭；AI_AUTO_INTERPRET=0仅关闭对账成功后的自动入队，不禁止管理员显式生成。使用同一AI_API_KEY/BASE_URL/MODEL，独立Worker运行，网络等待不持数据库事务。旧ai_interpret字段兼容，失效正文明确标识；AI无权更改数字或工资。

`verify-p4-reconciliation-browser.mjs`现同时验证逐店归因、无Key时后台解读失败/重试与金融数据不受影响；成功模型路径由mock集成测试覆盖，没有实际付费供应商验收。下载结果中归因是记录数，月度差异是点数，不能混用。


主档AI批处理：`STORE_AI_BATCH=0`关闭（默认开启），沿用私有运行时`AI_API_KEY/AI_BASE_URL/AI_MODEL`。没有Key时显式启动返回`AI_NOT_CONFIGURED`，不建立批次；有Key时管理员须确认再启动。每批至多25对，模型只处理近似候选；高置信度同店保留最早主档，高置信度不同店跳过，其余及跨城同店留人工。模型结论与应用结果分别保存于原有`ai_runs.summary`，应用有稳定请求标记，重启可以复用已保存结论。供应商请求完成但尚未保存结论时中断，仍可能再次请求；不承诺供应商计费的恰好一次。

Worker需支持`store.ai`；发布清单与待处理任务兼容闸门已包含该能力。主档浏览器脚本还检查确认按钮、无Key不产生批次和日文入口。成功模型采纳、断点恢复与候选变化由真实PG加mock模型测试覆盖，未做实际付费供应商验证。历史AI结果仍可读，旧版完成记录没有新版检查点时按原总数展示完成进度。


对账陌生表头：`RECON_LAYOUT_AI=0`关闭模型布局回退；默认仅标准表头无法识别且存在私有AI配置时调用，人员汇总/月度清单的指定类型、损坏文件及已识别表头的数据错误不触发。模型只返回前3个sheet、前14行、最多128列范围内的列映射，点数和金额仍按确定性规则计算。已校验映射保存至原recon_tasks.params，失败重试可复用。管理端失败任务可手动填写sheet、表头行和列位置（界面从1开始），显式映射不调用模型；服务端严格验证且归因共用映射。真实供应商效果未做付费验收。


姓名登录名候选：`LOGIN_NAME_AI=0`关闭模型（默认开启，仍需私有AI配置）。复用AI_API_KEY/BASE_URL/MODEL，最多25个姓名一批，候选按供应商配置/模型/凭证范围缓存在进程内，最多2000项。只接受请求中的姓名和最多80字符的登录名候选，失败/缺Key回退规则拼音或编号。模型调用在数据库事务外；最终建号按person_code判重、在事务内对用户名唯一化。管理员已填写登录名时不请求模型；导入只对缺少账号的人员请求，不覆盖已有队长/员工账号。成功路径由mock模型+真实PG测试覆盖；`node --env-file=.env.next.local scripts/verify-p4-login-name-browser.mjs`验证本地Docker缺Key回退和显式账号建号，拒绝有真实Key的环境。


文件判定报告：管理员GET `/api/imports/:id/report`，支持bucket/q/page，50行分页。按原clean_status计数并合并blank/visible_blank；有申诉筛选包含全部申诉状态，概览待处理数只计pending。报告在一个REPEATABLE READ READ ONLY事务中读取全文件汇总及分页明细，返回源sheet/行号、正式表标记与申诉，不返回original_row或其他原始字段。界面在结算文件中展开，原件下载仍使用原有鉴权接口。读取不入队、不重算、不登记工资。浏览器入口沿用`verify-p4-upload-browser.mjs`，仅隔离库合成文件。


完整历史读取核对：`pnpm test:history`（本地Docker PostgreSQL，读取.env.next.local中显式本地连接，但使用独立随机history库，绝不覆盖当前_refactor_p1）。命令完整dump/restore本地基线，连接强制只读，对照历史两期资金/结转及SQL汇总，输出data/refactor/p4-history.json，最后删除本次临时库与私有dump。旧Python脚本仅做开发期oracle。需要本地venv与docker客户端；不连接生产，不跑迁移，也不证明历史重判/重建结果已验收。本轮应用版本仍p4-file-report-b。

历史原件兼容：部署配置可设置`NEXT_LEGACY_UPLOAD_HOST_DIRECTORY=/absolute/existing/uploads`。部署命令要求目录已存在，向API和Worker挂载为`/data/legacy-uploads`且`read_only:true`，Nginx不挂载。数据库旧值`/data/uploads/<relative>`只在读取时映射，不修改数据库；新上传/原件删除仍限NEXT_UPLOAD_DIRECTORY，旧目录不会被新系统写入或清理。相对路径、越界、缺失和符号链接越界明确报FILE_MISSING。

`node --env-file=.env.next.local scripts/verify-legacy-files.mjs`只读审计本地基线中的文件引用，输出计数不输出文件名；`node scripts/verify-legacy-file-mount.mjs <built-release>`使用临时合成文件验证镜像只读挂载，结束删除临时目录。当前22条/data/uploads引用尚无本地映射原件，另1条相对引用不可用；仅同步数据库不能恢复文件。需从旧系统私有上传卷复制原件到独立本地目录后配置映射，再重跑可用性/解析验证。配置和原件均不进入仓库或CDN。

完整月度重建对照：`pnpm test:rebuild`。命令从本地基线创建两个一次性完整库，旧Python仅作开发期oracle；显式加载相同SysConfig，先验证封账拒绝，再仅在副本解封按月重放，输出data/refactor/p4-rebuild.json，最终清理副本/dump。输出区分未解释差异与经断言验证的旧跨月重判副作用、过时结转快照修正；不把所有字段严格相等作为虚假结论。原库只读，不连接生产，真实原件缺失项不由该命令补齐。执行可能耗时数分钟，不能使用在生产主机。

P5团队管理首批本地验证：`node --env-file=.env.next.local scripts/verify-p5-teams-browser.mjs`。仅隔离P1库，33个合成人员用于跨页选择、队长自动入队、同日加回复用历史、停用/启用同步、深路径刷新和日文界面；按fixture ID清理。入口/admin/teams，数据库不迁移。

发布网络回收：本地deploy在镜像/资源校验后，仅回收具有本地发布manifest、名称/Compose项目标签一致、local bridge/private且无关联端点的旧发布网络，排除当前/前一/目标版本。不是全局prune，不删除容器或数据卷。Docker若发现网络出现端点则拒绝删除，发布继续按既有失败保护处理。本轮发布已实际验证回收，解决多次本地演练后默认地址池耗尽。

P5车站任务执行首批：管理入口/admin/station-tasks；员工/队长/my/tasks，原/tasks/:id可打开。`node --env-file=.env.next.local scripts/verify-p5-station-browser.mjs`在隔离库生成合成资产/团队/账号，验证1440px管理端与390px队长/员工端派队、担当、上报/调整/确认/驳回和0店完成；结束按ID清理。单任务操作同步短事务，不新增Worker任务种类。审核通知使用原消息表，原上报者与当前担当并集收件；消息失败回滚整笔任务变更。历史审核时间未变化时保留DB列，不通过JS Date往返。

P5单任务转队/退池验证：`node --env-file=.env.next.local scripts/verify-p5-movement-browser.mjs`（仅隔离P1库）。队长转队无需接收确认、保留进度，转出端回列表并失权；管理员退池须无担当，清队伍/分配日但保留进度和历史。dispatch空teamId不再充当退池；显式return-pool不接受force参数。按任务ID清理合成消息与日志。实际旧多选批量跳过结果仍待后续批量接口迁移。

P5批量复核：`node --env-file=.env.next.local scripts/verify-p5-reviews-browser.mjs`，仅隔离P1库。管理端车站任务展开“批量撤销队长复核”；队长/my/tasks展开待确认队列（tab=pending自动展开）。按进度记录分页/跨页选择；撤销默认回原值，无原值跳过，可填专用值或统一值。只有最新日改变任务当前进度。批量撤销返回逐条跳过和通知失败，已保存撤销不因通知失败回滚；确认全部含跨天、不受当前日期筛选/分页限制，确认通知失败整体回滚。测试后按ID清理合成数据。

P5组合填报：`node --env-file=.env.next.local scripts/verify-p5-self-report-browser.mjs`，仅隔离P1库，测试日期必须在现有日报对账锁边界之后。员工/my/self-report（兼容原路径）或/my/report一次保存日报与未完成车站进度，管理员已有日报编辑同事务；补录只能点数。任务/日报过期或任一校验/计划物化刷新失败整笔回滚，任务不变且备注空跳过。UI显示完成店数、锁定和更新条数；无额外模型调用。按fixture清理。原P3回归需显式NEXT_BROWSER_BASE_URL=http://localhost:8080，避免默认指向开发端口。

P5多选转队/退池：`node --env-file=.env.next.local scripts/verify-p5-batch-movement-browser.mjs`（仅隔离P1库，33个合成任务）。车站任务页展开多选面板，分页/搜索后跨页选择。转队正常不合资格项逐条跳过，外队可转项整批拒绝；成功后失权详情清除。批量转队通知按接收队长/原担当去重汇总，失败整批回滚。管理员退池保留担当中的任务并返回原因，无force入口。实际复用单任务事务，原单任务浏览器也已在该版复查。

P5车站任务导出：管理员在车站任务页展开“导出车站任务工作簿”，独立选择线路/团队/关键词/派发日期/旧状态口径/停滞。`node --env-file=.env.next.local scripts/verify-p5-station-export-browser.mjs`仅隔离P1库，验证超过任务列表一页的三表下载、0店与完成用时、旧筛选语义、无写入、日期校验及窄屏日文。服务在只读快照中分块读取、私有临时目录流式构建，事务结束后发送并清理临时文件，不上传OSS/CDN，也不登记发薪。未分配为活跃物理车站池，只按线路/关键词；进行中/完成按各自旧筛选口径，界面有说明。旧Python仅用于开发期单元格对照，运行链路全部TypeScript。

P5批量统一进度：`node --env-file=.env.next.local scripts/verify-p5-uniform-browser.mjs`，仅隔离P1库，三端跨页33任务验收。统一进度逐条沿用普通上报规则及savepoint，单条失败返回原因且不影响其他成功项；管理员不能抢先处理pending，员工不能绕过confirmed或完成店数，队长可不填店数补录完成。GET station-tasks/movement?progress=1用于范围受控的多选列表，普通movement模式仍拒绝员工。原多选转队/退池浏览器也已在当前版复查。

P5车站/线路资产首批：管理入口/admin/stations。`node --env-file=.env.next.local scripts/verify-p5-assets-browser.mjs`仅隔离P1库，合成线路用于手工补站、同线重名拒绝、跨线物理站复用、站数同步、编辑保留任务、冲突重新读取、中文线路搜索及日文窄屏验收。线路主档只读，仍待原导入链迁移；手工补站不自动生成任务，去车站任务页幂等生成。编辑仅站×线名称/显示line文本/备注/状态，保留line_id/place_id，不自动更名物理站或合并任务。没有数据库迁移，按fixture ID清理。

P5物理站体检/重建：/admin/stations展开“资产体检与物理站重建”，先只读体检/预览，再输入REBUILD_STATION_PLACES确认。安全孤儿可删，被任务引用的孤儿保留并继续报问题；会改变已有任务物理站关联的预览阻断，不能强制绕过。版本变化需重预览。`node --env-file=.env.next.local scripts/verify-p5-place-rebuild-browser.mjs`仅隔离P1库验证全流程，按fixture清理。

TypeScript只读体检CLI：`node --env-file=.env.next.local --import tsx scripts/check-station-assets.mts`；可附铁路JSON路径做来源数量核对；`--baseline scripts/bd_kanto_rail.json`明确只读本地store_settle。脚本强制只读连接、无自动建表/迁移/修复，问题或来源不符非0退出，输出data/refactor/station-asset-check.json。未传参考文件时来源核对是未执行，不能将核心healthy当作来源已验收。市区町村仅完整度提示。不要把重建用在没有备份/验收的生产库。

P5铁路导入：/admin/stations展开“导入内置铁路资料”，内置固定N02-22离线资料（131线路/1920来源站行），预览后输入IMPORT_RAIL_ASSETS；可取消“按物理站补建缺少的任务”。先物理站归组再补缺，新旧task关联均算占用，已有任务不改。危险任务引用/同物理站多旧任务会阻断，不自动迁移。`node --env-file=.env.next.local scripts/verify-p5-rail-browser.mjs`在空铁路资产表的独立P1库验证完整资料两阶段导入与重复稳定，若已有用户资产拒绝，按创建ID清理。

新TS CLI默认预览：`node --env-file=.env.next.local --import tsx scripts/import-rail-assets.mts`；`--json /absolute/reference.json`用指定离线资料、`--no-tasks`仅资产、`--baseline`强制本地原库只读。写入须明确本地_refactor_p1库、`--apply --token <preview-token>`，可用`--actor <admin-id>`明确管理员。预览/结果保存在data/refactor/rail-import-cli.json，不输出数据库凭证；不支持通过该开发CLI写生产。CLI和新服务不调用Python，旧Python只作开发期对照。当前真实本地基线预览已有全部来源资产，若补任务将新增459个，尚未对原库执行。

P5沿线顺序：/admin/stations展开“沿线顺序预览与更新”，先预览每线覆盖/来源、待改及待清空数，再输入UPDATE_STATION_SEQUENCE确认。使用固定离线OSM顺序、已验收几何结果和人工名单，一线一个源；fallback按线路运营商/名称及该线站名校验，不使用旧站ID直接写库。只改seq/along_km/seq_src，不改任务；未匹配旧字段按明确预览清空。署名与公里单位在页面显示。

`node --env-file=.env.next.local --import tsx scripts/fill-station-sequence.mts`默认只读预览；`--baseline`仅本地原库只读；`--apply --token <preview-token>`仅允许本地_refactor_p1写验收。`node --env-file=.env.next.local --import tsx scripts/verify-station-sequence.mts`完整本地计划对照，旧脚本只在内存适配器运行，不写DB。`node --env-file=.env.next.local scripts/verify-p5-sequence-browser.mjs`仅隔离空站资产表合成验收，按fixture清理。几何结果重新生成、在线OSM/N02资料获取仍待迁移，本批无网络请求。

P5几何顺序生成（独立离线CLI，不更新数据库）：

```bash
node --env-file=.env.next.local --import tsx scripts/rail-geometry.mts --geojson /absolute/N02-22_RailroadSection.geojson
node --env-file=.env.next.local --import tsx scripts/rail-geometry.mts --geojson /absolute/N02-22_RailroadSection.geojson --write --output /absolute/new-geometry-result.json
node --env-file=.env.next.local --import tsx scripts/fill-station-sequence.mts --geometry /absolute/new-geometry-result.json
```

默认只计算OSM尚未覆盖的线路，`--all`计算全部。`--write`只发布新的本地文件，数据库始终只读；输出已存在就拒绝，不覆盖内置或其他结果。源JSON限256MiB且必须LineString，沿用90%/50%区间覆盖与250m投影门槛；不放宽质量。`--baseline`只读本地原库。生成文件经`fill-station-sequence --geometry`质量/身份校验后预览，数据库应用仍走顺序CLI的独立token/确认流程。当前本地未提供真实N02区间源文件，全量真实轨道重算和性能未验收；固定输入新旧对照及隔离库CLI衔接测试已通过。旧Python只作开发期oracle。

P5铁路来源获取/预处理（不连接数据库）：

```bash
# 显式联网获取固定版本，默认只试算；缓存位置在忽略目录
node --import tsx scripts/fetch-rail-source.mts --download
# 使用已下载的本地ZIP和县界，可完全离线；输出必须是新路径
node --import tsx scripts/fetch-rail-source.mts --archive /absolute/N02-22_GML.zip --prefectures /absolute/japan.geojson --output /absolute/new-kanto-rail.json --geometry-output /absolute/new-railroad-sections.geojson
```

`--cache /absolute/cache-dir`改变缓存位置；`--refresh`须同时指定`--download`，失败保留旧有效指针。缓存命中须校验URL/长度/SHA256和源格式，ZIP只读取固定站点/轨道条目，不任意解压。边界/站/轨道均有字节上限。每个输出文件通过临时文件原子发布，拒绝覆盖已有文件。获取/生成和导入数据库是分开的步骤，新JSON交`import-rail-assets --json`先预览，轨道交`rail-geometry --geojson`，其几何结果再交顺序CLI。当前版本只获取N02-22，不自动升级资料版本。

本次已实取N02压缩包和县界，10220条全国源记录→1949条一都三县记录→去重29→131线/1920站，生成payload与旧资料完全一致。提取21916段真实轨道，20条合格线/191站投影结果与旧几何文件完全一致，3条按质量门槛拒绝。原库只读，无自动应用；缓存/完整来源验收证据在data/refactor，私有原始巡店Excel恢复和OSM路线获取仍未由此完成。

P5 OSM路线获取/续传（无数据库连接，默认不联网）：

```bash
# 检查旧离线种子并落入独立逐关系缓存；会如实报告目标中缺少的关系
node --import tsx scripts/fetch-osm-routes.mts --seed scripts/bd_osm_routes.json
# 明确少量联网，只取指定关系；失败会非0退出，保留已完成检查点
node --import tsx scripts/fetch-osm-routes.mts --only 443259 --download --workers 1 --output /absolute/new-route-snapshot.json
# 合法完整快照交顺序CLI预览，不自动写数据库
node --env-file=.env.next.local --import tsx scripts/fill-station-sequence.mts --osm /absolute/complete-route-snapshot.json
```

`--ids <file>`、`--only <csv>`、`--discover-ids`互斥，发现ID也必须显式`--download`；`--limit <n>`限制取内容范围，`--ids-output <new-file>`只用于显式发现。`--refresh`需配`--download`，刷新失败保留旧检查点并报告keptPrevious；可按失败ID列表定向重试。每次最多4并发、单请求30秒/16MiB，429/509后不再派新请求；不承诺多个并发CLI进程的合计请求上限。

每个关系成功/空/已删除状态独立原子缓存，失败不覆盖旧成功路线；SIGINT/SIGTERM中断保留已完成记录。输出为新文件且带fetch_status，文件存在不等于资料完整；complete=false的刷新结果会被顺序CLI拒绝。默认离线检查可正常返回缺失列表，显式下载未完整时非0退出。ODbL/© OpenStreetMap contributors保留。当前实际单关系请求失败，全量API/Overpass实网来源未验收，旧495条快照也不等于527个目标完整覆盖。

P5 巡店照片：`p5-batch-photos-a` 的新照片存放在 `$NEXT_UPLOAD_DIRECTORY/batch-photos`（本地 Docker 为 `/data/jobs/uploads/batch-photos`）。API 按任务权限私有读取，Nginx 不直接公开目录，前端 CDN 不托管该目录。生产切换时需把该目录和数据库一同备份与恢复；移除图片仅解除引用，旧批次快照可能仍在使用文件，禁止按当前商户列表直接清理。旧照片 HTTPS 链接保持原样，旧对象搬迁和新照片长期存储方案尚未验收。

## 浏览器回归隔离（2026-11-11）

`pnpm test:browser` 复用数据库隔离运行器，创建 schema-only 随机测试库，在随机本地端口启动独立 NestJS API 与静态前端入口，并以私有临时任务目录启动 Worker。夹具、API、Worker 使用同一测试库。运行器只给子进程传递必要环境，不继承 AI/地图凭证或应用任务目录；应用 `.env.next.local` 与当前 Docker 候选不变。需先有当前源码的前端构建产物（`pnpm build`），浏览器使用现有 Chrome 或 `NEXT_BROWSER_EXECUTABLE`。

默认顺序覆盖 P1 登录/语言/账号、P3 日期计划/请假、消息/已读和导出/后台报告/重试/账号失效，再执行下文作业域六组回归。结束清理自己启动的 API/Worker、临时目录和临时库，并在 `data/refactor/browser-isolation-last.json` 记录来源只读、源副本计数与口令摘要一致、测试退出码及清理结果。各业务脚本的既有结果文件保留对应流程证据。

本命令不使用调用者提供的 `NEXT_BROWSER_BASE_URL`，也不代表 Docker/Nginx/CDN 验收。本文下方历史专项脚本仍有依赖当前 Docker release 的情况，未纳入本文已列出的十组隔离回归；不要将完整副本连接直接传入这些写入夹具脚本。后续专项验收须将服务与夹具一并隔离。

## 作业域浏览器隔离（2026-11-11）

`pnpm test:browser:ops` 在独立临时 API/Worker/数据库/照片目录中顺序运行六组回归：批次创建与快照、三角色派发与执行、事件统计、禁用、私有照片、新店申请/核实。`pnpm test:browser` 默认合并核心四组与作业六组；现有 Docker 专项入口仍保留，隔离模式的结果标记为 `isolated-source`，不会冒充当前已部署 release。

已通过六组作业浏览器验收：冻结门店与空网格、片区/网格派发、员工及队长本人执行、请求幂等与修订冲突恢复、按门店加权汇总、完成后只读、完成事件数量、批次隔离、禁用取消/确认与旧表单拒绝、日志筛选、照片私有读取/保存/刷新/移除且不重复计数、新店驳回后重提与认可后同步分母。六组均检查日语、横向溢出和页面异常。

照片专项在隔离模式不再进入当前 Docker API 容器删除文件，由运行器统一清理本次私有任务目录；真实历史照片与生产存储恢复仍待单独验收。新栈服务实现未因本次测试接线调整，数据库配置和当前候选容器未改。

## 员工地图全屏与定位退出（2026-11-11）

补齐批次地图视口全屏，保留同一地图实例和已选择门店，支持 Esc 退出、键盘焦点约束/恢复、背景滚动恢复；地图内「打开此店巡店填报」会退出全屏。全屏使用单指地图手势，普通页面保持协作手势。按钮中日双语，最小高度44px，适配安全区。

拖动地图、查看全部及停止跟随会清理定位 watcher；定位请求使用代次校验，停止后迟到的回调不能重新移动视角。权限失败清理 watcher 后允许重试，关闭地图也清理定位订阅；设备 GPS 仍只作显示，不写门店坐标。

`pnpm test:browser:maps` 已加入隔离运行器，默认 `pnpm test:browser` 扩展为核心四组、作业六组、地图一组共11组。地图专项复用既有权限/定位保存/完成快照/共享定位/双语断言，新增390×844全屏边界、Esc与焦点恢复、全屏进入填报退出、拖动/查看全部停止跟随、迟到回调忽略、权限失败重试和关闭清理等断言；全部通过，无页面异常或横向溢出。SDK使用既有离线模拟，不请求真实供应商。应用数据库配置、当前Docker候选及生产均未改；真实移动设备/Google SDK验收仍待实际配置。

## 道路街块离线快照 TypeScript 迁移（2026-11-11）

新增 `packages/domain/src/zone-snapshot.ts` 与 `pnpm zones:snapshot`，迁移旧 `bd_zones.prepare_snapshot` 的道路切块、海岸方向判陆、排除面、共边空间索引、按面积合并、小尾片吸收、商户归属及草案问题标记。使用现有 GEOS WASM/日本平面直角 IX 投影；面积目标仍按每个连通分量计算，不让店铺密度改变基础网格形状，不连接角点或真实离散地块。商户落在多个街块时仍计入排序最小的街块。

CLI输入为预处理后的 JSON 对象：`code`、`boundary`（GeoJSON面）、`roads`、`excluded`、`merchants`（经度/纬度对）、`coastlines`（可选）、`capturedAt`（或`captured_at`）及`source`。不直接把 Overpass 原始响应当作此输入，原始采集/解析及后续商户均衡分组/自然网格生成仍需迁移。默认只试算；必须同时传 `--write --output` 才输出全新文件，不覆盖输入、参考快照或已有输出，不下载、不连数据库、不调用Python。

```bash
pnpm zones:snapshot --input /absolute/path/prepared-input.json
pnpm zones:snapshot --input /absolute/path/prepared-input.json --reference /absolute/path/13104.json --write --output /absolute/path/13104-new.json
```

跨 GEOS/投影运行时，正负零的 WKB 表示及 JSON 浮点序列化可能改变内容ID。续用已有资产时传 `--reference`：在毫米精度规范化边界相同的网格上沿用旧ID，拒绝不同行政区、重复ID或重复边界；新边界才采用规范化WKB生成ID。保留`schema_version=1`，新文件标记`generation_version=4`并记录`id_reference_snapshot`；新`snapshot_id`按新内容序列化生成，不承诺等于旧文件的内容哈希。现有快照/网格ID/数据库均未改写。

开发期 `scripts/zone-snapshot-oracle.py` 只使用固定合成几何，Python不进入新CLI运行时。`scripts/verify-zone-snapshot.mts`已通过14组新旧对照，覆盖密集街块、Python式面积取整、分离地块、水域、孔洞、双向海岸、失败停止等：边界/作业面/网格在毫米精度的规范WKB一致，面积和商户计数一致，参考旧ID全保留，两类失败提示一致。结果在`data/refactor/zone-snapshot-parity.json`。相邻几何/河道/新快照及CLI共6项测试通过；domain/server/worker类型检查和构建通过。没有运行全量生产规模性能测试，也未部署或操作当前候选容器。

## 单区片区预览与完整网格找平迁移（2026-11-11）

新增 `packages/domain/src/zone-preview.ts` / `pnpm zones:preview`，按旧实际调用链迁移单区预览：共边邻接、面积/店数/紧凑度合并评分、版本化最小堆、完整网格找平、移动前源/目标连续面验证、离散陆块面积比上限与不可满足告警。基础快照的合并内核复用此实现，零店数模式仍保持上一阶段按面积生成规则。

区分建议数量与硬约束：店铺目标计算建议片区数，不切网格凑配额；因离散陆块无法满足面积比时保留草案，异常码/信息级数量提示/无店铺/待处理网格计数沿用旧实现。片区ID继续按排序后的成员网格ID生成，现有网格ID和成员关系不丢失。输出保留边界、作业面、source、summary、issues和`preview_only=true`。单区旧逻辑不套用整体范围的形状准入，不能将此实现冒充东京23区整体算法。

```bash
pnpm zones:preview --input /absolute/path/13104-new.json --stores 400 --max-area-km2 25
pnpm zones:preview --input /absolute/path/13104-new.json --stores 400 --max-area-km2 25 --write --output /absolute/path/13104-preview.json
```

默认只试算，显式写入只产生全新预览文件；不覆盖输入/已有输出、不连数据库、不采集网络、不调用Python、不生成正式资产或派发任务。`tokyo23`不完整快照按旧规则拒绝；完整整体快照也明确要求独立的覆盖/均衡算法，后续继续迁移该调用链，不拼接单区结果代替。

验证：`scripts/verify-zone-preview.mts`对照旧Python固定合成数据15组（12组成功、3组预期拒绝），完整字段、分组成员和片区ID一致，几何在毫米精度规范WKB一致，面积/统计一致。覆盖均匀/偏斜店数、面积封顶、离散地块、无解面积比、零店铺、待处理网格、超过网格数的建议目标、微小投影缝隙和孔洞。新增单测验证快照输入不变、完整网格守恒、生成版本4快照接入、整体范围拒绝和CLI不可覆盖。

最终9项相关测试通过；上一阶段快照14组旧实现对照继续通过，本阶段15组对照通过；domain/server/worker类型检查和构建通过。证据 `data/refactor/zone-preview-parity.json` 与 `zone-snapshot-parity.json`。无临时测试目录残留，数据库配置、当前Docker候选和生产均未操作。真实规模和整体范围性能仍待验收。

## 东京23区非线性负载分组内核迁移（2026-11-11）

新增 `packages/domain/src/zone-balance.ts`，完整迁移整体预览调用的负载分组内核：`BalanceOptions`、加权种子、真实共边图竞争生长、40轮速度调整、区间内停止、完整道路面边界调整、相邻片区重拼、仅共边残余合并，以及配额>128时的图距离递归分支。几何只在原道路面并集上变化；中心点凸包/二阶矩仅作候选粗排，不作为实际边界或填水域。

负载沿用 `w*(area/target_area)^2 + (1-w)*(merchants/target_stores)^2`。内核默认25km²、8000店、面积权重15%、区间0.75～1.35；旧入口显式25%权重的历史校准例子也已测试。面积为推荐尺度，不硬拆已在合理负载范围内的大面积低密度片区；移动/重拼不能增加超载或制造更多不合格形状。无法满足的地块仍保留归属，不能删除它们让分数好看。新设置对象不可变，拒绝非有限值、布尔值、null及非法区间。

`balanceScopeFaces`提供毫米精度的离线计算与完整成员输出，`partitionBalancedFaces`可供后续覆盖编排在同一GEOS arena内调用。分组输出不包含可发布承诺：完整scope预览仍须接任务网格、覆盖/重叠/越界、商户唯一绑定、排除复核和生成规则；目前没有把内核分数当作`confirmation_ready`或接入正式资产写入。整体源存在旧拓扑/邻接回退的分支也由后续覆盖编排按原契约处理。

验证命令：`node --import tsx scripts/verify-zone-balance.mts`（开发期旧Python仅作固定输入oracle）。12组新旧对照全部通过，分组成员顺序、规范几何、面积、商户和负载一致；包含300个道路面/高配额分支（输出134组，与旧实现一致）、偏斜密度、零店、孔洞、离散地块、权重边界、商业狭颈和空地狭颈。常规测试另覆盖64面守恒/连续性、输入不变、参数校验、真实共边/角点/间隙和超载残余保留。

最终5项相关测试通过，domain/server/worker类型检查和构建通过；证据 `data/refactor/zone-balance-parity.json`。没有数据库/网络调用，未改配置、未部署或重启当前候选/生产。整体覆盖与发布资格编排、生成规则及真实规模性能仍未完成，继续下一阶段。

## 东京23区整体预览：任务网格与审计迁移（2026-11-11）

新增 `zone-scope-partition.ts` / `zone-scope.ts`，将整体负载分组接入任务网格与预览审计，默认`generationRules=false`。整体范围直接使用完整道路基础面：显式EPSG:6677毫米面或经纬度投影；源拓扑缺失或长度不符时沿用旧共边邻接回退。非线性模式默认面积权重25%（与旧preview入口一致），也支持旧传统面积模式。二阶矩、中心点凸包仅作候选评分，真实输出始终是原道路面的毫米精度并集。形状修正只在真实边界改善时继续，不能细切到丢失责任范围。

审计沿用旧1m²阈值：漏覆盖、重叠、越界、道路面/任务面恰好一次归属、重复cell/region/grid ID、商户唯一绑定/预期数量/计数不符、未定位/源重复/无效坐标、排除商户复核。负载、残余、形状和任务网格面积冲突均保留为issues；不能通过转灰/删除面让审核通过，`pending_grids`保持空但冲突责任归属保留。来源审计未准备好时明确拒绝确认。预览输出保留旧features/grids/meta/audit/summary/issues/review_merchants/uncovered_features结构和`preview_only=true`。

`pnpm zones:preview`按`ward_code`选择单区或整体实现；整体输入为旧`prepare_source`格式，`road_cells`/`grids`、`merchant_audit`和绑定数据不能用单区输出拼接伪造。

```bash
pnpm zones:preview --input /absolute/path/tokyo23-source.json --stores 8000 --max-area-km2 25
pnpm zones:preview --input /absolute/path/tokyo23-source.json --stores 8000 --max-area-km2 25 --area-weight .25 --load-min .75 --load-max 1.35 --include-memberships --write --output /absolute/path/tokyo23-preview.json
# 仅需要对照旧传统面积模式时加 --legacy-area（不可同时传负载参数）
```

CLI仍默认只试算；显式写入新文件且拒绝覆盖，不连接数据库/供应商/Python。负载模式下`--max-area-km2`是旧入口的目标面积参数，输出硬面积上限为null，不擅自把它改成硬上限。`--include-memberships`可输出任务网格cell_ids。

正式生成的`generationRules=true`仍明确拒绝，不能用此预览代替`bd_generate_zones.py`的跨河合并/空地排除/规则及落库链路；这部分继续后续迁移。源采集、自然网格和真实规模验收亦未由本阶段完成。

验证：整体26组固定旧实现完整字段对照通过（24成功结果、2预期异常），覆盖可确认、无来源审计、重复商户/ID、计数不符、排除复核、漏覆盖/越界/重叠、传统模式、拓扑回退/经纬度源、狭长片区、弯曲海岸、不可细分超大面和独立残余。新旧片区/任务ID、成员、规范几何、审计字段和确认结果一致。狭长样本揭示浮点累计顺序影响近似同分候选，二阶矩固定按cell索引累计后对齐；不能据此承诺未验证真实规模全部匹配。

最终14项相邻测试通过，0失败/跳过；快照14组、单区15组、负载12组旧实现对照继续通过；domain/server/worker类型检查与构建通过。证据`data/refactor/zone-scope-parity.json`及其他zone对照文件。临时目录清理，无数据库操作，应用配置、当前Docker候选和生产均未改。

## 生成规则：包含归并、零店排除与窄河准入（2026-11-11）

新增 `zone-rules.ts`，迁移旧 `bd_zone_rules.consolidate` 并接入整体scope预览：数量上限 `max(1,floor(实际作业面积/目标面积*3))`、嵌套包含归并、外部零店地块排除、真实共边优先合并、数量仍超限时才考虑经证据验证的窄河合并。参与包含判断的外壳只用于判断包含关系，不用于面积、覆盖或实际围栏；水域/孔洞保留。

窄河最短连接必须被已知河道面（含旧1cm舍入容差）覆盖，局部两岸宽度受上限约束；距离近、部分水面、无关河道、河流中心线均不能替代河道面。100米阈值保留旧STRtree距离先验：100.0005m也不因后续1mm容差被批准。跨河还需整体紧凑/主要陆块形状准入，不允许负载超限或数量上限造成形状退化。无可行选择时旧完整错误提示停止生成。空间候选复用GEOS STRtree，不逐对扫描全部地块。

规则排除后scope审计以剩余实际作业范围核验覆盖/越界，记录`audit.generation_rules`与`excluded_empty_cell_ids`。被排除面若仍有merchant_ids直接拒绝，不能靠零计数掩盖绑定错误。仅规则合并成功并不自动获得确认资格：例如60米河道的两个连续陆块合并后仍保留真实水隙，旧形状审计如判冲突则`confirmation_ready=false`，本次保留此行为，不放松发布门槛。

离线命令新增显式规则模式（默认仍只预览、不落库）：

```bash
pnpm zones:preview --input /absolute/path/tokyo23-source.json --stores 8000 --max-area-km2 25 --generation-rules --river-directory /absolute/path/zone-evidence --max-river-width-m 100
# 保存候选：追加 --write --output /absolute/path/generated-candidate.json
```

规则模式默认面积权重15%（与旧正式生成脚本一致，普通scope预览仍默认25%），自动保留cell_ids。河道证据复用现有只读 `riverEvidence`：读取本地 `*.roads-v1.source.json`、核验文件、筛选带河道面标签的OSM元素并输出fingerprint/文件数；不自动联网，没有证据不批准跨河。0米关闭跨河，显式负载参数仍可覆盖默认。规则模式拒绝`--legacy-area`，非规则模式拒绝河道参数，未增加数据库apply选项。

验证：17组规则内核旧实现完整结果对照通过（含10组预期拒绝），覆盖共边、包含/多层包含、空店排除、全空停止、负载超限、30/60/100/100.0005/100.1/180m河道、缺失/部分/无关证据和禁用。整体契约扩至32组（27成功结果、5预期拒绝）全部通过，含规则排除后的覆盖与商户审计、隐藏绑定拒绝、60m合并保留冲突与101m停止。旧成员、几何、计数、policy与审计字段一致。18项相邻测试通过，domain/server/worker类型检查及构建通过；证据 `zone-rules-parity.json` / `zone-scope-parity.json`。

当前完成的是离线生成候选及审计，尚未迁移旧脚本的正式release落库、替换未开展版本和团队唯一映射保护；不可用候选文件宣称数据库正式版本已生成。真实规模性能、自然网格与原始采集仍待验收。数据库连接/数据、当前Docker候选和生产未操作，未调用真实供应商。

## 正式片区版本事务与未开展版本替换（2026-11-11）

新增 `apps/server/src/zone-release.ts` 与 `pnpm zones:release`。管理员必须当前在岗；写入与替换均在单一事务中取得旧同号PG advisory lock。指纹沿用旧source/parameters/片区ID/网格ID身份，按Python排序JSON（含浮点/Unicode）生成SHA256，同源同参数返回已有release；不同候选遇已有正式版本拒绝覆盖。仅写 `bd_zone_release` / `bd_region` / `bd_work_grid` / `bd_grid_store` / `bd_log`，不读取源商户内容，不创建巡店批次，不修改结算域或schema。

正式生成校验覆盖/商户审计、source版本、数量上限、唯一cell成员、排除审计及零店无绑定、完整面包含和门店唯一映射。保留旧语义：覆盖和商户归属通过的候选可保存带问题的责任单元，`confirmation_ready=false`不直接阻止保存；形状/任务面积等原因写入片区review和release审计，不隐藏问题。区域/网格批量插入，商户每5000行批量写入，避免逐店SQL。

显式替换 `--replace-unused` 要求 `--apply`：存在任何巡店批次历史、留痕、网格派发、进展或非初始状态即拒绝。锁保护旧release及资产/历史表，按完整商户集合映射团队，不按名称或最近距离猜测；原片区拆成多片可分别承接同一团队，多个团队流入同一新片区或缺少新归属拒绝。失败包括删除旧资产后的校验错误都由调用事务整体回滚，bd_work_store及商户历史内容不删。

```bash
# 默认只检查候选/来源文件、完整成员与指纹，不连接数据库
pnpm zones:release --source /absolute/path/tokyo23-source.json --candidate /absolute/path/generated-candidate.json
```

正式写入须显式追加 `--apply --admin-user <已有在岗管理员>`，替换须再加 `--replace-unused`；当前仅接受显式loopback PostgreSQL且库名以`_refactor_p1`结尾，生产入口未开放。本轮从未对完整基准副本调用apply，测试全部经现有schema-only随机库运行器。此命令不自动备份；替换的备份/验收副本安排仍按部署计划执行。CLI不修改环境文件，候选文件仍由生成/预览命令保存。

验证：旧Python指纹序列化对照（含Unicode来源及浮点）通过；真实临时PG覆盖管理员拒绝、并发重复确认只写一次、review保留、已有版本拒绝、两种替换回滚、一个旧团队拆分承接、不同团队冲突、已派发/进展/批次历史拒绝，结算域四表计数不变。CLI验证默认不连数据库、拒绝隐式替换/缺管理员/远端apply。

全量152项在隔离库执行，151项首次通过，唯一CLI失败是根工作区不能直接解析server的reflect-metadata依赖；按既有脚本改为server工作区路径后仅重跑该失败检查，1项通过且0跳过。未重跑151项已通过检查。随后7包类型检查与构建通过。原始test-isolation-last.json保留首次`testExit=1`的真实记录，同时`baselineUnchanged=true`、`cleanup=dropped`；修复后结果在`zone-release-verification.json`中关联说明，不能把原记录改成假全绿。无临时目录残留，完整副本/当前候选容器/生产未改。

已完成候选到正式版本及未开展替换的服务端/CLI链路；管理端生成预览/确认界面、真实规模性能、自然网格和源采集、Docker/CDN/50会话验收仍需推进。

## 完整道路面来源准备与私有商户CSV迁移（2026-11-11）

新增 `zone-source.ts`、`merchant-source-csv.ts` 与 `pnpm zones:source`：预处理后的边界/道路/排除面/海岸线生成完整道路基础面，不按任务网格0.5～2km²预先删除小面。商户CSV流式读取，按地理边界先过滤/判重，再按实际作业面绑定；多个基础面共边的点仍归最小排序面。水域等排除面内商户保留`excluded_terrain/unreviewed`，未定位、源重复、无效坐标及映射数分别审计，来源不合格会继续阻止后续确认。范围外的重复ID不消耗范围内商户身份。

CLI输入JSON要求 `boundary`、`roads`、`excluded`、可选`coastlines`、`capturedAt`（或`captured_at`）和`source`，为已采集并确认的完整范围几何；不会自行下载或声称从单区拼接校验23区完整性。现有原始采集器只把真实水体标签硬排除，森林等标签不能单凭缺少商户就排除。本阶段处理显式准备好的排除面，原始采集器仍待迁移。商户CSV须有`place_id/lat/lon`或`place_id/lat/lng`，支持UTF-8 BOM、CSV引号和多行中日文；仅输出汇总日志，私有商户行不上传CDN或进仓库。

```bash
pnpm zones:source --input /absolute/path/prepared-geometry.json --merchants /absolute/path/private-merchants.csv
pnpm zones:source --input /absolute/path/prepared-geometry.json --merchants /absolute/path/private-merchants.csv --reference /absolute/path/old-tokyo23-source.json --write --output /absolute/path/new-tokyo23-source.json
```

默认只试算，只有`--write --output`同时存在才创建新来源文件，拒绝覆盖任意输入/已有输出；原子发布文件权限沿用0600。可输出数据继续进入`zones:preview --generation-rules`及`zones:release`文件校验。CSV按流处理，逐点GEOS对象即时释放；arena支持异步来源并等待完成才统一清理，流异常和并行几何调用已覆盖测试，不以此代替真实规模RSS/吞吐测量。CSV解析使用server工作区显式固定`@fast-csv/parse@4.3.6`（现有锁文件依赖版本）。

正负零WKB跨运行时仍可能改变默认道路面ID，新来源标记`generation_version=6`，保留`schema_version=1` / `coverage_revision=2`。提供参考整体来源时，毫米规范几何匹配后沿用旧道路面ID，拒绝范围/投影不符、重复ID/边界，记录`id_reference_snapshot`；已对照样本的商户绑定不变时`snapshot_id`亦保持一致。新边界使用规范WKB新ID，不改写已有库内资产身份。

本阶段最终验证：8组固定旧来源契约对照通过（7成功、1预期停止），其中20,001行重复源跨旧20,000行分批界限，ID/几何/拓扑/商户绑定/审计/复核及参考快照摘要一致；证据`data/refactor/zone-source-parity.json`。156项完整隔离测试全部通过，0失败、0跳过，7包类型检查与构建通过。来源→整体规则预览→正式候选完整成员校验贯通；CSV格式与异常/并行GEOS清理也通过。完整副本`baselineUnchanged=true`、随机库`cleanup=dropped`、临时来源目录无残留。应用配置、完整副本业务数据、当前Docker候选与生产均未改，没有调用真实供应商。

## 原始OSM缓存→完整23区几何准备（2026-11-11）

新增 `osm-zone-source.ts` 与 `pnpm zones:geometry`，迁移旧缓存解析的关系拼面/内洞、水体硬排除、JIS区代码识别及道路/海岸线提取。关系成员先在经纬度空间节点化/polygonize，再扣内洞；海岸线方向保留。只认`admin_level=7`且ref前5位属于13101～13123的23区关系，不按同名区匹配；缺任何区、无效边界、某区道路缺失均停止，不生成矩形或假道路。铁路/林地等原始元素仍在缓存中，此道路切块准备只按旧highway口径提取，不自动把铁路变成道路边界。

水体硬排除沿用现行`natural=water` / `landuse=reservoir` / `waterway=riverbank`；林地/灌木/湿地和河流中心线本身不作为水体排除。跨区相同OSM type+ID的排除元素后值覆盖；分段外环/任意非inner成员及内洞处理与旧helper一致。坐标和关系ID新增边界检查，避免无效值或路径片段被作为缓存文件名。

```bash
pnpm zones:geometry --directory /absolute/path/local-osm-cache
pnpm zones:geometry --directory /absolute/path/local-osm-cache --captured-at 2026-11-11T00:00:00Z --write --output /absolute/path/prepared-geometry.json
# 下一步仍使用私有CSV：zones:source --input prepared-geometry.json --merchants private.csv
```

读取`boundaries.source.json`、`coastlines.source.json`及23个`<code>-<relation>.roads-v2.source.json`；v2仅在不存在时回退v1，损坏/remark不完整不得静默回退。检查单文件64MiB上限，输出包含缓存文件清单和SHA256指纹。默认只检查，不联网、不连数据库、不执行Python；显式写入全新文件，拒绝覆盖任何输入/已有输出。输出`code=tokyo23`可进入`zones:source`，后者也已拒绝把显式单区code当成整体来源。

验证：完整合成23区缓存对照旧关系/水体/区代码helper，分段环与内洞、整区并集、道路、排除面和海岸线一致；同名异市、缺区、缺道路、remark停止通过。6项相邻测试全部通过（0失败/跳过），包括v1/v2选择、坏v2拒绝、原文件不覆盖、无效关系ID拒绝及原始缓存→来源/商户绑定贯通；domain/server/worker类型检查和构建通过，证据`data/refactor/osm-zone-source-parity.json`。临时缓存目录清理完成。

本地目录实查无boundaries/coastlines缓存，131xx道路缓存数量0（不依赖rg忽略规则的文件枚举）。因此本阶段不宣称真实全量OSM资料采集/规模验收完成；没有为补缓存调用外网。自然网格分界及原始资料下载、真实规模性能继续后续；数据库和当前本地/生产运行服务未操作。

## 自然网格几何准备与未绑定候选迁移（2026-11-11）

新增 `natural-grids.ts` / `pnpm zones:natural`，迁移旧natural.prepare/candidate：仅由有标签的主干道、路线编号支路及地面铁路切块，按layer/bridge/tunnel/location识别道路投影、<=150m短下穿和铁路地面限制；住宅路/无编号支路不作为自然边界。补充分界必须有river或administrative/facility标签。按OSM type+ID去重/排序，输入倒序不改变结果。

自然模式保持旧独有口径：扣除真实水面后，以外轮廓覆盖内部湖面/飞地并入街区；开口到岸线的水域缺口保留在范围外。陆地分块只用于加速求交，10km瓦片线从不进入分割网络；道路在陆地内直接保留，复杂求交按旧毫米精度裁剪。polygonize_full保留cuts/dangles/invalid_rings审计，真实地块全部保留，不按面积先删除或画直线凑网格。覆盖仍仅允许既有边界2mm带的量化误差，内部漏地、重叠>0.01m²或非法环不能冒充误差。

```bash
# 由上一阶段缓存整理器显式保留原始铁路/道路标签；可选读取railways.source.json
pnpm zones:geometry --directory /absolute/path/cache --natural-input --write --output /absolute/path/natural-input.json
pnpm zones:natural --input /absolute/path/natural-input.json
pnpm zones:natural --input /absolute/path/natural-input.json --candidate --write --output /absolute/path/unbound-natural-candidate.json
```

`--natural-input`额外铁路缓存同type+ID后值覆盖，并重算水体排除，输入指纹包含额外文件。v1缓存没有独立铁路资料时明确`railway_collected=false`，不冒充真实铁路验收完成。普通geometry输出仍不携带整个raw元素数组。自然输入要求boundary/elements/excluded/coastlines/capturedAt/source，支持tokyo23/tokyo26/kanagawa/saitama/chiba地区标识；不自动认证任意几何的行政来源，仍须使用已核对原始缓存。

CLI只试算或原子创建新文件，不支持apply，不覆盖输入/旧输出，不联网不连数据库。输出`generation_version=natural-ts-1`、`hash_format=canonical-json-js-v1`，显式区分跨运行时内容哈希序列化；可用`--reference`按毫米规范边界保留旧自然网格ID，不能据新哈希声称旧ID已更新。候选`applied=false`、`zone_id=null`，未绑定商户时`merchant_pass=false`，几何合格不等于可导入。

验证：65种标签/长度分类旧实现对照一致；8组固定自然场景（6成功、2预期拒绝）几何、旧ID参考映射、面积、采用/跳过边界、悬挂线和覆盖审计一致。内容哈希/生成版本有明确新格式，不做虚假旧摘要相等断言。相关7项测试通过（0失败/跳过），含文件不覆盖、拒绝apply、输入倒序/未变、缓存额外铁路及新来源准备回归；domain/server/worker类型检查和构建通过。证据 `data/refactor/natural-grid-parity.json`，原始OSM解析对照亦通过。

本阶段只完成自然地块准备及候选输出；旧normalise中的碎片吸收/尺寸归一化、自然网格商户绑定、导入/追加地区与替换保护仍须迁移。旧源码保持，未把人工分组模式冒充自然地块。无真实全量原始缓存，真实规模RSS/耗时和铁路供应商验收未完成；数据库、当前本地候选和生产未操作。

## 自然网格商户绑定与只读PostgreSQL适配（2026-11-11）

新增 `zone-merchant-binding.ts`、`zone-merchant-source.ts` 与 `pnpm zones:bind`，迁移旧 `bd_zone_merchants.bind_rows/bind_database`。几何和排除区保持不变；商户按现有EPSG:6677道路面唯一绑定，多个面命中取最小索引。记录inside_boundary/inside_work/排除复核/未定位/源重复/无效坐标/mapped_to_faces；输入来源、道路面和自然候选不被改写。绑定后可继续进入自然候选审计，`merchant_pass`要求唯一、完整、无未定位/源重复/无效坐标。

数据库绑定明确只读：使用显式合法schema.table（默认`public.kanto_merchants_v20261029`），禁止`raw_records`及SQL片段；数据库必须是本地`store_settle`或`store_settle_refactor_p1`。同一REPEATABLE READ READ ONLY事务读取总数并声明PostgreSQL游标，5000行FETCH，避免一次性加载全表；游标关闭和GEOS资源在正常/异常/异步中断路径释放。不存在CSV回退，无法连接或表读取失败直接停止。

```bash
pnpm zones:bind --input /absolute/path/natural-source.json
pnpm zones:bind --input /absolute/path/natural-source.json --table public.kanto_merchants_v20261029 --write --output /absolute/path/natural-bound-source.json
```

默认只读试算，显式`--write --output`才写新来源文件，不覆盖输入/旧输出；不提供apply/生产入口。候选生成仍未绑定正式自然网格资产，后续正式导入继续使用`zones:release`的完整成员和保护校验。

验证：固定旧绑定oracle 8组（跨20,001行批次、边界多面命中、空/无效、排除地形、找不到面）完整输出/哈希/原几何不变；自然几何→绑定→候选串联通过。临时PG游标测试验证6003行、3次FETCH、只读repeatable-read、源表数量不变、表名注入/raw_records拒绝、缺表失败后可继续读取；CLI拒绝远程库、CSV回退和覆盖输入。完整隔离回归164项通过，0失败/跳过；之后受影响绑定两个测试文件定向重跑通过；7包类型检查/构建通过。证据`data/refactor/zone-merchant-binding-parity.json`。完整副本、生产和当前运行容器未操作。

## 自然网格导入事务、地区追加与替换保护（2026-11-11）

新增 `natural-grid-import.ts` / `pnpm zones:natural:import`，迁移旧import_candidate的服务端事务：必须当前在岗管理员，使用同号PG advisory锁，重新从source_document生成features/audit并核对候选内容指纹，不只信任传入的geometric_pass/merchant_pass。再核验真实几何有效性、外轮廓连续（允许外环内的岛屿多面）、覆盖2mm量化带、面积总和/并集一致、cell ID/商户唯一及完整数量。仅写bd_*，网格`zone_id=NULL`、ready、不创建片区或任务，不自动派发。

首个自然版本遇已有正式release拒绝；这沿用旧行为，并非普通formal生成的幂等接口。追加先拒绝同地区已有网格，再按内容指纹检查历史重复；追加/替换都需要源中已有quality_pass，未迁移的normalise不能由导入器自动补成true。追加拒绝既有跨地区商户及实际空间重叠，仅容忍旧边界量化带并记录重叠面积。

替换在事务/表锁保护下拒绝已派发、非初始进度/状态、巡店留痕及片区/网格批次任务引用；删除旧基础映射/网格/片区时保留历史bd_zone_release，审计记录旧团队归属（自然导入不自动承接团队或重建片区）。失败包括删除后的写入异常整体回滚。批量写网格，门店5000行批次；地理面积从实际几何重新计算，不能以候选area字段替代。旧兼容bd_zone表仅在存在时按旧事务清理。

```bash
# 默认仅校验文件与审计，不打开数据库
pnpm zones:natural:import --input /absolute/path/natural-candidate.json
```

正式本地导入须显式`--apply --admin-user <已有管理员>`；`--replace`与`--append-region`互斥且都需apply。当前仅允许loopback PostgreSQL重构库，生产入口未开放，不改环境文件。本轮没有向完整基准副本执行apply；数据库验证只在随机schema-only库。候选使用前面已声明的canonical-json-js-v1格式，旧Python候选不能未经重新生成/校验冒充同一哈希格式。

验证：候选篡改/假审计/重复商户拒绝，内洞含岛多面保留；临时PG验证未关联网格与映射、已有版本/同地区拒绝、质量门槛、跨地区商户/空间重叠、追加成功、进展/派发/批次任务保护、删除后强制回滚、替换成功且历史release保留，结算正式表不变。测试中的quality_pass只用于合成事务夹具，不代表旧normalise已迁移。CLI测试默认不连库、远端apply/隐式替换拒绝。

全量首次166项，164通过、2失败：新增历史夹具缺真实片区导致自身清理失败，并污染后续formal测试。补齐外键关系和失败清理后仅重跑自然导入+formal相关6项，全部通过无跳过；最后修正内洞岛外轮廓准入并重跑同6项通过。7包类型检查/构建通过；末次受影响server检查/构建亦通过。最终隔离证据baselineUnchanged=true、cleanup=dropped。不是宣称首次全量全绿，原失败日志保留；未重复其他164项已通过测试。

尺寸归一化/碎片吸收normalise仍未迁移，不能据导入事务完成宣称自然全流程可替换真实资产。管理端流程、真实规模性能、Docker/CDN及50会话验收继续后续。当前本地容器、生产、数据库配置和完整副本数据均未操作。


## 原件恢复验收结果（2026-11-11）

`node --env-file=.env.next.local --import tsx scripts/verify-restored-originals.mts`仅以数据库启动参数强制只读，读取imports/recon_tasks文件元数据并解析私有原件，不访问raw_records、不登记发薪、不重新入表。8份巡店原件合计211723行与存量parsed_rows一致、原件前后摘要不变；14条对账引用对应7份文件可读。证据`data/refactor/restored-originals-verification.json`仅记录计数/ID和摘要相等标记，无行内容。此为原件/解析验证，不代替对账金额的新旧业务对照。

剩余imports.id=10、stored_path=kept、零大小/特殊摘要由旧`scripts/reset_august.py`创建，代表7月保留记录，不能当演示数据删除。其独立原件未留存，404明确说明历史保留，仍可查询已有结算结果。数据库未改。相关private-files/visit-imports三项测试在随机临时库通过、无跳过，server类型检查通过；开发入口未登录原件访问401。标准22条历史引用已恢复，Docker只读挂载和非root可读权限在阶段验收时再检查，当前未重新构建镜像。

## GCS CORS 权限核对（2026-11-11）

当前HMAC可上传对象和公开读取，但用GCS原生`CorsConfig` XML及签名请求读取/写入桶CORS均返回403/AccessDenied，未成功修改配置。此前AWS格式400已排除，不能继续用格式重试掩盖权限问题。公开探针响应虽为200但没有`Access-Control-Allow-Origin`，CDN模块加载尚未验收通过。

可用桶管理身份从项目根执行：

```bash
gcloud storage buckets update gs://visitworld-assets --cors-file=deploy-next/gcs-cors.json
```

此配置针对已经公开读取的静态资源，只允许跨域GET/HEAD，不授予写入/删除权限；不用于私有原件或照片。若由当前HMAC身份管理，则需桶的`storage.buckets.get`及`storage.buckets.update`权限，不必提供新的对象上传密钥。配置后须重新核对CDN缓存后的实际响应和浏览器模块加载，不能只看控制台保存成功。

`pnpm assets:cors`只读核对；`pnpm assets:cors -- --apply`显式请求配置（权限不足退出非零），只输出HTTP状态/错误码，不输出密钥或服务器原始报错。XML配置、此前配置备份（仅可读时）、执行结果保存于忽略目录data/refactor。配置模板见本目录`gcs-cors.json`。相关资产/发布规则10项测试通过，未运行无关业务全量测试或构建Docker。


## 原件挂载与地图本地基础验收（2026-11-11）

复用现有API镜像、一次性`--read-only --user 1000:1000`容器，将已恢复目录以readonly绑定到`/data/legacy-uploads`，15份去重真实原件可读取；既有临时fixture写入被拒绝。证据`data/refactor/restored-originals-container.json`及`p4-legacy-mount.json`。未重建镜像、未重启或切流；最后release的实际compose挂载仍待R3核对。

真实Google SDK从127.0.0.1:8001发起1次请求，源码loader和地图构造成功、未鉴权失败、未产生供应商错误码，不调用Places；证据`data/refactor/live-map-acceptance.json`。此为基础加载验收，不代表完整手机矩阵或8080来源已验收。开发地址保持8001/admin和8002，8080旧Docker候选未更新。

当前本地商户档案、巡店任务结果与快照未发现照片引用；不额外下载生产私有目录。若数据后续新增真实引用，再按需恢复并验证。所有本轮数据库访问只读，生产未连接。


## 最新候选构建与独立容器验收（2026-11-11）

候选`local-acceptance-20261111-a`（linux/arm64、assets=cdn）已完成174项阶段全量测试、0失败/跳过、7包类型检查和构建，并生成API/Worker、Nginx镜像及归档。源码带未提交改动，manifest记录dirty状态，不能以git HEAD代替产物摘要。此为阶段验收构建，日常仍使用`pnpm dev`，没有要求每次小改重建。

`verify-candidate-container.mjs`仅接受本次隔离运行器的随机库与显式release，在独立网络/私有任务卷/随机本地端口验证：HTML及CDN引用、管理员/员工权限、授权私有原件读取、Worker队列、API/Worker重启后会话、MCP/OAuth404和缺失JS404。临时容器/卷/网络/原件目录及数据库均已清理，基准副本校验不变。不能直接把完整副本URL传给该脚本。证据`data/refactor/candidate-container-acceptance.json`，不代表浏览器CDN已验收或已切流。

4个公开构建资源已上传到`store-next/local-acceptance-20261111-a/`，真实CDN读取200、SHA256/MIME和元数据摘要一致；已有对象重复发布复用、不覆盖。输出未发现已配置私有密钥/会话值。源端Cache-Control为1年immutable，CDN实际返回1小时immutable；许可头仍为空，因此`cdnBrowserReady=false`。证据`data/refactor/candidate-assets-verification.json`。

当前8080入口仍`p5-mcp-auth-a`，最新候选未正式启动/切换，Vite8001/8002继续运行。CORS权限补齐后复用该候选，不重新执行已通过全量检查；仅核对受新配置影响的CDN响应、浏览器闭环与发布回滚。当前未操作生产。


## 最新候选浏览器功能验收（2026-11-11）

`local-acceptance-20261111-a`已在独立Docker Nginx/API/Worker、随机测试库/网络/任务卷完成16组既有浏览器流程：登录与双语、日期计划/请假、消息/已读、日报导出/后台分析失败重试、批次创建/执行/日志/禁用/照片/新店、员工批次地图、管理网格地图、片区目录改名/合并/拆分和网格启用。未改写测试业务断言。权限/并发冲突、数据/快照保留、手机宽度及页面错误断言通过；外部AI/地图用已有mock。资金结算/对账/发薪专项浏览器闭环不包含在这16组内，不能凭此宣称全部财务UI已完成新镜像验收。

**资源交付模式特别说明：** CORS权限未解决，验收进程用`candidate-browser-preload.mjs`拦截该release CDN URL，只重放manifest校验过摘要/大小的本地构建字节。该脚本严格要求随机库/隔离runtime/release，不进入应用镜像；没有修改公开桶或默认浏览器跨域安全策略。它证明候选前后端业务可以工作，不证明真实CDN/CORS/缓存通过，证据固定`cdnBrowserVerified=false`及明确`browserAssetMode`。配置CORS后仍须真实请求验收。

结果`data/refactor/candidate-browser-acceptance.json`列出16组实际脚本及release；来源快照计数/口令摘要未变，临时库删除、临时容器/卷/网络/原件目录清理。隔离helper4项测试通过，无重复业务全量检查或镜像重建。当前8001/8002仍为热更新开发，8080继续旧候选，生产未操作。


## 当前收尾计划同步（2026-11-11）

唯一当前计划见[重构与部署计划](../docs/计划-React-NestJS重构与部署.md) §5。候选`local-acceptance-20261111-a`已有16组基础/作业/地图及6组资金浏览器流程通过，业务浏览器资源为摘要校验后的本地重放，真实CDN尚未验收。删除专项实际观察到同一报告有两个可见删除入口，尚待定位原因；门店主档专项尚未在该候选执行，不能使用旧release结果补齐。

后续依次为：解决删除专项并补齐主档专项；管理员处理桶CORS后验证真实CDN；本地8080切换与原件/地图来源复核；发布失败保护和回滚演练。若修复影响产物，创建新release并只补充受影响检查，不覆盖既有候选。8080仍运行`p5-mcp-auth-a`，8001/8002为源码开发服务。生产尚未发布，需用户本地验收后另行安排；现有arm64候选还须与生产架构核对。


## 删除页修复与资金验收完成（2026-11-11）

`Settlement.tsx`中同级删除组件与判定报告使用相同React key，切换导入文件时导致DOM错误复用和两个可见删除入口。已按组件类型区分key；删除浏览器流程保留「唯一删除入口」回归断言，明确选择目标文件，不用`.first()`掩盖重复节点。

派生不可变候选`local-acceptance-20261111-b`仅重建管理端资源和Nginx，沿用a的API/Worker镜像及员工端字节。管理端类型检查、构建通过；候选b的判定入表、删除、门店主档浏览器流程通过，包含取消/确认删除、发薪台账保留及封账保护。其余5组资金流程沿用未受影响的候选a结果，汇总`data/refactor/candidate-finance-summary.json`保留各自release；不宣称全部在b重跑。隔离运行器确认基准校验未变并清理临时库/容器；一次性验收运行器已移除。

候选b的4个公开资源上传/CDN SHA256与MIME通过（`candidate-b-assets-verification.json`），CORS仍缺许可头，业务浏览器验证仍是摘要校验后的资源重放，不能当成真实CDN加载。新增候选manifest记录derivedFrom及实际复用镜像ID，归档含API和新网关镜像，a未覆盖。

真实`release.mjs deploy`对临时HTML摘要不符候选返回失败；8080健康/运行版本及local-state.json字节保持，临时目录清理（`release-failure-preserved-entry.json`）。仅证明摘要预检失败保护，完整启动失败/健康失败、切换/回滚还待验收。

剩余：管理员配置CORS后真实CDN验证；候选b本地8080切换与原件/地图来源复核；完整发布失败保护/回滚演练和最终命令记录；用户本地验收后另行安排生产替换。8001/8002源码热更新含本次修复，8080仍为旧`p5-mcp-auth-a`。未操作生产。


## 发布恢复隔离验收与最终本地命令（2026-11-11）

实际部署CLI现共用`release-transition.mjs`执行网关配置替换和Worker交接。修复：候选Worker停止失败不能阻断HTTP入口恢复；只有确认候选Worker已停止才重启旧Worker，防止双Worker争抢持久任务。首次发布失败恢复切换前配置；恢复异常会明确报告需要检查，不再笼统声称入口已恢复。

7项定向Node测试通过。`verify-release-transition.mjs`在既有安全隔离运行器创建的随机库、网络、任务卷及私有文件目录中，使用实际a/b网关、API/Worker镜像验证：无效Nginx配置、错误健康版本、Docker Worker启动失败、不兼容任务均受保护；a→b→a两方向system.check任务完成，目标Worker单实例，会话/授权原件读取持续成功，两版资源摘要与immutable响应通过。资源以本地只读镜像提供，不代表真实CDN已通过。证据`data/refactor/release-transition-acceptance.json`；运行器确认基准计数/口令摘要未变并删除临时库，临时容器清理，一次性运行器移除。候选b的API镜像与a相同，此次不声称验证数据库迁移或不同API版本兼容。

当前8080仍为`p5-mcp-auth-a`，源码开发端8001/8002继续运行；部署脚本改动在下次执行命令时生效，不需要重建API或前端镜像。以下是**CORS补齐、真实CDN浏览器验证通过后**的完整本地命令，本轮尚未执行切换：

```bash
# 桶管理员执行；本机当前没有gcloud，现有HMAC缺桶配置权限
gcloud storage buckets update gs://visitworld-assets --cors-file=deploy-next/gcs-cors.json
# 在项目根目录只读核对实际响应
pnpm assets:cors
# 复用已构建且公开资源已上传的候选，不重复全量构建
pnpm release:deploy -- --env local --release local-acceptance-20261111-b
# 如本地验收失败，回到本轮切换前的版本
pnpm release:rollback -- --env local --release p5-mcp-auth-a
```

回滚前仍检查任务兼容性；不删除任务/上传卷或旧资源。上述CLI只允许local目标，不执行生产。还需完成：真实CDN浏览器加载、8080发布/原件与真实地图来源复核、完整命令切换/回滚验收及最终运行记录。用户本地验收后再另行安排生产配置、架构、备份与切换窗口。


## 最终切换门槛（2026-11-11）

真实Google SDK已在`http://localhost:8080`来源的拦截测试页通过源码loader创建地图：单次SDK请求、无Places、无鉴权或供应商/页面错误。证据`data/refactor/live-map-8080-origin.json`，仅覆盖来源许可，不代表新候选已在8080上线。

最终公开资源实际响应仍缺`Access-Control-Allow-Origin`，见`data/refactor/final-cdn-gate.json`。现有HMAC无桶CORS配置权限，本机无gcloud；不以资源重放验收绕过此发布门槛，目标blocked等待管理员应用已交付的`gcs-cors.json`。完成后继续真实CDN浏览器验收、8080候选发布及完整命令回滚验收。源码服务8001/8002保留，8080仍为p5-mcp-auth-a，生产未操作。


## 开发端口调整（2026-11-11）

管理端8001、员工端8002，使用`pnpm dev`统一启动；单独Vite启动及跨端跳转使用相同端口。API仍3000。已重启当前项目开发服务并通过两端登录页/代理/跳转/Worker心跳验证、2项配置测试及两端类型检查；未重建Docker，8080仍p5-mcp-auth-a。新系统OAuth/MCP没有模块或依赖，仅网关保留旧路径404；不修改旧系统与数据库。


## 已完成本地Docker部署与真实CDN验收（2026-11-11）

当前入口为`local-acceptance-20261111-c`：管理端`http://localhost:8080/admin/`，员工端`http://localhost:8080/`。源码热更新仍8001/8002。生产未操作。

运维配置CORS后，一份候选b资源仍命中缺许可头的旧CDN缓存；GCS直连与新缓存响应已正确。候选c使用新不可变资源前缀，复用b的4个资源字节与API/Worker镜像，只重建Nginx，不覆盖旧对象，不重复无关构建/全量测试。

真实浏览器直接访问CDN：7个管理页面、3个员工页面，4个资源加载通过，无CORS/页面错误，员工管理API403。授权原件下载、片区/网格读取和部署地图配置创建真实地图通过，未调用Places。API/Worker健康，任务卷共享，system.check完成，历史目录只读递归87文件可读。证据`data/refactor/local-docker-cdn-browser.json`和`local-docker-runtime.json`。

实际已执行并通过：

```bash
pnpm release:deploy -- --env local --release local-acceptance-20261111-c
pnpm release:rollback -- --env local --release p5-mcp-auth-a
pnpm release:deploy -- --env local --release local-acceptance-20261111-c
```

回滚旧目标最初缺历史目录映射，旧镜像支持该映射；已在私有忽略目录备份旧目标compose/runtime.env，给API/Worker补齐相同只读挂载和NEXT_LEGACY_UPLOAD_DIRECTORY，然后回滚验收通过。未改变旧镜像或数据库。回滚保留原会话、私有原件，两版本8个资源均通过摘要校验，仅旧Worker运行；重新部署后仅候选c Worker运行。回滚结果`local-full-command-rollback.json`。最终数据库结构、业务表计数及口令摘要与部署前一致（`local-deploy-baseline.json`）。

剩余是用户本地业务验收及另行安排生产架构/域名/HTTPS/备份/切换窗口；真实设备兼容仍需人工确认。前文候选a/b、CORS受阻及旧8080状态是历史记录，以本节和当前重构计划§5为准。
