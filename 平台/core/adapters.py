"""被测对象适配器层。

设计目标：任何外部 Agent（自研 HTTP / Dify / Coze / OpenAI-compatible）都通过
一份声明式配置接入，测试用例与判定逻辑不需要改动。

统一输出（Normalized Response）：
    {
        "ok": bool,
        "status_code": int,
        "answer": str,
        "trace": [ {"type": "plan|retrieval|tool|answer|...", ...}, ... ],
        "citations": [str],
        "latency_ms": float,
        "engine": str,
        "fault_profile": str,
        "error": str | None,
        "raw": dict,
    }
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class AdapterError(Exception):
    pass


def _dig(data, path, default=None):
    """按 a.b.c 取值；支持列表下标 a.0.b。"""
    if not path:
        return default
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return default
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur if cur is not None else default


def _fill(template, message, session_id):
    """递归替换请求模板里的占位符。"""
    if isinstance(template, str):
        return template.replace("{message}", message).replace("{session_id}", session_id)
    if isinstance(template, list):
        return [_fill(item, message, session_id) for item in template]
    if isinstance(template, dict):
        return {key: _fill(value, message, session_id) for key, value in template.items()}
    return template


# ---------------------------------------------------------------- trace 归一化
#
# 不同外部 Agent 暴露的中间步骤格式完全不同（LangChain 的 intermediate_steps、
# 自研的 steps、日志行……）。这里登记若干转换器，把任意形状统一成平台的
# 事件列表：{"type": "plan|retrieval|tool|answer", ...}
#
# 这样"能不能做决策级判定"就取决于被测方是否暴露步骤，
# 而不取决于它用哪套框架。

def _as_event(**kwargs):
    event = {"type": kwargs.pop("type", "step")}
    event.update(kwargs)
    return event


def _norm_langchain_steps(steps):
    """LangChain agent 的 intermediate_steps：
    [{"action": {"tool": "x", "tool_input": {...}}, "observation": "..."}, ...]
    也可能被序列化成 [[{...}, "obs"], ...]
    """
    events = []
    for item in steps or []:
        action = None
        observation = None
        if isinstance(item, dict):
            action = item.get("action") or item.get("tool")
            observation = item.get("observation") if "observation" in item else item.get("output")
            if isinstance(action, dict):
                name = action.get("tool") or action.get("name")
                args = action.get("tool_input") or action.get("args") or {}
            else:
                name = action if isinstance(action, str) else None
                args = item.get("tool_input") or item.get("args") or {}
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            head, observation = item[0], item[1]
            if isinstance(head, dict):
                name = head.get("tool") or head.get("name")
                args = head.get("tool_input") or head.get("args") or {}
            else:
                name, args = str(head), {}
        else:
            events.append(_as_event(type="raw", raw=str(item)[:300]))
            continue

        status = "ok"
        text = observation if isinstance(observation, str) else json.dumps(observation, ensure_ascii=False, default=str)
        lowered = text.lower()
        if any(token in lowered for token in ("error", "invalid", "failed", "not found", "错误", "失败", "无效")):
            status = "error"
        events.append(_as_event(
            type="tool", name=name, args=args if isinstance(args, dict) else {},
            status=status, output=text[:600],
        ))
    if events:
        events.insert(0, _as_event(type="plan", intent="", source="intermediate_steps"))
    return events


def _norm_generic_steps(steps):
    """通用形状：[{"step": "查知识库", "tool": "search", "result": "..."}]"""
    events = [_as_event(type="plan", intent="", source="steps")]
    for item in steps or []:
        if not isinstance(item, dict):
            events.append(_as_event(type="raw", raw=str(item)[:300]))
            continue
        kind = str(item.get("type") or "").lower()
        if item.get("tool") or item.get("action"):
            events.append(_as_event(
                type="tool",
                name=item.get("tool") or item.get("action"),
                args=item.get("args") or item.get("tool_input") or {},
                status=str(item.get("status") or "ok"),
                output=str(item.get("output") or item.get("result") or "")[:600],
            ))
        elif kind in ("retrieval", "search", "rag") or item.get("documents"):
            docs = item.get("documents") or item.get("sources") or []
            events.append(_as_event(type="retrieval", documents=docs if isinstance(docs, list) else []))
        else:
            events.append(_as_event(type=kind or "thought", text=str(item)[:400]))
    return events


TRACE_NORMALIZERS = {
    "langchain_steps": _norm_langchain_steps,
    "generic_steps": _norm_generic_steps,
}


def normalize_trace(name, trace):
    func = TRACE_NORMALIZERS.get(name)
    if not func:
        return trace
    try:
        return func(trace)
    except Exception:  # noqa: BLE001 归一化失败不能中断整轮测试
        return trace


def build_trace_from_fields(payload, fields):
    """把响应里的散装标量字段组装成决策链事件。

    很多真实项目不返回数组型 trace，而是把决策信息拆成独立字段，例如
    intent / flow_name / blocked / retrieved_count。这类目标其实「是可观测的」，
    只是没包成数组。按声明把字段映射成事件，就能做 L3 决策级判定。
    """
    events = []
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not path:
            continue
        value = payload
        for part in str(path).split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        if value is None or value == "" or value == []:
            continue
        kind = str(item.get("type") or "step")
        event = {
            "type": kind,
            "source": item.get("label") or path,
            "value": value,
        }
        if kind == "plan":
            event["intent"] = str(value)
        if kind == "retrieval":
            event["docs"] = value if isinstance(value, list) else []
        events.append(event)
    return events


def _trace_from_lg_messages(messages):
    """把 LangGraph 风格的 ai/tool 消息序列还原成决策链事件。"""
    trace = [{"type": "plan", "source": "lg.messages"}]
    for msg in messages:
        mtype = msg.get("type")
        if mtype == "ai":
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                trace.append({
                    "type": "tool",
                    "name": call.get("name"),
                    "args": call.get("args") or {},
                    "status": "ok",
                    "output": "",
                })
        elif mtype == "tool":
            result = msg.get("content")
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            for event in reversed(trace):
                if event.get("type") == "tool" and not event.get("output"):
                    event["output"] = str(text)[:400]
                    status = str(text).lower()
                    if any(t in status for t in ("error", "invalid", "failed", "not found", "错误", "失败")):
                        event["status"] = "error"
                    break
    return trace


def citations_to_retrieval(citations):
    """把引用列表转成一条 retrieval 事件，供检索类断言使用。"""
    docs = []
    for item in citations or []:
        if isinstance(item, dict):
            docs.append({
                "id": item.get("source") or item.get("id") or item.get("doc") or "",
                "score": item.get("score") or item.get("similarity") or 0,
                "text": item.get("text") or item.get("content") or "",
                "deprecated": bool(item.get("deprecated")),
            })
        else:
            docs.append({"id": str(item), "score": 0, "text": "", "deprecated": False})
    return {"type": "retrieval", "docs": docs, "source": "citations"}


class BaseAdapter(object):
    type_name = "base"

    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.timeout = float(self.cfg.get("timeout") or 60)

    def health(self):
        raise NotImplementedError

    def reset(self, session_id):
        return {"reset": False}

    def chat(self, message, session_id):
        raise NotImplementedError

    # -- 公共工具 -------------------------------------------------------
    def _request(self, url, body=None, method="POST"):
        data = None
        headers = {"Content-Type": "application/json"}
        headers.update(self.cfg.get("headers") or {})
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return exc.code, raw, (time.perf_counter() - started) * 1000, str(exc)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return 0, "", (time.perf_counter() - started) * 1000, "连接失败：%s" % reason
        elapsed = (time.perf_counter() - started) * 1000
        return status, raw, elapsed, None


class HttpJsonAdapter(BaseAdapter):
    """通用 HTTP+JSON 适配器：请求体模板 + 响应字段映射，配置即接入。"""

    type_name = "http_json"

    def _url(self, path):
        base = str(self.cfg.get("base_url") or "").rstrip("/")
        if not base:
            raise AdapterError("base_url 未配置")
        path = path or ""
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    def health(self):
        # health_path 显式设为空串表示"该目标不做健康探测"（如部分 OpenAI 兼容服务）
        if self.cfg.get("health_path") == "":
            return {"reachable": None, "status_code": 0, "latency_ms": 0,
                    "error": None, "raw": {}, "note": "未配置健康检查"}
        path = self.cfg.get("health_path") or "/health"
        status, raw, elapsed, error = self._request(self._url(path), None, method="GET")
        payload = {}
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        return {
            "reachable": status == 200 and not error,
            "status_code": status,
            "latency_ms": round(elapsed, 2),
            "error": error,
            "raw": payload,
        }

    def reset(self, session_id):
        path = self.cfg.get("reset_path")
        if not path:
            return {"reset": False, "reason": "未配置重置接口"}
        try:
            status, raw, _, error = self._request(self._url(path), {"session_id": session_id})
            return {"reset": status == 200 and not error, "status_code": status, "error": error}
        except AdapterError as exc:
            return {"reset": False, "error": str(exc)}

    def _http_stream(self, url, body):
        """读取 SSE 流，返回 [(event_name, data_dict), ...]。"""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        headers.update(self.cfg.get("headers") or {})
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        started = time.perf_counter()
        events = []
        event_name = None
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        obj = json.loads(payload)
                    except ValueError:
                        obj = {"_raw": payload}
                    name = event_name
                    if not name and isinstance(obj, dict):
                        tag = obj.get("type")
                        if isinstance(tag, str):
                            name = tag
                    events.append((name or "message", obj))
                elif not line:
                    event_name = None
        return events, (time.perf_counter() - started) * 1000

    def _chat_sse(self, body):
        """流式目标：把 token 事件拼成回答，把 trace 事件转成决策链。"""
        smap = self.cfg.get("sse_map") or {}
        answer_cfg = smap.get("answer") or {"event": "token", "field": "text"}
        citation_cfg = smap.get("citations") or {"event": "citations", "field": "citations"}
        trace_cfg = smap.get("trace") or {"event": "trace", "field": "steps"}
        error_cfg = smap.get("error") or {"event": "error", "field": "message"}

        try:
            events, elapsed = self._http_stream(self._url(self.cfg.get("chat_path") or "/chat"), body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            return {"ok": False, "status_code": exc.code, "answer": "", "trace": [], "citations": [],
                    "latency_ms": 0, "engine": "", "fault_profile": "", "error": detail, "raw": {}}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return {"ok": False, "status_code": 0, "answer": "", "trace": [], "citations": [],
                    "latency_ms": 0, "engine": "", "fault_profile": "",
                    "error": "连接失败：%s" % reason, "raw": {}}

        chunks = []
        citations = []
        steps = []
        model_info = ""
        totals = {}
        error_text = ""
        lg_messages = []

        trace_from_messages = bool(self.cfg.get("trace_from_messages"))
        for name, obj in events:
            if trace_from_messages and name == "message" and isinstance(obj, dict):
                inner = obj.get("content")
                if isinstance(inner, dict):
                    lg_messages.append(inner)
                continue
            if name == answer_cfg.get("event"):
                piece = _dig(obj, answer_cfg.get("field") or "text", "")
                if isinstance(piece, str):
                    chunks.append(piece)
            elif name == citation_cfg.get("event"):
                found = _dig(obj, citation_cfg.get("field") or "citations", [])
                if isinstance(found, list):
                    citations = found
            elif name == trace_cfg.get("event"):
                found = _dig(obj, trace_cfg.get("field") or "steps", [])
                if isinstance(found, list):
                    steps = found
                totals = obj.get("totals") or {}
                model = obj.get("model") or {}
                if isinstance(model, dict) and model.get("name"):
                    model_info = str(model.get("name"))
            elif name == error_cfg.get("event"):
                error_text = str(_dig(obj, error_cfg.get("field") or "message", "") or "")

        answer = "".join(chunks).strip()

        # LangGraph 风格消息流：从 ai/tool 消息里还原工具调用与结果
        trace = []
        if lg_messages:
            trace = _trace_from_lg_messages(lg_messages)
            if not answer:
                for msg in reversed(lg_messages):
                    if msg.get("type") == "ai" and isinstance(msg.get("content"), str):
                        answer = msg["content"].strip()
                        if answer:
                            break
        elif steps:
            trace.append({"type": "plan", "source": "sse.trace"})
            docs = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                trace.append({
                    "type": "tool",
                    "name": step.get("tool"),
                    "args": step.get("args") or {},
                    "status": "ok",
                    "output": "",
                })
                for item in step.get("results") or []:
                    if isinstance(item, dict):
                        docs.append({
                            "id": item.get("source") or item.get("title") or "",
                            "score": item.get("rrf_score") or 0,
                            "text": item.get("snippet") or "",
                            "deprecated": False,
                        })
            if docs:
                trace.append({"type": "retrieval", "docs": docs, "source": "sse.trace"})

        latency = totals.get("duration_ms") if isinstance(totals, dict) else None
        return {
            "ok": bool(answer),
            "status_code": 200,
            "answer": answer,
            "trace": trace,
            "citations": citations if isinstance(citations, list) else [],
            "latency_ms": latency or round(elapsed, 2),
            "engine": model_info,
            "fault_profile": "",
            "error": error_text or None,
            "raw": {"event_count": len(events), "model": model_info, "totals": totals},
        }

    def chat(self, message, session_id):
        template = self.cfg.get("request_template") or {"message": "{message}", "session_id": "{session_id}"}
        body = _fill(template, message, session_id)
        if str(self.cfg.get("stream_mode") or "").lower() == "sse":
            return self._chat_sse(body)
        status, raw, elapsed, error = self._request(self._url(self.cfg.get("chat_path") or "/chat"), body)

        payload = {}
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                error = error or "响应不是合法 JSON"

        mapping = self.cfg.get("response_map") or {}
        answer = _dig(payload, mapping.get("answer", "answer"), "")
        trace = _dig(payload, mapping.get("trace", "trace"), []) or []
        citations = _dig(payload, mapping.get("citations", "citations"), []) or []

        # 通用 trace 归一化：外部 Agent 的中间步骤结构各异，
        # 用 trace_normalizer 把 [{"tool": ..., "output": ...}] 之类的形状
        # 转成平台认识的 step 列表。normalizer 名不存在时原样透传。
        normalizer = self.cfg.get("trace_normalizer")
        if normalizer and isinstance(trace, list):
            trace = normalize_trace(normalizer, trace)

        # trace 为空但配置了字段映射：用散装标量字段组装决策链
        if not trace and self.cfg.get("trace_fields"):
            trace = build_trace_from_fields(payload, self.cfg.get("trace_fields"))

        # 有引用但没有检索事件：把引用转成 retrieval 事件，供检索类断言使用
        if isinstance(trace, list) and citations:
            if not any(isinstance(e, dict) and e.get("type") == "retrieval" for e in trace):
                trace.append(citations_to_retrieval(citations))

        if isinstance(answer, dict):
            answer = json.dumps(answer, ensure_ascii=False)
        if not isinstance(trace, list):
            trace = []
        if not isinstance(citations, list):
            citations = [str(citations)]

        return {
            "ok": bool(status == 200 and not error and answer),
            "status_code": status,
            "answer": str(answer or ""),
            "trace": trace,
            "citations": [str(c) for c in citations],
            "latency_ms": _dig(payload, mapping.get("latency_ms", "latency_ms"), None) or round(elapsed, 2),
            "engine": _dig(payload, mapping.get("engine", "engine"), ""),
            "fault_profile": _dig(payload, mapping.get("fault_profile", "fault_profile"), ""),
            "error": error or (payload.get("error") if isinstance(payload, dict) else None),
            "raw": payload if isinstance(payload, dict) else {"_body": raw[:500]},
        }


class OpenAICompatAdapter(BaseAdapter):
    """直连大模型接口。没有 trace 能力，用于纯黑盒基线对比。"""

    type_name = "openai_compat"

    def health(self):
        return {"reachable": bool(self.cfg.get("base_url")), "status_code": None, "raw": {}}

    def chat(self, message, session_id):
        body = {
            "model": self.cfg.get("model") or "deepseek-chat",
            "messages": [{"role": "user", "content": message}],
            "temperature": self.cfg.get("temperature", 0.2),
            "stream": False,
        }
        headers = self.cfg.get("headers") or {}
        key = self.cfg.get("api_key") or ""
        if key:
            headers = dict(headers, Authorization="Bearer " + key)
        merged = dict(self.cfg, headers=headers)
        original, self.cfg = self.cfg, merged
        try:
            status, raw, elapsed, error = self._request(self.cfg.get("base_url"), body)
        finally:
            self.cfg = original
        payload = {}
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                error = error or "响应不是合法 JSON"
        answer = _dig(payload, "choices.0.message.content", "")
        return {
            "ok": bool(status == 200 and not error and answer),
            "status_code": status,
            "answer": str(answer or ""),
            "trace": [],
            "citations": [],
            "latency_ms": round(elapsed, 2),
            "engine": str(self.cfg.get("model") or ""),
            "fault_profile": "",
            "error": error,
            "raw": payload if isinstance(payload, dict) else {},
        }


ADAPTERS = {
    HttpJsonAdapter.type_name: HttpJsonAdapter,
    OpenAICompatAdapter.type_name: OpenAICompatAdapter,
}


def build_adapter(cfg):
    kind = (cfg or {}).get("type") or "http_json"
    cls = ADAPTERS.get(kind)
    if cls is None:
        raise AdapterError("未知的适配器类型：%s（可选：%s）" % (kind, "、".join(ADAPTERS)))
    return cls(cfg)



