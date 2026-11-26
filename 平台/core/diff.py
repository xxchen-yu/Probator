"""运行对比（run diff）：把两次运行转成一条可执行的回归结论。

回答的问题不是"这次多少分"，而是"比起上次，坏了什么"。
CI 门禁看的就是这个：改完 prompt，哪几条从过变挂、哪几条新变不稳定。
"""

from __future__ import annotations

STATUS_LABELS = {"pass": "通过", "flaky": "不稳定", "fail": "失败"}
_RANK = {"fail": 0, "flaky": 1, "pass": 2}


def status_of(record):
    status = (record or {}).get("status")
    if status in _RANK:
        return status
    return "pass" if (record or {}).get("passed") else "fail"


def _index(run):
    return {(item or {}).get("id"): item for item in ((run or {}).get("results") or [])}


def _brief(record):
    return {
        "case_id": record.get("id"),
        "title": record.get("title"),
        "dimension": record.get("dimension"),
        "priority": record.get("priority"),
        "root_cause": record.get("root_cause"),
        "pass_count": record.get("pass_count"),
        "samples": record.get("samples"),
    }


def _summary_head(run):
    summary = (run or {}).get("summary") or {}
    target = (run or {}).get("target") or {}
    return {
        "run_id": (run or {}).get("run_id"),
        "started_at": (run or {}).get("started_at"),
        "target": target.get("name"),
        "fault_profile": target.get("actual_fault_profile") or target.get("expected_fault_profile"),
        "score": summary.get("score"),
        "pass_rate": summary.get("pass_rate"),
        "passed": summary.get("passed"),
        "total": summary.get("total"),
        "flaky": summary.get("flaky"),
        "stability": summary.get("stability"),
        "checks_pass_rate": summary.get("check_pass_rate"),
        "samples": summary.get("max_samples") or 1,
    }


def compare(base, head):
    """base = 基线运行，head = 本次运行。"""
    if not base or not head:
        raise ValueError("缺少可对比的运行记录")

    b_idx, h_idx = _index(base), _index(head)
    regressed, improved, new_flaky, stabilized, still_flaky = [], [], [], [], []
    added, removed = [], []

    for case_id, head_rec in h_idx.items():
        head_status = status_of(head_rec)
        base_rec = b_idx.get(case_id)

        if base_rec is None:
            added.append(dict(_brief(head_rec), status=head_status))
            continue

        base_status = status_of(base_rec)
        item = dict(_brief(head_rec), base_status=base_status, head_status=head_status)

        if _RANK[head_status] < _RANK[base_status]:
            item["new_failed_checks"] = [
                cid for cid in (head_rec.get("failed_checks") or [])
                if cid not in (base_rec.get("failed_checks") or [])
            ]
            regressed.append(item)
        elif _RANK[head_status] > _RANK[base_status]:
            item["fixed_checks"] = [
                cid for cid in (base_rec.get("failed_checks") or [])
                if cid not in (head_rec.get("failed_checks") or [])
            ]
            if head_status == "flaky":
                new_flaky.append(item)          # 从必挂变成不稳定，仍阻断发布
            elif base_status == "flaky":
                stabilized.append(item)
            else:
                improved.append(item)
        elif head_status == "flaky":
            still_flaky.append(item)

    for case_id, base_rec in b_idx.items():
        if case_id not in h_idx:
            removed.append(dict(_brief(base_rec), status=status_of(base_rec)))

    b_sum, h_sum = _summary_head(base), _summary_head(head)
    warnings = []
    if b_sum["samples"] != h_sum["samples"]:
        warnings.append("两次采样次数不同（%s vs %s），结论仅供参考" % (b_sum["samples"], h_sum["samples"]))
    if b_sum["total"] != h_sum["total"]:
        warnings.append("用例集规模不同（基线 %s 条 / 本次 %s 条）" % (b_sum["total"], h_sum["total"]))
    comparable = not warnings
    blocking = bool(regressed or new_flaky)
    if blocking:
        verdict = "regressed"
    elif improved or stabilized:
        verdict = "improved"
    else:
        verdict = "unchanged"

    dimension_delta = []
    b_dims = {d["key"]: d for d in ((base.get("summary") or {}).get("by_dimension") or [])}
    for item in ((head.get("summary") or {}).get("by_dimension") or []):
        before = b_dims.get(item["key"]) or {}
        if not item.get("total"):
            continue
        delta = round((item.get("pass_rate") or 0) - (before.get("pass_rate") or 0), 2)
        dimension_delta.append({
            "key": item["key"], "label": item["label"],
            "base": before.get("pass_rate"), "head": item.get("pass_rate"),
            "delta": delta, "total": item.get("total"),
        })
    dimension_delta.sort(key=lambda x: x["delta"])

    return {
        "base": b_sum,
        "head": h_sum,
        "comparable": comparable,
        "warning": "；".join(warnings) or None,
        "delta": {
            "score": round((h_sum["score"] or 0) - (b_sum["score"] or 0), 2),
            "pass_rate": round((h_sum["pass_rate"] or 0) - (b_sum["pass_rate"] or 0), 2),
            "passed": (h_sum["passed"] or 0) - (b_sum["passed"] or 0),
            "flaky": (h_sum["flaky"] or 0) - (b_sum["flaky"] or 0),
        },
        "verdict": verdict,
        "blocking": blocking,
        "counts": {
            "regressed": len(regressed), "new_flaky": len(new_flaky),
            "improved": len(improved), "stabilized": len(stabilized),
            "still_flaky": len(still_flaky), "added": len(added), "removed": len(removed),
        },
        "transitions": {
            "regressed": regressed,
            "new_flaky": new_flaky,
            "improved": improved,
            "stabilized": stabilized,
            "still_flaky": still_flaky,
            "added": added,
            "removed": removed,
        },
        "dimension_delta": dimension_delta,
    }


