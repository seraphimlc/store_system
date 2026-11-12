# -*- coding: utf-8 -*-
"""MCP tool annotations 统一工厂（P1-6：title + readOnly/idempotent/destructive hint）。

给**全部**工具（含后续新增）的 `@mcp.tool(...)` 加 `title=` 与 `annotations=ToolAnnotations(...)`，
让 WorkBuddy 端一次就看到：这是读还是写、能否安全重试、会不会破坏数据。

约定（与 mcp SDK 2.2.0 `mcp.types.ToolAnnotations` 对齐）：
- read_only_hint=True   → 不改环境（读/导出工具）
- destructive_hint=True → 写工具（覆盖/删除/重建等，可能破坏既有数据）
- idempotent_hint=True  → 相同参数重复调用无额外副作用（可安全重试）
- open_world_hint=False → 域内闭合（只访问本系统数据，不接触外部世界）

**为什么 idempotent 单独标**：`visit_upload` 是覆盖式写、且可能触发
全链路重算，**不是**幂等（同参数重复调用仍有副作用）→ 显式标 False。
"""
from mcp.types import ToolAnnotations


def read(title: str) -> ToolAnnotations:
    """只读工具：不改环境、可安全重试、无破坏性。"""
    return ToolAnnotations(
        title=title,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


def write(title: str, *, idempotent: bool = False,
          destructive: bool = True) -> ToolAnnotations:
    """写工具：默认破坏性（覆盖/重建等）；idempotent 按工具语义显式传。"""
    return ToolAnnotations(
        title=title,
        read_only_hint=False,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=False,
    )


def preview(title: str) -> ToolAnnotations:
    """只读预演（visit_rebuild action=preview）：会落一行审计拿 preview_id，
    但**不破坏任何数据** → read_only=False、destructive=False。"""
    return ToolAnnotations(
        title=title,
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,     # 每次调用产生新的 preview_id
        open_world_hint=False,
    )
