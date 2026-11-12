# 管理端Appica与详情路由核对

更新：2026-11-11。依据用户指定官方索引 https://appica.dev/ui/react/llms.txt 与对应markdown组件文档；安装版本为`@appica/ui-react@1.4.0`。原有仅Button/Input等局部使用不能视为全站组件化，本轮按实际用途接入并验证表单契约。

## 组件使用矩阵

| 用户重点组件 | 当前实现 / 适用边界 |
|---|---|
| Form | 管理端所有本地表单使用Appica Form；共享消息/批次等通过管理端Provider注入，H5默认原生form不变 |
| Input、Textarea | 普通输入/多行输入使用Appica；file/hidden为原生浏览器文件与表单数据语义，不伪造文件输入 |
| Select | AdminSelect封装Appica Select/Trigger/Value/Content/Item，保持name、受控值、默认值、重置与FormData；不再用原生select绘制 |
| Button | 管理操作使用Appica Button；导航使用NavigationLink而非按钮冒充链接 |
| Calendar、Date Field、Popover | AdminDateInput按月使用DateField、按日使用DatePicker（内部Calendar/Popover）；提交仍为YYYY-MM或YYYY-MM-DD，支持默认/受控/重置、required与范围校验 |
| Checkbox、Radio | Appica Checkbox/RadioGroup/Radio；共享H5仍按原生input呈现，不注入桌面样式 |
| Slider、Switch | 片区详情用Appica Switch切换当前片区网格边界；无Slider场景，数值进度仍可精确输入 |
| Table | 管理端及Provider覆盖的共享表格使用Appica Table及Header/Body/Row/Head/Cell |
| Data Table | 官方是TanStack组合配方，不是包内单组件。现有列表继续服务器分页/筛选，使用Appica Table/Checkbox/Input/Badge/Pagination，不引入第二套客户端全量分页；未宣称已完成TanStack高级列管理配方 |
| Card、Badge | 上传/筛选容器接入Card，状态标识使用Badge；不为装饰给所有区块套卡片 |
| Navigation | 侧栏Navigation/NavigationLink，保留深链接、修饰键打开及aria-current |
| Navigation Menu | 侧栏不需要hover展开富面板，不额外增加Navigation Menu |
| Pagination | AdminPagination使用Appica Pagination/List/Item，沿用真实服务器page参数与前后页禁用；共享渲染默认保持H5原div |
| Tabs | 片区管理、主档候选面板使用Appica Tabs，支持键盘焦点 |
| Dialog | 团队创建、批次创建、员工/日报编辑、发薪/删除/主档确认等统一Appica Dialog，移除本地原生dialog.showModal接线 |
| Drawer | 网格列表/组合和片区详情使用Appica非模态右侧Drawer，地图保持可操作；其他实体详情保持独立路由。H5地图抽屉由另一agent维护 |
| Tooltip | 账号头像采用Appica Tooltip；主要操作保持可见文字，不靠悬浮提示才可理解 |
| Alert、Toast | 错误/持久结果用Alert，成功结果同时通过Appica Toast宣告；测试区分非模态Toast与业务Dialog |

## 独立详情与页面检查

列表不自动打开首条；点击详情进入路由，可刷新、浏览器返回和显式返回列表。创建、危险操作的确认、编辑可以继续用Dialog，地图的操作工具可以Popover/Dialog，不把实体详情挂列表底部。

| 模块 | 实体详情入口 |
|---|---|
| 月度对账 | /admin/recon/:id；上传/列表不与对账明细、归因、AI解读同时显示 |
| 判定文件 | /admin/files/:id；原件布局、判定报告、删除预览、明细集中详情页。批量上传完成保留列表与各文件结果，不因首个完成而卸载上传器 |
| 团队 | /admin/teams/:id；资料、成员、历史/日志在详情；创建/逻辑删除确认保留Dialog |
| 车站资产 | /admin/stations/:id |
| 车站任务 | /admin/station-tasks/:id；管理端独立渲染，避免覆写H5 agent的任务界面 |
| 主档留痕 | /admin/stores/:id；查看某主档留痕不挂在目录底部。目录内有意展开归并子项仍是列表关系展示 |
| 经营概览员工 | /admin/overview/staff/:code；不在公司概览底部自动追加员工分析 |
| 日报分析报告 | /admin/staff-reports/analyses/:id；列表与报告正文分离 |
| 消息 | /admin/messages/:id（可带box=sent）；GET详情仅发送者/收件人可读，不产生已读写入；H5未传detailBase仍保留既有展开方式 |
| 片区 | /admin/zones 大地图内打开右侧详情抽屉；/admin/zones/:id 直接访问也打开对应抽屉 |
| 商户 | /admin/merchant-map/:identity；主页面保持大地图，范围/数据列表为工具弹窗，商户实体信息为独立页 |
| 巡店批次 | /admin/zone-tasks/:id；创建使用地图点击片区，列表仅备用；批次/网格任务维持独立路由 |
| 绩效/配置/日期计划/日志 | 本身为统计/设置/计划或审计列表，未增加自动实体详情。编辑/确认仍按既有业务流程使用Dialog |

