"""MiniMax ASR adapter (asr-1.0) — multipart upload, Bearer auth.

Standalone client for evaluating the MiniMax speech_to_text endpoint
(POST /v1/speech_to_text). Reuses asr_client exceptions and ASRResult so
it can later slot into ASRRouter without protocol changes. Not wired
into the daemon yet; integrate only after evaluation.

The audio file is uploaded via multipart/form-data (WAV supported
natively); no public URL or base64 payload is needed.
"""
import logging
import time

import requests

from asr_client import (ASRResult, ASRTimeoutError, ASRUnavailableError,
                        ASREmptyError)

logger = logging.getLogger("voice_input.asr.minimax")

MINIMAX_ASR_DEFAULT_ENDPOINT = "https://api.minimaxi.com/v1/speech_to_text"


class MiniMaxASRClient:
    """MiniMax speech_to_text client (model asr-1.0)."""

    def __init__(self, endpoint: str = MINIMAX_ASR_DEFAULT_ENDPOINT,
                 model: str = "asr-1.0", api_key: str = "", timeout: int = 20,
                 response_format: str = "json", timestamp_level: str = ""):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.response_format = response_format
        self.timestamp_level = timestamp_level
        self.backend = "minimax"
        # Keep-alive session: reuse TCP+TLS across transcriptions.
        self._session = requests.Session()

    def transcribe(self, wav_path: str) -> ASRResult:
        f = open(wav_path, "rb")
        files = {"file": ("audio.wav", f, "audio/wav")}
        data = {"model": self.model, "response_format": self.response_format}
        if self.timestamp_level:
            data["timestamp_level"] = self.timestamp_level
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            start = time.monotonic()
            resp = self._session.post(self.endpoint, files=files, data=data,
                                      headers=headers,
                                      timeout=(5, self.timeout))
            elapsed_ms = (time.monotonic() - start) * 1000
        except requests.exceptions.Timeout as e:
            raise ASRTimeoutError("MiniMax ASR request timed out") from e
        except requests.exceptions.RequestException as e:
            raise ASRUnavailableError("MiniMax ASR request failed") from e
        finally:
            f.close()

        if resp.status_code in (401, 403):
            raise ASRUnavailableError(
                f"MiniMax ASR auth failed (HTTP {resp.status_code})")

        try:
            body = resp.json()
        except ValueError as e:
            raise ASRUnavailableError(
                f"MiniMax ASR invalid response (HTTP {resp.status_code})") from e

        # MiniMax uses an OpenAI-style envelope; success may carry either
        # no error key or {"base_resp": {"status_code": 0, ...}}.
        err = body.get("base_resp") or body.get("error") or {}
        err_status = err.get("status_code", 0) if isinstance(err, dict) else 0
        if resp.status_code >= 400 or (isinstance(err, dict) and err_status):
            message = (err.get("status_msg") or err.get("message")
                       if isinstance(err, dict) else None) or f"HTTP {resp.status_code}"
            raise ASRUnavailableError(f"MiniMax ASR error: {message}")

        text = (body.get("text") or "").strip()
        if not text:
            raise ASREmptyError("MiniMax ASR returned empty text")

        request_id = body.get("trace_id", "")
        usage = {}
        if body.get("duration") is not None:
            usage["seconds"] = body["duration"]  # audio duration, billed unit
        if "n_speakers" in body:
            usage["n_speakers"] = body["n_speakers"]
        if isinstance(body.get("usage"), dict):
            usage.update(body["usage"])

        logger.info("[asr] backend=%s latency=%.0fms text_len=%d trace_id=%s",
                    self.backend, elapsed_ms, len(text), request_id)
        return ASRResult(text=text, duration_ms=elapsed_ms,
                         request_id=request_id, backend=self.backend,
                         usage=usage, elapsed_ms=elapsed_ms)

    def health_check(self) -> bool:
        # No free liveness endpoint; failures surface at transcribe time.
        return True
