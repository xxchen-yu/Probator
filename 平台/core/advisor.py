# -*- coding: utf-8 -*-
"""基于测试结果生成改进方案。

设计原则：**建议必须绑定证据**。
模型产出的每条方案都要能落到真实失败用例与失败断言上，落不到就丢弃，
避免出现「建议优化提示词」这类放到任何项目都成立的空话。

模块内按四段排列：持久化 → 证据抽取 → 提示词 → 校验与编排。
"""
import json
import os

from core.llm import LLMError, chat_once, extract_json_array

# ---------------------------------------------------------------- 常量

LAYERS = ("prompt", "config", "code", "data")
EFFORTS = ("S", "M", "L")
PRIORITY_WEIGHTS = {"P0": 5, "P1": 2, "P2": 1}

TEXT_LIMITS = {
    "input": 300,
    "answer": 500,
    "check": 140,
    "tool_output": 160,
    "cluster": 60,
    "target": 120,
    "patch": 600,
    "verify": 400,
    "max_failed": 8,
    "max_tools": 8,
    "max_docs": 8,
    "max_cases": 8,
    "max_evidence": 6,
    "error": 300,
}

SYSTEM_PROMPT = """你是资深 AI 智能体工程质量工程师。
你会收到一次自动化测试的失败清单，包含失败用例、失败断言、工具调用记录与检索记录。

你的任务：归纳出**少数几条根因级改进方案**，而不是逐条复述失败。

硬性要求：
1. 每条方案必须绑定证据——evidence 数组里必须出现**真实存在的用例编号或断言编号**，不得编造。
2. affected_cases 只能取自给定清单里的用例编号，不得新增。
3. fix.layer 只能取 prompt / config / code / data 之一，表示该改哪一层。
4. fix.patch 要写成可以直接照着做的动作，不要写「优化提示词」这种空话。
5. verify 要给出可复跑的验证方法（跑哪些用例、期望什么变化）。
6. 同一根因导致的多个失败必须合并成一条，不要拆成多条。

只输出 JSON 数组，不要解释文字，不要 Markdown 代码块。"""


# ---------------------------------------------------------------- 持久化

def advice_path(runs_dir, run_id):
    return os.path.join(runs_dir, "%s.advice.json" % run_id)


def save_advice(runs_dir, run_id, data):
    path = advice_path(runs_dir, run_id)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return path


