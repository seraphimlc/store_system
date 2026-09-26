# -*- coding: utf-8 -*-
"""连接器包结构与占位符一致性（MCP OAuth 版）。

连接器已改为 OAuth（commit：连接器改为 OAuth，不再配置 Token）：
- `auth_mode=oauth`，不再有 token-schema.json（用户无需填写 Token）；
- mcp.json 只留 url，不配 headers.Authorization；
- 新增 oauth-info.json 声明发现端点 / 流程 / token 寿命。

占位符一致性：唯一允许的占位符是 `${VISIT_BASE_URL}`（由客户端配置），
**不允许**残留 `${VISIT_TOKEN}` 或 Authorization 头——否则客户端会要求填 Token。
"""
import json
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "deploy" / "connector" / "visit-settle"


def _load(name):
    return json.loads((PKG / name).read_text(encoding="utf-8"))


def test_files_exist():
    for f in ("connector-meta.json", "oauth-info.json", "mcp.json", "icon.svg",
              "skills/visit-settle/SKILL.md"):
        assert (PKG / f).exists(), f"缺少 {f}"
    assert not (PKG / "token-schema.json").exists(), \
        "OAuth 连接器不应再带 token-schema.json"


def test_auth_mode_is_oauth():
    meta = _load("connector-meta.json")
    assert meta["auth_mode"] == "oauth"
    assert "无需在连接器里填写 Token" in meta["auth_note"]


def test_oauth_info_documents_discovery_and_flow():
    info = _load("oauth-info.json")
    assert info["auth_mode"] == "oauth"
    assert info["flow"] == "authorization_code + PKCE(S256)"
    disc = info["discovery"]
    assert disc["protected_resource_metadata"].endswith(
        "/.well-known/oauth-protected-resource")
    assert disc["authorization_server_metadata"].endswith(
        "/.well-known/oauth-authorization-server")


def test_mcp_json_points_at_mcp_path_without_token():
    server = next(iter(_load("mcp.json")["mcpServers"].values()))
    assert server["url"] == "${VISIT_BASE_URL}/mcp"
    assert "headers" not in server, "OAuth 连接器不应再带 Authorization 头"
    assert server["type"] == "streamableHttp"
    assert server["timeout"] == 300000


def test_no_token_placeholders_anywhere():
    """全包不允许出现 ${VISIT_TOKEN} 或 Authorization：Bearer（用户不再配 Token）。"""
    bad = []
    for p in PKG.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".md", ".svg"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if "${VISIT_TOKEN}" in text or "Bearer" in text:
                bad.append(str(p.relative_to(PKG)))
    assert not bad, f"残留 Token 占位/头：{bad}"


def test_only_base_url_placeholder_is_used():
    """唯一占位符 ${VISIT_BASE_URL}（由客户端配置）；全包无未声明占位符。"""
    used = set()
    for p in PKG.rglob("*.json"):
        used |= set(re.findall(r"\$\{([A-Z0-9_]+)\}", p.read_text(encoding="utf-8")))
    assert used == {"VISIT_BASE_URL"}, f"不允许的占位符：{used - {'VISIT_BASE_URL'}}"
