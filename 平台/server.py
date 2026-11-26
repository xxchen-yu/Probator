"""AgentTestHub 平台服务（FastAPI）。

启动：
    python server.py            # 默认 http://127.0.0.1:8500

职责：用例集管理、目标健康探测、异步执行、进度推送、报告与运行记录读取。
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from core import calibration, diff, metrics, runner, store
from core.adapters import AdapterError, _dig, build_adapter

app = FastAPI(title="AgentTestHub", version="1.0", description="AI 智能体质量保障平台")

WEB_DIR = os.path.join(BASE_DIR, "web")
if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ---------------------------------------------------------------- 页面

@app.get("/", response_class=HTMLResponse)
def index():
    path = os.path.join(WEB_DIR, "index.html")
    if not os.path.exists(path):
        return HTMLResponse("<h1>前端未就绪</h1>", status_code=500)
    with open(path, "r", encoding="utf-8") as file:
        # 页面单文件、无构建步骤，必须禁用缓存，否则改了前端刷新看不到
        return HTMLResponse(
            file.read(),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )


# ---------------------------------------------------------------- 元信息

@app.get("/api/meta")
def get_meta():
    config = store.load_config()
    store.apply_llm_config(config)
    return {
        "dimensions": [
            {"key": key, "label": value["label"], "weight": value["weight"]}
            for key, value in metrics.DIMENSIONS.items()
        ],
        "layers": metrics.LAYER_LABELS,
        "case_files": store.list_case_files(),
        "targets": config.get("targets") or [],
        "runner": config.get("runner") or {},
        "judge_enabled": bool((config.get("judge") or {}).get("enabled")),
        "llm": store.public_llm_config(),
    }


# ---------------------------------------------------------------- 用例集

@app.get("/api/cases")
def get_cases(file: str = Query(default=store.DEFAULT_CASE_FILE)):
    cases = store.load_cases(file)
    return {"file": file, "cases": cases, "count": len(cases)}


@app.post("/api/cases")
def post_cases(payload: Dict[str, Any] = Body(...)):
    cases = payload.get("cases") or []
    file = payload.get("file") or store.DEFAULT_CASE_FILE
    if not isinstance(cases, list):
        raise HTTPException(status_code=400, detail="cases 必须是数组")
    total = store.save_cases(cases, file)
    return {"saved": total, "file": file}


@app.post("/api/cases/upsert")
def post_cases_upsert(payload: Dict[str, Any] = Body(...)):
    result = store.upsert_cases(payload.get("cases") or [], payload.get("file") or store.DEFAULT_CASE_FILE)
    return result


@app.post("/api/cases/generate")
def generate_cases_api(payload: Dict[str, Any] = Body(default={})):
    """AI 生成用例：返回通过校验的用例与被拒清单，不落盘。"""
    from core import generator

    payload = payload or {}
    config = store.load_config()
    store.apply_llm_config(config)

    targets = config.get("targets") or []
    target_id = payload.get("target_id")
    target = next((t for t in targets if t.get("id") == target_id),
                  targets[0] if targets else None)

    existing = store.load_cases(payload.get("case_file") or store.DEFAULT_CASE_FILE)

    # 目标能力上下文：能拿到 /meta 就带上，拿不到也能生成
    meta = None
    if target:
        try:
            adapter = build_adapter(target)
            base = str(adapter.cfg.get("base_url") or "").rstrip("/")
            status, raw, _, _ = adapter._request(
                base + str(target.get("meta_path") or "/meta"), None, method="GET")
            if status == 200 and raw:
                meta = json.loads(raw)
        except Exception:  # noqa: BLE001
            meta = None

    dimensions = payload.get("dimensions") or ["functional"]
    try:
        accepted, rejected, _ = generator.generate_cases(
            meta, existing, config,
            count=int(payload.get("count") or 10),
            dimensions=dimensions,
            focus=payload.get("focus") or None,
            prefix=payload.get("prefix") or "G",
        )
    except generator.GeneratorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "accepted": accepted,
        "rejected": rejected,
        "count": len(accepted),
        "rejected_count": len(rejected),
        "has_meta": bool(meta),
        "target": (target or {}).get("name"),
    }


# ---------------------------------------------------------------- 配置与目标

@app.get("/api/config")
def get_config():
    config = store.load_config()
    safe = dict(config)
    judge = dict(config.get("judge") or {})
    judge["has_key"] = bool(
        judge.get("api_key") or os.getenv(judge.get("api_key_env") or "JUDGE_API_KEY")
        or os.getenv("MODEL_API_KEY")
    )
    safe["judge"] = judge
    return safe


@app.post("/api/config")
def post_config(payload: Dict[str, Any] = Body(...)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="配置必须是对象")
    return store.save_config(payload)


# ---------------------------------------------------------------- AI 模型设置

@app.get("/api/config/llm")
def get_llm_config():
    return store.public_llm_config()


@app.post("/api/config/llm")
def set_llm_config(payload: Dict[str, Any] = Body(...)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是对象")
    saved = store.save_secrets(payload)
    return {"saved": True, **store.public_llm_config()}


@app.post("/api/config/llm/test")
def test_llm_config(payload: Dict[str, Any] = Body(default={})):
    """用当前（或临时传入的）配置发一次最小请求，验证地址与密钥可用。"""
    import time as _time
    import urllib.error
    import urllib.request

    secrets = store.load_secrets()
    api_url = store._normalize_chat_url((payload or {}).get("api_url") or secrets.get("api_url"))
    model = (payload or {}).get("model") or secrets.get("model")
    api_key = (payload or {}).get("api_key") or secrets.get("api_key") \
        or os.getenv("JUDGE_API_KEY") or os.getenv("MODEL_API_KEY")
    if not api_key:
        return {"ok": False, "error": "未填写 API Key"}

    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "只回复两个字：可用"}],
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url, data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    started = _time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
        latency = round((_time.perf_counter() - started) * 1000, 2)
        try:
            parsed = json.loads(raw)
            reply = (parsed.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except ValueError:
            reply = ""
        return {"ok": True, "latency_ms": latency, "model": model,
                "api_url": api_url, "reply": (reply or "").strip()[:40]}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "status_code": exc.code,
                "error": "接口返回 %s：%s" % (exc.code, detail)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"ok": False, "error": "连接失败：%s" % reason}


def _target_headers(payload):
    """保存目标时的请求头：显式传入优先，OpenAI 兼容接口可复用已保存的密钥。"""
    headers = dict((payload or {}).get("headers") or {})
    if (payload or {}).get("use_llm_key") and not any(k.lower() == "authorization" for k in headers):
        saved = (store.load_secrets().get("api_key")
                 or os.getenv("JUDGE_API_KEY") or os.getenv("MODEL_API_KEY"))
        if saved:
            headers["Authorization"] = "Bearer " + saved
    return headers


def _apply_preset(payload, headers):
    """按接口类型生成请求模板：OpenAI 兼容接口的请求体与自定义接口完全不同。"""
    preset = str(payload.get("preset") or "").strip().lower()
    if preset != "openai":
        return (payload.get("request_template")
                or {"message": "{message}", "session_id": "{session_id}"})
    secrets = store.load_secrets()
    model = str(payload.get("model") or secrets.get("model") or "deepseek-chat")
    return {
        "model": model,
        "messages": [{"role": "user", "content": "{message}"}],
        "stream": False,
        "temperature": 0.2,
    }


@app.post("/api/targets/probe")
def probe_target(payload: Dict[str, Any] = Body(default={})):
    """接入向导：用一条真实消息探测目标，回传原始响应与猜测的字段映射。"""
    payload = payload or {}
    base_url = str(payload.get("base_url") or "").strip()
    chat_path = str(payload.get("chat_path") or "/chat").strip()
    if not base_url:
        return {"ok": False, "error": "请填写接口地址"}

    headers = dict(payload.get("headers") or {})
    # OpenAI 兼容接口：允许直接复用「AI 模型设置」里保存的密钥，省得再填一遍
    if payload.get("use_llm_key") and not any(k.lower() == "authorization" for k in headers):
        saved = (store.load_secrets().get("api_key")
                 or os.getenv("JUDGE_API_KEY") or os.getenv("MODEL_API_KEY"))
        if saved:
            headers["Authorization"] = "Bearer " + saved

    temp = {
        "id": "probe",
        "name": "probe",
        "type": "http_json",
        "base_url": base_url,
        "chat_path": chat_path,
        "timeout": float(payload.get("timeout") or 60),
        "headers": headers,
        "request_template": _apply_preset(payload, headers),
        "response_map": payload.get("response_map") or {},
        "capabilities": payload.get("capabilities") or {},
    }
    try:
        adapter = build_adapter(temp)
        resp = adapter.chat(payload.get("message") or "你好，请简单介绍一下你自己。", "probe-target")
    except AdapterError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}

    raw = resp.get("raw") or {}
    if not isinstance(raw, dict):
        raw = {"_raw": raw}
    guess = store.guess_response_map(raw)
    # 用猜出来的路径回取回答，避免默认映射取不到值
    answer_text = _dig(raw, guess.get("answer"), "") if guess.get("answer") else resp.get("answer")
    if isinstance(answer_text, (dict, list)):
        answer_text = json.dumps(answer_text, ensure_ascii=False)
    preview = str(raw)
    return {
        "ok": bool(resp.get("status_code") == 200 and raw),
        "status_code": resp.get("status_code"),
        "latency_ms": resp.get("latency_ms"),
        "error": resp.get("error"),
        "answer": str(answer_text or "")[:400],
        "trace_count": len(resp.get("trace") or []),
        "citation_count": len(resp.get("citations") or []),
        "raw_keys": list(raw.keys()),
        "paths": guess.get("paths") or list(raw.keys()),
        "raw_preview": preview[:1600],
        "guess": guess,
    }


@app.post("/api/targets")
def create_target(payload: Dict[str, Any] = Body(default={})):
    """保存新接入的目标。"""
    payload = payload or {}
    base_url = str(payload.get("base_url") or "").strip()
    chat_path = str(payload.get("chat_path") or "/chat").strip()
    mapping = payload.get("response_map") or {}
    answer_field = str(mapping.get("answer") or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="缺少接口地址")
    if not answer_field:
        raise HTTPException(status_code=400, detail="必须指定哪个字段是回答内容")

    tid = str(payload.get("id") or "").strip()
    if not tid:
        tid = "custom-" + uuid.uuid4().hex[:6]

    trace_field = str(mapping.get("trace") or "").strip()
    citation_field = str(mapping.get("citations") or "").strip()
    trace_fields = payload.get("trace_fields") or []

    target = {
        "id": tid,
        "name": str(payload.get("name") or tid).strip(),
        "type": "http_json",
        "base_url": base_url.rstrip("/"),
        "chat_path": chat_path if chat_path.startswith("/") else "/" + chat_path,
        "timeout": float(payload.get("timeout") or 60),
        "headers": _target_headers(payload),
        "request_template": _apply_preset(payload, payload.get("headers") or {}),
        "response_map": {
            "answer": answer_field,
            "trace": trace_field,
            "citations": citation_field,
            "latency_ms": str(mapping.get("latency_ms") or ""),
            "engine": str(mapping.get("engine") or ""),
            "fault_profile": str(mapping.get("fault_profile") or ""),
        },
        "trace_normalizer": payload.get("trace_normalizer") or "",
        "stream_mode": str(payload.get("stream_mode") or ""),
        "sse_map": payload.get("sse_map") or {},
        # 用户能选到 trace / 引用 字段，才认为具备相应能力
        "capabilities": {
            "trace": bool(trace_field) or bool(trace_fields) or bool(payload.get("stream_mode")),
            "citations": bool(citation_field),
            "tools": bool(trace_field),
        },
        "expected_fault_profile": "unknown",
    }
    if trace_fields:
        target["trace_fields"] = trace_fields

    health_path = str(payload.get("health_path") or "").strip()
    if health_path:
        target["health_path"] = health_path
    elif str(payload.get("preset") or "").lower() == "openai":
        # OpenAI 兼容服务通常没有 /health，显式声明"不探测"，避免被误判成离线
        target["health_path"] = ""

    store.upsert_target(target)
    return {"saved": True, "target": target}


@app.get("/api/targets/health")
def target_health(target_id: Optional[str] = Query(default=None)):
    config = store.load_config()
    targets = config.get("targets") or []
    target = next((t for t in targets if t.get("id") == target_id), targets[0] if targets else None)
    if not target:
        raise HTTPException(status_code=404, detail="未配置被测目标")
    try:
        adapter = build_adapter(target)
        health = adapter.health()
    except AdapterError as exc:
        return {"target": target.get("name"), "reachable": False, "error": str(exc)}
    return {"target": target.get("name"), "id": target.get("id"), **health}


# ---------------------------------------------------------------- 执行

@app.post("/api/run")
def post_run(payload: Dict[str, Any] = Body(default={})):
    config = store.load_config()
    store.apply_llm_config(config)
    targets = config.get("targets") or []
    target_id = payload.get("target_id")
    target = next((t for t in targets if t.get("id") == target_id), targets[0] if targets else None)
    if not target:
        raise HTTPException(status_code=400, detail="未配置被测目标")

    file = payload.get("case_file") or store.DEFAULT_CASE_FILE
    cases = store.load_cases(file)
    if payload.get("case_ids"):
        wanted = set(payload["case_ids"])
        cases = [c for c in cases if c.get("id") in wanted]
    if payload.get("dimensions"):
        wanted_dims = set(payload["dimensions"])
        cases = [c for c in cases if c.get("dimension") in wanted_dims]
    if not cases:
        raise HTTPException(status_code=400, detail="没有可执行的用例")

    if payload.get("concurrency"):
        config.setdefault("runner", {})["concurrency"] = int(payload["concurrency"])
    if payload.get("samples"):
        config.setdefault("runner", {})["samples"] = int(payload["samples"])

    use_judge = payload.get("use_judge")
    if use_judge is None:
        use_judge = bool((config.get("judge") or {}).get("enabled"))

    run_id, progress = runner.start_background_run(target, cases, config,
                                                   use_judge=bool(use_judge), case_file=file)
    return {"run_id": run_id, "total": len(cases), "use_judge": bool(use_judge)}


@app.get("/api/run/{run_id}/progress")
def run_progress(run_id: str):
    progress = runner.get_progress(run_id)
    if not progress:
        run = store.load_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="运行记录不存在")
        return {"run_id": run_id, "status": "finished", "done": run["summary"]["total"],
                "total": run["summary"]["total"], "summary": run["summary"]}
    return progress


# ---------------------------------------------------------------- 变异体校准

CALIBRATION = {}


@app.get("/api/calibrate/profiles")
def calibration_profiles():
    return {"profiles": calibration.PROFILE_SPECS}


@app.post("/api/calibrate")
def start_calibration(payload: Dict[str, Any] = Body(default={})):
    """依次跑完所有故障模式，产出检出率 / 误报率矩阵。"""
    import threading

    payload = payload or {}
    config = store.load_config()
    store.apply_llm_config(config)

    targets = config.get("targets") or []
    target_id = payload.get("target_id")
    target = next((t for t in targets if t.get("id") == target_id),
                  targets[0] if targets else None)
    if not target:
        raise HTTPException(status_code=404, detail="未配置被测目标")

    case_file = payload.get("case_file") or store.DEFAULT_CASE_FILE
    cases = store.load_cases(case_file)
    if not cases:
        raise HTTPException(status_code=400, detail="用例集为空")

    limit = payload.get("limit")
    if limit:
        try:
            cases = calibration.stratified_sample(cases, max(1, int(limit)))
        except (TypeError, ValueError):
            pass

    profiles = payload.get("profiles") or None
    calib_id = "calib-" + uuid.uuid4().hex[:8]
    progress = {"status": "starting", "done": 0, "total": len(calibration.PROFILE_SPECS),
                "rows": [], "result": None, "current": ""}
    CALIBRATION[calib_id] = progress

    def worker():
        try:
            calibration.run_calibration(target, cases, config, profiles=profiles,
                                        progress=progress, run_id=calib_id)
        except Exception as exc:  # noqa: BLE001
            progress["status"] = "failed"
            progress["error"] = "%s: %s" % (type(exc).__name__, exc)

    threading.Thread(target=worker, daemon=True).start()
    return {"calibration_id": calib_id, "cases": len(cases),
            "profiles": len(calibration.PROFILE_SPECS)}


@app.get("/api/calibrate/latest")
def calibration_latest():
    """返回最近一次落盘的校准结果，供界面回看。"""
    snapshot = calibration.latest_snapshot()
    if not snapshot:
        raise HTTPException(status_code=404, detail="还没有校准记录")

    result = snapshot.get("result") or {}
    return {
        "status": result and "finished" or snapshot.get("status"),
        "rows": result.get("rows") or snapshot.get("rows") or [],
        "summary": result.get("summary") or {},
        "target": snapshot.get("target"),
        "case_total": snapshot.get("case_total"),
        "done": snapshot.get("done"),
        "total": snapshot.get("total"),
        "restored": True,
    }


@app.get("/api/calibrate/{calib_id}/progress")
def calibration_progress(calib_id: str):
    """先查内存，再查落盘快照——服务重启后仍能读到上次结果。"""
    state = CALIBRATION.get(calib_id)
    if state:
        return state

    snapshot = calibration.load_snapshot(calib_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="校准任务不存在")
    snapshot["restored"] = True
    if snapshot.get("status") == "running":
        snapshot["status"] = "interrupted"
        snapshot["error"] = "服务重启导致校准中断，已完成 %d/%d 个变异体" % (
            snapshot.get("done") or 0, snapshot.get("total") or 0)
    return snapshot


@app.get("/api/runs")
def get_runs(limit: int = Query(default=30, ge=1, le=200)):
    return {
        "runs": store.list_runs(limit),
        "disk_bytes": store.runs_disk_usage(),
    }


@app.delete("/api/runs")
def clear_runs():
    """清空全部运行记录。"""
    removed = store.delete_all_runs()
    return {"removed": removed}


@app.delete("/api/runs/{run_id}")
def remove_run(run_id: str):
    ok = store.delete_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"removed": 1, "run_id": run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


@app.get("/api/runs/{run_id}/markdown", response_class=PlainTextResponse)
def get_run_markdown(run_id: str):
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return metrics.to_markdown(run)


@app.get("/api/runs/{run_id}/advice")
def get_run_advice(run_id: str):
    """读取已生成的改进方案（不触发模型调用）。"""
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    from core import advisor

    cached = advisor.load_advice(store.RUNS_DIR, run_id)
    if cached is None:
        return {"cached": False, "advice": [], "rejected": [], "evidence_count": 0}
    payload = dict(cached)
    payload["cached"] = True
    return payload


@app.post("/api/runs/{run_id}/advice")
def post_run_advice(run_id: str, payload: Dict[str, Any] = Body(default={})):
    """基于该次运行的失败证据生成改进方案，结果落盘缓存。"""
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")

    from core import advisor
    from core.llm import LLMError

    payload = payload or {}
    if not payload.get("force"):
        cached = advisor.load_advice(store.RUNS_DIR, run_id)
        if cached is not None:
            cached["cached"] = True
            return cached

    config = store.load_config()
    store.apply_llm_config(config)
    try:
        data = advisor.generate_advice(run, config,
                                       max_items=int(payload.get("max_items") or 6))
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    advisor.save_advice(store.RUNS_DIR, run_id, data)
    result = dict(data)
    result["cached"] = False
    return result


@app.get("/api/runs/{run_id}/diff")
def get_run_diff(run_id: str, base: Optional[str] = Query(default=None)):
    """与基线运行对比。base 省略时自动取同目标、同用例集的上一轮。"""
    head = store.load_run(run_id)
    if not head:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    target_id = (head.get("target") or {}).get("id")
    if base:
        base_run = store.load_run(base)
        if not base_run:
            raise HTTPException(status_code=404, detail="基线运行不存在")
    else:
        base_run = store.find_previous_run(target_id, head.get("case_file"), exclude_run_id=run_id, before_run_id=run_id)
    if not base_run:
        return {"available": False, "reason": "没有可对比的历史运行"}
    result = diff.compare(base_run, head)
    result["available"] = True
    return result


@app.get("/api/runs/{run_id}/diff/markdown", response_class=PlainTextResponse)
def get_run_diff_markdown(run_id: str, base: Optional[str] = Query(default=None)):
    head = store.load_run(run_id)
    if not head:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    base_run = store.load_run(base) if base else store.find_previous_run(
        (head.get("target") or {}).get("id"), head.get("case_file"), exclude_run_id=run_id, before_run_id=run_id)
    if not base_run:
        return "没有可对比的历史运行"
    return diff.render_markdown(diff.compare(base_run, head))


@app.get("/api/dimensions")
def get_dimensions():
    return {"dimensions": [{"key": k, "label": v["label"], "weight": v["weight"]}
                           for k, v in metrics.DIMENSIONS.items()]}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PLATFORM_PORT", "8500"))
    print("AgentTestHub 平台：http://127.0.0.1:%d" % port)
    print("被测目标：%s" % ", ".join(t.get("name", "") for t in store.load_config().get("targets") or []))
    uvicorn.run(app, host=os.getenv("PLATFORM_HOST", "127.0.0.1"), port=port, log_level="info")



















