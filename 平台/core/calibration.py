"""变异体校准：用注入缺陷的智能体验证判定器本身。

思路来自传统软件测试的变异测试（mutation testing）：造一批「只坏一个点」的
被测对象，用固定用例集去测，测量两个指标——

    检出（caught）        该坏的用例确实失败了
    误报（false_positive） 不该受影响的用例也被判失败了

只有同时做到「该抓的抓到、不该抓的没抓」，判定结果才可信。这也是本平台
回答「你的测试工具凭什么准」的唯一方式。
"""

from __future__ import annotations

import copy
import json
import os
import uuid

from . import runner, store

# 每个变异体只破坏一个能力，因此只应当影响特定维度
PROFILE_SPECS = [
    {
        "id": "clean",
        "label": "干净基线",
        "desc": "无故障注入，作为对照基准",
        "expect": [],
        "baseline": True,
    },
    {
        "id": "misroute",
        "label": "工具误路由",
        "desc": "把请求路由到相邻的错误工具",
        "expect": ["tool_invocation", "functional"],
    },
    {
        "id": "halluc_param",
        "label": "参数幻觉",
        "desc": "工具参数被编造（周次、房间号等）",
        "expect": ["tool_invocation", "functional"],
    },
    {
        "id": "rag_infidelity",
        "label": "检索失真",
        "desc": "命中知识库中已废止的旧版条款",
        "expect": ["rag", "functional"],
    },
    {
        "id": "memory_decay",
        "label": "记忆衰减",
        "desc": "多轮会话中丢失上文关键信息",
        "expect": ["multiturn"],
    },
    {
        "id": "weak_guard",
        "label": "护栏失守",
        "desc": "越权与套取提示词等请求未被拦截",
        "expect": ["safety"],
    },
]

PROFILE_INDEX = {p["id"]: p for p in PROFILE_SPECS}


def _variant_target(base_target, profile):
    """复制一份目标配置，把故障模式注入请求体。"""
    target = copy.deepcopy(base_target)
    template = copy.deepcopy(target.get("request_template") or {})
    if not template:
        template = {"message": "{message}", "session_id": "{session_id}"}
    template["fault_profile"] = profile
    target["request_template"] = template
    target["id"] = "%s__%s" % (base_target.get("id"), profile)
    target["name"] = "%s · %s" % (base_target.get("name"), profile)
    return target


def _failed_ids(run):
    return {
        r.get("id") for r in (run.get("results") or [])
        if not r.get("passed")
    }


def _dimension_map(cases):
    return {c.get("id"): (c.get("dimension") or "functional") for c in (cases or [])}


def stratified_sample(cases, limit):
    """按维度轮转取样，保证小样本也覆盖全部维度。

    直接截取前 N 条会集中在一两个维度上，校准结果就没有意义。
    """
    cases = list(cases or [])
    if not limit or limit >= len(cases):
        return cases

    buckets = {}
    for c in cases:
        buckets.setdefault(c.get("dimension") or "functional", []).append(c)

    order = ["tool_invocation", "functional", "rag", "multiturn",
             "safety", "boundary", "performance"]
    keys = [k for k in order if k in buckets] + \
           [k for k in buckets if k not in order]

    picked, i = [], 0
    while len(picked) < limit:
        progressed = False
        for k in keys:
            if i < len(buckets[k]):
                picked.append(buckets[k][i])
                progressed = True
                if len(picked) >= limit:
                    break
        if not progressed:
            break
        i += 1
    return picked


CALIB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "calibrations")