def load_advice(runs_dir, run_id):
    path = advice_path(runs_dir, run_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError:
        return None


# ---------------------------------------------------------------- 证据抽取

def _response_of(result):
    response = result.get("response")
    return response if isinstance(response, dict) else {}


def _clip(value, key):
    return str(value or "")[:TEXT_LIMITS[key]]


def _failed_checks(result):
    """优先取断言明细；明细缺失时退化为断言编号列表。"""
    checks = []
    for check in result.get("checks") or []:
        if check.get("passed") is not False:
            continue
        checks.append({
            "check": check.get("id"),
            "expect": _clip(check.get("expected"), "check"),
            "actual": _clip(check.get("actual"), "check"),
        })
        if len(checks) >= TEXT_LIMITS["max_failed"]:
            return checks

    if checks:
        return checks

    for name in (result.get("failed_checks") or [])[:TEXT_LIMITS["max_failed"]]:
        checks.append({"check": str(name), "expect": "", "actual": ""})
    return checks


def _tool_calls(result):
    calls = []
    for event in _response_of(result).get("trace") or []:
        if event.get("type") != "tool":
            continue
        calls.append({
            "name": event.get("name"),
            "args": event.get("args"),
            "status": event.get("status"),
            "output": _clip(event.get("output"), "tool_output"),
        })
        if len(calls) >= TEXT_LIMITS["max_tools"]:
            break
    return calls


def _retrieval_docs(result):
    docs = []
    for event in _response_of(result).get("trace") or []:
        if event.get("type") != "retrieval":
            continue
        for doc in event.get("docs") or []:
            docs.append({
                "id": doc.get("id"),
                "score": doc.get("score"),
                "deprecated": bool(doc.get("deprecated")),
            })
            if len(docs) >= TEXT_LIMITS["max_docs"]:
                return docs
    return docs


def collect_evidence(run):
    """把运行记录里的失败用例整理成可供模型分析的证据。"""
    evidence = []
    for result in run.get("results") or []:
        if str(result.get("status") or "").lower() not in ("fail", "failed"):
            continue

        item = {
            "case_id": result.get("id"),
            "dimension": result.get("dimension"),
            "priority": result.get("priority"),
            "title": result.get("title"),
            "input": _clip(result.get("input"), "input"),
            "answer": _clip(_response_of(result).get("answer"), "answer"),
            "failed_checks": _failed_checks(result),
        }

        tools = _tool_calls(result)
        if tools:
            item["tool_calls"] = tools

        docs = _retrieval_docs(result)
        if docs:
            item["retrieval"] = docs

        evidence.append(item)
    return evidence


# ---------------------------------------------------------------- 提示词

def build_prompt(run, evidence):
    summary = run.get("summary") or {}
    target = (run.get("target") or {}).get("name") or "未知"

    lines = [
        "被测目标：%s" % target,
        "加权质量分：%s　用例通过：%s/%s" % (
            summary.get("score"), summary.get("passed"), summary.get("total")),
        "",
        "各维度通过率：",
    ]
    for dim in summary.get("by_dimension") or []:
        lines.append("  %s：%s/%s（%.1f%%）" % (
            dim.get("label"), dim.get("passed"), dim.get("total"),
            float(dim.get("pass_rate") or 0)))

    lines.extend([
        "",
        "失败用例清单（共 %d 条）：" % len(evidence),
        json.dumps(evidence, ensure_ascii=False, indent=1),
        "",
        "请输出改进方案 JSON 数组。每条格式：",
        json.dumps({
            "cluster": "一句话概括根因",
            "affected_cases": ["只能取上面清单里的 case_id"],
            "evidence": ["必须引用真实 case_id 或 check 编号，说明现象"],
            "fix": {
                "layer": "prompt|config|code|data",
                "target": "具体位置",
                "patch": "可以直接照做的动作",
                "effort": "S|M|L",
            },
            "verify": "可复跑的验证方法",
        }, ensure_ascii=False, indent=1),
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------- 校验

def _known_ids(evidence):
    case_ids, check_ids = set(), set()
    for item in evidence:
        if item.get("case_id"):
            case_ids.add(item["case_id"])
        for check in item.get("failed_checks") or []:
            if check.get("check"):
                check_ids.add(check["check"])
    return case_ids, check_ids


def _priority_of(evidence, case_ids):
    priorities = []
    for entry in evidence:
        if entry.get("case_id") in case_ids and entry.get("priority"):
            priorities.append(entry["priority"])
    return sum(PRIORITY_WEIGHTS.get(str(p).upper(), 1) for p in priorities)


def _normalize_fix(raw):
    fix = raw if isinstance(raw, dict) else {}
    layer = str(fix.get("layer") or "").strip().lower()
    effort = str(fix.get("effort") or "").strip().upper()
    return {
        "layer": layer if layer in LAYERS else "prompt",
        "target": _clip(fix.get("target"), "target"),
        "patch": _clip(fix.get("patch"), "patch"),
        "effort": effort if effort in EFFORTS else "M",
    }


def validate(raw_items, evidence):
    """校验模型产出：证据必须落在真实用例或断言上，否则丢弃。"""
    case_ids, check_ids = _known_ids(evidence)
    accepted, rejected = [], []

    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            rejected.append({"index": index, "reason": "不是对象"})
            continue

        cluster = _clip(item.get("cluster"), "cluster").strip()
        if not cluster:
            rejected.append({"index": index, "reason": "缺少 cluster"})
            continue

        affected = [str(x) for x in (item.get("affected_cases") or [])]
        affected = [x for x in affected if x in case_ids][:TEXT_LIMITS["max_cases"]]
        if not affected:
            rejected.append({"index": index, "reason": "affected_cases 不含真实用例编号"})
            continue

        evidence_lines = [str(x).strip() for x in (item.get("evidence") or []) if str(x).strip()]
        grounded = [
            line for line in evidence_lines
            if any(cid in line for cid in affected) or any(chk in line for chk in check_ids)
        ][:TEXT_LIMITS["max_evidence"]]
        if not grounded:
            rejected.append({"index": index, "reason": "证据未引用任何真实用例或断言"})
            continue

        fix = _normalize_fix(item.get("fix"))
        if not fix["patch"]:
            rejected.append({"index": index, "reason": "缺少可执行的 fix.patch"})
            continue

        accepted.append({
            "cluster": cluster,
            "affected_cases": affected,
            "evidence": grounded,
            "fix": fix,
            "verify": _clip(item.get("verify"), "verify"),
            "priority_score": _priority_of(evidence, set(affected)),
        })

    accepted.sort(key=lambda entry: -entry["priority_score"])
    return accepted, rejected


# ---------------------------------------------------------------- 编排

def generate_advice(run, config, max_items=6):
    evidence = collect_evidence(run)
    if not evidence:
        return {
            "advice": [],
            "rejected": [],
            "evidence_count": 0,
            "note": "该次运行没有失败用例，无需改进建议",
        }

    text, cfg = chat_once(config, SYSTEM_PROMPT, build_prompt(run, evidence), temperature=0.2)

    raw_items = extract_json_array(text)
    if not isinstance(raw_items, list):
        raise LLMError("模型输出不是数组")

    accepted, rejected = validate(raw_items, evidence)
    return {
        "advice": accepted[:max_items],
        "rejected": rejected,
        "evidence_count": len(evidence),
        "model": cfg.get("model"),
    }