def render_markdown(diff):
    base, head, delta = diff["base"], diff["head"], diff["delta"]
    lines = [
        "## 回归对比",
        "",
        "- 基线：`%s`（%s）　得分 %s / 通过率 %s%%" % (base["run_id"], base["started_at"], base["score"], base["pass_rate"]),
        "- 本次：`%s`（%s）　得分 %s / 通过率 %s%%" % (head["run_id"], head["started_at"], head["score"], head["pass_rate"]),
        "- 得分变化：**%+g**　通过率变化：**%+g** 个百分点　不稳定：%s → %s" % (
            delta["score"], delta["pass_rate"], base["flaky"], head["flaky"]),
        "- 结论：**%s**" % {"regressed": "回归（有阻塞项）", "improved": "改善", "unchanged": "无变化"}[diff["verdict"]],
    ]
    if diff.get("warning"):
        lines.append("- ⚠ %s" % diff["warning"])

    groups = [
        ("regressed", "回归用例（由好变坏，阻塞发布）"),
        ("new_flaky", "新增不稳定（阻塞发布）"),
        ("stabilized", "转为稳定"),
        ("improved", "缺陷已修复"),
        ("added", "新增用例"),
        ("removed", "已删除用例"),
        ("still_flaky", "仍不稳定"),
    ]
    for key, title in groups:
        rows = diff["transitions"].get(key) or []
        if not rows:
            continue
        lines.extend(["", "### %s（%d）" % (title, len(rows)), "", "| 用例 | 维度 | 变化 | 关键断言 |", "| --- | --- | --- | --- |"])
        for row in rows:
            if key in ("added", "removed"):
                change = STATUS_LABELS.get(row.get("status"), "-")
            else:
                change = "%s → %s" % (STATUS_LABELS.get(row.get("base_status"), "-"),
                                      STATUS_LABELS.get(row.get("head_status"), "-"))
            keys = row.get("new_failed_checks") or row.get("fixed_checks") or []
            lines.append("| %s | %s | %s | %s |" % (
                row.get("case_id"), row.get("dimension"), change,
                "、".join(keys[:4]) or row.get("root_cause") or "-"))
    return "\n".join(lines)
