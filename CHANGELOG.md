# Changelog

## 2026-07-06 (tenant-drift pin + file-test wrapper)
- **Fix: RTT no longer breaks when the `az` CLI active context drifts to another tenant.** The openai/llmspeech resources live in the DEV tenant; when `az` is switched to a CORP subscription (e.g. by msx-se-hub / `az-corp`), an unpinned `DefaultAzureCredential` mints a wrong-tenant token and the resource rejects it with `HTTP 500 "Unable to get resource information"` — a cross-tenant rejection that masquerades as a server error, **not** a 401. (This was the actual cause of the "500 outage" first mislabeled as transient — the resource was healthy; only the token's tenant was wrong.) The resource has `disableLocalAuth=true`, so API keys are not an option.
  - **`RTT_AAD_SUBSCRIPTION`** (new `.env` var): when set, `_build_aad_credential()` pins minting via `AzureCliCredential(subscription=...)`. Pinning the SUBSCRIPTION is required — `tenant_id=` alone still follows the active account and fails. Makes RTT immune to `az` drift; no more manually running `az-dev` before launch. Unset → falls back to `DefaultAzureCredential` (prior behavior).
  - Proven end-to-end: with `az` active on a CORP sub, the real CLI printed `Pinning AAD token to subscription …` and transcribed cleanly; unpinned mint 500s under the same condition.
  - **`run-filetest.sh`** (new): file-mode test wrapper that sources `.env` for creds/endpoints but UNSETS capture vars (RTT_INPUT_DEVICE, RTT_INCLUDE_MIC, RTT_MIX_MIC, RTT_MIC_DEVICE, RTT_USE_LOOPBACK) for the invocation only — so file tests never collide with the live-capture "use either --input-file or --input-device" guard, and `.env` is never hand-edited/restored (which previously left it half-masked and broke live capture).
  - Tests: `tests/test_aad_credential.py` +5 (pin-when-set, fallback-when-unset, blank-ignored, cli-unavailable-fallback, no-credential-raises). Full suite 68/68.

## 2026-07-06 (transient-outage resilience)
- **Fix: chunked backends survive a transient failure instead of silently dropping audio.** During a stretch of `HTTP 500` responses the old per-chunk log-and-skip dropped ~140 chunks (~23 min) with no visible signal, discovered only by watching stderr. (NOTE: the specific 500s that day were later root-caused to tenant drift, not a server outage — see the tenant-drift pin entry above. This resilience layer is still correct and valuable for GENUINE transient blips/throttling, and its escalation is what surfaces a persistent failure like drift loudly instead of silently.)
  - **Bounded transient retry:** `_post()` now retries 429/500/502/503/504 and network-level `RequestException`s up to 2× with linear backoff. A brief blip recovers instead of dropping that chunk. The 401 token-refresh is separate and does NOT consume the transient budget.
  - **Loud health escalation:** after 3 consecutive failed chunks, ONE `⚠️ RTT BACKEND DOWN …` line is written into the transcript (not just stderr) telling you audio is dropping and to switch backend (rttheb/rttold/local). On recovery, a `⚠️ RTT RECOVERED` line + normal transcription resumes. A long outage is now visible at ~30s, not 23 min later.
  - Escalates exactly once per outage; resets on recovery so future outages re-escalate.
  - Tests: `tests/test_chunked_backends.py` +8 (transient retry/exhaust, 429, network-exc, 401-doesn't-eat-budget, escalate-once, recovery-announced, single-failure-no-spam). Full suite 63/63.
  - NOT done (deliberate): automatic backend failover — has label/language/cost tradeoffs, left as a user decision; the escalation line prompts the switch.

## 2026-07-06
- **Fix: chunked backends (`openai`/`llmspeech`) no longer die mid-meeting on AAD token expiry.** A live call was dropping ~24 min in (not 60) with a permanent `chunk failed: HTTP 401` storm that never recovered — forcing a restart that reset diarization/speaker labels. Three compounding causes: (1) `DefaultAzureCredential` falls through to the shared `az` CLI token, often handed over already-aged (<30 min life); (2) the token cache used a blind 30-min wall-clock TTL that ignored the token's real `exp`; (3) a 401 never busted the cache, so the dead token was re-sent forever.
  - **Real-expiry cache:** the AAD token provider now returns `(token, expires_on)`; the cache refreshes 5 min before actual expiry instead of a fixed TTL. Bare-string providers still supported.
  - **One-shot 401 self-heal:** new `ChunkedHTTPBackend._post()` catches a 401/403, force-mints a fresh token, and retries the same chunk ONCE. A transient token death now self-heals in ~one chunk (~10s) in the **same process / same session** — no restart, so speaker labels are preserved. Persistent auth failure surfaces cleanly (no infinite loop); key-auth skips the path entirely.
  - Tests: `tests/test_chunked_backends.py` +5 (real-expiry refresh, bare-string TTL fallback, 401-retry-with-fresh-token, no-infinite-retry, key-auth no-op). Full suite 55/55.

## 2026-07-04 (rtt-cli)
- **`rtt-cli`** — interactive menu / wizard / chat front-end for configuring and launching RTT. Zero new dependencies (pure stdlib).
  - **Schema-driven** (`config_schema.py`): one declarative `Field` registry is the single source of truth. Wizard, chat parser, validation, and the launch-command builder all read from it — adding a new option = one Field entry, no UI changes.
  - Three modes: main menu (profiles + edit + save/load), wizard (walks every applicable option, backend-aware), chat (natural language — "use hebrew", "chunk 8", "faster", "add my mic", "switch to local").
  - 5 profiles (`rtt`/`rttheb`/`rttold`/`rtthebold`/`local`) resolved from the schema, mirroring the shell aliases. Named configs save to `~/.config/rtt-cli/`.
  - `rtt-cli.sh` launcher + `rtt-cli` alias. Non-interactive `--print-argv` / `--launch` / `--profile` / `--load` entry points.
  - Tests: `tests/test_rtt_cli.py` (23 tests incl. a same-backend alias-collision guard). Full suite 50/50.

## 2026-07-04
- **Two new transcription backends** for the newer Azure speech models (chunked HTTP, `chunked_backends.py`):
  - **`--backend openai`** → Azure OpenAI `gpt-4o-transcribe-diarize`. Better WER, native diarization, ~1/3 the per-hour cost of classic Speech. New default for the `rtt` alias. Runs on a dedicated `rtt-transcribe-*` resource (eastus2).
  - **`--backend llmspeech`** → Azure Speech "LLM Speech" `enhancedMode`. LLM-enhanced quality, multilingual auto-detect (Hebrew/English code-switching), diarization. New default for the `rttheb` alias. Runs on the existing Speech resource.
- Both use a **micro-batch** design: audio is buffered into `--chunk-seconds` windows (default 10s) and POSTed per chunk. Trades true real-time latency for model quality. Includes a silence gate (skips quiet chunks) and per-chunk failure isolation (a dropped chunk never kills the run).
- Auth reuses the AAD `token_provider` discipline (fresh bearer token on demand, 30-min cache); API-key auth also supported.
- **Alias changes:** `rtt`→`run-openai.sh`, `rttheb`→`run-llmspeech.sh`. Classic Speech SDK backends preserved as **`rttold`** (`run-azure.sh`) and **`rtthebold`** (`run-azure-heb.sh`) for fallback + A/B baseline.
- New CLI options: `--chunk-seconds`, `--openai-endpoint`, `--openai-deployment`, `--llmspeech-endpoint`, `--llmspeech-locales`. New env vars: `RTT_OPENAI_ENDPOINT`, `RTT_LLMSPEECH_ENDPOINT`.
- Unit tests: `tests/test_chunked_backends.py` (20 tests, no network).
- API contract note: the OpenAI audio endpoint needs stable data-plane api-version `2024-10-21` (Speech-style `2025-10-15` 404s). Diarize returns letter labels (`Speaker A`), LLM Speech returns numeric (`Speaker 1`).

## 2026-05-06
- **Cross-platform installer** (`install.sh` / `install\windows.ps1`) — zero-friction onboarding:
  - macOS: auto-installs BlackHole via Homebrew, programmatically creates a `RTT Multi-Output` aggregate device (Swift + CoreAudio), sets it as default output, runs a tone-based audio smoke test.
  - Linux: detects PulseAudio/PipeWire monitor source.
  - Windows: configures WASAPI loopback (built-in, no drivers). *Beta — not yet validated on a live Windows machine.*
- **`--include-mic`** flag and `RTT_INCLUDE_MIC=1` env var — mixes the system microphone into the captured stream so the user's own voice appears in the transcript. Companion flags `--mic-device` and `--mic-gain`.
- **`--loopback`** flag and `RTT_USE_LOOPBACK=1` — enables WASAPI loopback capture on Windows.
- Added `MixedDeviceCapture` class: dual-stream capture (system + mic) with bounded ring buffers and real-time mixing.
- Sanitized public-repo references; added `.stubs/`, `.vfsmeta/`, `devices.txt`, `AGENTS.md` to `.gitignore`.

## 2026-03-12
- Fixed Azure AD token authentication: tokens are now formatted as `aad#<resource-id>#<token>` as required by the Speech SDK.
- Added `AZURE_SPEECH_RESOURCE_ID` env var and `--azure-resource-id` CLI flag (required for Azure AD auth).
- Updated `.env.example` with Azure AD auth instructions.
- Expanded Troubleshooting section with common 401 causes (stale keys, missing resource ID, RBAC roles).

## 2026-01-18
- Updated terminal hotkeys for clipboard workflow:
  - `Ctrl+S` copies full transcript so far and sets the delta baseline.
  - `Ctrl+E` copies transcript delta since the last `Ctrl+S`.
- Added `Ctrl+P` to pause/resume transcribing.
- Removed the mistaken VS Code-extension approach; hotkeys are handled in-terminal.
- Added `run.sh` and `run-azure.sh` helpers to simplify startup and auto-load `.env`.
