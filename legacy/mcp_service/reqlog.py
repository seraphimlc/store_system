# -*- coding: utf-8 -*-
"""请求级证据日志：JSON Lines。

这是本 P0 唯一可信的证据源——客户端界面的报错往往无法区分
"端点不可达 / 凭据被剥离 / 凭据错误"三者（spec §5.6）。

凭据值、查询串、请求体原文一律不落盘；头只记名字。
"""
import json
import os
import threading
from typing import Any, Iterable


def redact_path(path: str) -> str:
    """去掉查询串——它可能携带凭据。"""
    return path.split("?", 1)[0]


def redact_headers(raw: Iterable[tuple[bytes, bytes]]) -> list[str]:
    """只返回头名字（小写），不返回值。"""
    return [k.decode("latin-1").lower() for k, _ in raw]


class RequestLogger:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()

    def __call__(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
