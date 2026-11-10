from scripts.benchmark_cleanup import samples, summarize


def test_benchmark_samples_are_synthetic_and_different_lengths():
    texts = samples()
    assert [name for name, _ in texts] == ["short", "medium", "long"]
    assert 30 <= len(texts[0][1]) <= 80
    assert 150 <= len(texts[1][1]) <= 300
    assert 800 <= len(texts[2][1]) <= 1200


def test_summary_does_not_count_timeouts_as_completed_latencies():
    summary = summarize([
        {"model": "M", "sample": "short", "ok": True, "elapsed_ms": 100, "ttft_ms": 50, "valid": True},
        {"model": "M", "sample": "short", "ok": True, "elapsed_ms": 300, "ttft_ms": 80, "valid": True},
        {"model": "M", "sample": "short", "ok": False, "elapsed_ms": 500, "error": "timeout"},
    ])[0]
    assert summary["completed"] == 2
    assert summary["failed"] == 1
    assert summary["complete_p50_ms"] == 200
    assert summary["complete_p95_ms"] == 300
    assert summary["within_300ms"] == 2
    assert summary["within_500ms"] == 2
