"""指标聚合：维度得分、加权总分、缺陷清单、分层检出统计。"""

from __future__ import annotations

# 维度权重与赛事评分导向一致：越是智能体特有的风险，权重越高
DIMENSIONS = {
    "functional": {"label": "功能准确性", "weight": 0.20},
    "tool_invocation": {"label": "工具调用", "weight": 0.20},
    "rag": {"label": "RAG 检索质量", "weight": 0.15},
    "multiturn": {"label": "多轮对话", "weight": 0.15},
    "safety": {"label": "对抗与安全", "weight": 0.15},
    "boundary": {"label": "边界与异常", "weight": 0.10},
    "performance": {"label": "性能", "weight": 0.05},
}

LAYER_LABELS = {"L1": "规则判定", "L2": "语义判定", "L3": "决策判定"}


def _percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    pos = (len(ordered) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return round(ordered[low] * (1 - frac) + ordered[high] * frac, 2)


def aggregate(results):
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    flaky = [r for r in results if r.get("status") == "flaky"]
    sample_counts = [r.get("samples") or 1 for r in results]
    multi_sampled = any(s > 1 for s in sample_counts)
    stability = (
        round(sum((r.get("stability") if r.get("stability") is not None else (1.0 if r.get("passed") else 0.0))
                  for r in results) / total, 4) if total else 0.0
    )

    by_dimension = {}
    for key, meta in DIMENSIONS.items():
        by_dimension[key] = {
            "key": key, "label": meta["label"], "weight": meta["weight"],
            "total": 0, "passed": 0, "pass_rate": 0.0, "score": 0.0,
        }

    severity = {"blocker": 0, "major": 0, "minor": 0}
    layers = {k: {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0} for k in LAYER_LABELS}
    checks_total = checks_passed = 0
    skipped_total = 0
    latencies = []
    defects = []

    for record in results:
        dim = record.get("dimension") or "functional"
        bucket = by_dimension.setdefault(dim, {
            "key": dim, "label": dim, "weight": 0.0,
            "total": 0, "passed": 0, "pass_rate": 0.0, "score": 0.0,
        })
        bucket["total"] += 1
        if record.get("passed"):
            bucket["passed"] += 1

        response = record.get("response") or {}
        if response.get("latency_ms"):
            try:
                latencies.append(float(response["latency_ms"]))
            except (TypeError, ValueError):
                pass

        for check in record.get("checks") or []:
            if check.get("skipped"):
                skipped_total += 1
                continue
            layer = check.get("layer") or "L1"
            layers.setdefault(layer, {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0})
            layers[layer]["total"] += 1
            if check.get("passed"):
                layers[layer]["passed"] += 1
            else:
                layers[layer]["failed"] += 1
                sev = check.get("severity") or "major"
                severity[sev] = severity.get(sev, 0) + 1
            checks_total += 1
            if check.get("passed"):
                checks_passed += 1

        if not record.get("passed"):
            failed = [c for c in (record.get("checks") or [])
                      if not c.get("passed") and c.get("severity") in ("blocker", "major")]
            defects.append({
                "case_id": record.get("id"),
                "title": record.get("title"),
                "dimension": dim,
                "dimension_label": DIMENSIONS.get(dim, {}).get("label", dim),
                "priority": record.get("priority"),
                "severity": max(
                    [c.get("severity") for c in failed] or ["major"],
                    key=lambda s: {"blocker": 3, "major": 2, "minor": 1}.get(s, 0),
                ),
                "root_cause": record.get("root_cause") or "待定位",
                "failed_checks": [{
                    "id": c.get("id"), "layer": c.get("layer"), "label": c.get("label"),
                    "expected": c.get("expected"), "actual": c.get("actual"),
                    "detail": c.get("detail"),
                } for c in failed][:8],
                "answer": (record.get("response") or {}).get("answer", "")[:400],
            })

    for bucket in by_dimension.values():
        bucket["pass_rate"] = round(bucket["passed"] / bucket["total"] * 100, 2) if bucket["total"] else 0.0
        bucket["score"] = bucket["pass_rate"]

    for layer in layers.values():
        layer["pass_rate"] = round(layer["passed"] / layer["total"] * 100, 2) if layer["total"] else 0.0

    # 加权总分：只统计有用例的维度，权重归一化；最低分维度额外惩罚
    active = [b for b in by_dimension.values() if b["total"]]
    weight_sum = sum(b["weight"] for b in active) or 1.0
    weighted = sum(b["pass_rate"] * b["weight"] for b in active) / weight_sum if active else 0.0
    weakest = min(active, key=lambda b: b["pass_rate"]) if active else None
    if weakest and weakest["pass_rate"] < 60:
        weighted *= 0.9  # 短板惩罚：任何一个关键维度崩了，总分不能虚高

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total * 100, 2) if total else 0.0,
        "score": round(weighted, 2),
        "flaky": len(flaky),
        "flaky_cases": [{
            "case_id": r.get("id"), "title": r.get("title"),
            "dimension": r.get("dimension"),
            "stability": r.get("stability"),
            "pass_count": r.get("pass_count"),
            "samples": r.get("samples"),
            "flaky_checks": r.get("flaky_checks") or [],
        } for r in flaky],
        "stability": stability,
        "multi_sampled": multi_sampled,
        "max_samples": max(sample_counts) if sample_counts else 1,
        "l3_available": layers.get("L3", {}).get("total", 0) > 0,
        "skipped_checks": skipped_total,
        "checks_total": checks_total,
        "checks_passed": checks_passed,
        "check_pass_rate": round(checks_passed / checks_total * 100, 2) if checks_total else 0.0,
        "by_dimension": list(by_dimension.values()),
        "layers": [dict(value, layer=key, label=LAYER_LABELS.get(key, key)) for key, value in layers.items()],
        "severity": severity,
        "latency": {
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "avg": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "samples": len(latencies),
        },
        "weakest_dimension": weakest["label"] if weakest else None,
        "defects": defects,
    }


def to_markdown(run):
    summary = run.get("summary") or {}
    lines = [
        "# 智能体测试报告",
        "",
        "- 被测对象：%s" % (run.get("target") or {}).get("name"),
        "- 运行编号：%s" % run.get("run_id"),
        "- 执行时间：%s ~ %s" % (run.get("started_at"), run.get("finished_at")),
        "- 用例总数：%s，通过 %s，失败 %s" % (summary.get("total"), summary.get("passed"), summary.get("failed")),
        "- 通过率：%s%%，加权质量分：%s" % (summary.get("pass_rate"), summary.get("score")),
        "",
        "## 维度得分",
        "",
        "| 维度 | 权重 | 用例 | 通过 | 通过率 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for bucket in summary.get("by_dimension") or []:
        lines.append("| %s | %.0f%% | %s | %s | %s%% |" % (
            bucket["label"], bucket["weight"] * 100, bucket["total"], bucket["passed"], bucket["pass_rate"]))
    lines.extend(["", "## 缺陷清单", "", "| 用例 | 维度 | 严重级别 | 责任点 | 首个失败断言 |", "| --- | --- | --- | --- | --- |"])
    for defect in summary.get("defects") or []:
        first = (defect.get("failed_checks") or [{}])[0]
        lines.append("| %s | %s | %s | %s | %s |" % (
            defect.get("case_id"), defect.get("dimension_label"), defect.get("severity"),
            defect.get("root_cause"), first.get("label")))
    return "\n".join(lines)
