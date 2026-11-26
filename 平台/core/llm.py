# -*- coding: utf-8 -*-
"""模型调用的公共底座。

涉及模型调用的模块（用例生成、改进方案）都从这里取配置解析与 HTTP 调用，
避免各自复制一份，也避免跨模块导入私有函数。

模块内按三段排列：异常 → 配置 → 调用。
"""
import json
import os
import re
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 异常

class LLMError(Exception):
    """模型调用或输出解析失败。"""


# ---------------------------------------------------------------- 配置

DEFAULTS = {
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-chat",
    "api_key_env": "JUDGE_API_KEY",
    "timeout": 120,
}

FALLBACK_KEY_ENV = "MODEL_API_KEY"


def resolve_cfg(config):
    """从整体配置里取出 judge 段，补齐默认值。"""
    cfg = dict((config or {}).get("judge") or {})
    for key, value in DEFAULTS.items():
        cfg.setdefault(key, value)
    return cfg


def resolve_api_key(cfg):
    """按键值优先级取 API Key：配置内直填 → 指定环境变量 → 兜底环境变量。"""
    return (
        cfg.get("api_key")
        or os.getenv(cfg.get("api_key_env") or DEFAULTS["api_key_env"])
        or os.getenv(FALLBACK_KEY_ENV)
    )


# ---------------------------------------------------------------- 调用

def post_json(url, body, api_key, timeout):
    """向模型接口发一次 POST，返回解析后的响应对象。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise LLMError("模型接口返回 %s：%s" % (exc.code, detail))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError("模型接口连接失败：%s" % exc)


def extract_json_array(text):
    """从模型输出里抠出 JSON 数组，容忍代码块包裹与前后废话。"""
    if not text:
        raise LLMError("模型返回空内容")

    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise LLMError("模型输出里找不到 JSON 数组")

    try:
        return json.loads(cleaned[start:end + 1])
    except ValueError as exc:
        raise LLMError("JSON 解析失败：%s" % exc)


def chat_once(config, system_prompt, user_prompt, temperature=0.2):
    """单轮问答，返回模型输出的文本。生成用例与生成建议共用。"""
    cfg = resolve_cfg(config)
    api_key = resolve_api_key(cfg)
    if not api_key:
        raise LLMError("未配置模型 API Key（环境变量 %s 或 %s）"
                       % (cfg.get("api_key_env"), FALLBACK_KEY_ENV))

    body = {
        "model": cfg.get("model"),
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    payload = post_json(cfg.get("api_url"), body, api_key, int(cfg.get("timeout") or 120))

    try:
        return payload["choices"][0]["message"]["content"], cfg
    except (KeyError, IndexError, TypeError):
        raise LLMError(
            "模型响应结构异常：%s" % json.dumps(payload, ensure_ascii=False)[:300])
