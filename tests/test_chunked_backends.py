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
