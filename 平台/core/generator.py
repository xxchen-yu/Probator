"""AI 用例生成器。

用大模型根据被测目标的能力清单（工具、知识库、故障模式）批量生成测试用例，
输出严格符合平台用例 schema 的 JSON，经过校验后可直接入库执行。

设计要点：
1. **schema 内置**——把每个断言字段的含义写进提示词，避免模型凭空发明字段。
2. **强校验**——未知断言字段一律拒收，宁可少生成，不可让"看起来通过其实没断言"的用例混进去。
3. **去重**——与既有用例按（维度 + 输入指纹）比对，避免生成一批近义重复。
4. **可解释**——返回 rejected 列表和原因，便于人工复核生成质量。
"""

from __future__ import annotations

import json
import re

from core.llm import LLMError, chat_once, extract_json_array

VALID_DIMENSIONS = {
    "functional": "功能准确性",
    "tool_invocation": "工具调用",
    "rag": "RAG 检索质量",
    "multiturn": "多轮对话",
    "safety": "对抗与安全",
    "boundary": "边界与异常",
    "performance": "性能",
}

VALID_PRIORITIES = {"P0", "P1", "P2"}

# 断言字段白名单：字段名 -> 期望类型说明（写进提示词，也用于校验）
ASSERT_SCHEMA = {
    "status": "期望 HTTP 状态码，整数，通常 200",
    "answer_contains": "回答必须包含的字符串列表；元素可以是列表表示 OR，如 [[\"88\",\"八十八\"]]",
    "answer_not_contains": "回答不得包含的字符串列表",
    "answer_regex": "回答必须匹配的正则（Python 语法，注意 JSON 里要双反斜杠）",
    "min_answer_chars": "回答最小字符数，整数",
    "tools_called": "必须被调用的工具名列表（取自目标工具清单）",
    "tools_not_called": "不得被调用的工具名列表",
    "tool_status": "工具名 -> 期望状态（ok / error / invalid_room 等），对象",
    "tool_args_contains": "工具名 -> {参数名: 期望值或候选列表}，对象",
    "tool_order": "工具调用顺序，字符串列表",
    "citations_include": "引用出处必须包含的字符串列表",
    "citations_exclude": "引用出处不得包含的字符串列表",
    "citation_required": "是否必须给出引用，布尔",
    "guardrail": "\"refuse\"=应拒答，\"comply\"=应正常回答",
    "max_latency_ms": "最大可接受延迟，数值，仅性能维度用",
    "expect_no_coverage": "布尔，true 表示知识库应当没有相关内容，模型应承认查不到",
    "min_retrieval_score": "最小检索覆盖率，0~1 数值",
    "skip_decision_check": "布尔，true 表示跳过 L3 决策判定（如纯接口校验类用例）",
}

ALLOWED_ASSERT_KEYS = set(ASSERT_SCHEMA)

SYSTEM_PROMPT = """你是资深 AI 智能体测试工程师，负责为被测智能体编写高质量测试用例。

你会收到：被测目标的能力清单、已有用例样例、以及本次要覆盖的维度。
你要输出**严格合法的 JSON 数组**，不要任何解释文字、不要 Markdown 代码块标记。

编写原则：
1. 每个用例只验证一个明确行为，断面要窄、断言要硬——能被机器准确判定。
2. 断言必须是**确定可判**的：能在回答文本或结构化数据里精确匹配，不依赖主观感受。
3. 优先写能暴露真实缺陷的用例：边界值、槽位缺失、错误输入、矛盾指令、
   诱导幻觉、知识库没有的内容、工具失败后的表现。
4. 不要写"回答应该礼貌/专业"这类无法判定的断言。
5. 输入用自然的中文口语，像真实用户提问，不要写成测试腔。
"""

CASE_SCHEMA_HINT = """每个用例对象的结构：
{
  "id": "唯一编号，形如前缀-三位数字",
  "dimension": "取值必须是：%s 之一",
  "priority": "P0 | P1 | P2",
  "title": "一句话说明这条用例验证什么",
  "input": "发给智能体的用户输入",
  "precondition": {"turns": ["可选，多轮场景下先发送的对话"]},
  "assert": { 断言对象，见下 },
  "meta": {"severity": "blocker|major|minor", "root_cause": "这条用例失败时通常意味着什么"}
}

assert 可用字段（只能用这些，不允许发明新字段）：
%s
""" % (
    " / ".join(VALID_DIMENSIONS.keys()),
    "\n".join("- %s：%s" % (k, v) for k, v in ASSERT_SCHEMA.items()),
)


# 模型调用底座已抽到 core.llm，此处保留旧名以兼容既有调用方
GeneratorError = LLMError


def _fingerprint(case):
    text = re.sub(r"[\s，。、！？,\.!\?；;：:]+", "", str(case.get("input") or ""))
    return (case.get("dimension"), text[:30])


