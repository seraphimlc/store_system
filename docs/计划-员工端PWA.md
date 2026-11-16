# 员工端 PWA 改造方案

日期：2026-11-16。范围：员工端与必要的 Gateway 静态文件交付；不发布生产，不修改 API、数据库、管理端或 Worker。

## 验收目标

1. Android 支持浏览器原生安装；iOS 提供添加到主屏幕引导，独立窗口内不再显示安装入口。
2. Manifest、Worker、图标与离线页固定从应用同源交付，业务 JS/CSS 保留现有 CDN。
3. 只缓存无个人信息的离线说明页；不缓存 HTML 应用壳、API、地图、上传下载或写请求，不重放写操作。
4. Worker 仅处理明确的员工页面 GET 导航；管理端、OAuth、MCP、未知路径和第三方资源不处理。
5. 新版本只提示，不强制刷新。用户完成手头操作并再次确认后才刷新本窗口；其他窗口不自动刷新。
6. 提供可交付的停用 Worker，保留同源稳定地址，清理员工端自身缓存而不影响其他缓存。
7. 复用现有 Node 测试与员工端 typecheck/build，补充真实 Worker 的浏览器回归。iOS/Android 系统安装与独立窗口布局需要人工真机确认。

## 实现决策

第一期只缓存一个离线说明页，自定义轻量 Worker 加本地 Vite 构建插件即可；不引入 PWA 插件、Workbox 或共享锁文件变更。版本由员工端构建产物摘要生成。Vite 在 HTML 变换后注入同源安装标签，避免 ASSET_BASE_URL 把 Manifest 链接改到 CDN。

PWA 生命周期与界面放入独立模块，入口仅增加导入、账号安装入口与更新提示。只在生产构建注册 Worker，普通开发不注册。iOS 安装引导与更新提示支持中日文；成功用 Toast，错误和风险说明保留。

Worker 用网络优先处理员工导航，只有网络抛错才返回离线说明，不掩盖 HTTP 401/403/404/5xx。所有业务请求不调用 respondWith，不增加业务数据持久化。缓存使用独立前缀，激活时只删除该前缀的旧缓存。

更新采用 waiting Worker：不自动 skipWaiting、不自动 clients.claim、不在 controllerchange 时刷新。更新入口必须由当前窗口用户确认；多窗口激活不触发其他窗口 reload。安装失败不阻止普通网页使用；安装引导不承诺与浏览器共享会话。

## 发布与回滚

Gateway 同源提供 /sw.js、/manifest.webmanifest、/pwa/。Worker/Manifest 重新验证，离线页不长期缓存；图标用稳定地址并允许重新验证。现有版本化 CDN 资源策略不变。发布打包必须把同源文件纳入 Gateway 与摘要校验，纯管理端发布应保留已有员工 PWA 文件。

停用时将独立的停用 Worker 交付至 /sw.js，清理自身缓存并注销；不能只删除脚本。候选环境验证旧版→新版→停用及多窗口。回退服务器时同样确保 /sw.js 继续可获取。

生产发布另行授权：对照上次实际清单，只构建员工端和 Gateway，保留管理端/API/Worker，使用既有加密 OSS 流程。此任务不触发生产构建或上传。

## 协作边界与检查

优先新增 apps/staff/src/pwa/、apps/staff/public/pwa/、独立测试及发布辅助模块。共享入口与发布脚本仅做定点插入，编辑前复查差异，不覆盖其他 agent 工作，不提交或重置他人改动。

自动验收覆盖：导航白名单、敏感请求旁路、离线回退、HTTP 错误不掩盖、缓存清理、更新需确认、多窗口不强刷、构建 CDN/同源分离、Gateway 文件交付和停用。浏览器测试使用无业务数据的静态构建与合成请求，不读取业务库。

## 实施记录

- 源码改造完成，尚未发布。新增 Manifest、192/512/180 图标、静态离线页、独立 PWA 生命周期与账号入口；未增加依赖或改共享锁文件。
- 构建插件 `apps/staff/pwa-build.ts` 在 HTML 变换后注入同源标签，构建产物摘要填入 Worker 版本；普通 Vite 开发不注册 Worker。
- `scripts/staff-pwa-release.mjs` 提供完整文件白名单、同源暂存、复用摘要校验及 Gateway location。`scripts/release.mjs` 本地组包已经接入，`scripts/release-lib.mjs` 已生成相应精确路由；没有执行 Docker 构建/部署。
- 工作区并发出现的服务端、管理端、考勤组件、测试运行器等改动均保留。共享员工端入口只插入 PWA 导入、Provider 与账号入口；Worker 白名单兼容并发新增的 `/my/attendance`、`/my/team-attendance`。

### 自动验收结果（2026-11-16）

