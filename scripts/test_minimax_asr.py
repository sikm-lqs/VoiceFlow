#!/usr/bin/env python3
"""Test harness for the MiniMax ASR adapter (standalone, no daemon changes).

Usage:
  # 用现有 WAV 实测（可多文件）:
  python3 scripts/test_minimax_asr.py file1.wav [file2.wav ...]

  # 现场录音测试（默认 6 秒，按回车倒计时后说话）:
  python3 scripts/test_minimax_asr.py --record 6

  # 同一文件重复调用看延迟分布:
  python3 scripts/test_minimax_asr.py --repeat 5 file.wav

  # verbose_json：含分段/字级时间戳:
  python3 scripts/test_minimax_asr.py --verbose-json file.wav

Key resolution (never printed): --key > $MINIMAX_ASR_KEY/$MM_KEY >
key-file (default: the Tem-Proj env.sh). Run from the repo root:
  cd /home/sikm/Project/AI-Project/TTS && uv run python scripts/test_minimax_asr.py
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DEFAULT_KEY_FILE = Path(
    "/home/sikm/Project/Tem-Proj/19-MiniMax-Plan-Test/scripts/env.sh")


def _clean_env_value(raw):
    """Strip trailing comments, quotes and shell defaults: ${VAR:-x} -> x."""
    value = re.sub(r'\s+#.*$', "", raw.strip()).strip()
    value = value.strip('"').strip("'")
    m = re.match(r"^\$\{[A-Za-z_][A-Za-z_0-9]*:-(.*)\}$", value)
    if m:
        value = m.group(1)
    return value


def load_credentials(key_file):
    """Return (api_key, base_url); env vars win over the key file."""
    api_key = os.environ.get("MINIMAX_ASR_KEY") or os.environ.get("MM_KEY") or ""
    base = os.environ.get("MM_BASE") or ""
    if not api_key and key_file.is_file():
        text = key_file.read_text(encoding="utf-8")
        m = re.search(r'^\s*(?:export\s+)?MM_KEY\s*=\s*(.+)$', text, re.M)
        if m:
            api_key = _clean_env_value(m.group(1))
        if not base:
            m = re.search(r'^\s*(?:export\s+)?MM_BASE\s*=\s*(.+)$', text, re.M)
            if m:
                base = _clean_env_value(m.group(1))
    return api_key, base


def record_wav(seconds, path):
    import sounddevice as sd
    import numpy as np  # noqa: F401  (sounddevice returns numpy arrays)
    print(f"2 秒后开始录音 {seconds}s — 请准备说话...", flush=True)
    time.sleep(2)
    rec = sd.rec(int(16000 * seconds), samplerate=16000, channels=1,
                 dtype="int16")
    sd.wait()
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(rec.tobytes())
    print(f"已保存录音: {path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wavs", nargs="*", help="WAV 文件路径")
    parser.add_argument("--record", type=float, metavar="SEC",
                        help="现场录音 SEC 秒后识别")
    parser.add_argument("--repeat", type=int, default=1,
                        help="每个文件重复调用次数（默认 1）")
    parser.add_argument("--verbose-json", action="store_true",
                        help="response_format=verbose_json，打印分段")
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE,
                        help="含 MM_KEY/MM_BASE 的 env 文件")
    args = parser.parse_args()

    api_key, base = load_credentials(args.key_file)
    if not api_key:
        print("未找到 key（--key-file / $MM_KEY / $MINIMAX_ASR_KEY）",
              file=sys.stderr)
        return 2
    endpoint = (base.rstrip("/") if base else
                "https://api.minimaxi.com") + "/v1/speech_to_text"

    from minimax_asr import MiniMaxASRClient
    from asr_client import ASREmptyError, ASRTimeoutError, ASRUnavailableError
    client = MiniMaxASRClient(
        endpoint=endpoint, api_key=api_key, timeout=30,
        response_format="verbose_json" if args.verbose_json else "json")

    if args.record:
        wav = REPO / "build" / "minimax-asr-test.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        record_wav(args.record, wav)
        args.wavs = [str(wav)]
    if not args.wavs:
        parser.error("请提供 WAV 文件或使用 --record")

    latencies = []
    ok = 0
    for wav in args.wavs:
        for i in range(args.repeat):
            label = Path(wav).name if args.repeat == 1 else f"{Path(wav).name}#{i+1}"
            try:
                r = client.transcribe(wav)
                ok += 1
                latencies.append(r.elapsed_ms)
                secs = r.usage.get("seconds")
                print(f"[{label}] {r.elapsed_ms:.0f}ms  "
                      f"audio={secs if secs is not None else '-'}s  "
                      f"backend={r.backend}  trace={r.request_id}")
                print(f"  text: {r.text}")
                if args.verbose_json and r.usage.get("n_speakers") is not None:
                    print(f"  n_speakers={r.usage['n_speakers']}")
            except (ASREmptyError, ASRTimeoutError, ASRUnavailableError) as e:
                print(f"[{label}] FAILED: {type(e).__name__}: {e}")
    total = len(args.wavs) * args.repeat
    print("-" * 60)
    print(f"成功 {ok}/{total}", end="")
    if latencies:
        print(f"  延迟 avg {sum(latencies)/len(latencies):.0f}ms  "
              f"min {min(latencies):.0f}  max {max(latencies):.0f}")
    else:
        print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
