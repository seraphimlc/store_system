# 巡店结算连接器（新栈源码包）

本目录是当前 React/NestJS 的 WorkBuddy 风格连接器资源，旧包在 `legacy/deploy/connector/visit-settle/` 保留。

- `connector-meta.json`：连接器名称、图标和 OAuth 方式。
- `mcp.json`：Streamable HTTP 地址和超时。
- `oauth-info.json`：两类发现地址及连接说明。
- `skills/visit-settle/SKILL.md`：27 个工具、6/10/27 权限、写入/下载规则。

本地源码入口：`pnpm dev`，API `http://127.0.0.1:3000`，授权页由 8001 提供。原始配置中的 `${VISIT_BASE_URL}` 是不带尾斜杠的服务源地址。客户端必须识别该变量，或使用下面已替换地址的包；不能将未替换的字符串当成有效 URL。

生成本地可导入包：

```sh
node scripts/package-mcp-connector.mjs --base-url http://127.0.0.1:3000 --output data/refactor/visit-settle-local.zip
```

只打包本目录白名单文件，替换三个 JSON 中的地址，不包含凭据、数据库、上传原件或环境文件。服务源地址只接受 HTTPS 或本机 HTTP，不能带路径、查询参数、用户信息或 fragment。该命令不部署或上传，也不更改客户端设置。

在支持此连接器格式的客户端中导入 ZIP 或目录，点击连接，通过浏览器登录并明确同意范围。OAuth access 为24小时、refresh为90天并轮换；被撤销或旧刷新凭据重用时重新连接，不复用其他人的凭据。

本包保持旧连接器文件结构；不同 WorkBuddy 版本是否直接识别 ZIP、读取元信息/Skill 的方式，需要用实际客户端核对。未完成目标客户端验证时，只能称“包生成/SDK验证通过”。截至本包生成时没有执行生产发布；不要默认连接生产源地址。Docker 8000 的公开 MCP/OAuth 地址及网关接线在 M6 验收。
