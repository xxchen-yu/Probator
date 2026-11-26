"""AI 用例生成命令行。

    # 预览（不写文件）
    python gen_cases.py --count 12 --dimensions rag,boundary

    # 生成并追加到 seed.json
    python gen_cases.py --count 12 --dimensions rag,boundary --append

    # 生成到新文件
    python gen_cases.py --count 20 --file seed_ai.json --append

    # 指定关注点
    python gen_cases.py --count 10 --dimensions safety --focus "提示词注入、越权查询"

依赖环境变量 JUDGE_API_KEY 或 MODEL_API_KEY（与 AI 判定共用）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import generator, store


def _setup_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _target_meta(target):
    """尽量拿到目标的 /meta；拿不到就返回 None，生成器会省掉这段上下文。"""
    try:
        from core.adapters import build_adapter
        adapter = build_adapter(target)
        path = (adapter.cfg.get("health_path") or "/health")
        base = str(adapter.cfg.get("base_url") or "").rstrip("/")
        meta_path = (target.get("meta_path") or "/meta")
        status, raw, _, _ = adapter._request(base + meta_path, None, method="GET")
        if status == 200 and raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
        return None
    except Exception:  # noqa: BLE001 目标不可达不影响生成
        return None


def pick_target(config, target_id):
    targets = config.get("targets") or []
    if not targets:
        raise SystemExit("配置里没有目标")
    if not target_id:
        return targets[0]
    for item in targets:
        if item.get("id") == target_id:
            return item
    raise SystemExit("找不到目标：%s" % target_id)


def main():
    _setup_stdout()
    parser = argparse.ArgumentParser(description="AI 生成测试用例")
    parser.add_argument("--target", default=None, help="目标 id（用于取工具与知识库上下文）")
    parser.add_argument("--file", default=store.DEFAULT_CASE_FILE, help="写入的用例文件名")
    parser.add_argument("--count", type=int, default=10, help="生成条数")
    parser.add_argument("--dimensions", default="functional",
                        help="逗号分隔：functional,tool_invocation,rag,multiturn,safety,boundary,performance")
    parser.add_argument("--focus", default=None, help="重点缺陷类型，自由文本")
    parser.add_argument("--prefix", default="G", help="生成用例的编号前缀")
    parser.add_argument("--append", action="store_true", help="追加写入用例文件（默认只预览）")
    args = parser.parse_args()

    config = store.load_config()
    store.apply_llm_config(config)
    target = pick_target(config, args.target)
    existing = store.load_cases(args.file)
    dimensions = [d.strip() for d in args.dimensions.split(",") if d.strip()]

    meta = _target_meta(target)
    print("目标：%s" % target.get("name"))
    print("既有用例：%d 条（%s）" % (len(existing), args.file))
    print("目标能力：%s" % ("已获取 /meta" if meta else "未获取（跳过工具与知识库上下文）"))
    print("生成 %d 条，维度 %s …" % (args.count, "、".join(dimensions)))

    try:
        accepted, rejected, _ = generator.generate_cases(
            meta, existing, config, count=args.count,
            dimensions=dimensions, focus=args.focus, prefix=args.prefix,
        )
    except generator.GeneratorError as exc:
        print("生成失败：%s" % exc)
        return 2

    print("")
    print("通过校验 %d 条：" % len(accepted))
    for case in accepted:
        print("  + %s [%s] %s" % (case["id"], case["dimension"], case["title"]))
        print("    输入：%s" % case["input"].replace("\n", " ")[:90])
        print("    断言：%s" % json.dumps(case["assert"], ensure_ascii=False)[:160])

    if rejected:
        print("")
        print("被拒 %d 条：" % len(rejected))
        for item in rejected:
            print("  - #%s %s → %s" % (item.get("index"), item.get("title") or "", item.get("reason")))

    if not accepted:
        print("")
        print("没有可用用例，未写入。")
        return 1

    if args.append:
        result = store.upsert_cases(accepted, args.file)
        if isinstance(result, dict):
            print("")
            print("已写入 %s：新增 %d 条，更新 %d 条，现共 %d 条" % (
                args.file, result.get("added", 0), result.get("updated", 0), result.get("total", 0)))
        else:
            print("")
            print("已写入 %s，现共 %s 条" % (args.file, result))
    else:
        print("")
        print("预览模式，未写入。确认后加 --append 落盘。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print("异常：%s: %s" % (type(exc).__name__, exc))
        raise SystemExit(2)

