import base64
import logging
import time

import requests

logger = logging.getLogger("voice_input.asr")

CLOUD_ASR_DEFAULT_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)


class ASRTimeoutError(Exception):
    pass


class ASRUnavailableError(Exception):
    pass


class ASREmptyError(Exception):
    pass


class ASRResult:
    def __init__(self, text: str, duration_ms: float, request_id: str = "",
                 backend: str = "local"):
        self.text = text
        self.duration_ms = duration_ms
        self.request_id = request_id
        self.backend = backend

    def __repr__(self) -> str:
        return (f"ASRResult(text={self.text!r}, duration_ms={self.duration_ms}, "
                f"backend={self.backend!r})")


class ASRClient:
    def __init__(self, endpoint: str = "http://127.0.0.1:8000",
                 timeout: int = 20, max_retries: int = 1,
                 language: str = "zh", task: str = "transcribe",
                 prompt: str = "", model: str = ""):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.language = language
        self.task = task
        self.prompt = prompt
        self.model = model
        self.backend = "local"

    def transcribe(self, wav_path: str) -> ASRResult:
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._do_request(wav_path)
            except (ASRTimeoutError, ASRUnavailableError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    logger.warning("ASR attempt %d failed (%s), retrying...",
                                   attempt + 1, type(e).__name__)
                    time.sleep(0.3)
                continue

        raise last_exc

    def _do_request(self, wav_path: str) -> ASRResult:
        url = f"{self.endpoint}/v1/audio/transcriptions"
        f = open(wav_path, "rb")
        files = {"file": ("audio.wav", f, "audio/wav")}
        data = {"model": self.model}
        if self.language:
            data["language"] = self.language
        if self.prompt:
            data["prompt"] = self.prompt

        try:
            start = time.monotonic()
            resp = requests.post(url, files=files, data=data,
                                 timeout=(2, self.timeout))
            elapsed_ms = (time.monotonic() - start) * 1000
        except requests.exceptions.Timeout as e:
            raise ASRTimeoutError("ASR request timed out") from e
        except requests.exceptions.ConnectionError as e:
            raise ASRUnavailableError("ASR service unreachable") from e
        finally:
            f.close()

        if resp.status_code == 503:
            raise ASRUnavailableError(f"ASR busy: {resp.text}")
        if resp.status_code >= 500:
            raise ASRUnavailableError(f"ASR server error: {resp.status_code}")
        if resp.status_code >= 400:
            raise ASRUnavailableError(f"ASR client error: {resp.status_code}")

        body = resp.json()
        if "error" in body:
            raise ASRUnavailableError(body["error"].get("message", str(body["error"])))

        text = body.get("text", "").strip()
        if not text:
            raise ASREmptyError("ASR returned empty text")

        duration = body.get("duration_ms", elapsed_ms)
        request_id = body.get("request_id", "")

        logger.info("[asr] backend=%s latency=%.0fms text_len=%d request_id=%s",
                    self.backend, elapsed_ms, len(text), request_id)
        return ASRResult(text=text, duration_ms=duration, request_id=request_id,
                         backend=self.backend)

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{self.endpoint}/v1/models", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False


class CloudASRClient:
    """DashScope multimodal-generation ASR client (e.g. qwen3-asr-flash).

    Sends local WAV audio as a base64 data URI; no public URL required.
    """

    def __init__(self, endpoint: str = CLOUD_ASR_DEFAULT_ENDPOINT,
                 model: str = "qwen3-asr-flash", api_key: str = "",
                 timeout: int = 20, language: str = "",
                 enable_itn: bool = True):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.language = language
        self.enable_itn = enable_itn
        self.backend = "cloud"

    def transcribe(self, wav_path: str) -> ASRResult:
        start = time.monotonic()
        try:
            with open(wav_path, "rb") as f:
                audio = f.read()
        except OSError as e:
            raise ASRUnavailableError(f"cannot read audio file: {e}") from e

        data_uri = "data:audio/wav;base64," + base64.b64encode(audio).decode()
        asr_options = {"enable_itn": bool(self.enable_itn)}
        if self.language:
            asr_options["language"] = self.language
        payload = {
            "model": self.model,
            "input": {
                "messages": [
                    {"role": "user", "content": [{"audio": data_uri}]}
                ]
            },
            "parameters": {
                "result_format": "message",
                "asr_options": asr_options,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-SSE": "disable",
        }

        try:
            resp = requests.post(self.endpoint, json=payload, headers=headers,
                                 timeout=(5, self.timeout))
        except requests.exceptions.Timeout as e:
            raise ASRTimeoutError("cloud ASR request timed out") from e
        except requests.exceptions.RequestException as e:
            raise ASRUnavailableError(f"cloud ASR request failed: {e}") from e

        elapsed_ms = (time.monotonic() - start) * 1000

        if resp.status_code in (401, 403):
            raise ASRUnavailableError(
                f"cloud ASR auth failed (HTTP {resp.status_code})")

        try:
            body = resp.json()
        except ValueError as e:
            raise ASRUnavailableError(
                f"cloud ASR invalid response (HTTP {resp.status_code})") from e

        if resp.status_code >= 400 or body.get("code"):
            message = body.get("message") or f"HTTP {resp.status_code}"
            raise ASRUnavailableError(f"cloud ASR error: {message}")

        text = self._extract_text(body)
        if not text:
            raise ASREmptyError("cloud ASR returned empty text")

        request_id = body.get("request_id", "")
        logger.info("[asr] backend=%s latency=%.0fms text_len=%d request_id=%s",
                    self.backend, elapsed_ms, len(text), request_id)
        return ASRResult(text=text, duration_ms=elapsed_ms,
                         request_id=request_id, backend=self.backend)

    @staticmethod
    def _extract_text(body: dict) -> str:
        try:
            content = body["output"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return "".join(parts).strip()
        return ""


class ASRRouter:
    """Local-first ASR router with optional cloud fallback.

    Falls back to the cloud client only when the local backend is
    unavailable (connection refused, timeout, 5xx). An empty result from
    a healthy local backend is treated as "no speech" and does not
    trigger the fallback.
    """

    def __init__(self, primary: ASRClient, fallback: CloudASRClient | None = None):
        self.primary = primary
        self.fallback = fallback

    def transcribe(self, wav_path: str) -> ASRResult:
        try:
            return self.primary.transcribe(wav_path)
        except ASREmptyError:
            raise
        except (ASRTimeoutError, ASRUnavailableError) as e:
            if self.fallback is None:
                raise
            logger.warning("[asr] local backend failed (%s), falling back to cloud",
                           type(e).__name__)
            return self.fallback.transcribe(wav_path)

    def health_check(self) -> bool:
        return self.primary.health_check()
