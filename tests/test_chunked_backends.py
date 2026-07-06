"""Unit tests for the chunked HTTP transcription backends.

Pure logic only — no network. Covers the response formatters (the parts most
likely to break when Azure tweaks a response shape), WAV encoding, the silence
gate, and audio chunk framing.

Run with: python -m pytest tests/test_chunked_backends.py -v
"""
import io
import wave
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chunked_backends import (  # noqa: E402
    ChunkedHTTPBackend,
    OpenAITranscribeBackend,
    LLMSpeechBackend,
)


# --------------------------------------------------------------------------
# OpenAITranscribeBackend._format_diarized (diarized_json → lines)
# --------------------------------------------------------------------------

def test_format_diarized_segments_with_speakers():
    payload = {
        "segments": [
            {"speaker": "A", "text": "Hello there."},
            {"speaker": "B", "text": "General Kenobi."},
        ]
    }
    out = OpenAITranscribeBackend._format_diarized(payload)
    assert out == ["Speaker A: Hello there.", "Speaker B: General Kenobi."]


def test_format_diarized_numeric_speaker():
    payload = {"segments": [{"speaker": 0, "text": "zero indexed"}]}
    out = OpenAITranscribeBackend._format_diarized(payload)
    assert out == ["Speaker 0: zero indexed"]


def test_format_diarized_missing_speaker_falls_back_to_text():
    payload = {"segments": [{"text": "no speaker field"}]}
    out = OpenAITranscribeBackend._format_diarized(payload)
    assert out == ["no speaker field"]


def test_format_diarized_skips_empty_text():
    payload = {"segments": [{"speaker": "A", "text": "   "}, {"speaker": "A", "text": "real"}]}
    out = OpenAITranscribeBackend._format_diarized(payload)
    assert out == ["Speaker A: real"]


def test_format_diarized_no_segments_uses_combined_text():
    payload = {"text": "flat transcript, no diarization"}
    out = OpenAITranscribeBackend._format_diarized(payload)
    assert out == ["flat transcript, no diarization"]


def test_format_diarized_empty_payload():
    assert OpenAITranscribeBackend._format_diarized({}) == []
    assert OpenAITranscribeBackend._format_diarized({"segments": []}) == []


# --------------------------------------------------------------------------
# LLMSpeechBackend._format_phrases (fast-transcription → lines)
# --------------------------------------------------------------------------

def test_format_phrases_with_speakers():
    payload = {
        "phrases": [
            {"speaker": 1, "text": "First speaker."},
            {"speaker": 2, "text": "Second speaker."},
        ]
    }
    out = LLMSpeechBackend._format_phrases(payload)
    assert out == ["Speaker 1: First speaker.", "Speaker 2: Second speaker."]


def test_format_phrases_missing_speaker():
    payload = {"phrases": [{"text": "anon"}]}
    out = LLMSpeechBackend._format_phrases(payload)
    assert out == ["anon"]


def test_format_phrases_falls_back_to_combined():
    payload = {"combinedPhrases": [{"text": "the whole thing"}]}
    out = LLMSpeechBackend._format_phrases(payload)
    assert out == ["the whole thing"]


def test_format_phrases_empty():
    assert LLMSpeechBackend._format_phrases({}) == []
    assert LLMSpeechBackend._format_phrases({"phrases": []}) == []


# --------------------------------------------------------------------------
# WAV encoding
# --------------------------------------------------------------------------

