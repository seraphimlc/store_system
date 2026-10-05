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
            "model": os.environ.get("AI_MODEL", "gpt-4o-mini"),
            # 输出上限（可配 AI_MAX_TOKENS；DeepSeek 官网 deepseek-chat 8K 足够）
            "max_tokens": int(os.environ.get("AI_MAX_TOKENS", "8000") or 8000)}


def _client_kwargs():
    """httpx 客户端参数：**代理口径**（2026-10-06 真 bug 修复）。

    ⚠️ httpx 默认 `trust_env=True` → 会读 **macOS 系统代理**（`getproxies()`）。
    本机实测：系统代理 `127.0.0.1:7897` 对 HTTPS 是坏的（SSL EOF），
    于是**所有 AI 调用全部失败**（看板分析、报告、派工建议都受影响）——
    而直连 `api.deepseek.com` 是通的（HTTP 401 = 正常可达）。

    所以默认 **直连**（`trust_env=False`）；要代理就显式配 `AI_PROXY`。
    旧行为可用 `AI_TRUST_ENV=1` 恢复。
    """
    if os.environ.get("AI_TRUST_ENV", "") in ("1", "true", "yes"):
        return {}
    proxy = (os.environ.get("AI_PROXY") or "").strip()
    kw = {"trust_env": False}
    if proxy:
        kw["proxy"] = proxy
    return kw


def configured() -> bool:
    c = _cfg()
    return bool(c["key"] and c["base"])


def chat(prompt: str, timeout: int = 600, retries: int = 1,
         max_tokens: int = None, return_usage: bool = False):
    """调用模型，返回文本；失败自动重试（共 retries+1 次），仍失败抛异常。

    兼容推理型模型：思考很长 → max_tokens 取配置（默认 380000）；
    思考在 reasoning_content、content 可能为空 → 返回 reasoning_content
    让调用方用 extract_json 提取。
    """
    c = _cfg()
    if max_tokens is None:
        max_tokens = c.get("max_tokens") or 8000
    last = None
    for i in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, **_client_kwargs()) as _c:
                r = _c.post(c["base"] + "/chat/completions",
                            headers={"Authorization": "Bearer " + c["key"]},
                            json={"model": c["model"],
                                  "messages": [{"role": "user",
                                                "content": prompt}],
                                  "max_tokens": max_tokens})
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0].get("message", {})
            content = (msg.get("content") or "").strip()
            if not content:
                content = (msg.get("reasoning_content") or "").strip()
            if return_usage:      # 可选：一并回传 usage（token 留痕用）
                return content, (data.get("usage") or {})
            return content
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def extract_json(text: str):
    """从模型输出提取 JSON：容错 markdown 代码块 / 前后杂文 / 截断。

    推理模型可能把答案写在思考里或 content 前带 ```json ``` ——逐级降级解析。
    """
    import json as _json
    import re as _re
    if not text:
        return None
    t = text.strip()
    # 1) 去 markdown 代码块
    if t.startswith("```"):
        t = _re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = _re.sub(r"\s*```$", "", t)
        t = t.strip()
    # 2) 直接解析
    try:
        return _json.loads(t)
    except Exception:  # noqa: BLE001
        pass
    # 3) 取首个 { ... }（可能截断/夹带文字）
    m = _re.search(r"\{.*\}", t, _re.S)
    if m:
        try:
            return _json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            pass
    return None
