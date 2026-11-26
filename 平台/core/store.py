"""持久化层：用例集、运行记录、目标配置。全部落 JSON，无数据库依赖。"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR = os.path.join(BASE_DIR, "cases")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# 模型凭据单独存放，不进 config.json，也不随作品一起打包
SECRETS_PATH = os.path.join(BASE_DIR, ".secrets.json")

DEFAULT_CASE_FILE = "seed.json"

DEFAULT_LLM = {
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-chat",
    "api_key": "",
}


def load_secrets():
    data = _read_json(SECRETS_PATH, {})
    if not isinstance(data, dict):
        data = {}
    merged = dict(DEFAULT_LLM)
    for key in merged:
        if data.get(key):
            merged[key] = data[key]
    return merged


def save_secrets(payload):
    """只保存显式传入的字段；api_key 传空字符串表示清除。"""
    current = load_secrets()
    for key in ("api_url", "model", "api_key"):
        if key in (payload or {}):
            value = str(payload[key] or "").strip()
            if key == "api_key" and value == "":
                current[key] = ""
            elif value:
                current[key] = value
    _write_json(SECRETS_PATH, current)
    return current


def _normalize_chat_url(url):
    """允许用户只填 base，例如 https://api.deepseek.com/v1。"""
    url = str(url or "").strip().rstrip("/")
    if not url:
        return DEFAULT_LLM["api_url"]
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    return url + "/chat/completions"


def apply_llm_config(config):
    """把本地凭据叠加到运行时配置上，供 AI 判定与用例生成使用。"""
    secrets = load_secrets()
    judge = config.setdefault("judge", {})
    if secrets.get("api_url"):
        judge["api_url"] = _normalize_chat_url(secrets["api_url"])
    if secrets.get("model"):
        judge["model"] = secrets["model"]
    if secrets.get("api_key"):
        judge["api_key"] = secrets["api_key"]
    return config


def public_llm_config():
    """给前端看的脱敏配置：不回传完整 key。"""
    secrets = load_secrets()
    key = secrets.get("api_key") or ""
    return {
        "api_url": secrets.get("api_url") or DEFAULT_LLM["api_url"],
        "model": secrets.get("model") or DEFAULT_LLM["model"],
        "has_key": bool(key),
        "key_hint": (key[:5] + "…" + key[-4:]) if len(key) >= 12 else ("已设置" if key else ""),
        "env_key": bool(os.getenv("JUDGE_API_KEY") or os.getenv("MODEL_API_KEY")),
    }


DEFAULT_CONFIG = {
    "targets": [
        {
            "id": "campus-clean",
            "name": "校园智能助手（基线）",
            "type": "http_json",
            "base_url": "http://127.0.0.1:8000",
            "chat_path": "/chat",
            "reset_path": "/reset",
            "timeout": 60,
            "headers": {},
            "request_template": {"message": "{message}", "session_id": "{session_id}"},
            "response_map": {
                "answer": "answer",
                "trace": "trace",
                "citations": "citations",
                "latency_ms": "latency_ms",
                "fault_profile": "fault_profile",
            },
            "expected_fault_profile": "clean",
            "capabilities": {"trace": True, "citations": True, "tools": True},
        },
        {
            "id": "campus-blackbox",
            "name": "校园助手（黑盒视图·不暴露 trace）",
            "type": "http_json",
            "base_url": "http://127.0.0.1:8000",
            "chat_path": "/chat",
            "reset_path": "/reset",
            "timeout": 60,
            "headers": {},
            "request_template": {"message": "{message}", "session_id": "{session_id}"},
            "response_map": {
                "answer": "answer",
                "trace": "__unavailable__",
                "citations": "__unavailable__",
                "latency_ms": "latency_ms",
                "fault_profile": "fault_profile",
            },
            "expected_fault_profile": "clean",
            "capabilities": {"trace": False, "citations": False, "tools": False},
        },
        {
            "id": "external-real-estate",
            "name": "外部开源项目 · AI 房产助手（Docker/DeepSeek）",
            "type": "http_json",
            "base_url": "http://127.0.0.1:8082",
            "chat_path": "/api/v1/chat",
            "health_path": "/health",
            "timeout": 120,
            "headers": {"X-API-Key": "ytest-secret-key"},
            "request_template": {
                "message": "{message}",
                "session_id": "{session_id}",
                "stream": False,
                "include_intermediate_steps": True,
            },
            "response_map": {
                "answer": "response",
                "trace": "intermediate_steps",
                "citations": "sources",
                "latency_ms": "latency_ms",
            },
            "trace_normalizer": "langchain_steps",
            "expected_fault_profile": "unknown",
            "capabilities": {"trace": True, "citations": True, "tools": True},
        }
    ],
    "judge": {
        "enabled": True,
        "provider": "deepseek",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "api_key_env": "JUDGE_API_KEY",
        "timeout": 60,
        "temperature": 0.0,
    },
    "runner": {"concurrency": 4, "retry": 1, "case_timeout": 90, "stop_on_fail": False, "samples": 1},
}