1. `node --import tsx --test scripts/staff-pwa.test.mjs scripts/release-lib.test.mjs`：18/18 通过；补齐并发考勤路由后仅重跑受影响的 PWA 7 项，全部通过。
2. `pnpm --filter @store/staff typecheck`：通过。
3. `ASSET_BASE_URL=https://pwa-assets.invalid/staff/ pnpm --filter @store/staff build`：通过，确认 CDN base 下 Manifest 与图标链接仍为同源绝对路径。构建仍提示单 JS 块超过 500kB，本次不做无关拆包。
4. `node scripts/verify-staff-pwa-browser.mjs`：六组验收通过，包括同源注册/缓存内容、安装事件与中日文引导、离线深链与旁路、取消/确认更新及多窗口、停用清理、独立窗口隐藏安装入口。真实浏览器安装菜单由系统控制，此处使用确定性安装事件；不将合成事件当作系统安装成功。
5. 浏览器回归只启动随机 loopback 端口的短时静态服务器，提供合成会话并拦截虚构 CDN 地址；未连接业务库或外部业务服务。成功和失败路径均关闭测试浏览器与静态服务，未停止既有开发服务。

浏览器迭代仅修正验收脚本中的语言入口位置与真实浏览器安装事件干扰，没有为绕过失败放宽业务断言。无截图，无真实业务读写。

### 后续候选交付与生产门槛

- **不能直接执行全组件本地 `release:build` 作为日常生产升级。**本次修改该脚本只补齐其本地 PWA 交付能力；生产继续执行唯一生产手册，基于实际部署清单准备员工端/Gateway 最小化候选。
- 历史 `prepare-staff-viewport-release.mjs` 等脚本固定了历史基线，未改写这些证据脚本。授权生产准备时，新的作用域组包应从已提交的明确员工端源码清单构建，导入 `stageStaffPwa(dist, gatewayHtml)`，把返回文件列表及摘要记入候选 manifest；普通 CDN 循环通过 `isStaffPwaPath(name)` 排除同源文件，再将 `staffPwaGatewayLocations()` 合并到当时真实 Gateway 配置，保留现有 OAuth/MCP/管理端配置。不得使用基础 `gatewayConfig` 覆盖生产配置。
- `stageStaffPwa` 固定交付 7 个文件：`sw.js`、`manifest.webmanifest`、`pwa/offline.html`、两个应用 PNG、Apple Touch Icon、停用 Worker。Gateway 产物校验使用 `verifyStaffPwa`；员工端复用必须同时复用这些文件，不能只复用 HTML/CDN。
- 普通回滚可重新交付旧版员工 HTML/Worker 与其同源文件；需要撤销 PWA 时，将候选里的 `pwa/sw-disable.js` 字节作为 `/sw.js` 发布，更新候选文件摘要。保留原地址与重新验证响应头，不删除脚本，不强刷窗口。停用期间前端旧注册代码可能再次获取脚本，但该脚本不会接管请求并会自行注销；后续正式移除功能时再删除注册入口。
- **人工待验收**：iPhone Safari 与 Android Chrome 各一次真实系统安装；安装后登录/过期/退出；独立窗口键盘、地图抽屉、safe-area；多窗口更新/回滚。
- **候选环境待验收**：实际 Nginx 配置与响应类型/缓存头、深链、CDN CORS、旧版本升级/回滚、管理端与 OAuth/MCP 不受影响。浏览器合成静态服务不代表真实 Gateway/Docker 已验收。
- 本地浏览器测试构建使用虚构 CDN，仅作自动验收，不能直接发布其 dist。生产构建必须使用真实冻结资源前缀，并先核对并发任务的源码归属，不能把整个当前工作区默认纳入发布。


## 本地 Docker 候选验收（2026-11-16 10:02，新加坡）

