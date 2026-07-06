"""Chunked HTTP transcription backends for RTT.

These back the newer Azure model paths that do NOT support the Speech SDK's
continuous streaming recognition:

  - OpenAITranscribeBackend  → Azure OpenAI `gpt-4o-transcribe-diarize`
        via POST /openai/deployments/<dep>/audio/transcriptions
        (response_format=diarized_json → segments[].speaker/.text)
  - LLMSpeechBackend         → Azure Speech "LLM Speech" fast-transcription
        via POST /speechtotext/transcriptions:transcribe (enhancedMode)

Both share the same micro-batch design: audio frames are buffered into
N-second chunks, each chunk is WAV-encoded and POSTed, and the returned
text (with speaker labels when available) is emitted. This trades true
real-time latency for model quality + diarization — see AGENTS.md.

Auth reuses the same AAD token_provider discipline as AzureSpeechBackend:
a callable that mints a FRESH bearer token on demand (tokens expire ~60 min).

Design notes
------------
* Chunk window default = 10s (tunable via --chunk-seconds). Shorter feels
  more live but cuts mid-sentence and resets diarization more often; longer
  is more accurate but lands further behind the speaker.
* Cross-chunk speaker-number stability is NOT guaranteed (chunk 1 "Speaker 1"
  may be chunk 2 "Speaker 2"). Acceptable for A/B eval; speaker-stitching is
  a future enhancement, not v1.
* Network failures on a single chunk are logged and skipped, never fatal —
  one dropped chunk must not kill the meeting (same resilience philosophy as
  the AzureSpeechBackend 401 auto-recovery).
"""

from __future__ import annotations

import io
import queue
import threading
import time
import wave
from typing import Callable, List, Optional, Tuple, Union

# A token provider returns either a bare token string or (token, expires_on_epoch).
TokenProvider = Callable[[], Union[str, Tuple[str, float]]]

# HTTP statuses worth a short bounded retry: transient server-side blips and
# throttling. A sustained outage across these still escalates loudly (see
# ChunkedHTTPBackend._record_failure) rather than silently dropping audio.
# 401/403 are handled separately (token refresh), NOT here.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

import numpy as np
import requests
import typer