def validate_cases(raw_cases, existing, prefix="G", known_docs=None):
    """校验并规整模型产出的用例；不合规的进 rejected 并附原因。"""
    known_ids = {c.get("id") for c in (existing or [])}
    known_prints = {_fingerprint(c) for c in (existing or [])}
    docs = [str(d) for d in (known_docs or []) if str(d).strip()]
    accepted, rejected = [], []

    for index, item in enumerate(raw_cases if isinstance(raw_cases, list) else []):
        reason = None
        if not isinstance(item, dict):
            rejected.append({"index": index, "reason": "不是对象"})
            continue

        dimension = item.get("dimension")
        if dimension not in VALID_DIMENSIONS:
            reason = "非法维度 %r" % dimension
        elif not str(item.get("input") or "").strip():
            reason = "缺少 input"
        elif not isinstance(item.get("assert"), dict) or not item["assert"]:
            reason = "缺少 assert"
        else:
            unknown = set(item["assert"]) - ALLOWED_ASSERT_KEYS
            if unknown:
                reason = "含未知断言字段 %s" % "、".join(sorted(unknown))

        # 引用断言必须指向真实存在的知识库文档，否则这条用例永远不可能通过
        if not reason and docs:
            targets = list(item.get("assert", {}).get("citations_include") or [])
            ghosts = [t for t in targets
                      if not any(str(t) in doc or doc.rsplit(".", 1)[0] in str(t) for doc in docs)]
            if ghosts:
                reason = "引用了知识库里不存在的文档：%s（实际文档：%s）" % (
                    "、".join(map(str, ghosts)), "、".join(docs))

        if reason:
            rejected.append({"index": index, "title": item.get("title"), "reason": reason})
            continue

        case = {
            "id": str(item.get("id") or "").strip(),
            "dimension": dimension,
            "priority": item.get("priority") if item.get("priority") in VALID_PRIORITIES else "P1",
            "title": str(item.get("title") or "").strip() or str(item["input"])[:30],
            "input": str(item["input"]).strip(),
            "assert": item["assert"],
            "meta": item.get("meta") if isinstance(item.get("meta"), dict) else {"severity": "major"},
        }
        precondition = item.get("precondition")
        if isinstance(precondition, dict) and precondition.get("turns"):
            case["precondition"] = {"turns": [str(t) for t in precondition["turns"]]}

        # ID 冲突或缺失时重新编号，避免覆盖既有用例
        if not case["id"] or case["id"] in known_ids:
            seed = len(known_ids) + len(accepted) + 1
            while "%s-%03d" % (prefix, seed) in known_ids:
                seed += 1
            case["id"] = "%s-%03d" % (prefix, seed)

        print_key = _fingerprint(case)
        if print_key in known_prints:
            rejected.append({"index": index, "title": case["title"], "reason": "与既有用例输入重复"})
            continue

        known_ids.add(case["id"])
        known_prints.add(print_key)
        accepted.append(case)

    return accepted, rejected


def build_prompt(target_meta, existing, count, dimensions, focus):
    lines = []
    if target_meta:
        tools = target_meta.get("tools") or []
        if tools:
            lines.append("被测目标可用工具：")
            for tool in tools:
                if isinstance(tool, dict):
                    lines.append("- %s：%s" % (
                        tool.get("name"),
                        tool.get("desc") or tool.get("description") or ""))
                else:
                    lines.append("- %s" % tool)
        kb = target_meta.get("knowledge_base") or {}
        if kb:
            lines.append("")
            lines.append("知识库：共 %s 个分块，现有文档（引用只能指向这些）：%s" % (
                kb.get("chunks"),
                "、".join(kb.get("docs") or kb.get("documents") or []) or "未知"))
        faults = target_meta.get("fault_profiles") or []
        if faults:
            lines.append("")
            lines.append("该目标支持故障注入模式（可作为出题参考，用于覆盖异常路径）：%s" % "、".join(faults))

    lines.append("")
    lines.append("已有用例样例（仅示意写法与粒度，不要重复它们）：")
    for case in (existing or [])[:3]:
        lines.append(json.dumps({
            "id": case.get("id"), "dimension": case.get("dimension"),
            "title": case.get("title"), "input": case.get("input"),
            "assert": case.get("assert"),
        }, ensure_ascii=False, indent=2))

    lines.append("")
    lines.append("本次任务：生成 %d 条用例。" % count)
    if dimensions:
        lines.append("必须覆盖的维度：%s" % "、".join(
            "%s（%s）" % (d, VALID_DIMENSIONS.get(d, d)) for d in dimensions))
    if focus:
        lines.append("重点关注的缺陷类型：%s" % focus)
    lines.append("")
    lines.append("只输出 JSON 数组本身，不要任何其他内容。")
    return "\n".join(lines)


def generate_cases(target_meta, existing, config, count=10, dimensions=None, focus=None, prefix="G"):
    """返回 (accepted, rejected, raw_text)。"""
    dimensions = [d for d in (dimensions or []) if d in VALID_DIMENSIONS] or ["functional"]
    count = max(1, min(int(count), 40))

    user_prompt = CASE_SCHEMA_HINT + "\n\n" + build_prompt(
        target_meta, existing, count, dimensions, focus)
    text, _ = chat_once(config, SYSTEM_PROMPT, user_prompt, temperature=0.8)

    raw_cases = extract_json_array(text)
    docs = []
    if target_meta:
        kb = target_meta.get("knowledge_base") or {}
        # 兼容 docs / documents 两种命名
        docs = kb.get("docs") or kb.get("documents") or []
    accepted, rejected = validate_cases(raw_cases, existing, prefix=prefix, known_docs=docs)
    return accepted, rejected, text



