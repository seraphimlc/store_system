# -*- coding: utf-8 -*-
"""连接器包结构与占位符一致性（spec §6）。

最容易出的错：mcp.json 里用了 ${VISIT_BASE_URL} 但 token-schema.json 没声明该字段，
客户端会解析不出地址，表现为"连接失败"但看不出原因。
"""
import json
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "deploy" / "connector" / "visit-settle"


def _load(name):
    return json.loads((PKG / name).read_text(encoding="utf-8"))


def test_files_exist():
    for f in ("connector-meta.json", "token-schema.json", "mcp.json", "icon.svg",
              "skills/visit-settle/SKILL.md"):
        assert (PKG / f).exists(), f"缺少 {f}"


def test_auth_mode_is_token():
    assert _load("connector-meta.json")["auth_mode"] == "token"


def test_mcp_json_points_at_mcp_path():
    server = next(iter(_load("mcp.json")["mcpServers"].values()))
    assert server["url"] == "${VISIT_BASE_URL}/mcp"
    assert server["headers"]["Authorization"] == "Bearer ${VISIT_TOKEN}"
    assert server["timeout"] == 300000


def test_every_placeholder_is_declared_in_token_schema():
    """mcp.json 用到的每个 ${VAR} 都必须由 token-schema.json 声明。"""
    text = (PKG / "mcp.json").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{([A-Z0-9_]+)\}", text))
    declared = {f["key"] for f in _load("token-schema.json")["fields"]}
    assert used <= declared, f"未声明的占位符：{used - declared}"


def test_token_field_is_password_type():
    fields = {f["key"]: f for f in _load("token-schema.json")["fields"]}
    assert fields["VISIT_TOKEN"]["type"] == "password"
    assert fields["VISIT_TOKEN"]["required"] is True
    assert fields["VISIT_BASE_URL"]["required"] is True