class ChunkedHTTPBackend:
    """Base class: buffers audio into fixed-duration chunks and POSTs each.

    Subclasses implement `_transcribe_chunk(wav_bytes) -> List[str]` returning
    already-formatted transcript lines (e.g. "Speaker 1: hello").
    """

    def __init__(
        self,
        sample_rate: int,
        chunk_seconds: float = 10.0,
        token_provider: Optional["TokenProvider"] = None,
        api_key: Optional[str] = None,
        request_timeout: float = 60.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._chunk_seconds = chunk_seconds
        self._token_provider = token_provider
        self._api_key = api_key
        self._request_timeout = request_timeout
        self._samples_per_chunk = max(1, int(sample_rate * chunk_seconds))
        self._url: str = ""  # set by subclass __init__; used by _post()
        self._buffer = np.empty((0,), dtype=np.float32)
        self._text_queue: "queue.Queue[str]" = queue.Queue()
        self._stop_event = threading.Event()
        # Token cache. The provider may return either a bare token string OR a
        # (token, expires_on_epoch) tuple. When the real expiry is known we
        # refresh 5 min before it; otherwise we fall back to a conservative TTL.
        # NOTE: with AzureCliCredential the token is SHARED with `az` and can be
        # handed over already-aged (<30 min life), so a blind wall-clock TTL is a
        # lie — honoring the real `expires_on` is what prevents the mid-meeting
        # death. Reactive 401 recovery (see _post) is the belt-and-suspenders.
        self._cached_token: Optional[str] = None
        self._token_expiry: float = 0.0   # real epoch expiry when provider supplies it
        self._token_ts: float = 0.0
        self._token_ttl = 1800.0          # fallback only when expiry is unknown
        self._token_refresh_margin = 300.0
        self._token_lock = threading.Lock()
        # Transient-failure retry + health tracking. A single blip (429/5xx) is
        # retried in-process with short backoff so we don't drop that chunk. A
        # SUSTAINED outage (many consecutive failures) is escalated LOUDLY into
        # the transcript exactly once, so a 20-min backend outage can't silently
        # masquerade as one bad chunk (as the 2026-07-06 HTTP 500 outage did).
        self._max_transient_retries = 2      # per-chunk in-request retries
        self._retry_backoff = 1.5            # seconds, ×attempt
        self._consecutive_failures = 0
        self._unhealthy = False              # True once we've escalated "DOWN"
        self._escalate_after = 3             # consecutive failures → escalate

    # --- auth -------------------------------------------------------------
    def _auth_header(self, force_refresh: bool = False) -> dict:
        if self._api_key:
            return {"Ocp-Apim-Subscription-Key": self._api_key}
        token = self._get_token(force_refresh=force_refresh)
        return {"Authorization": f"Bearer {token}"}

    def _get_token(self, force_refresh: bool = False) -> str:
        if self._token_provider is None:
            raise RuntimeError("No api_key and no token_provider configured.")
        now = time.time()
        with self._token_lock:
            if self._token_expiry:
                stale = now >= (self._token_expiry - self._token_refresh_margin)
            else:
                stale = (now - self._token_ts) > self._token_ttl
            if force_refresh or self._cached_token is None or stale:
                result = self._token_provider()
                if isinstance(result, tuple):
                    self._cached_token = result[0]
                    self._token_expiry = float(result[1])
                else:
                    self._cached_token = result
                    self._token_expiry = 0.0
                self._token_ts = now
            return self._cached_token

    def _invalidate_token(self) -> None:
        """Drop the cached token so the next _get_token mints a fresh one.

        Called after a 401/403 so an expired token can't be re-sent forever.
        Forcing a re-mint after expiry makes AzureCliCredential/MSAL refresh the
        underlying `az` token instead of returning the dead cached one.
        """
        with self._token_lock:
            self._cached_token = None
            self._token_expiry = 0.0

    def _post(self, *, files, data=None) -> "requests.Response":
        """POST to self._url with AAD/key auth, one-shot 401 recovery, and
        bounded retry on transient 5xx/429.

        Shared by all chunked subclasses so recovery lives in ONE place:
        - 401/403 (AAD): invalidate the cached token, force a fresh mint, retry
          once — the mid-meeting token-expiry self-heal.
        - 429/5xx: retry up to `_max_transient_retries` times with linear
          backoff — a brief Azure blip recovers instead of dropping that chunk.
        A file payload (BytesIO) can be consumed by the first send, so we build
        the multipart fresh on each attempt via the passed-in bytes/dicts, which
        are safe to re-send.
        Returns the final Response (caller decides on non-200); a network-level
        exception on the LAST attempt propagates to the caller.
        """
        token_refreshed = False
        last_exc: Optional[Exception] = None
        resp: Optional["requests.Response"] = None
        # total attempts = 1 initial + transient retries
        for attempt in range(self._max_transient_retries + 1):
            try:
                resp = requests.post(
                    self._url,
                    headers=self._auth_header(),
                    files=files,
                    data=data,
                    timeout=self._request_timeout,
                )
            except requests.RequestException as exc:
                # Network-level failure (DNS, connection reset, read timeout) is
                # itself transient — back off and retry, same as a 5xx.
                last_exc = exc
                if attempt < self._max_transient_retries:
                    time.sleep(self._retry_backoff * (attempt + 1))
                    continue
                raise

            # AAD token death: one forced refresh + immediate retry (does NOT
            # consume the transient budget — it's a distinct failure mode).
            if (
                resp.status_code in (401, 403)
                and self._token_provider is not None
                and not token_refreshed
            ):
                typer.echo(
                    f"[{self.__class__.__name__}] auth {resp.status_code} — "
                    f"refreshing AAD token and retrying chunk",
                    err=True,
                )
                self._invalidate_token()
                token_refreshed = True
                resp = requests.post(
                    self._url,
                    headers=self._auth_header(force_refresh=True),
                    files=files,
                    data=data,
                    timeout=self._request_timeout,
                )

            # Transient server blip / throttle: back off and retry.
            if (
                resp.status_code in _TRANSIENT_STATUS
                and attempt < self._max_transient_retries
            ):
                time.sleep(self._retry_backoff * (attempt + 1))
                continue

            return resp

        # Exhausted retries on transient status — return the last response so the
        # caller raises with the real status (feeds health tracking).
        if last_exc is not None:
            raise last_exc
        assert resp is not None  # loop runs ≥1 time (_max_transient_retries ≥ 0)
        return resp

    # --- audio framing ----------------------------------------------------
    @staticmethod
    def _wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
        """Encode a float32 [-1,1] mono array as 16-bit PCM WAV bytes."""
        pcm = (np.clip(np.squeeze(audio), -1.0, 1.0) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

    def push_audio(self, audio: np.ndarray) -> None:
        self._buffer = np.concatenate((self._buffer, np.squeeze(audio)))

    def _drain_ready_chunk(self, force: bool = False) -> Optional[np.ndarray]:
        """Return one full chunk of audio if buffered, else None.

        force=True flushes whatever remains (used at end-of-stream)."""
        if self._buffer.shape[0] >= self._samples_per_chunk:
            chunk = self._buffer[: self._samples_per_chunk]
            self._buffer = self._buffer[self._samples_per_chunk :]
            return chunk
        if force and self._buffer.size:
            chunk = self._buffer
            self._buffer = np.empty((0,), dtype=np.float32)
            return chunk
        return None

    # --- subclass hook ----------------------------------------------------
    def _transcribe_chunk(self, wav_bytes: bytes) -> List[str]:
        raise NotImplementedError

    def transcribe_ready(self, force: bool = False) -> None:
        """Encode + POST any ready chunk, queueing resulting lines.

        On failure a single chunk is never fatal, but SUSTAINED failures are
        escalated once (see _record_failure) so a backend outage is visible in
        the transcript instead of silently dropping minutes of audio.
        """
        chunk = self._drain_ready_chunk(force=force)
        if chunk is None:
            return
        # Skip near-silent chunks (avoids paying for empty POSTs). RMS gate.
        if float(np.sqrt(np.mean(np.square(chunk)))) < 1e-4:
            return
        wav = self._wav_bytes(chunk, self._sample_rate)
        try:
            lines = self._transcribe_chunk(wav)
        except Exception as exc:  # noqa: BLE001 — a dropped chunk must not kill the run
            typer.echo(f"[{self.__class__.__name__}] chunk failed: {exc}", err=True)
            self._record_failure(exc)
            return
        self._record_success()
        for line in lines:
            if line and line.strip():
                self._text_queue.put(line.strip())

    def _record_success(self) -> None:
        """Reset the failure streak; announce recovery if we were unhealthy."""
        if self._unhealthy:
            self._text_queue.put(
                "⚠️ RTT RECOVERED — transcription backend is responding again."
            )
            typer.echo(
                f"[{self.__class__.__name__}] backend recovered after "
                f"{self._consecutive_failures} consecutive failures",
                err=True,
            )
        self._unhealthy = False
        self._consecutive_failures = 0

    def _record_failure(self, exc: Exception) -> None:
        """Count a failed chunk; escalate LOUDLY the first time we cross the
        consecutive-failure threshold, so a long outage can't masquerade as one
        dropped chunk (the 2026-07-06 HTTP 500 outage dropped ~23 min silently).
        """
        self._consecutive_failures += 1
        if not self._unhealthy and self._consecutive_failures >= self._escalate_after:
            self._unhealthy = True
            msg = (
                f"⚠️ RTT BACKEND DOWN — {self._consecutive_failures} consecutive "
                f"chunks failed ({type(exc).__name__}: {str(exc)[:120]}). "
                f"Audio is being DROPPED until it recovers. "
                f"Consider switching backend (rttheb / rttold / local)."
            )
            self._text_queue.put(msg)
            typer.echo(f"[{self.__class__.__name__}] {msg}", err=True)

    def drain_text(self) -> List[str]:
        lines: List[str] = []
        while True:
            try:
                lines.append(self._text_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def start(self) -> None:  # symmetry with AzureSpeechBackend
        pass

    def stop(self) -> None:
        self._stop_event.set()


class OpenAITranscribeBackend(ChunkedHTTPBackend):
    """Azure OpenAI gpt-4o-transcribe-diarize via the /audio/transcriptions API.

    Uses response_format=diarized_json → the response carries segments[] each
    with .speaker and .text, which we format as "Speaker <id>: <text>".
    """

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        sample_rate: int,
        chunk_seconds: float = 10.0,
        api_version: str = "2024-10-21",
        language: Optional[str] = None,
        token_provider: Optional["TokenProvider"] = None,
        api_key: Optional[str] = None,
    ) -> None:
        super().__init__(sample_rate, chunk_seconds, token_provider, api_key)
        base = endpoint.rstrip("/")
        self._url = (
            f"{base}/openai/deployments/{deployment}"
            f"/audio/transcriptions?api-version={api_version}"
        )
        self._deployment = deployment
        self._language = language

    def _transcribe_chunk(self, wav_bytes: bytes) -> List[str]:
        files = {"file": ("chunk.wav", wav_bytes, "audio/wav")}
        data = {
            "model": self._deployment,
            "response_format": "diarized_json",
        }
        if self._language:
            # NOTE: the diarize model is documented to sometimes ignore
            # `language` on the realtime path; on this batch path it is honored.
            data["language"] = self._language
        resp = self._post(files=files, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        return self._format_diarized(payload)

    @staticmethod
    def _format_diarized(payload: dict) -> List[str]:
        """Turn a diarized_json payload into 'Speaker N: text' lines.

        Handles both the segment-list shape (segments[].speaker/.text) and a
        plain {text:...} fallback when diarization data is absent."""
        segments = payload.get("segments")
        lines: List[str] = []
        if isinstance(segments, list) and segments:
            for seg in segments:
                text = (seg.get("text") or "").strip()
                if not text:
                    continue
                spk = seg.get("speaker")
                if spk is not None and spk != "":
                    # Normalize "0"/0 → "Guest-1" style to match RTT's existing
                    # "Speaker Guest-N" convention as closely as possible.
                    lines.append(f"Speaker {spk}: {text}")
                else:
                    lines.append(text)
            return lines
        # Fallback: no diarization, just the combined text.
        text = (payload.get("text") or "").strip()
        return [text] if text else []


class LLMSpeechBackend(ChunkedHTTPBackend):
    """Azure Speech "LLM Speech" fast-transcription via enhancedMode.

    POST /speechtotext/transcriptions:transcribe with a definition enabling
    enhancedMode + diarization. Multilingual by default (auto-detects), which
    is why this is the Hebrew/English code-switch path. Response carries
    phrases[] each with .speaker and .text.
    """

    def __init__(
        self,
        endpoint: str,
        sample_rate: int,
        chunk_seconds: float = 10.0,
        api_version: str = "2025-10-15",
        max_speakers: int = 4,
        locales: Optional[List[str]] = None,
        token_provider: Optional["TokenProvider"] = None,
        api_key: Optional[str] = None,
    ) -> None:
        super().__init__(sample_rate, chunk_seconds, token_provider, api_key)
        base = endpoint.rstrip("/")
        self._url = (
            f"{base}/speechtotext/transcriptions:transcribe"
            f"?api-version={api_version}"
        )
        self._max_speakers = max_speakers
        self._locales = locales  # e.g. ["en-US","he-IL"] to bias; None = auto

    def _transcribe_chunk(self, wav_bytes: bytes) -> List[str]:
        import json as _json

        definition: dict = {
            "enhancedMode": {"enabled": True, "task": "transcribe"},
            "diarization": {"maxSpeakers": self._max_speakers, "enabled": True},
        }
        if self._locales:
            definition["locales"] = self._locales
        files = {
            "audio": ("chunk.wav", wav_bytes, "audio/wav"),
            "definition": (None, _json.dumps(definition)),
        }
        resp = self._post(files=files)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return self._format_phrases(resp.json())

    @staticmethod
    def _format_phrases(payload: dict) -> List[str]:
        """Turn a fast-transcription payload into 'Speaker N: text' lines."""
        phrases = payload.get("phrases")
        lines: List[str] = []
        if isinstance(phrases, list) and phrases:
            for ph in phrases:
                text = (ph.get("text") or "").strip()
                if not text:
                    continue
                spk = ph.get("speaker")
                if spk is not None and spk != "":
                    lines.append(f"Speaker {spk}: {text}")
                else:
                    lines.append(text)
            return lines
        # Fallback to combinedPhrases when no per-phrase diarization present.
        combined = payload.get("combinedPhrases")
        if isinstance(combined, list) and combined:
            out = []
            for c in combined:
                t = (c.get("text") or "").strip()
                if t:
                    out.append(t)
            return out
        return []


def process_chunked_backend(
    backend: ChunkedHTTPBackend,
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    paused_event: threading.Event,
    emit,
    poll_interval: float = 0.5,
) -> None:
    """Worker: drain audio queue → buffer → POST ready chunks → emit lines.

    Mirrors process_azure_backend's contract so main() can dispatch it the
    same way. On stop, flushes the final partial chunk so the tail of the
    meeting isn't lost."""
    backend.start()
    last_poll = time.time()
    try:
        while not stop_event.is_set() or not audio_queue.empty():
            if paused_event.is_set():
                # Drop queued audio while paused (matches other backends).
                while True:
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        break
                time.sleep(0.1)
                continue
            try:
                chunk = audio_queue.get(timeout=0.2)
                backend.push_audio(chunk)
            except queue.Empty:
                pass
            # Periodically flush any complete chunk.
            if (time.time() - last_poll) >= poll_interval:
                backend.transcribe_ready(force=False)
                for line in backend.drain_text():
                    emit(line)
                last_poll = time.time()
        # End of stream: flush remaining full chunks + the final partial one.
        backend.transcribe_ready(force=False)
        backend.transcribe_ready(force=True)
        for line in backend.drain_text():
            emit(line)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Chunked backend worker failed: {exc}", err=True)
        stop_event.set()
    finally:
        backend.stop()
        for line in backend.drain_text():
            emit(line)