def _save_snapshot(run_id, payload):
    """每完成一个变异体就落盘一次，服务重启也不丢进度。"""
    try:
        if not os.path.isdir(CALIB_DIR):
            os.makedirs(CALIB_DIR)
        path = os.path.join(CALIB_DIR, "%s.json" % run_id)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_snapshot(run_id):
    path = os.path.join(CALIB_DIR, "%s.json" % run_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError:
        return None


def latest_snapshot():
    """取最近一次落盘的校准结果（按文件修改时间）。"""
    if not os.path.isdir(CALIB_DIR):
        return None
    files = [name for name in os.listdir(CALIB_DIR) if name.endswith(".json")]
    if not files:
        return None
    newest = max((os.path.join(CALIB_DIR, name) for name in files), key=os.path.getmtime)
    return load_snapshot(os.path.basename(newest)[:-5])


def run_calibration(target_cfg, cases, config, profiles=None, samples=1,
                    progress=None, run_id=None):
    """依次跑完全部变异体，返回校准矩阵。

    progress: 可选字典，用于向前端推送进度。
    """
    run_id = run_id or ("calib-" + uuid.uuid4().hex[:8])
    wanted = [p for p in PROFILE_SPECS
              if (not profiles or p["id"] in profiles)]
    dims = _dimension_map(cases)

    if progress is not None:
        progress.update({
            "run_id": run_id, "total": len(wanted), "done": 0,
            "status": "running", "current": "", "rows": [],
        })

    rows = []
    baseline_failed = set()

    for index, spec in enumerate(wanted):
        pid = spec["id"]
        if progress is not None:
            progress["current"] = spec["label"]

        variant = _variant_target(target_cfg, pid)
        run = runner.run_suite(
            variant, cases, config, use_judge=False,
            case_file=None, persist=False,
        )

        failed = _failed_ids(run)
        summary = run.get("summary") or {}

        if spec.get("baseline"):
            baseline_failed = failed

        # 归因：只在「基线通过、变异体失败」时才算被检出
        caught = sorted(failed - baseline_failed) if baseline_failed else sorted(failed)
        expect = spec.get("expect") or []

        expected_hit, false_positive = [], []
        for cid in caught:
            dim = dims.get(cid, "functional")
            if expect and dim in expect:
                expected_hit.append(cid)
            elif expect:
                false_positive.append(cid)
            else:
                expected_hit.append(cid)   # 基线本身不计归因

        # 未受影响：属于该能力覆盖范围、但变异体下仍通过的用例（仅作参考，不判为漏检）
        untouched = []
        if expect and baseline_failed is not None:
            for cid, dim in dims.items():
                if dim in expect and cid not in failed:
                    untouched.append(cid)

        rows.append({
            "profile": pid,
            "label": spec["label"],
            "desc": spec["desc"],
            "score": summary.get("score"),
            "passed": summary.get("passed"),
            "total": summary.get("total"),
            "checks_passed": summary.get("checks_passed"),
            "checks_total": summary.get("checks_total"),
            "caught": caught,
            "caught_count": len(caught),
            "expected_hit": expected_hit,
            "false_positive": false_positive,
            "fp_count": len(false_positive),
            "untouched": untouched,
            "untouched_count": len(untouched),
            "blockers": len([c for c in (run.get("summary") or {}).get("defects") or []
                             if c.get("severity") == "blocker"]),
        })

        _save_snapshot(run_id, {
            "run_id": run_id,
            "status": "running",
            "target": (target_cfg or {}).get("name"),
            "case_total": len(cases or []),
            "total": len(wanted),
            "done": index + 1,
            "current": spec["label"],
            "rows": rows,
        })

        if progress is not None:
            progress["rows"] = rows
            progress["done"] = index + 1

    # 汇总指标（不含基线）
    mutants = [r for r in rows if r["profile"] != "clean"]
    total_caught = sum(r["caught_count"] for r in mutants)
    total_fp = sum(r["fp_count"] for r in mutants)
    precise = [r for r in mutants if r["caught_count"] > 0 and r["fp_count"] == 0]

    result = {
        "calibration_id": run_id,
        "target": (target_cfg or {}).get("name"),
        "case_total": len(cases or []),
        "rows": rows,
        "summary": {
            "mutants": len(mutants),
            "caught_total": total_caught,
            "false_positive_total": total_fp,
            "zero_fp_mutants": len(precise),
            "zero_fp_profiles": [r["profile"] for r in precise],
            "mean_score": round(
                sum((r["score"] or 0) for r in mutants) / len(mutants), 2
            ) if mutants else 0,
        },
    }

    _save_snapshot(run_id, {
        "run_id": run_id,
        "status": "finished",
        "target": result.get("target"),
        "case_total": result.get("case_total"),
        "total": len(wanted),
        "done": len(wanted),
        "current": "",
        "rows": rows,
        "result": result,
    })

    if progress is not None:
        progress.update({"status": "finished", "result": result})

    return result



