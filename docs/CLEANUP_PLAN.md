# Lightweight transcript cleanup

Date: 2026-11-10 (Asia/Shanghai). Status: implementation in development source only.

## Scope

Keep the existing recorder, ASR router, Rust/UI protocol and paste behavior.
Add a standard-library-only conservative rules pass after successful ASR.
Missing cleanup configuration means disabled (backwards compatible); the
development example opts into rules. No new local model or dependency.

Modes: `off`, `rules`, `minimax`. An explicit environment override
`VOICEFLOW_CLEANUP_MODE` may select a mode at process startup. Smart cleanup
is opt-in and sends transcript text to MiniMax, not audio. Never persist keys
or log transcripts in the new cleanup code.

## Rule contract

- Delete only configured hesitation tokens with explicit standalone boundaries.
- Preserve quoted strings, backtick code, URLs, email addresses and paths.
- Keep ambiguous words such as 就是/然后/那个/啊 unchanged by default.
- Repair only punctuation/spacing adjacent to a deletion, preserve paragraphs.
- Preserve standalone acknowledgements/questions and return raw text if cleanup
  is empty or punctuation-only. Do not perform ITN, paraphrasing or punctuation
  restoration unrelated to a deletion.

## Smart cleanup contract

Use MiniMax's documented Anthropic-compatible interface. M3.1-Flash uses
`output_config.effort=low`; M3 uses `thinking.type=disabled`. These differ:
https://platform.minimax.cn/docs/api-reference/text-anthropic-api

API key is read from an environment variable or hidden benchmark prompt.
Credentials never appear in command arguments, repository configuration or
result files. Only approved HTTPS MiniMax hosts, no redirects/retries.

An optional reusable HTTP worker is a separate Python process: the parent
enforces a total wall-clock deadline and terminates it on timeout. This avoids
uncancellable background request threads and late duplicate output. No worker
exists in off/rules mode. Failures, truncation or failed output checks fall
back to rules. Numeric, negation and structured-token checks are conservative
heuristics, not a semantic equivalence proof.

## Validation and delivery

1. Tests before rule implementation, then rule performance measurements.
2. Mocked provider/protocol, deadline and fallback tests.
3. Synthetic-text real API benchmark, recording first text token and complete
   text, first/reused connections, per-model/length P50/P95 and failures.
4. Sidecar integration tests without recording, pasting or restarting services.
5. Update setuptools, deb inclusion and configuration documentation.
6. Run full Python tests and package staging verification. Do not install or
   restart the production service during this task.

Preserve pre-existing uncommitted changes. No automatic commits or pushes.
