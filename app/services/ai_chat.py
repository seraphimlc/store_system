# -*- coding: utf-8 -*-
"""公共 AI 调用（OpenAI 兼容 /chat/completions）。

配置：AI_API_KEY / AI_BASE_URL / AI_MODEL（可选）；未配置时 configured()=False，
调用方应自行回退到规则实现。
"""
import os
import httpx


def _cfg():
    return {"key": os.environ.get("AI_API_KEY", ""),
            "base": os.environ.get("AI_BASE_URL", "").rstrip("/"),
            "model": os.environ.get("AI_MODEL", "gpt-4o-mini")}


def configured() -> bool:
    c = _cfg()
    return bool(c["key"] and c["base"])


def chat(prompt: str, timeout: int = 120, retries: int = 2) -> str:
    """调用模型，返回文本；失败自动重试（共 retries+1 次），仍失败抛异常。"""
    c = _cfg()
    last = None
    for i in range(retries + 1):
        try:
            r = httpx.post(c["base"] + "/chat/completions",
                           headers={"Authorization": "Bearer " + c["key"]},
                           json={"model": c["model"],
                                 "messages": [{"role": "user",
                                               "content": prompt}]},
                           timeout=timeout)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:  # noqa: BLE001
            last = e
    raise last
