import pytest
import requests_mock as rm

from asr_client import (ASRClient, CloudASRClient, ASRRouter, ASRResult,
                        ASRTimeoutError, ASRUnavailableError, ASREmptyError)
import requests


ASR_URL = "http://127.0.0.1:8000"
TRANSCRIBE_URL = f"{ASR_URL}/v1/audio/transcriptions"
CLOUD_URL = "https://dashscope.example/api/v1/services/aigc/multimodal-generation/generation"
WAV_PATH = "/tmp/test.wav"


def _create_wav(path: str = WAV_PATH) -> str:
    import numpy as np
    from scipy.io import wavfile
    data = np.zeros(16000, dtype=np.int16)
    wavfile.write(path, 16000, data)
    return path


@pytest.fixture(autouse=True)
def wav_file():
    path = _create_wav()
    yield path
    import os
    if os.path.exists(path):
        os.unlink(path)


def test_successful_transcription():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, json={"text": "你好世界", "duration_ms": 500})
        client = ASRClient(endpoint=ASR_URL, max_retries=0)
        result = client.transcribe(WAV_PATH)
        assert isinstance(result, ASRResult)
        assert result.text == "你好世界"
        assert result.duration_ms == 500


def test_service_unavailable():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, status_code=503)
        client = ASRClient(endpoint=ASR_URL, max_retries=0)
        with pytest.raises(ASRUnavailableError):
            client.transcribe(WAV_PATH)


def test_connection_error():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, exc=requests.exceptions.ConnectionError("refused"))
        client = ASRClient(endpoint=ASR_URL, max_retries=0)
        with pytest.raises(ASRUnavailableError):
            client.transcribe(WAV_PATH)


def test_timeout():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, exc=requests.exceptions.Timeout("timeout"))
        client = ASRClient(endpoint=ASR_URL, max_retries=0)
        with pytest.raises(ASRTimeoutError):
            client.transcribe(WAV_PATH)


def test_empty_result():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, json={"text": "", "duration_ms": 100})
        client = ASRClient(endpoint=ASR_URL, max_retries=0)
        with pytest.raises(ASREmptyError):
            client.transcribe(WAV_PATH)


def test_retry_success():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, [
            {"status_code": 503},
            {"json": {"text": "重试成功", "duration_ms": 300}},
        ])
        client = ASRClient(endpoint=ASR_URL, max_retries=1)
        result = client.transcribe(WAV_PATH)
        assert result.text == "重试成功"


def test_retry_all_fail():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, [
            {"status_code": 503},
            {"status_code": 503},
        ])
        client = ASRClient(endpoint=ASR_URL, max_retries=1)
        with pytest.raises(ASRUnavailableError):
            client.transcribe(WAV_PATH)


def test_error_response_body():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, json={
            "error": {"code": "MODEL_BUSY", "message": "GPU queue is busy"}
        })
        client = ASRClient(endpoint=ASR_URL, max_retries=0)
        with pytest.raises(ASRUnavailableError, match="GPU queue is busy"):
            client.transcribe(WAV_PATH)


def test_health_check_ok():
    with rm.Mocker() as m:
        m.get(f"{ASR_URL}/v1/models", status_code=200)
        client = ASRClient(endpoint=ASR_URL)
        assert client.health_check() is True


def test_health_check_fail():
    with rm.Mocker() as m:
        m.get(f"{ASR_URL}/v1/models", status_code=500)
        client = ASRClient(endpoint=ASR_URL)
        assert client.health_check() is False


def _cloud_ok_body(text: str) -> dict:
    return {
        "output": {
            "choices": [
                {"finish_reason": "stop",
                 "message": {"role": "assistant", "content": [{"text": text}]}}
            ]
        },
        "usage": {},
        "request_id": "cloud-req-1",
    }


