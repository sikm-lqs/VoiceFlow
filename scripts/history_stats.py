#!/usr/bin/env python3
"""Print recent VoiceFlow transcription history and aggregate stats.

Usage:
    python3 history_stats.py [--dir PATH] [--full]

Reads history.jsonl written by the sidecar's opt-in history recorder
(default off; enable with history.enabled in config). --full prints the
complete raw/cleaned text of each entry instead of a short preview.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def history_dir():
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "voiceflow" / "history"


def load_entries(directory):
    index = directory / "history.jsonl"
    try:
        lines = index.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def fmt_usage(usage):
    if not usage:
        return "-"
    parts = []
    for key in ("seconds", "input_tokens", "output_tokens", "total_tokens"):
        if key in usage:
            parts.append(f"{key}={usage[key]}")
    return " ".join(parts) if parts else json.dumps(usage, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=history_dir())
    parser.add_argument("--full", action="store_true",
                        help="print full text instead of a preview")
    args = parser.parse_args()

    entries = load_entries(args.dir)
    if not entries:
        print(f"no history entries in {args.dir} "
              f"(recorder off, or nothing recorded yet)")
        return 0

    print(f"{'#':>2}  {'time':<19}  {'backend':<7}  {'lat_ms':>7}  "
          f"{'audio_s':>7}  {'usage':<28}  text / error")
    print("-" * 110)
    shown = entries[-20:]
    for i, e in enumerate(shown, start=1):
        ts = datetime.fromtimestamp(e.get("ts", 0)).strftime("%Y-%m-%d %H:%M:%S")
        latency = e.get("elapsed_ms") or e.get("latency_ms")
        latency_s = f"{latency:.0f}" if latency is not None else "-"
        seconds = (e.get("usage") or {}).get("seconds")
        seconds_s = f"{seconds:.1f}" if seconds is not None else "-"
        if e.get("error"):
            text = f"[{e['error']}]"
        else:
            text = e.get("text_cleaned", "")
            if not args.full:
                text = text[:40] + ("…" if len(text) > 40 else "")
        print(f"{i:>2}  {ts:<19}  {e.get('backend', '-'):<7}  {latency_s:>7}  "
              f"{seconds_s:>7}  {fmt_usage(e.get('usage') or ''):<28}  {text}")
        if args.full and not e.get("error"):
            raw = e.get("text_raw", "")
            if raw != e.get("text_cleaned", ""):
                print(f"    raw: {raw}")

    ok = [e for e in entries if not e.get("error")]
    lats = [e["elapsed_ms"] for e in ok if e.get("elapsed_ms") is not None]
    secs = [(e.get("usage") or {}).get("seconds") for e in ok]
    secs = [s for s in secs if s is not None]
    chars = [len(e.get("text_cleaned", "")) for e in ok]
    print("-" * 110)
    print(f"entries: {len(entries)} total, {len(ok)} ok, "
          f"{len(entries) - len(ok)} failed")
    if lats:
        lats_sorted = sorted(lats)
        p95 = lats_sorted[min(len(lats_sorted) - 1, int(len(lats_sorted) * 0.95))]
        print(f"latency  ms: avg {sum(lats)/len(lats):.0f}  "
              f"min {min(lats):.0f}  max {max(lats):.0f}  p95 {p95:.0f}")
    if secs:
        print(f"billed   s: total {sum(secs):.1f}  avg {sum(secs)/len(secs):.1f}")
    if chars:
        print(f"chars    : avg {sum(chars)/len(chars):.0f}  total {sum(chars)}")
    print(f"dir: {args.dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
