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

    兼容推理型模型：思考可能很长、`content` 可能为空 → 回退取 `reasoning_content`
    让调用方用 `extract_json` 提取。

    ⚠️ **2026-10-17 修复：截断的输出绝不能被当成答案。**
    看板「总体分析」原来硬编码 `max_tokens=2500`，而 `deepseek-v4.1-flash`
    这类推理模型的思考同样占输出预算 → 思考把预算吃光、`content` 为空、
    `finish_reason="length"`；旧代码见 `content` 为空就回退 `reasoning_content`
    → **把没写完的思维链当分析返回**，调用方落库并长期缓存，页面上就是
    半句英文推理稿（用户 2026-10-17 报的"分析的是什么垃圾"）。
    现在：`finish_reason == "length"` 一律视为**失败** —— 先把预算翻倍重试
    （上限 4 倍 / 至少 16000），仍截断就抛异常，由调用方显示"生成失败"。
    只有模型**正常收尾**（`finish_reason == "stop"`）却把答案写在
    `reasoning_content` 时才回退取它（JSON 提取类调用方依赖这个行为）。
    """
    c = _cfg()
    budget = int(max_tokens or c.get("max_tokens") or 8000)
    ceiling = max(budget * 4, 16000)
    last = None
    while True:
        truncated = False
        for _ in range(retries + 1):
            try:
                with httpx.Client(timeout=timeout, **_client_kwargs()) as _c:
                    r = _c.post(c["base"] + "/chat/completions",
                                headers={"Authorization": "Bearer " + c["key"]},
                                json={"model": c["model"],
                                      "messages": [{"role": "user",
                                                    "content": prompt}],
                                      "max_tokens": budget})
                r.raise_for_status()
                data = r.json()
                ch = (data.get("choices") or [{}])[0]
                msg = ch.get("message") or {}
                finish = ch.get("finish_reason")
                if finish == "length":
                    truncated = True
                    last = RuntimeError(
                        f"AI 输出被截断（max_tokens={budget}）："
                        f"思考占满预算、答案未产出")
                    continue
                content = (msg.get("content") or "").strip()
                if not content:
                    content = (msg.get("reasoning_content") or "").strip()
                if return_usage:      # 可选：一并回传 usage（token 留痕用）
                    return content, (data.get("usage") or {})
                return content
            except Exception as e:  # noqa: BLE001
                last = e
        if truncated and budget < ceiling:
            budget = min(budget * 2, ceiling)
            continue                   # 放大预算重来
        raise last if last else RuntimeError("AI 调用失败")


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
