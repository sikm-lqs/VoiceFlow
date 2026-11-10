import json
import sys
import time

import pytest
import requests_mock

from minimax_cleanup import MiniMaxCleaner, MiniMaxHTTPClient, ProviderError, build_payload
from text_cleanup import TextCleaner, validate_smart_output

ENDPOINT = "https://api.minimax.cn/anthropic/v1/messages"


def events(text="测试。", stop="end_turn"):
    return "\n\n".join("data: " + json.dumps(event, ensure_ascii=False) for event in [
        {"type": "message_start", "message": {"usage": {"input_tokens": 20}}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "DO NOT RETURN"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 8}},
        {"type": "message_stop"},
    ]) + "\n\n"


def test_model_specific_low_latency_parameters():
    flash = build_payload("测试", "MiniMax-M3.1-Flash", 2048)
    assert flash["output_config"] == {"effort": "low"}
    assert flash.get("thinking") != {"type": "disabled"}
    m3 = build_payload("测试", "MiniMax-M3", 2048)
    assert m3["thinking"] == {"type": "disabled"}
    assert "output_config" not in m3


def test_unknown_model_fails_before_network():
    with pytest.raises(ValueError):
        build_payload("测试", "unknown-model", 2048)


def test_stream_ignores_reasoning_and_collects_text_usage():
    with requests_mock.Mocker() as mock:
        mock.post(ENDPOINT, text=events())
        client = MiniMaxHTTPClient()
        result = client.clean("嗯，测试。", "test-key", "MiniMax-M3")
        client.close()
        assert result["text"] == "测试。"
        assert result["usage"] == {"input_tokens": 20, "output_tokens": 8}
        assert result["ttft_ms"] <= result["api_ms"]
        assert mock.last_request.headers["x-api-key"] == "test-key"
        assert mock.last_request.json()["stream"] is True


def test_redirect_is_not_followed():
    with requests_mock.Mocker() as mock:
        mock.post(ENDPOINT, status_code=302, headers={"Location": "https://attacker.example/"})
        with pytest.raises(ProviderError, match="http_302"):
            MiniMaxHTTPClient().clean("测试", "SECRET", "MiniMax-M3")
        assert mock.call_count == 1


@pytest.mark.parametrize("body", [
    events(stop="max_tokens"),
    'data: {"type":"error","error":{"message":"SECRET"}}\n\n',
    'data: not-json\n\n',
    events().replace('data: {"type": "message_stop"}', ""),
])
def test_incomplete_or_error_stream_rejected_without_body_leak(body):
    with requests_mock.Mocker() as mock:
        mock.post(ENDPOINT, text=body)
        with pytest.raises(ProviderError) as error:
            MiniMaxHTTPClient().clean("测试", "SECRET", "MiniMax-M3")
        assert "SECRET" not in str(error.value)


def test_http_errors_never_include_response_body():
    with requests_mock.Mocker() as mock:
        mock.post(ENDPOINT, status_code=401, text="SECRET")
        with pytest.raises(ProviderError, match="http_401") as error:
            MiniMaxHTTPClient().clean("测试", "SECRET", "MiniMax-M3")
        assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("endpoint", [
    "http://api.minimax.cn/anthropic/v1/messages",
    "https://attacker.example/anthropic/v1/messages",
    "https://api.minimax.cn@attacker.example/anthropic/v1/messages",
    "https://api.minimax.cn/other",
])
def test_only_documented_https_endpoints_allowed(endpoint):
    with pytest.raises(ValueError):
        MiniMaxHTTPClient(endpoint=endpoint)


def test_worker_deadline_kills_process_and_has_no_late_result(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    client = MiniMaxCleaner(
        {"timeout_seconds": 0.08},
        _command=[sys.executable, "-c", "import time; time.sleep(10)"],
    )
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        client.clean("测试")
    assert time.monotonic() - start < 1.0
    assert client._process is None
    client.close()


def test_worker_reuses_process_and_does_not_put_key_in_arguments(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    command = [sys.executable, "-u", "-c", (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " print(json.dumps({'text':r['text'],'api_ms':0,'ttft_ms':0,'usage':{}}),flush=True)\n"
    )]
    client = MiniMaxCleaner({"timeout_seconds": 2}, _command=command)
    try:
        assert client.clean("第一条")["worker_reused"] is False
        process = client._process
        assert client.clean("第二条")["worker_reused"] is True
        assert client._process is process
        assert "test-key" not in " ".join(process.args)
    finally:
        client.close()
    assert process.poll() is not None


def test_missing_key_never_starts_worker(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    client = MiniMaxCleaner({})
    with pytest.raises(ProviderError, match="missing_key"):
        client.clean("测试")
    assert client._process is None


@pytest.mark.parametrize("output", [
    "把价格改成 500 元。",
    "把价格改成 300 元。",
    "不要把价格改成 300 元。这里是新增的另一项很长很长的完整计划，请继续执行。",
    "",
])
def test_obvious_semantic_damage_rejected(output):
    assert not validate_smart_output("不要把价格改成 300 元。", output)


def test_structure_terms_numbers_and_negations_preserved():
    raw = "嗯，不要改 Qwen3-ASR，保留 3.14 和 `/tmp/嗯.txt`。"
    assert validate_smart_output(raw, "不要改 Qwen3-ASR，保留 3.14 和 `/tmp/嗯.txt`。")
    assert not validate_smart_output(raw, "不要改 Qwen3-ASR，保留 3.14 和 `/tmp/x.txt`。")


class FakeSmart:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def clean(self, text):
        if self.error:
            raise self.error
        return {"text": self.value}

    def close(self):
        pass


@pytest.mark.parametrize("error", [TimeoutError("secret"), RuntimeError("secret")])
def test_cloud_failure_falls_back_to_rules_without_secret_logging(error, caplog):
    cleaner = TextCleaner({"enabled": True, "mode": "minimax"}, FakeSmart(error=error))
    result = cleaner.clean("呃，测试。")
    assert result.text == "测试。"
    assert result.mode == "rules"
    assert result.fallback_reason
    assert "secret" not in caplog.text


def test_invalid_cloud_output_falls_back_to_rules():
    cleaner = TextCleaner({"enabled": True, "mode": "minimax"}, FakeSmart(value="改成 500 元。"))
    result = cleaner.clean("呃，不要改成 300 元。")
    assert result.text == "不要改成 300 元。"
    assert result.fallback_reason == "invalid_output"


def test_valid_cloud_output_is_returned():
    cleaner = TextCleaner({"enabled": True, "mode": "minimax"}, FakeSmart(value="测试接口。"))
    assert cleaner.clean("嗯，测试接口。").mode == "minimax"