## 地图与资源加载

地图只挂载当前工作区。片区几何按地区游标读取并在组件生命周期缓存，选择变化只setStyle不重建地图/图层或请求geometry；选中集合用Set查找。巡店批次创建同样复用RegionMap点击片区，保留最多2000片区的服务端约束。商户地图保留视口聚合、1000点阈值与180ms防抖/迟到响应保护；进入商户详情停止视口请求并释放地图，返回重新挂载。未进行算法生成或几何替换。

接入完整组件后一次性入口约1061KB，已按相同本地构建拆成路由和日历懒加载，入口约544KB；这是构建块大小对照，不是总资源减半或真实延迟/并发性能验收。大地图与管理流程使用离线SDK测试，不重复付费地图请求。

## H5并行工作边界

不修改apps/staff。共享组件只把原生标签换成UI适配标签，H5无Provider时输出同样原生form/input/select/button/table；消息独立路由为可选detailBase，仅管理端启用。新增两项静态渲染断言验证默认原生属性与Provider隔离。H5类型检查和构建与管理端分别运行；不能把其它agent的页面调整当成本轮错误直接覆盖。

## 发布状态

本轮仅8001源码热更新；Docker8080仍local-acceptance-20261111-c，不包含本轮Appica与详情路由改动；生产未操作。后续需按新release做阶段Docker/CDN分包验收。

## 本轮验证记录

- 静态核对42个管理端TSX文件：本地代码没有原生form/select/table/dialog/button/textarea残留；保留file/hidden/日期校验代理输入。共享渲染通过管理端Provider注入，不声称H5也已迁移Appica。
- 管理端、共享client、H5、服务端类型检查通过；管理端/服务端与H5受影响构建通过。分包后最终入口约544KB（gzip约175KB），仍有500KB阈值警告，未隐藏警告或认定真实性能已验收。
- 管理流程浏览器：团队独立详情/逻辑删除、Appica表单提交、地图点选创建批次、片区选择不重新请求geometry/重建图层、商户独立详情/工具弹窗、日志/中日文/390宽度通过。证据`data/refactor/management-flow-acceptance.json`。
- 财务专项：真实合成Excel上传、规则识别、对账差异、失败替换保持上一版本、手动列映射修复、对账下载、发薪导出登记及重复下载不重复记账通过；AI缺凭证安全失败/重试仍有效。证据`p4-reconciliation-browser.json`。没有重复无关财务全量检查。
- 控件：月份键盘调整与YYYY-MM FormData、Select隐藏字段、Calendar/Popover、Radio互斥、共享表单/Textarea、Dialog Escape；对账上传行4个控件均36px、底部差0px、无横向溢出。证据`appica-control-browser.json`、`appica-admin-source.json`及布局实测记录。
- 独立详情刷新/返回：对账、判定文件、团队、车站、概览员工、主档留痕通过只读实际数据；日报分析真实库无记录，使用隔离库done夹具验证正文/刷新/返回，无模型调用。证据`appica-admin-routes.json`、`appica-analysis-route.json`。
- 消息接口权限测试通过：发送者/收件人可读，无关管理员/他人404、未登录401，GET不写已读。管理端详情刷新/返回与H5收信/已读/字面HTML安全联测通过；原旧标题断言因H5 agent更改入口而更新，未回改apps/staff。证据`p3-messages-browser.json`。
- H5默认原生表单属性/控件结构、分页/fieldset，以及Provider仅作用于自身子树，共3项渲染断言通过。`scripts/ui-controls.test.mjs`可在现有tsx测试环境运行。
- 所有写fixture均由既有隔离运行器创建随机schema-only临时库执行，基准聚合/口令摘要未变；运行器清理临时库/任务目录/服务。实际源库页面验证强制只读数据库并拦截非GET API。不操作生产或重建Docker。

### 2026-11-12 地图交互调整

按最新需求，网格管理与片区查看使用大地图和右侧 Appica Drawer，不套用普通实体独立详情页规则。筛选栏悬浮于地图，Select 使用白底描边；网格列表、选中集合、组合与所属片区拆分位于抽屉。片区点击打开详情，显示商户状态分布、面积、团队/负责人，并用 Appica Switch 切换当前片区网格边界。抽屉非模态，地图保持可点击，地图点击不自动关闭抽屉。批次列表名称和详情入口改为真实链接，保留新标签页与修饰键行为。

新验收入口为 `pnpm test:browser:map-workspace`，覆盖分页、地图选择、抽屉、组合/拆分、启用保护及版本冲突。旧 P5 地图脚本中沿用旧布局的选择器尚未全部更新，本轮未将其计入通过项目。