- 候选：`local-pwa-20261116015417`，入口 **http://127.0.0.1:32801/**；容器 `store-next-local-pwa-20261116015417-gateway` 保留供用户本机验收。默认8000入口未切换，8001/8002/3000开发进程未停止。
- 基线源码 `8438589853c07db45684b4db6a326f89ebf6ac8f`，从Git提取已提交员工端/共享源码，再叠加明确PWA文件与4处入口插入。**不打包并发未完成考勤改动**，不覆盖共享工作区dist。候选清单标记`acceptanceOnly`，不能直接作为生产包。
- 只构建员工静态资源和Gateway覆盖层。复用现有本地Gateway镜像的管理端文件和配置，新增PWA同源路由；API/Worker复用运行中的本地实例，不重建、不迁移、不启动第二Worker。资源使用版本化同源`/r/<id>/staff/`只读文件交付；不上传OSS/CDN。
- 镜像 `store-next-gateway:local-pwa-20261116015417`，身份 `sha256:f0c07ba0ad2c4770baae3ded1051599ebe8d402fd4c262c243edc8123c4bdac7`。原管理HTML摘要保持一致；原Gateway/API/Worker/edge的容器ID、镜像身份和启动时间逐项保持一致，`.release/local-state.json`保持原字节语义。
- 候选独立源码typecheck/build通过；实际容器`nginx -t`通过。HTTP验收：7个PWA文件摘要、MIME和`no-cache`/`nosniff`正确，缺失文件404而非SPA，员工深链正常，版本化JS保留immutable；复用API readiness=200，匿名session=401。只调用无凭据会话与健康检查，不读取真实业务列表，不执行登录/写入。
- 真实Chrome使用专门的临时普通浏览器profile，`Page.getInstallabilityErrors`为空。Worker仅缓存匿名离线页；安装事件/取消后引导、离线深链与API/管理端旁路通过。业务页面交互全部模拟API，不连接真实业务数据。
- 更新演练通过实际Nginx交付v2 Worker：取消后不刷新，明确确认后刷新当前窗口，其他窗口保留内存状态；再回退原Worker成功，无需清浏览器数据；停用Worker清自身缓存/注销且不刷新其他窗口。结束时已恢复容器内Worker为镜像原始字节并核对摘要。
- 证据：`.release/local-pwa-20261116015417/manifest.json`、`acceptance.json`、`docker-build.log`、`typecheck.log`；验收脚本`.release/verify-pwa-docker.mjs`。临时浏览器及profile已关闭/清理，保留候选服务与构建证据。
- 构建过程中修复的是隔离快照依赖软链接和本地镜像标签引用；浏览器脚本修正隐私模式安装限制、离线mock需显式中断，不修改产品逻辑或放宽安装性断言。

### 仍需区分的限制

1. 本次验证实际Docker/Nginx，但静态JS/CSS为本地版本化资源，**不代表真实公网CDN/CORS已验收**。
2. 候选仅绑定本机loopback，`http://127.0.0.1`可用于本机PWA验证；手机不能直接通过电脑LAN HTTP地址完成安全上下文安装验收，仍需后续受信HTTPS候选与iPhone/Android真机安装、登录/过期/退出、键盘/地图抽屉验收。
3. 复用本地旧API/管理端不表示本轮验收了它们的最新并发源码。没有新建/写入测试库或修改业务库。
4. 生产未操作。待明确授权后另按生产唯一流程准备最小化已提交源码候选，不能把当前验收目录或其他agent工作区变更直接上线。

## 本地单实例全量更新（2026-11-16）

用户要求按AGENTS生命周期规范重建并清理。本轮从已提交源码8438589叠加PWA显式改动，管理端/员工端/API/Worker配套构建，排除未完成考勤。改为固定Compose项目store-next-local，三服务一个实例，固定入口http://127.0.0.1:8000/。原32801候选已清理，不再保留历史验收实例。

- 新API和Gateway切换前健康/nginx检查通过；真实本地管理员登录后批次3详情和地图接口均200，页面正常。首次切换脚本误以为登录会保留深链，实际应用会回首页；脚本失败时自动恢复旧入口/Worker，修正脚本重新进入深链后验收通过，无产品逻辑变更。
- 四模块typecheck、server/worker编译及两个前端构建通过。全量测试初次386项中309通过/71失败/6跳过，主要因隔离快照缺失.venv链接导致Python oracle失败并残留临时库fixture；补齐链接后仅重跑59个失败文件，112通过/2失败。MCP mock测试启动预热连接了不存在的数据库，修正测试使用隔离运行器专属数据库后4/4通过；搜索建议文件单独干净库3/3通过。未重复已通过项，隔离库均已删除，业务库未选作测试目标。
- 唯一共享源码修正为apps/server/test/mcp.test.ts的mock启动数据库接入；其他agent未提交源码保留，不带入本地镜像。
- 复用原store_settle、任务卷store-next-jobs-store-settle、上传挂载及会话密钥；不改密码，不应用新迁移。切换前后users/结算记录/批次数量与密码摘要一致。真实登录仅产生正常认证会话，无业务fixture或提交。
- 清理6个旧容器、20个本项目无引用镜像标签、2个无引用网络。只保留两应用镜像（API/Worker共享），其他项目和基础镜像不动，未全局prune或删除持久卷。共享builder缓存未做全局清理。
- 证据保留在.release/local-build/，运行配置在.release/local/compose.json。API/Gateway镜像的运行结构为固定local-current标签，后续更新应先验收再通过固定Compose替换服务，不再按时间创建长期部署。
- **仍未完成**：本地商户搜索007–009/部分OAuth001结构未迁移；本轮不自动写结构/生成搜索索引，因此搜索/OAuth部分功能仍可能提示未就绪，不能声称所有业务已验收。真机安装及公网CDN未验收。生产未操作。
