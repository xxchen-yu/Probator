"""AgentTestHub 命令行入口（CI 门禁用）。

    python run.py                                    # 跑 seed.json 全量，默认门禁
    python run.py --samples 3 --fail-under 95        # 三采样 + 质量分下限
    python run.py --case smoke.json --max-flaky 1    # 允许 1 条不稳定
    python run.py --no-gate                          # 只跑不卡（生成报告用）

退出码：
    0  全部达标
    1  门禁未通过（有失败 / 不稳定 / 分数不达标 / 相对基线回归）
    2  执行异常
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import diff as diff_mod
from core import runner, store


def _setup_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def build_parser():
    parser = argparse.ArgumentParser(description="智能体回归测试与上线门禁")
    parser.add_argument("--target", default=None, help="目标 id，默认取配置里的第一个")
    parser.add_argument("--case", default=store.DEFAULT_CASE_FILE, help="用例文件，默认 seed.json")
    parser.add_argument("--samples", type=int, default=None, help="每个用例采样次数")
    parser.add_argument("--concurrency", type=int, default=None, help="并发数")
    judge = parser.add_mutually_exclusive_group()
    judge.add_argument("--judge", dest="judge", action="store_true", help="启用 L2 语义判定")
    judge.add_argument("--no-judge", dest="judge", action="store_false", help="关闭 L2 语义判定")
    parser.set_defaults(judge=None)
    parser.add_argument("--fail-under", type=float, default=None, help="加权质量分下限，例如 95")
    parser.add_argument("--max-flaky", type=int, default=0, help="允许的不稳定用例数上限")
    parser.add_argument("--max-fail", type=int, default=0, help="允许的失败用例数上限")
    parser.add_argument("--baseline", default="auto", help="基线 run_id；auto=同目标上一轮；none=不对比")
    parser.add_argument("--no-gate", action="store_true", help="不因门禁失败而返回非零退出码")
    parser.add_argument("--json-out", default=None, help="把本次运行记录另存为指定路径")
    return parser


def pick_target(config, target_id):
    targets = config.get("targets") or []
    if not targets:
        raise SystemExit("配置里没有被测目标，请先编辑 config.json")
    if not target_id:
        return targets[0]
    for item in targets:
        if item.get("id") == target_id:
            return item
    raise SystemExit("找不到目标 id：%s（可选：%s）" % (target_id, "、".join(t.get("id", "") for t in targets)))


def main():
    _setup_stdout()
    args = build_parser().parse_args()

    config = store.load_config()
    store.apply_llm_config(config)
    target = pick_target(config, args.target)
    cases = store.load_cases(args.case)
    if not cases:
        print("用例集为空：%s" % args.case)
        return 2

    runner_cfg = dict(config.get("runner") or {})
    if args.samples:
        runner_cfg["samples"] = args.samples
    if args.concurrency:
        runner_cfg["concurrency"] = args.concurrency
    config["runner"] = runner_cfg

    use_judge = bool((config.get("judge") or {}).get("enabled")) if args.judge is None else args.judge

    print("目标：%s（%s）" % (target.get("name"), target.get("base_url")))
    print("用例：%s × %d 条　采样 %d 次　并发 %d　判定 %s" % (
        args.case, len(cases), runner_cfg.get("samples", 1),
        runner_cfg.get("concurrency", 4), "L1+L2+L3" if use_judge else "L1+L3"))
    print("执行中…")

    run = runner.run_suite(target, cases, config, use_judge=use_judge, case_file=args.case)
    summary = run["summary"]

    print("")
    print("运行编号：%s" % run["run_id"])
    print("加权质量分 %.2f　通过率 %.2f%%　用例 %d/%d　不稳定 %d　断言 %d/%d" % (
        summary["score"], summary["pass_rate"], summary["passed"], summary["total"],
        summary.get("flaky", 0), summary["checks_passed"], summary["checks_total"]))
    for bucket in summary.get("by_dimension") or []:
        if not bucket["total"]:
            continue
        print("  %-10s %2d/%-2d  %5.1f%%  (权重 %d%%)" % (
            bucket["label"], bucket["passed"], bucket["total"], bucket["pass_rate"],
            round(bucket["weight"] * 100)))

    if summary.get("flaky_cases"):
        print("")
        print("不稳定用例：")
        for item in summary["flaky_cases"]:
            print("  ! %s  %d/%d  飘移断言：%s" % (
                item["case_id"], item["pass_count"], item["samples"],
                "、".join(item.get("flaky_checks") or []) or "-"))

    if summary.get("defects"):
        print("")
        print("失败用例：")
        for item in summary["defects"][:12]:
            first = (item.get("failed_checks") or [{}])[0]
            print("  x %s [%s] %s ← %s" % (item["case_id"], item["severity"],
                                           item.get("root_cause") or "-", first.get("label") or "-"))

    # ---- 回归对比 ----
    base_run = None
    if args.baseline != "none":
        if args.baseline == "auto":
            base_run = store.find_previous_run(target.get("id"), args.case, exclude_run_id=run["run_id"], before_run_id=run["run_id"])
        else:
            base_run = store.load_run(args.baseline)
    diff_result = None
    if base_run:
        diff_result = diff_mod.compare(base_run, run)
        print("")
        print("对比基线 %s：得分 %+g　通过率 %+g 个百分点" % (
            base_run["run_id"], diff_result["delta"]["score"], diff_result["delta"]["pass_rate"]))
        counts = diff_result["counts"]
        print("  回归 %d　新增不稳定 %d　修复 %d　转稳 %d" % (
            counts["regressed"], counts["new_flaky"], counts["improved"], counts["stabilized"]))
        for item in diff_result["transitions"]["regressed"]:
            print("  ↓ %s  %s → %s  %s" % (
                item["case_id"], item["base_status"], item["head_status"],
                "、".join(item.get("new_failed_checks") or []) or ""))
        for item in diff_result["transitions"]["new_flaky"]:
            print("  ~ %s  %s → %s" % (item["case_id"], item["base_status"], item["head_status"]))
        if diff_result.get("warning"):
            print("  ⚠ %s" % diff_result["warning"])
    else:
        print("")
        print("对比基线：无可对比的历史运行（首次运行请以此结果为基线）")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as file:
            json.dump(run, file, ensure_ascii=False, indent=2)
        print("运行记录已另存：%s" % args.json_out)

    # ---- 门禁 ----
    reasons = []
    failed = summary["total"] - summary["passed"]
    if failed > args.max_fail:
        reasons.append("失败用例 %d 条超过上限 %d" % (failed, args.max_fail))
    if summary.get("flaky", 0) > args.max_flaky:
        reasons.append("不稳定用例 %d 条超过上限 %d" % (summary.get("flaky", 0), args.max_flaky))
    if args.fail_under is not None and summary["score"] < args.fail_under:
        reasons.append("质量分 %.2f 低于下限 %g" % (summary["score"], args.fail_under))
    if diff_result and diff_result["blocking"]:
        reasons.append("相对基线出现回归（%d 条回归 + %d 条新增不稳定）" % (
            diff_result["counts"]["regressed"], diff_result["counts"]["new_flaky"]))

    print("")
    if reasons:
        print("门禁未通过：")
        for reason in reasons:
            print("  - %s" % reason)
        print("退出码 1" + ("（--no-gate 已忽略）" if args.no_gate else ""))
        return 0 if args.no_gate else 1
    print("门禁通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print("执行异常：%s: %s" % (type(exc).__name__, exc))
        raise SystemExit(2)


