# -*- coding: utf-8 -*-
"""MCP 测试全局隔离。

**为什么需要**：上传类测试会经 `importer.upload_and_store` 把 blob 写进
`UPLOAD_DIR`（默认 `./data/uploads`）——实测在仓库里留下 ~80 个 5KB 的测试文件，
污染真实上传目录。这里把上传目录与导出目录统一重定向到临时目录。

注意：`app/config.get_settings()` 带 @lru_cache，改 env 后必须 cache_clear()。
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_dirs(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    exports = tmp_path / "exports"
    uploads.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    monkeypatch.setenv("VISIT_MCP_EXPORT_DIR", str(exports))
    try:
        from app.config import get_settings
        get_settings.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    yield
    try:
        from app.config import get_settings
        get_settings.cache_clear()
    except Exception:  # noqa: BLE001
        pass
