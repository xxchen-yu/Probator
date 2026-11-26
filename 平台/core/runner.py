"""执行引擎：用例调度、会话隔离、多轮前置、结果聚合。"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from . import judges, store
from .adapters import AdapterError, build_adapter

# 运行态进度表：run_id -> {done,total,status,current,started_at,log[]}
PROGRESS = {}
_LOCK = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_session(run_id, case_id):
    return "%s-%s" % (run_id, str(case_id).replace(" ", "_"))


def _exec_once(adapter, case, session_id, retry):
    """执行一次采样，含失败重试。返回 (normalized_response, attempts, precondition_log)。"""
    attempts = 0
    result = None
    precondition = []
    while attempts <= max(retry, 0):
        attempts += 1
        try:
            adapter.reset(session_id)
            precondition = []
            for turn in (case.get("precondition") or {}).get("turns") or []:
                warmed = adapter.chat(turn, session_id)
                precondition.append({
                    "input": turn,
                    "answer": (warmed.get("answer") or "")[:400],
                    "ok": warmed.get("ok"),
                })
            result = adapter.chat(case.get("input", ""), session_id)
            if result.get("ok") or attempts > max(retry, 0):
                break
        except AdapterError as exc:
            result = {"ok": False, "status_code": 0, "answer": "", "trace": [], "citations": [],
                      "latency_ms": 0, "error": str(exc), "raw": {}}
            if attempts > max(retry, 0):
                break
    if result is None:
        result = {"ok": False, "status_code": 0, "answer": "", "trace": [], "citations": [],
                  "latency_ms": 0, "error": "未获取到响应", "raw": {}}
    return result, attempts, precondition


def _exec_case(adapter, case, run_id, config, use_judge, retry, samples=1):
    """多次采样执行。

    大模型输出非确定性，单次采样得出的通过/失败结论不可用。这里对同一用例
    采样 N 次，输出三态：pass（N 次全过）/ flaky（部分通过）/ fail（全挂）。
    flaky 在真实团队里和 fail 一样阻断发布，但定位方式不同（前者是稳定性问题，
    后者是确定性缺陷），必须分开统计。
    """
    case_id = case.get("id") or uuid.uuid4().hex[:8]
    session_id = _new_session(run_id, case_id)
    started = time.perf_counter()
    samples = max(int(samples or 1), 1)
    record = {
        "id": case_id,
        "title": case.get("title") or case.get("input", "")[:40],
        "dimension": case.get("dimension", "functional"),
        "priority": case.get("priority", "P1"),
        "input": case.get("input", ""),
        "severity": (case.get("meta") or {}).get("severity", "major"),
        "root_cause": (case.get("meta") or {}).get("root_cause"),
        "precondition": [],
        "response": None,
        "checks": [],
        "passed": False,
        "status": "fail",
        "score": 0.0,
        "attempts": 0,
        "samples": samples,
        "pass_count": 0,
        "stability": 0.0,
        "error": None,
        "elapsed_ms": 0.0,
    }

    sample_verdicts = []
    responses = []
    precondition = []
    total_attempts = 0

    for index in range(samples):
        result, attempts, precondition = _exec_once(adapter, case, session_id, retry)
        total_attempts += attempts
        responses.append(result)
        try:
            verdict = judges.evaluate_case(case, result, config, use_judge=use_judge)
        except Exception as exc:  # noqa: BLE001 判定异常不得中断整轮测试
            verdict = {
                "checks": [], "passed": False, "score": 0.0,
                "failed_checks": ["judge_error"], "minor_failures": [],
                "mode": "graybox" if (result.get("trace") or []) else "blackbox",
                "skipped": 0, "error": "判定异常：%s" % exc,
            }
        sample_verdicts.append(verdict)

    pass_count = sum(1 for item in sample_verdicts if item["passed"])
    if pass_count == samples:
        status = "pass"
    elif pass_count == 0:
        status = "fail"
    else:
        status = "flaky"

    # 代表样本：优先取失败样本，便于定位；全过则取第一个
    representative = next((i for i, v in enumerate(sample_verdicts) if not v["passed"]), 0)
    verdict = sample_verdicts[representative]
    resp = responses[representative]

    record["precondition"] = precondition
    record["attempts"] = total_attempts
    record["checks"] = verdict["checks"]
    record["passed"] = status == "pass"
    record["status"] = status
    record["score"] = verdict["score"]
    record["failed_checks"] = verdict["failed_checks"]
    record["minor_failures"] = verdict["minor_failures"]
    record["mode"] = verdict.get("mode")
    record["skipped_checks"] = verdict.get("skipped", 0)
    record["pass_count"] = pass_count
    record["stability"] = round(pass_count / float(samples), 3)
    record["sample_results"] = [
        {"passed": item["passed"], "score": item["score"],
         "failed": item["failed_checks"][:6]}
        for item in sample_verdicts
    ]

    # 跨样本出现的失败断言并集，比单样本更能反映真实缺陷面
    union_failed = []
    for item in sample_verdicts:
        for cid in item["failed_checks"]:
            if cid not in union_failed:
                union_failed.append(cid)
    record["failed_checks_union"] = union_failed
    record["flaky_checks"] = [
        cid for cid in union_failed
        if sum(1 for item in sample_verdicts if cid in item["failed_checks"]) < samples
    ]

    record["response"] = {
        "status_code": resp.get("status_code"),
        "answer": resp.get("answer"),
        "citations": resp.get("citations"),
        "latency_ms": resp.get("latency_ms"),
        "engine": resp.get("engine"),
        "fault_profile": resp.get("fault_profile"),
        "trace": resp.get("trace"),
        "error": resp.get("error"),
    }
    record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return record


def run_suite(target_cfg, cases, config, use_judge=True, progress=None, run_id=None, case_file=None, persist=True):
    """同步执行一轮测试，返回完整 run 记录。"""
    from . import metrics

    run_id = run_id or store.new_run_id()
    runner_cfg = config.get("runner") or {}
    concurrency = max(int(runner_cfg.get("concurrency") or 4), 1)
    retry = max(int(runner_cfg.get("retry") or 0), 0)
    samples = max(int(runner_cfg.get("samples") or 1), 1)
    started_at = _now()
    clock = time.perf_counter()

    adapter = build_adapter(target_cfg)
    health = {}
    try:
        health = adapter.health()
    except Exception as exc:  # noqa: BLE001
        health = {"reachable": False, "error": str(exc)}

    # 能力声明：黑盒目标看不到 trace / 工具事件 / 引用时，相应断言必须跳过而不是判失败
    eval_config = dict(config)
    eval_config["_capabilities"] = target_cfg.get("capabilities") or {}

    if progress is not None:
        progress.update({"run_id": run_id, "total": len(cases), "done": 0, "status": "running",
                         "started_at": started_at, "results": []})

    results = []
    with ThreadPoolExecutor(max_workers=min(concurrency, max(len(cases), 1))) as pool:
        futures = {
            pool.submit(_exec_case, adapter, case, run_id, eval_config, use_judge, retry, samples): case
            for case in cases
        }
        for future in as_completed(futures):
            case = futures[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001
                record = {
                    "id": case.get("id"), "title": case.get("title") or "",
                    "dimension": case.get("dimension", "functional"),
                    "input": case.get("input", ""), "checks": [], "passed": False,
                    "score": 0.0, "error": "执行异常：%s" % exc, "response": None,
                    "elapsed_ms": 0.0, "priority": case.get("priority", "P1"),
                }
            results.append(record)
            if progress is not None:
                progress["done"] = len(results)
                progress["results"].append({
                    "id": record.get("id"), "passed": record.get("passed"),
                    "dimension": record.get("dimension"), "score": record.get("score"),
                })

    order = {case.get("id"): i for i, case in enumerate(cases)}
    results.sort(key=lambda r: order.get(r.get("id"), 9999))

    summary = metrics.aggregate(results)
    has_trace = any(bool((r.get("response") or {}).get("trace")) for r in results)
    summary["mode"] = "graybox" if has_trace else "blackbox"
    summary["mode_label"] = "灰盒（含决策链路）" if has_trace else "黑盒（仅输入输出）"
    run = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": _now(),
        "case_file": case_file,
        "duration_ms": round((time.perf_counter() - clock) * 1000, 2),
        "target": {
            "id": target_cfg.get("id"),
            "name": target_cfg.get("name"),
            "type": target_cfg.get("type"),
            "base_url": target_cfg.get("base_url"),
            "expected_fault_profile": target_cfg.get("expected_fault_profile"),
            "actual_fault_profile": (health.get("raw") or {}).get("fault_profile"),
            "engine": (health.get("raw") or {}).get("engine"),
        },
        "health": health,
        "options": {"concurrency": concurrency, "retry": retry, "samples": samples,
                    "use_judge": bool(use_judge)},
        "summary": summary,
        "results": results,
    }
    if persist:
        store.save_run(run)

    if progress is not None:
        progress.update({"status": "finished", "summary": summary, "run_id": run_id})
    return run


def start_background_run(target_cfg, cases, config, use_judge=True, case_file=None):
    """给 Web 用的异步执行入口，返回 run_id 与进度表引用。"""
    run_id = store.new_run_id()
    progress = {
        "run_id": run_id, "total": len(cases), "done": 0, "status": "starting",
        "started_at": _now(), "results": [], "summary": None,
    }
    with _LOCK:
        PROGRESS[run_id] = progress

    def worker():
        try:
            run = run_suite(target_cfg, cases, config, use_judge=use_judge,
                            progress=progress, run_id=run_id, case_file=case_file)
            progress["run_id"] = run["run_id"]
        except Exception as exc:  # noqa: BLE001
            progress["status"] = "failed"
            progress["error"] = "%s: %s" % (type(exc).__name__, exc)

    thread = threading.Thread(target=worker, name="run-" + run_id, daemon=True)
    thread.start()
    return run_id, progress


def get_progress(run_id):
    return PROGRESS.get(run_id)