def test_cloud_success():
    with rm.Mocker() as m:
        m.post(CLOUD_URL, json=_cloud_ok_body("你好云端"))
        client = CloudASRClient(endpoint=CLOUD_URL, model="qwen3-asr-flash",
                                api_key="sk-test")
        result = client.transcribe(WAV_PATH)
        assert isinstance(result, ASRResult)
        assert result.text == "你好云端"
        assert result.backend == "cloud"
        assert result.request_id == "cloud-req-1"
        history = m.request_history[0]
        assert history.headers["Authorization"] == "Bearer sk-test"
        assert '"audio": "data:audio/wav;base64,' in history.text


def test_cloud_auth_error():
    with rm.Mocker() as m:
        m.post(CLOUD_URL, status_code=401,
               json={"code": "InvalidApiKey", "message": "bad key"})
        client = CloudASRClient(endpoint=CLOUD_URL, api_key="sk-bad")
        with pytest.raises(ASRUnavailableError, match="auth failed"):
            client.transcribe(WAV_PATH)


def test_cloud_error_envelope():
    with rm.Mocker() as m:
        m.post(CLOUD_URL, status_code=400,
               json={"code": "BadRequest", "message": "boom"})
        client = CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test")
        with pytest.raises(ASRUnavailableError, match="boom"):
            client.transcribe(WAV_PATH)


def test_cloud_empty_result():
    with rm.Mocker() as m:
        m.post(CLOUD_URL, json=_cloud_ok_body("  "))
        client = CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test")
        with pytest.raises(ASREmptyError):
            client.transcribe(WAV_PATH)


def test_router_prefers_local():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, json={"text": "本地结果", "duration_ms": 100})
        router = ASRRouter(ASRClient(endpoint=ASR_URL, max_retries=0),
                           CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test"))
        result = router.transcribe(WAV_PATH)
        assert result.text == "本地结果"
        assert result.backend == "local"
        assert len(m.request_history) == 1


def test_router_falls_back_to_cloud():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL,
               exc=requests.exceptions.ConnectionError("refused"))
        m.post(CLOUD_URL, json=_cloud_ok_body("云端兜底"))
        router = ASRRouter(ASRClient(endpoint=ASR_URL, max_retries=0),
                           CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test"))
        result = router.transcribe(WAV_PATH)
        assert result.text == "云端兜底"
        assert result.backend == "cloud"
        assert len(m.request_history) == 2


def test_router_local_empty_does_not_fall_back():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, json={"text": "", "duration_ms": 100})
        router = ASRRouter(ASRClient(endpoint=ASR_URL, max_retries=0),
                           CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test"))
        with pytest.raises(ASREmptyError):
            router.transcribe(WAV_PATH)
        assert len(m.request_history) == 1


def test_router_without_fallback_raises():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL,
               exc=requests.exceptions.ConnectionError("refused"))
        router = ASRRouter(ASRClient(endpoint=ASR_URL, max_retries=0))
        with pytest.raises(ASRUnavailableError):
            router.transcribe(WAV_PATH)


