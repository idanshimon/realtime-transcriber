# Feature: MAI-Transcribe-2 Batch Backend (`--backend mai`)

**Status:** Implemented (available for evaluation; no default/alias repoint)
**Owner:** idanshimon
**Created:** 2026-10-14

## Problem

RTT's highest-quality diarizing backends are `openai`
(gpt-4o-transcribe-diarize) and `llmspeech` (Azure Speech LLM Speech). Microsoft
shipped **MAI-Transcribe-2**, an LLM-enhanced transcription model that supports
**speaker diarization** and is reachable on the existing Azure Speech
fast-transcription REST surface. The stock `LLMSpeechBackend` is NOT already
MAI-ready: it always sends `maxSpeakers` and selects no model, and MAI rejects
`maxSpeakers` with `HTTP 400 "maxSpeakers is not supported for MAI transcription
yet."`

## Goal

Add a `--backend mai` option (plus a `mai` profile and `run-mai.sh`) so
MAI-Transcribe-2 can be A/B evaluated against the existing backends, WITHOUT
disturbing any default or alias routing. The parent repoints `rtt` (or any
alias) to `mai` only after a separate, deliberate live-verification decision.

## Non-goals

- Changing the `rtt` / `rttheb` / `rttold` / `rtthebold` defaults or aliases.
- MAI-Transcribe-2-**Streaming** (the Speech-SDK streaming model). That route
  was explored and dropped: the diarization-capable model Idan wants is the
  **batch** MAI-Transcribe-2, reached over the chunked HTTP path. Streaming has
  no diarization and an unbounded `stop_*_async().get()` drain risk.
- Cross-chunk speaker-number stitching (inherited chunked limitation).

## Design

### Reuse, don't rebuild

MAI-Transcribe-2 uses the SAME REST endpoint, Speech resource, auth, chunk
framing, and response shape (`phrases[].speaker/.text`) as `llmspeech`. So
`MAITranscribeBackend` **extends `LLMSpeechBackend`** and overrides only the
request `definition`:

| Aspect | `llmspeech` (stock) | `mai` |
|---|---|---|
| `enhancedMode` | `{enabled, task}` | `{enabled, task, model: "MAI-Transcribe-2"}` |
| `diarization` | `{maxSpeakers: 4, enabled}` | `{enabled}` — **no `maxSpeakers`** (400 if sent) |
| endpoint / auth / formatter | — | inherited unchanged |

The definition was refactored into `LLMSpeechBackend._build_definition()` so the
subclass overrides exactly that one method. `_transcribe_chunk`, `_post` (401
self-heal + transient retry + loud escalation), the silence gate, and
`_format_phrases` are all inherited.

### Bounded stop path

`mai` is a chunked HTTP backend, so it runs through `process_chunked_backend`,
whose stop path is already bounded: each chunk POST has a `request_timeout`, and
on stop it flushes the final partial chunk and drains the text queue. There is
NO unbounded SDK drain (contrast the Speech-SDK `azure` path, whose
`stop_continuous_recognition_async().get()` can hang — observed in a streaming
probe). This is a key reason the batch route was chosen over streaming.

### CLI / schema

- `transcribe.py`: `BACKEND_MAI = "mai"`, guard includes it, dispatch branch
  builds `MAITranscribeBackend`. Flags `--mai-endpoint`, `--mai-model`
  (default `MAI-Transcribe-2`), `--mai-locales`. Endpoint resolution:
  `--mai-endpoint` → `RTT_MAI_ENDPOINT` → `RTT_LLMSPEECH_ENDPOINT` →
  `AZURE_SPEECH_ENDPOINT`.
- `config_schema.py`: `mai` in `BACKENDS` + `BACKEND_HELP`, three `mai_*`
  Fields (chunked group), `chunk_seconds` now applies to `mai`, a `mai` profile,
  and an endpoint validation check.
- `run-mai.sh`: launcher (`run.sh --backend mai`). Eval alias — not wired into
  `.zshrc`.

### Auth

Reuses the existing pinned AAD discipline: `_build_aad_credential()` honors
`RTT_AAD_SUBSCRIPTION` (DEV sub `b3a032cf`) so minting is drift-proof; bare
bearer token on the data plane; `cog_token_provider` returns `(token,
expires_on)` for real-expiry refresh; API key supported via `AZURE_SPEECH_KEY`.

## API contract notes (verified empirically 2026-10-14)

- `enhancedMode.model = "MAI-Transcribe-2"` on
  `POST /speechtotext/transcriptions:transcribe?api-version=2025-10-15` → HTTP 200.
- `diarization` must OMIT `maxSpeakers` — including it → HTTP 400.
- Response carries `phrases[].speaker` as integers (0-indexed) → `Speaker 0:`,
  `Speaker 1:` … (differs from `azure`'s `Guest-N` and `openai`'s `Speaker A`).
- `--mai-model MAI-Transcribe-1.5` is selectable without a code change.

## Testing

### Unit (no network)
- `tests/test_mai_backend.py` (10): class hierarchy, URL surface, the
  model-selecting definition, diarization-without-maxSpeakers, model override,
  locales on/off, `_format_phrases` reuse, AND a regression guard that
  `LLMSpeechBackend` still sends `maxSpeakers` + no model.
- `tests/test_mai_cli.py` (2): `BACKEND_MAI` constant + guard-set membership.
- `tests/test_mai_schema.py` (6): registered backend, profile resolution, argv
  build, endpoint validation, field isolation, and a guard that
  `rtt`/`rttheb`/`rttold` routing is unchanged.
- Full suite: **98/98**.

### E2E (real CLI, `run-filetest.sh`, test.mp3, DEV sub pinned)
- `--backend mai --max-seconds 30 --chunk-seconds 15` → HTTP 200, `Speaker 0:`
  diarized output.
- `--backend mai --skip-seconds 300 --max-seconds 40 --chunk-seconds 20` →
  `Speaker 0 / 1 / 2` correctly attributed across a multi-speaker segment.
- Classic `azure`/`rttold` reverified independently (still emits `Guest-N`).

## Risks & mitigations

- **Risk:** chunked = not true real-time; transcript lags `chunk_seconds`.
  **Mitigation:** tunable; `rttold` remains for true-realtime.
- **Risk:** cross-chunk speaker numbers not stable.
  **Mitigation:** acceptable for A/B eval; stitching is future work.
- **Risk:** MAI model/endpoint availability changes.
  **Mitigation:** runs on the existing Speech resource; `--mai-model` lets you
  fall back to another MAI version; other backends unaffected.

## Open questions

1. Repoint `rtt` (or a new alias) to `mai` after live A/B? — deferred to parent.
2. Right default `--chunk-seconds` for MAI live use.
3. Does MAI diarization stability beat `openai` enough to switch the default?
