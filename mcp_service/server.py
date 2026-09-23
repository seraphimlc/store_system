# -*- coding: utf-8 -*-
"""WorkBuddy MCP 服务入口（独立进程，streamable HTTP）。

启动方式（cwd 由 WorkBuddy 客户端决定，故必须自举 sys.path，见计划坑 2）：
    VISIT_MCP_TOKEN=... DATABASE_URL=... python mcp_service/server.py

退出码：配置不合法 -> 2（spec §8 T2 的"空配置启动非零退出"）。
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp.server.mcpserver import MCPServer            # noqa: E402  (坑 3)
from mcp.server.transport_security import (           # noqa: E402
    TransportSecuritySettings,
)

from mcp_service import config                        # noqa: E402
from mcp_service.auth import BearerAuthMiddleware      # noqa: E402
from mcp_service.reqlog import RequestLogger          # noqa: E402


def transport_security_settings(public_host: str | None = None) -> TransportSecuritySettings:
    """保留 DNS-rebinding 保护 + 显式白名单（spec §5.6）。

    SDK 行为：allowed_hosts 支持 "host:*" 通配端口；缺失 Origin 放行。
    意外 Origin 是本地连通的典型静默失败源——它会被记录进日志（Task 2）。
    """
    hosts = ["127.0.0.1:*", "localhost:*"]
    if public_host:
        hosts.append(f"{public_host}:*")
        hosts.append(public_host)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[],
    )


def build_server() -> MCPServer:
    """构造 MCPServer 并注册工具。

    必须由 build_app 在 config.load() 之后调用（config 校验先于任何 app.* import）。
    """
    from mcp_service import tools  # 延迟 import：tools 依赖 app.* 的能力层
    mcp = MCPServer(name="visit-settle-mcp", version="0.1.0")
    tools.register(mcp)
    return mcp


def build_app(settings: config.McpSettings, public_host: str | None = None):
    mcp = build_server()
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=False,                     # P0 默认有状态；生产多副本再启用
        transport_security=transport_security_settings(public_host),
    )
    log = RequestLogger(settings.log_path)
    return BearerAuthMiddleware(app, token=settings.token, log=log)


def main() -> int:
    settings = config.load()                    # 必须先于任何 app.* import（坑 1）
    import uvicorn

    app = build_app(settings)
    print(f"[mcp] listening on http://{settings.host}:{settings.port}/mcp")
    print(f"[mcp] DATABASE_URL = {settings.database_url}")
    print(f"[mcp] log = {settings.log_path}")
    uvicorn.run(app, host=settings.host, port=settings.port,
                log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except config.ConfigError as exc:
        print(f"[mcp] 启动被拒绝：{exc}", file=sys.stderr)
        raise SystemExit(2)