def test_router_local_timeout_falls_back():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, exc=requests.exceptions.Timeout("slow"))
        m.post(CLOUD_URL, json=_cloud_ok_body("超时兜底"))
        router = ASRRouter(ASRClient(endpoint=ASR_URL, max_retries=0),
                           CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test"))
        result = router.transcribe(WAV_PATH)
        assert result.backend == "cloud"


def test_router_cloud_primary_uses_cloud_directly():
    # asr.prefer=cloud: cloud is primary — one request, no local attempt.
    with rm.Mocker() as m:
        m.post(CLOUD_URL, json=_cloud_ok_body("云端优先"))
        router = ASRRouter(CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test"),
                           ASRClient(endpoint=ASR_URL, max_retries=0))
        result = router.transcribe(WAV_PATH)
        assert result.text == "云端优先"
        assert result.backend == "cloud"
        assert len(m.request_history) == 1


def test_router_cloud_primary_falls_back_to_local():
    with rm.Mocker() as m:
        m.post(CLOUD_URL, exc=requests.exceptions.Timeout("slow"))
        m.post(TRANSCRIBE_URL, json={"text": "本地兜底", "duration_ms": 100})
        router = ASRRouter(CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test"),
                           ASRClient(endpoint=ASR_URL, max_retries=0))
        result = router.transcribe(WAV_PATH)
        assert result.text == "本地兜底"
        assert result.backend == "local"
        assert len(m.request_history) == 2


def test_router_cloud_primary_empty_does_not_fall_back():
    with rm.Mocker() as m:
        m.post(CLOUD_URL, json=_cloud_ok_body("  "))
        router = ASRRouter(CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test"),
                           ASRClient(endpoint=ASR_URL, max_retries=0))
        with pytest.raises(ASREmptyError):
            router.transcribe(WAV_PATH)
        assert len(m.request_history) == 1


def test_cloud_health_check_stub():
    client = CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test")
    assert client.health_check() is True
    router = ASRRouter(client, ASRClient(endpoint=ASR_URL, max_retries=0))
    assert router.health_check() is True


def test_cloud_result_carries_usage_and_elapsed():
    body = _cloud_ok_body("带用量")
    body["usage"] = {"seconds": 3.88, "total_tokens": 210}
    with rm.Mocker() as m:
        m.post(CLOUD_URL, json=body)
        client = CloudASRClient(endpoint=CLOUD_URL, api_key="sk-test")
        result = client.transcribe(WAV_PATH)
        assert result.usage == {"seconds": 3.88, "total_tokens": 210}
        assert result.elapsed_ms is not None


def test_local_result_usage_defaults_empty():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, json={"text": "本地", "duration_ms": 100})
        client = ASRClient(endpoint=ASR_URL, max_retries=0)
        result = client.transcribe(WAV_PATH)
        assert result.usage == {}
        assert result.elapsed_ms is not None


def test_router_chain_first_backend_quota_error_fails_over():
    # minimax primary hits quota/auth -> dashscope answers; frontend unaware.
    with rm.Mocker() as m:
        m.post(CLOUD_URL, json=_cloud_ok_body("兜底成功"))
        class QuotaMiniMax:
            backend = "minimax"
            def transcribe(self, path):
                raise ASRUnavailableError("MiniMax ASR error: quota exhausted")
        router = ASRRouter([QuotaMiniMax(),
                            CloudASRClient(endpoint=CLOUD_URL, api_key="sk")])
        result = router.transcribe(WAV_PATH)
        assert result.backend == "cloud"
        assert result.text == "兜底成功"


def test_router_chain_all_fail_raises_last_error():
    with rm.Mocker() as m:
        class DeadA:
            backend = "a"
            def transcribe(self, path):
                raise ASRUnavailableError("a down")
        class DeadB:
            backend = "b"
            def transcribe(self, path):
                raise ASRTimeoutError("b timeout")
        router = ASRRouter([DeadA(), DeadB()])
        with pytest.raises(ASRTimeoutError):
            router.transcribe(WAV_PATH)


def test_router_chain_empty_from_any_backend_never_fails_over():
    class EmptyMiniMax:
        backend = "minimax"
        def transcribe(self, path):
            raise ASREmptyError("no speech")
    with rm.Mocker() as m:
        m.post(CLOUD_URL, json=_cloud_ok_body("不该被调用"))
        router = ASRRouter([EmptyMiniMax(),
                            CloudASRClient(endpoint=CLOUD_URL, api_key="sk")])
        with pytest.raises(ASREmptyError):
            router.transcribe(WAV_PATH)
        assert len(m.request_history) == 0


def test_router_chain_requires_backend():
    with pytest.raises(ValueError):
        ASRRouter([])


def test_router_chain_list_form_primary_and_fallback_arg():
    with rm.Mocker() as m:
        m.post(TRANSCRIBE_URL, json={"text": "第三级", "duration_ms": 1})
        class DeadA:
            backend = "a"
            def transcribe(self, path):
                raise ASRUnavailableError("down")
        local = ASRClient(endpoint=ASR_URL, max_retries=0)
        router = ASRRouter([DeadA()], fallback=local)
        assert router.backends[1] is local
        result = router.transcribe(WAV_PATH)
        assert result.backend == "local"
