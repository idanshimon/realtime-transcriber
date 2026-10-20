"""Unit tests for the 'mai' backend in the rtt-cli config schema.

mai = Azure Speech MAI-Transcribe-2 (batch/chunked, diarization-capable). It is
a chunked backend like openai/llmspeech, selected by a named profile. The schema
must register it, expose its endpoint/model/locales fields, resolve a profile,
and build a valid argv — without disturbing the existing backends/profiles.

Run: python -m pytest tests/test_mai_schema.py -v
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config_schema as s  # noqa: E402


def test_mai_is_a_registered_backend():
    assert "mai" in s.BACKENDS
    assert "mai" in s.BACKEND_HELP


def test_mai_profile_resolves_to_mai_backend():
    cfg = s.resolve_profile("mai")
    assert cfg["backend"] == "mai"


def test_mai_profile_builds_valid_argv():
    cfg = s.resolve_profile("mai")
    cfg["mai_endpoint"] = "https://rttspeech1763582903.cognitiveservices.azure.com/"
    argv = s.build_argv(cfg)
    assert argv[:2] == ["--backend", "mai"]
    # validation passes once the endpoint is present
    assert s.validate_config(cfg) == []


def test_mai_backend_needs_endpoint():
    cfg = s.default_config("mai")
    cfg["mai_endpoint"] = None
    errs = s.validate_config(cfg)
    assert any("endpoint" in e for e in errs)


def test_mai_fields_are_mai_only():
    """mai_* fields must not leak into other backends' argv."""
    openai_fields = {f.name for f in s.fields_for_backend("openai")}
    assert "mai_endpoint" not in openai_fields
    assert "mai_model" not in openai_fields
    mai_fields = {f.name for f in s.fields_for_backend("mai")}
    assert "mai_endpoint" in mai_fields
    assert "mai_model" in mai_fields


def test_existing_profiles_unchanged():
    """Guard: adding mai must not alter rtt/rttheb/rttold routing."""
    assert s.resolve_profile("rtt")["backend"] == "openai"
    assert s.resolve_profile("rttheb")["backend"] == "llmspeech"
    assert s.resolve_profile("rttold")["backend"] == "azure"
