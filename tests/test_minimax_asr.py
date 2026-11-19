import pytest
import requests
import requests_mock as rm

from asr_client import ASREmptyError, ASRTimeoutError, ASRUnavailableError
from minimax_asr import MiniMaxASRClient

ASR_URL = "https://api.minimaxi.example/v1/speech_to_text"
WAV_PATH = "/tmp/test-minimax-asr.wav"


@pytest.fixture(autouse=True)
def wav_file():
    import struct
    import wave
    with wave.open(WAV_PATH, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * 1600)
    yield WAV_PATH
    import os
    if os.path.exists(WAV_PATH):
        os.unlink(WAV_PATH)


def test_success_json_shape():
    with rm.Mocker() as m:
        m.post(ASR_URL, json={
            "text": "你好，MiniMax。",
            "duration": 3.5,
            "trace_id": "trace-1",
        })
        client = MiniMaxASRClient(endpoint=ASR_URL, api_key="sk-test")
        result = client.transcribe(WAV_PATH)
        assert result.text == "你好，MiniMax。"
        assert result.backend == "minimax"
        assert result.request_id == "trace-1"
        assert result.usage["seconds"] == 3.5
        assert result.elapsed_ms is not None
        # request shape: multipart fields + Bearer auth
        req = m.request_history[0]
        assert req.headers["Authorization"] == "Bearer sk-test"
        body = req.body if isinstance(req.body, bytes) else req.body.encode()
        assert b'name="model"' in body and b"asr-1.0" in body
        assert b'name="response_format"' in body
        assert b'name="file"; filename="audio.wav"' in body
        assert b"timestamp_level" not in body  # only sent when requested


def test_verbose_json_carries_speakers():
    with rm.Mocker() as m:
        m.post(ASR_URL, json={
            "text": "你好。",
            "duration": 1.0,
            "n_speakers": 1,
            "segments": [{"id": 0, "start": 0.0, "end": 0.5,
                          "speaker": "S1", "text": "你好。"}],
            "trace_id": "t2",
        })
        client = MiniMaxASRClient(endpoint=ASR_URL, api_key="k",
                                  response_format="verbose_json",
                                  timestamp_level="word")
        result = client.transcribe(WAV_PATH)
        assert result.usage["n_speakers"] == 1
        body = m.request_history[0].body
        if not isinstance(body, bytes):
            body = body.encode()
        assert b"verbose_json" in body and b"timestamp_level" in body


def test_auth_error():
    with rm.Mocker() as m:
        m.post(ASR_URL, status_code=401,
               json={"base_resp": {"status_code": 1004, "status_msg": "invalid key"}})
        client = MiniMaxASRClient(endpoint=ASR_URL, api_key="bad")
        with pytest.raises(ASRUnavailableError, match="auth failed"):
            client.transcribe(WAV_PATH)


def test_base_resp_error_envelope():
    with rm.Mocker() as m:
        m.post(ASR_URL, status_code=200,
               json={"base_resp": {"status_code": 1004,
                                    "status_msg": "quota exhausted"}})
        client = MiniMaxASRClient(endpoint=ASR_URL, api_key="k")
        with pytest.raises(ASRUnavailableError, match="quota exhausted"):
            client.transcribe(WAV_PATH)


def test_openai_style_error_envelope():
    with rm.Mocker() as m:
        m.post(ASR_URL, status_code=400,
               json={"error": {"message": "audio too long"}})
        client = MiniMaxASRClient(endpoint=ASR_URL, api_key="k")
        with pytest.raises(ASRUnavailableError, match="audio too long"):
            client.transcribe(WAV_PATH)


def test_empty_text_raises():
    with rm.Mocker() as m:
        m.post(ASR_URL, json={"text": "   ", "duration": 2.0})
        client = MiniMaxASRClient(endpoint=ASR_URL, api_key="k")
        with pytest.raises(ASREmptyError):
            client.transcribe(WAV_PATH)


def test_timeout_maps():
    with rm.Mocker() as m:
        m.post(ASR_URL, exc=requests.exceptions.Timeout("slow"))
        client = MiniMaxASRClient(endpoint=ASR_URL, api_key="k")
        with pytest.raises(ASRTimeoutError):
            client.transcribe(WAV_PATH)


def test_health_check_stub():
    assert MiniMaxASRClient(endpoint=ASR_URL, api_key="k").health_check() is True
