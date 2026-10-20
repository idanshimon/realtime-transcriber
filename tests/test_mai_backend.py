"""Unit tests for the MAI-Transcribe-2 batch backend (--backend mai).

MAI-Transcribe-2 is the Azure Speech fast-transcription model that supports
speaker diarization. It is reachable on the SAME REST surface as the existing
LLM Speech backend (/speechtotext/transcriptions:transcribe), so
MAITranscribeBackend extends LLMSpeechBackend — reusing the auth, the chunk
framing, the transient-retry/escalation resilience, and the phrases[] →
"Speaker N: text" formatter. The ONLY differences, both verified empirically:

  * the enhancedMode definition selects the model: {"enabled": true,
    "model": "MAI-Transcribe-2"}
  * diarization is {"enabled": true} with NO maxSpeakers — MAI rejects
    maxSpeakers with HTTP 400 "maxSpeakers is not supported for MAI
    transcription yet." (the stock LLMSpeechBackend always sends maxSpeakers,
    so it is NOT already MAI-ready).

Pure logic, no network.

Run: python -m pytest tests/test_mai_backend.py -v
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chunked_backends import (  # noqa: E402
    ChunkedHTTPBackend,
    LLMSpeechBackend,
    MAITranscribeBackend,
)


def _make_backend(**kwargs) -> MAITranscribeBackend:
    return MAITranscribeBackend(
        endpoint="https://rttspeech1763582903.cognitiveservices.azure.com/",
        sample_rate=16000,
        api_key="fake-key",  # key path → no token minting
        **kwargs,
    )


# --------------------------------------------------------------------------
# Class wiring
# --------------------------------------------------------------------------

def test_mai_is_a_chunked_backend():
    """mai must flow through the existing chunked worker contract."""
    assert issubclass(MAITranscribeBackend, ChunkedHTTPBackend)
    assert issubclass(MAITranscribeBackend, LLMSpeechBackend)


def test_mai_url_is_fast_transcription_surface():
    b = _make_backend()
    assert "/speechtotext/transcriptions:transcribe" in b._url
    assert "api-version=" in b._url


# --------------------------------------------------------------------------
# The definition — the crux of MAI support
# --------------------------------------------------------------------------

def test_mai_definition_selects_the_model():
    b = _make_backend()
    definition = b._build_definition()
    assert definition["enhancedMode"]["enabled"] is True
    assert definition["enhancedMode"]["model"] == "MAI-Transcribe-2"


def test_mai_definition_enables_diarization_without_maxspeakers():
    """MAI rejects maxSpeakers (HTTP 400). Diarization must still be enabled."""
    b = _make_backend()
    definition = b._build_definition()
    assert definition["diarization"]["enabled"] is True
    assert "maxSpeakers" not in definition["diarization"]


def test_mai_model_is_overridable():
    """Allow selecting MAI-Transcribe-1.5 etc. without a code change."""
    b = _make_backend(model="MAI-Transcribe-1.5")
    assert b._build_definition()["enhancedMode"]["model"] == "MAI-Transcribe-1.5"


def test_mai_locales_included_when_set():
    b = _make_backend(locales=["en-US", "he-IL"])
    assert b._build_definition()["locales"] == ["en-US", "he-IL"]


def test_mai_locales_omitted_when_unset():
    b = _make_backend()
    assert "locales" not in b._build_definition()


# --------------------------------------------------------------------------
# Contrast guard: the stock LLM Speech backend is NOT MAI-ready
# --------------------------------------------------------------------------

def test_llmspeech_still_sends_maxspeakers_and_no_model():
    """Regression guard: extending the base must NOT change llmspeech behavior.
    The classic LLM Speech path still sends maxSpeakers and selects no model."""
    lls = LLMSpeechBackend(
        endpoint="https://rttspeech1763582903.cognitiveservices.azure.com/",
        sample_rate=16000,
        api_key="fake-key",
    )
    definition = lls._build_definition()
    assert definition["diarization"]["maxSpeakers"] == 4
    assert "model" not in definition["enhancedMode"]


# --------------------------------------------------------------------------
# Formatter reuse — MAI phrases[].speaker=0 → "Speaker 0: ..."
# --------------------------------------------------------------------------

def test_mai_formats_diarized_phrases():
    payload = {
        "phrases": [
            {"speaker": 0, "text": "Hello there."},
            {"speaker": 1, "text": "General Kenobi."},
        ]
    }
    out = MAITranscribeBackend._format_phrases(payload)
    assert out == ["Speaker 0: Hello there.", "Speaker 1: General Kenobi."]


def test_mai_formats_combined_phrases_fallback():
    payload = {"combinedPhrases": [{"text": "no diarization here"}]}
    out = MAITranscribeBackend._format_phrases(payload)
    assert out == ["no diarization here"]