def _ensure_dirs():
    for path in (CASES_DIR, RUNS_DIR):
        os.makedirs(path, exist_ok=True)


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (ValueError, OSError):
        return default


def _write_json(path, value):
    _ensure_dirs()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 用例集

def list_case_files():
    _ensure_dirs()
    out = []
    for name in sorted(os.listdir(CASES_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(CASES_DIR, name)
        data = _read_json(path, [])
        if isinstance(data, dict):
            meta_name = data.get("name")
            data = data.get("cases") or []
        else:
            meta_name = None
        out.append({
            "file": name,
            "name": meta_name,
            "count": len(data) if isinstance(data, list) else 0,
            "size": os.path.getsize(path),
            "updated_at": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return out


def load_cases(name=DEFAULT_CASE_FILE):
    """用例集支持两种顶层结构：裸数组，或 {name, description, cases:[...]}。"""
    path = os.path.join(CASES_DIR, name)
    data = _read_json(path, [])
    if isinstance(data, dict):
        data = data.get("cases") or []
    return data if isinstance(data, list) else []


def save_cases(cases, name=DEFAULT_CASE_FILE):
    path = os.path.join(CASES_DIR, name)
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    _write_json(path, cases)
    return len(cases)


def upsert_cases(new_cases, name=DEFAULT_CASE_FILE):
    existing = load_cases(name)
    index = {case.get("id"): i for i, case in enumerate(existing)}
    added = updated = 0
    for case in new_cases:
        cid = case.get("id")
        if cid in index:
            existing[index[cid]] = case
            updated += 1
        else:
            existing.append(case)
            index[cid] = len(existing) - 1
            added += 1
    save_cases(existing, name)
    return {"added": added, "updated": updated, "total": len(existing)}


# ---------------------------------------------------------------- 配置

def load_config():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    stored = _read_json(CONFIG_PATH, {})
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def save_config(config):
    _write_json(CONFIG_PATH, config)
    return config


# ---------------------------------------------------------------- 运行记录删除

def delete_run(run_id):
    """删除单条运行记录，返回是否删除成功。"""
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        return False
    path = os.path.join(RUNS_DIR, run_id + ".json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def delete_all_runs():
    """清空全部运行记录，返回删除条数。"""
    _ensure_dirs()
    count = 0
    for name in os.listdir(RUNS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            os.remove(os.path.join(RUNS_DIR, name))
            count += 1
        except OSError:
            pass
    return count


def runs_disk_usage():
    """粗略统计运行记录占用空间（字节）。"""
    _ensure_dirs()
    total = 0
    for name in os.listdir(RUNS_DIR):
        if name.endswith(".json"):
            try:
                total += os.path.getsize(os.path.join(RUNS_DIR, name))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- 目标注册

ANSWER_KEYS = ("answer", "response", "reply", "content", "text", "message", "output", "result", "data")
TRACE_KEYS = ("trace", "steps", "intermediate_steps", "intermediateSteps", "events",
              "actions", "tool_calls", "toolCalls", "thoughts", "spans")
CITATION_KEYS = ("citations", "sources", "references", "contexts", "documents",
                 "retrieved_documents", "retrievedDocuments", "chunks")


def guess_response_map(raw):
    """从一次真实响应里猜字段映射，用猜测值给向导做默认选项。"""
    if not isinstance(raw, dict):
        return {"answer": "", "trace": "", "citations": "", "paths": [], "candidates": {}}

    # OpenAI 兼容格式：回答藏在 choices.0.message.content
    choices = raw.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        head = choices[0]
        if isinstance(head.get("message"), dict):
            guess = {
                "answer": "choices.0.message.content",
                "trace": "choices.0.message.tool_calls" if head["message"].get("tool_calls") else "",
                "citations": "",
            }
        elif head.get("text") is not None:
            guess = {"answer": "choices.0.text", "trace": "", "citations": ""}
        else:
            guess = {"answer": "choices.0", "trace": "", "citations": ""}
        guess["paths"] = _flatten_paths(raw)
        guess["candidates"] = {"answer": guess["paths"], "trace": guess["paths"],
                               "citations": guess["paths"]}
        return guess

    def pick(keys):
        lowered = {str(k).lower(): k for k in raw.keys()}
        for want in keys:
            if want.lower() in lowered:
                return lowered[want.lower()]
        for key in raw.keys():
            for want in keys:
                if want.lower() in str(key).lower():
                    return key
        return ""

    answer = pick(ANSWER_KEYS)
    if not answer:
        best = ""
        for key, value in raw.items():
            if isinstance(value, str) and len(value) > len(str(raw.get(best) or "")):
                best = key
        answer = best

    return {
        "answer": answer,
        "trace": pick(TRACE_KEYS),
        "citations": pick(CITATION_KEYS),
        "paths": _flatten_paths(raw),
        "candidates": {
            "answer": _flatten_paths(raw),
            "trace": _flatten_paths(raw),
            "citations": _flatten_paths(raw),
        },
    }


def _flatten_paths(obj, prefix="", depth=0, limit=80):
    """把响应展开成 a.b.0.c 形式的候选路径，供字段映射下拉选择。"""
    out = []
    if depth > 4 or len(out) >= limit:
        return out
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = "%s.%s" % (prefix, key) if prefix else str(key)
            out.append(path)
            if isinstance(value, (dict, list)):
                out.extend(_flatten_paths(value, path, depth + 1, limit))
    elif isinstance(obj, list) and obj:
        path = "%s.0" % prefix if prefix else "0"
        out.append(path)
        out.extend(_flatten_paths(obj[0], path, depth + 1, limit))
    return out[:limit]


def upsert_target(target):
    """新增或更新被测目标，返回最新配置。"""
    config = load_config()
    targets = config.get("targets") or []
    tid = target.get("id")
    for index, item in enumerate(targets):
        if item.get("id") == tid:
            targets[index] = target
            break
    else:
        targets.append(target)
    config["targets"] = targets
    save_config(config)
    return target


# ---------------------------------------------------------------- 运行记录

def new_run_id():
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def save_run(run):
    run_id = run.get("run_id") or new_run_id()
    run["run_id"] = run_id
    _write_json(os.path.join(RUNS_DIR, run_id + ".json"), run)
    return run_id


def load_run(run_id):
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")
    path = os.path.join(RUNS_DIR, safe + ".json")
    return _read_json(path, None)


def list_runs(limit=30):
    _ensure_dirs()
    out = []
    for run in iter_runs(limit):
        summary = run.get("summary") or {}
        target = run.get("target") or {}
        out.append({
            "run_id": run.get("run_id"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "target": target.get("name"),
            "target_id": target.get("id"),
            "case_file": run.get("case_file"),
            "fault_profile": target.get("actual_fault_profile") or target.get("expected_fault_profile"),
            "engine": target.get("engine"),
            "total": summary.get("total"),
            "passed": summary.get("passed"),
            "flaky": summary.get("flaky"),
            "pass_rate": summary.get("pass_rate"),
            "score": summary.get("score"),
        })
    return out


def iter_runs(limit=None):
    """按时间倒序遍历运行记录（跳过损坏文件）。"""
    _ensure_dirs()
    files = sorted((name for name in os.listdir(RUNS_DIR) if name.endswith(".json")), reverse=True)
    if limit:
        files = files[:limit]
    for name in files:
        run = _read_json(os.path.join(RUNS_DIR, name), None)
        if isinstance(run, dict) and run.get("run_id"):
            yield run


def find_previous_run(target_id, case_file=None, exclude_run_id=None, before_run_id=None):
    """找同目标、同用例集的最近一次**更早**运行，作为回归基线。

    run_id 形如 YYYYMMDD-HHMMSS-xxxx，可直接按字符串定序，
    避免把"更新的运行"误当基线（那样对比方向就反了）。
    """
    for run in iter_runs():
        run_id = run.get("run_id")
        if exclude_run_id and run_id == exclude_run_id:
            continue
        if before_run_id and run_id >= before_run_id:
            continue
        target = run.get("target") or {}
        if target_id and target.get("id") != target_id:
            continue
        if case_file and run.get("case_file") != case_file:
            continue
        return run
    return None
