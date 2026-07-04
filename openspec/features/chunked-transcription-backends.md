# Feature: Chunked HTTP Transcription Backends (openai / llmspeech)

**Status:** Implemented (branch `feat/speaker-rename-hotkey`)
**Owner:** idanshimon
**Created:** 2026-07-04

## Problem

RTT's Azure backend uses the Speech SDK `ConversationTranscriber` — the classic
real-time engine. Microsoft's newer, higher-accuracy speech models are NOT
reachable through the Speech SDK:

- **gpt-4o-transcribe-diarize** (Azure OpenAI) — better WER, native diarization,
  ~1/3 the per-hour cost of classic Speech. Reachable only via the Azure OpenAI
  `/audio/transcriptions` REST API.
- **LLM Speech** (Azure Speech fast-transcription `enhancedMode`) — LLM-enhanced
  quality, multilingual auto-detect (handles Hebrew/English code-switching),
  diarization. Reachable only via the `/speechtotext/transcriptions:transcribe`
  REST API.

Neither supports the continuous-streaming push model the SDK backend uses.

## Goal

Add two new `--backend` options so the newer models can be A/B tested against
the classic engine without disturbing the proven path:

- `--backend openai`    → gpt-4o-transcribe-diarize (new default for `rtt`)
- `--backend llmspeech` → LLM Speech enhancedMode (new default for `rttheb`)

The classic Speech SDK backend stays exactly as-is (`--backend azure`), reachable
via the preserved `rttold` / `rtthebold` aliases.

## Non-goals

- Replacing the Speech SDK backend (it remains the true-realtime fallback,
  especially for Hebrew where sub-second latency matters).
- True streaming for the new models (they are request/response; see Design).
- Cross-chunk speaker-label stitching (a future enhancement).

## Design

### Micro-batch architecture

Both new models are request/response, not continuous streams. So both backends
share a **micro-batch** design (`ChunkedHTTPBackend` base class in
`chunked_backends.py`):

1. Audio frames from the capture queue are buffered.
2. When `--chunk-seconds` (default 15s) of audio has accumulated, the chunk is
   WAV-encoded (16-bit PCM mono) and POSTed.
3. Returned text (with speaker labels when present) is emitted to the transcript.

This trades true real-time latency for model quality — transcript lands up to
`chunk_seconds` behind the speaker. Tunable per-run via `--chunk-seconds`.

### Class layout (`chunked_backends.py`)

| Class | Purpose |
|---|---|
| `ChunkedHTTPBackend` | Base: audio buffering, WAV encode, silence gate, AAD token cache, chunk framing. Subclasses implement `_transcribe_chunk(wav) -> List[str]`. |
| `OpenAITranscribeBackend` | POST `/openai/deployments/<dep>/audio/transcriptions` with `response_format=diarized_json`. Formats `segments[].speaker/.text` → `Speaker <id>: <text>`. |
| `LLMSpeechBackend` | POST `/speechtotext/transcriptions:transcribe` with `enhancedMode`+`diarization`. Formats `phrases[].speaker/.text`. Multilingual auto-detect; optional `--llmspeech-locales` bias. |
| `process_chunked_backend` | Worker mirroring `process_azure_backend`'s contract. Flushes the final partial chunk on stop so the meeting tail isn't lost. |

### Auth

Reuses the AAD `token_provider` discipline from the Speech SDK backend's 401 fix:
a callable that mints a FRESH bearer token on demand (tokens expire ~60 min).
Tokens are cached for 30 min to avoid minting per-chunk. API-key auth also
supported (`Ocp-Apim-Subscription-Key`). Bare bearer token here (not the
`aad#<rid>#<token>` form the Speech SDK requires).

### Resilience

- **Silence gate:** near-silent chunks (RMS < 1e-4) are skipped — no wasted POST.
- **Per-chunk failure isolation:** a failed chunk POST is logged and skipped,
  never fatal. One dropped chunk must not kill the meeting (same philosophy as
  the Speech SDK backend's 401 auto-recovery).

### Infrastructure

- `rtt` (openai) runs on a **dedicated** resource `rtt-transcribe-*` (eastus2,
  rg-rtt-speech) — isolated from shared dev resources. Endpoint in
  `RTT_OPENAI_ENDPOINT`.
- `rttheb` (llmspeech) runs on the existing classic Speech resource
  `rttspeech1763582903` (eastus). Endpoint in `RTT_LLMSPEECH_ENDPOINT`.

## API contract notes (verified empirically 2026-07-04)

- OpenAI audio endpoint requires a **stable data-plane** api-version
  (`2024-10-21`), NOT the Speech-style `2025-10-15` (which 404s).
- `diarized_json` returns letter speaker labels (`Speaker A`, `Speaker B`).
- LLM Speech `enhancedMode` returns numeric labels (`Speaker 1`, `Speaker 2`).
  Both differ cosmetically from the classic engine's `Guest-N`.
- gpt-4o-transcribe-diarize is documented to ignore `language` on the *realtime*
  path; on this *batch* path `language` is honored. English default is unaffected.

## Aliases

| Alias | Script | Backend |
|---|---|---|
| `rtt` | `run-openai.sh` | openai (gpt-4o-transcribe-diarize) — NEW default |
| `rttheb` | `run-llmspeech.sh` | llmspeech (LLM Speech, locales en-US,he-IL) — NEW default |
| `rttold` | `run-azure.sh` | classic ConversationTranscriber (preserved) |
| `rtthebold` | `run-azure-heb.sh` | classic continuous-LID Hebrew (preserved) |

## Testing strategy

### Unit (`tests/test_chunked_backends.py`, 20 tests, no network)
- `_format_diarized` / `_format_phrases` — speaker/no-speaker/empty/fallback shapes
- `_wav_bytes` — roundtrip, clipping
- chunk framing — full-window vs force-flush partial
- silence gate — quiet chunk skipped, loud chunk transcribed
- per-chunk failure swallowed
- URL construction (api-version, path)

### E2E (verified 2026-07-04, real audio via the actual CLI)
- `transcribe.py --backend openai --input-file test.mp3` → diarized output ✓
- `transcribe.py --backend llmspeech --input-file test.mp3` → diarized output ✓
- Dedicated resource `rtt-transcribe-*` E2E ✓
- Both launch scripts (`run-openai.sh`, `run-llmspeech.sh`) ✓

### Manual (live, planned next week)
- A/B `rtt` vs `rttold` on an English meeting
- A/B `rttheb` vs `rtthebold` on a Hebrew/English code-switch meeting
- Tune `--chunk-seconds` for the latency/accuracy balance that feels right live

## Risks & mitigations

- **Risk:** Chunked = not true real-time; transcript lags `chunk_seconds`.
  **Mitigation:** Tunable via `--chunk-seconds`; classic `rttold`/`rtthebold`
  remain for true-realtime needs.
- **Risk:** Cross-chunk speaker numbers not stable (chunk 1 "Speaker A" may be
  chunk 2 "Speaker B").
  **Mitigation:** Acceptable for A/B eval; stitching is a future enhancement.
- **Risk:** `rtt` depends on a cloud resource; if the deployment/quota changes
  it breaks.
  **Mitigation:** Dedicated `rtt-transcribe-*` resource isolates it from other
  projects; classic fallback always available.

## Open questions

1. Right default `--chunk-seconds` for live use? 15s is the starting guess;
   tune after next week's live A/B.
2. Add speaker-stitching across chunks, or leave labels chunk-local?
3. Once validated, retire the classic backend or keep it permanently as the
   low-latency Hebrew path?
