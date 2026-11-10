"""Conservative, dependency-free transcript cleanup. No model or I/O in rules mode."""

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass

logger = logging.getLogger("voice_input.cleanup")

# Protect whole structured/literal regions, including unfinished code/quotes.
# Never remove bracket contents as a supposed ASR hallucination.
_PROTECTED = re.compile(
    r"```[\s\S]*?(?:```|$)|`[^`]*(?:`|$)"
    r'|“[^”]*(?:”|$)|「[^」]*(?:」|$)|『[^』]*(?:』|$)'
    r'|"(?:\\.|[^"\\])*(?:"|$)'
    r"|'(?:\\.|[^'\\])*(?:'|$)"
    r"|https?://[^\s，。！？；“”<>]+"
    r"|[\w.+-]+@[\w.-]+\.\w+"
    r"|(?:[A-Za-z]:\\|(?<!\w)/)[^\s，。！？；“”<>]+",
    re.UNICODE,
)
_CONTENT = re.compile(r"[\w\u3400-\u9fff]", re.UNICODE)
_NUMBERS = re.compile(r"\d+(?:[./:-]\d+)*%?")
_NEGATIONS = re.compile(r"不要|不能|不必|不需要|不允许|没有|不是|不得|未|无|不|\b(?:not|never|no)\b", re.I)
_TERMS = re.compile(r"[A-Za-z_][A-Za-z_0-9.+-]*")


class RuleCleaner:
    def __init__(self, fillers=None):
        fillers = ["呃", "嗯"] if fillers is None else fillers
        if not isinstance(fillers, list) or any(
            not isinstance(word, str) or not word or len(word) > 16 for word in fillers
        ):
            raise ValueError("cleanup.fillers must be a list of nonempty short strings")
        tokens = "|".join("(?:%s)+" % re.escape(word) for word in sorted(set(fillers), key=len, reverse=True))
        self._pattern = re.compile(
            r"(?<!\w)(?:%s)(?=[ \t，,。.!！？?；;：:]|$)" % tokens
        ) if tokens else None

    def clean(self, text):
        if not text or self._pattern is None:
            return text
        protected = [match.span() for match in _PROTECTED.finditer(text)]
        pieces, resume, changed = [], 0, False
        for match in self._pattern.finditer(text):
            start, end = match.span()
            if start < resume or any(start < right and end > left for left, right in protected):
                continue
            tail = end
            while tail < len(text) and text[tail] in " \t":
                tail += 1
            # An acknowledgement or questioning "嗯？" is not a filler.
            if tail < len(text) and text[tail] in "?？!！":
                continue
            if tail < len(text) and text[tail] in ",，":
                tail += 1
                while tail < len(text) and text[tail] in " \t":
                    tail += 1
            before = text[resume:start]
            if tail < len(text) and text[tail] in "。.;；:：":
                # A deleted hesitation can bring a comma next to a stronger
                # separator. Repair only that site, never global punctuation.
                before = before.rstrip(" \t")
                if before.endswith((",", "，")):
                    before = before[:-1]
            prefix = "".join(pieces) + before
            # Remove only punctuation made orphaned at this deletion site.
            if not prefix.rstrip(" \t") or prefix.rstrip().endswith(("。", ".", "!", "！", "?", "？", "\n")):
                while tail < len(text) and text[tail] in "，,。；;：: \t":
                    tail += 1
            if before.endswith((" ", "\t")):
                before = before.rstrip(" \t")
                previous = ("".join(pieces) + before)[-1:]
                following = text[tail:tail + 1]
                if previous and following and previous.isascii() and following.isascii() and previous.isalnum() and following.isalnum():
                    before += " "
            pieces.append(before)
            resume, changed = tail, True
        if not changed:
            return text
        result = "".join(pieces) + text[resume:]
        return result if _CONTENT.search(result) else text


def validate_smart_output(raw, output):
    """Fail closed on obvious damage. These checks do NOT prove semantic equality."""
    if not isinstance(output, str) or not _CONTENT.search(output):
        return False
    if len(output) < len(raw) * 0.45 or len(output) > len(raw) * 1.25 + 8:
        return False
    if Counter(_NUMBERS.findall(raw)) != Counter(_NUMBERS.findall(output)):
        return False
    if Counter(_NEGATIONS.findall(raw)) != Counter(_NEGATIONS.findall(output)):
        return False
    if any(match.group() not in output for match in _PROTECTED.finditer(raw)):
        return False
    if any(term not in output for term in set(_TERMS.findall(raw))):
        return False
    if output.startswith(("清洗后的文本：", "清洗后：", "Cleaned transcript:")) and not raw.startswith(output.split("：")[0]):
        return False
    return True


@dataclass
class CleanupResult:
    text: str
    mode: str
    elapsed_ms: float
    fallback_reason: str = None


class TextCleaner:
    def __init__(self, config=None, smart_client=None):
        cfg = config or {}
        self.mode = cfg.get("mode", "rules") if cfg.get("enabled", False) else "off"
        if self.mode not in ("off", "rules", "minimax"):
            raise ValueError("cleanup.mode must be off, rules or minimax")
        self.rules = RuleCleaner(cfg.get("fillers"))
        self.smart = smart_client
        if self.mode == "minimax" and self.smart is None:
            from minimax_cleanup import MiniMaxCleaner
            self.smart = MiniMaxCleaner(cfg.get("minimax") or {})

    def clean(self, text):
        start = time.monotonic()
        if self.mode == "off":
            return CleanupResult(text, "off", 0.0)
        fallback = text
        try:
            fallback = self.rules.clean(text)
            if self.mode == "rules":
                return CleanupResult(fallback, "rules", (time.monotonic() - start) * 1000)
            response = self.smart.clean(text)
            if not validate_smart_output(text, response["text"]):
                return CleanupResult(fallback, "rules", (time.monotonic() - start) * 1000, "invalid_output")
            return CleanupResult(response["text"], "minimax", (time.monotonic() - start) * 1000)
        except Exception as exc:
            # Never log exception messages: third-party exceptions may contain
            # credentials, transcript text, headers or response bodies.
            reason = "timeout" if isinstance(exc, TimeoutError) else "cleanup_failed"
            logger.warning("cleanup fallback reason=%s exception_type=%s", reason, type(exc).__name__)
            return CleanupResult(fallback, "rules", (time.monotonic() - start) * 1000, reason)

    def close(self):
        if self.smart is not None:
            self.smart.close()
