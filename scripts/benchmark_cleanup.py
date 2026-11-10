#!/usr/bin/env python3
"""Explicit, synthetic-text benchmark. Never reads audio, history or clipboard."""

import argparse
import concurrent.futures
import getpass
import json
import math
import os
from pathlib import Path
import statistics
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from minimax_cleanup import MODELS, MiniMaxCleaner, ProviderError
from text_cleanup import RuleCleaner, validate_smart_output


def samples():
    short = "嗯，今天我们我们先测试 Qwen3-ASR 接口，呃，不要改动 3.14 这个数值。"
    medium = (
        "嗯，我们今天先验证 VoiceFlow 的轻清洗功能，呃，不要改录音和自动粘贴流程。"
        "本地 Qwen3-ASR 已经满足日常使用，接下来只删除明确的犹豫声和无意义重复。"
        "测试时保留版本 v3.14、价格 300 元和 API 这些技术表达，不要改变否定含义。"
        "然后检查普通编辑器和终端的行为是否一致，记录原文与结果，最后确认失败时可以回退。"
        "呃，特别注意，用户没有说的内容不要补充，也不要为了书面化重写整段话。"
    )
    actions = [
        "麦克风选择", "录音时长限制", "首字缓冲", "音量显示", "松键停止",
        "本地识别调用", "云端失败兜底", "窗口目标保存", "终端粘贴判断", "剪贴板恢复",
        "文本轻清洗", "数字保护", "术语保护", "否定词保护", "引用保护",
        "路径保护", "异常回退", "取消超时请求", "测试结果记录", "安装前验收",
    ]
    long = "\n".join(
        "呃，第%d项检查%s，保留现有实现，不要扩大修改范围，嗯，完成后记录实际结果供下一次对照。"
        % (i, action) for i, action in enumerate(actions, 1)
    )
    return [("short", short), ("medium", medium), ("long", long)]


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row["model"], row["sample"]), []).append(row)
    result = []
    for (model, sample), group in sorted(groups.items()):
        completed = [row for row in group if row["ok"]]
        values = sorted(row["elapsed_ms"] for row in completed)
        ttfts = [row["ttft_ms"] for row in completed if row.get("ttft_ms") is not None]
        result.append({
            "model": model, "sample": sample, "attempts": len(group),
            "completed": len(completed), "failed": len(group) - len(completed),
            "valid": sum(row.get("valid", False) for row in completed),
            "complete_p50_ms": statistics.median(values) if values else None,
            "complete_p95_ms": values[math.ceil(len(values) * .95) - 1] if values else None,
            "ttft_p50_ms": statistics.median(ttfts) if ttfts else None,
            **{
                "within_%dms" % budget: sum(
                    row["elapsed_ms"] <= budget and row.get("valid", False)
                    for row in completed
                ) for budget in (300, 500, 1000)
            },
        })
    return result


def benchmark_rules():
    cleaner, rows = RuleCleaner(), []
    for name, text in samples():
        for _ in range(300):
            cleaner.clean(text)
        timings = []
        for _ in range(5000):
            start = time.perf_counter_ns()
            cleaner.clean(text)
            timings.append((time.perf_counter_ns() - start) / 1e6)
        timings.sort()
        rows.append({
            "sample": name, "chars": len(text), "calls": len(timings),
            "p50_ms": statistics.median(timings),
            "p95_ms": timings[math.ceil(.95 * len(timings)) - 1],
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument("--api-key-prompt", action="store_true")
    parser.add_argument("--rules-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 50:
        parser.error("--repeats must be in [1, 50]")
    data = {
        "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "scope": "Synthetic transcripts only; complete latency includes worker/IPC. TTFT is API-only. At most one request per model; two models may run concurrently. No retries.",
        "model_parameters": {
            "MiniMax-M3.1-Flash": {"output_config": {"effort": "low"}},
            "MiniMax-M3": {"thinking": {"type": "disabled"}},
        },
        "timeout_seconds": args.timeout, "repeats_per_model_length": args.repeats,
        "samples": dict(samples()), "rules": benchmark_rules(), "requests": [],
    }
    if args.api_key_prompt and not args.rules_only:
        os.environ["MINIMAX_API_KEY"] = getpass.getpass("MiniMax API key (hidden): ").strip()
    if not args.rules_only and not os.environ.get("MINIMAX_API_KEY"):
        parser.error("Set MINIMAX_API_KEY or use --api-key-prompt")
    lock = threading.Lock()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        data["summary"] = summarize(data["requests"])
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(args.output)

    def run(model):
        client = MiniMaxCleaner({"model": model, "timeout_seconds": args.timeout})
        try:
            for repeat in range(args.repeats):
                for name, text in samples():
                    start = time.monotonic()
                    row = {"model": model, "sample": name, "repeat": repeat, "chars": len(text)}
                    try:
                        response = client.clean(text)
                        row.update(response)
                        row.update(ok=True, valid=validate_smart_output(text, response["text"]))
                    except Exception as exc:
                        row.update(
                            ok=False, elapsed_ms=(time.monotonic() - start) * 1000,
                            error=str(exc) if isinstance(exc, (ProviderError, TimeoutError)) else type(exc).__name__,
                        )
                    with lock:
                        data["requests"].append(row)
                        save()
                        print(
                            "%s %s #%d complete=%.0fms ok=%s valid=%s" %
                            (model, name, repeat + 1, row["elapsed_ms"], row["ok"], row.get("valid")),
                            flush=True,
                        )
        finally:
            client.close()

    save()
    if not args.rules_only:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.models)) as executor:
            list(executor.map(run, args.models))
    data["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    save()
    print("Saved:", args.output, flush=True)


if __name__ == "__main__":
    main()