def test_wav_bytes_roundtrip():
    sr = 16000
    # 0.5s sine wave, float32 in [-1, 1]
    t = np.linspace(0, 0.5, sr // 2, endpoint=False, dtype=np.float32)
    audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    wav = ChunkedHTTPBackend._wav_bytes(audio, sr)
    # Parse it back
    with wave.open(io.BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2      # 16-bit
        assert wf.getframerate() == sr
        frames = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(frames, dtype=np.int16)
    assert len(pcm) == len(audio)
    # Peak should be ~0.3 * 32767
    assert 8000 < int(np.max(np.abs(pcm))) < 11000


def test_wav_bytes_clips_out_of_range():
    sr = 16000
    audio = np.array([2.0, -2.0, 0.0], dtype=np.float32)  # out of [-1,1]
    wav = ChunkedHTTPBackend._wav_bytes(audio, sr)
    with wave.open(io.BytesIO(wav), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    assert pcm[0] == 32767   # clipped high
    assert pcm[1] == -32767  # clipped low


# --------------------------------------------------------------------------
# Chunk framing + silence gate
# --------------------------------------------------------------------------

def _make_backend(sr=16000, chunk_seconds=1.0):
    # api_key set so no token_provider needed; we never actually POST here.
    return ChunkedHTTPBackend(sample_rate=sr, chunk_seconds=chunk_seconds, api_key="dummy")


def test_drain_ready_chunk_waits_for_full_window():
    b = _make_backend(sr=100, chunk_seconds=1.0)  # 100 samples per chunk
    b.push_audio(np.zeros(50, dtype=np.float32))
    assert b._drain_ready_chunk() is None          # only half a chunk
    b.push_audio(np.zeros(60, dtype=np.float32))   # now 110 total
    chunk = b._drain_ready_chunk()
    assert chunk is not None and len(chunk) == 100  # exactly one window
    assert b._buffer.shape[0] == 10                 # remainder retained


def test_drain_ready_chunk_force_flushes_partial():
    b = _make_backend(sr=100, chunk_seconds=1.0)
    b.push_audio(np.zeros(30, dtype=np.float32))
    assert b._drain_ready_chunk(force=False) is None
    chunk = b._drain_ready_chunk(force=True)
    assert chunk is not None and len(chunk) == 30


def test_silence_gate_skips_quiet_chunk(monkeypatch):
    """A near-silent chunk should NOT trigger a POST."""
    b = _make_backend(sr=100, chunk_seconds=1.0)
    called = {"n": 0}

    def _fake_transcribe(wav_bytes):
        called["n"] += 1
        return ["should not happen"]

    monkeypatch.setattr(b, "_transcribe_chunk", _fake_transcribe)
    b.push_audio(np.full(100, 1e-6, dtype=np.float32))  # essentially silent
    b.transcribe_ready(force=True)
    assert called["n"] == 0
    assert b.drain_text() == []


def test_loud_chunk_triggers_transcription(monkeypatch):
    b = _make_backend(sr=100, chunk_seconds=1.0)

    def _fake_transcribe(wav_bytes):
        return ["Speaker A: loud and clear"]

    monkeypatch.setattr(b, "_transcribe_chunk", _fake_transcribe)
    b.push_audio(np.full(100, 0.5, dtype=np.float32))  # loud
    b.transcribe_ready(force=True)
    assert b.drain_text() == ["Speaker A: loud and clear"]


def test_chunk_failure_does_not_raise(monkeypatch):
    """A failing chunk POST must be swallowed, never kill the run."""
    b = _make_backend(sr=100, chunk_seconds=1.0)

    def _boom(wav_bytes):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(b, "_transcribe_chunk", _boom)
    b.push_audio(np.full(100, 0.5, dtype=np.float32))
    b.transcribe_ready(force=True)  # must not raise
    assert b.drain_text() == []


# --------------------------------------------------------------------------
# URL construction
# --------------------------------------------------------------------------

def test_openai_url_construction():
    b = OpenAITranscribeBackend(
        endpoint="https://res.cognitiveservices.azure.com/",
        deployment="gpt-4o-transcribe-diarize",
        sample_rate=16000,
        api_key="dummy",
    )
    assert "/openai/deployments/gpt-4o-transcribe-diarize/audio/transcriptions" in b._url
    assert "api-version=2024-10-21" in b._url


def test_llmspeech_url_construction():
    b = LLMSpeechBackend(
        endpoint="https://res.cognitiveservices.azure.com/",
        sample_rate=16000,
        api_key="dummy",
    )
    assert "/speechtotext/transcriptions:transcribe" in b._url


# --------------------------------------------------------------------------
# AAD token cache: real-expiry honoring + 401 self-heal (the mid-meeting fix)
#
# These lock in the behavior that lets a live call SURVIVE a token death without
# a restart — same process, same session, diarization uninterrupted.
# --------------------------------------------------------------------------

import time as _time


def test_token_provider_tuple_honors_real_expiry(monkeypatch):
    """A (token, expires_on) tuple must refresh on the REAL expiry, not the
    blind 30-min TTL. This is the root-cause fix: an already-aged `az` token
    with <30 min life must be re-minted before it dies mid-meeting."""
    now = _time.time()
    mints = []

    def provider():
        # First token already almost dead (expires in 60s); second is healthy.
        idx = len(mints)
        mints.append(idx)
        exp = now + 60 if idx == 0 else now + 3600
        return (f"tok{idx}", exp)

    b = ChunkedHTTPBackend(sample_rate=16000, token_provider=provider)
    # First fetch caches tok0 (expiring in 60s, inside the 300s margin → stale).
    assert b._get_token() == "tok0"
    # Next fetch sees it's within the refresh margin of real expiry → re-mints.
    assert b._get_token() == "tok1"
    assert len(mints) == 2


def test_token_bare_string_uses_fallback_ttl(monkeypatch):
    """A bare-string provider (no expiry known) falls back to the wall-clock
    TTL and does NOT re-mint on every call."""
    mints = []

    def provider():
        mints.append(1)
        return "bare-token"

    b = ChunkedHTTPBackend(sample_rate=16000, token_provider=provider)
    b._get_token()
    b._get_token()  # within TTL → cached, no second mint
    assert len(mints) == 1


class _FakeResp:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_post_retries_once_on_401_with_fresh_token(monkeypatch):
    """The load-bearing fix: a 401 must invalidate the token, force a fresh
    mint, and retry the SAME chunk once — so a mid-call token death self-heals
    instead of 401-storming forever."""
    import chunked_backends as cb

    mints = []

    def provider():
        mints.append(len(mints))
        return (f"tok{len(mints) - 1}", _time.time() + 3600)

    b = OpenAITranscribeBackend(
        endpoint="https://res.cognitiveservices.azure.com/",
        deployment="gpt-4o-transcribe-diarize",
        sample_rate=16000,
        token_provider=provider,
    )

    calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls.append(headers.get("Authorization"))
        # First call 401s, second (after refresh) succeeds.
        if len(calls) == 1:
            return _FakeResp(401, text="expired")
        return _FakeResp(200, payload={"segments": [{"speaker": "A", "text": "recovered"}]})

    monkeypatch.setattr(cb.requests, "post", fake_post)

    lines = b._transcribe_chunk(b"fakewav")
    assert lines == ["Speaker A: recovered"]
    assert len(calls) == 2                       # retried exactly once
    assert calls[0] != calls[1]                  # a DIFFERENT (fresh) token
    assert calls[0].endswith("tok0")
    assert calls[1].endswith("tok1")


def test_post_no_infinite_retry_on_persistent_401(monkeypatch):
    """If auth stays broken, we retry ONCE then surface the error — we do not
    loop forever, and the worker's per-chunk isolation swallows it."""
    import chunked_backends as cb

    def provider():
        return ("tok", _time.time() + 3600)

    b = OpenAITranscribeBackend(
        endpoint="https://res.cognitiveservices.azure.com/",
        deployment="d",
        sample_rate=16000,
        token_provider=provider,
    )

    calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls.append(1)
        return _FakeResp(401, text="still broken")

    monkeypatch.setattr(cb.requests, "post", fake_post)

    import pytest
    with pytest.raises(RuntimeError, match="HTTP 401"):
        b._transcribe_chunk(b"fakewav")
    assert len(calls) == 2  # original + one retry, then give up


def test_key_auth_never_triggers_token_refresh(monkeypatch):
    """API-key auth must not go near the token path (no provider → no 401
    refresh attempt). Regression guard for the key-auth no-op contract."""
    import chunked_backends as cb

    b = OpenAITranscribeBackend(
        endpoint="https://res.cognitiveservices.azure.com/",
        deployment="d",
        sample_rate=16000,
        api_key="secret-key",
    )

    calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        calls.append(headers)
        return _FakeResp(401, text="nope")

    monkeypatch.setattr(cb.requests, "post", fake_post)

    import pytest
    with pytest.raises(RuntimeError, match="HTTP 401"):
        b._transcribe_chunk(b"fakewav")
    # Only ONE call — no token to refresh, so no retry; and it used the key.
    assert len(calls) == 1
    assert calls[0].get("Ocp-Apim-Subscription-Key") == "secret-key"
