"""Unit tests for the rtt-cli config schema + chat parser. No network.

Run: python -m pytest tests/test_rtt_cli.py -v
"""
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config_schema as s  # noqa: E402
import rtt_cli as cli  # noqa: E402


# --------------------------------------------------------------------------
# Schema integrity
# --------------------------------------------------------------------------

def test_field_names_unique():
    names = [f.name for f in s.FIELDS]
    assert len(names) == len(set(names))


def test_field_flags_unique():
    flags = [f.flag for f in s.FIELDS]
    assert len(flags) == len(set(flags))


def test_no_same_backend_alias_collisions():
    """Two fields applicable to the SAME backend must not share an alias —
    that would make the chat parser ambiguous. (Cross-backend dupes are fine
    because _match_field filters by backend.)"""
    for backend in s.BACKENDS:
        seen = defaultdict(list)
        for f in s.fields_for_backend(backend):
            for a in f.aliases:
                seen[a].append(f.name)
        collisions = {a: fs for a, fs in seen.items() if len(fs) > 1}
        assert not collisions, f"backend {backend} alias collisions: {collisions}"


def test_every_group_has_a_label():
    for g in s.GROUPS:
        assert g in s.GROUP_LABELS


def test_all_profiles_resolve_and_validate_structurally():
    for name in s.PROFILES:
        cfg = s.resolve_profile(name)
        assert cfg["backend"] in s.BACKENDS
        # build_argv must not raise and must start with --backend
        argv = s.build_argv(cfg)
        assert argv[0] == "--backend"


# --------------------------------------------------------------------------
# build_argv
# --------------------------------------------------------------------------

def test_build_argv_emits_backend_first():
    cfg = s.default_config("local")
    argv = s.build_argv(cfg)
    assert argv[:2] == ["--backend", "local"]


def test_build_argv_bool_true_emits_flag():
    cfg = s.default_config("azure")
    cfg["azure_speaker_labels"] = True  # default is True
    cfg["include_mic"] = True           # default False → should emit
    argv = s.build_argv(cfg)
    assert "--include-mic" in argv


def test_build_argv_bool_false_emits_no_variant():
    cfg = s.default_config("azure")
    cfg["azure_speaker_labels"] = False  # default True → emit --no- variant
    argv = s.build_argv(cfg)
    assert "--no-azure-speaker-labels" in argv


def test_build_argv_only_applicable_fields():
    """openai config must not leak whisper/azure-only flags."""
    cfg = s.default_config("openai")
    cfg["openai_endpoint"] = "https://x"
    argv = " ".join(s.build_argv(cfg))
    assert "--model-size" not in argv       # whisper-only
    assert "--azure-region" not in argv     # azure-only
    assert "--openai-endpoint" in argv


def test_build_argv_skips_default_values():
    cfg = s.default_config("openai")
    cfg["openai_endpoint"] = "https://x"
    cfg["chunk_seconds"] = 10.0  # equals default → should NOT emit
    argv = s.build_argv(cfg)
    assert "--chunk-seconds" not in argv
    cfg["chunk_seconds"] = 8.0   # differs → should emit
    assert "--chunk-seconds" in s.build_argv(cfg)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_validate_flags_bad_backend():
    errs = s.validate_config({"backend": "nonsense"})
    assert errs


def test_validate_flags_out_of_range():
    cfg = s.default_config("openai")
    cfg["openai_endpoint"] = "https://x"
    cfg["chunk_seconds"] = 999
    errs = s.validate_config(cfg)
    assert any("chunk_seconds" in e for e in errs)


def test_validate_openai_needs_endpoint():
    cfg = s.default_config("openai")
    cfg["openai_endpoint"] = None
    errs = s.validate_config(cfg)
    assert any("endpoint" in e for e in errs)


def test_validate_clean_config_passes():
    cfg = s.default_config("local")
    assert s.validate_config(cfg) == []


# --------------------------------------------------------------------------
# Chat parser
# --------------------------------------------------------------------------

def test_chat_hebrew_switches_to_llmspeech():
    cfg = s.resolve_profile("rtt")
    cli.chat_apply(cfg, "use hebrew")
    assert cfg["backend"] == "llmspeech"
    assert cfg["llmspeech_locales"] == "en-US,he-IL"


def test_chat_chunk_number():
    cfg = s.resolve_profile("rtt")
    cli.chat_apply(cfg, "chunk 8")
    assert cfg["chunk_seconds"] == 8.0


def test_chat_faster_slower():
    cfg = s.resolve_profile("rtt")
    cfg["chunk_seconds"] = 10.0
    cli.chat_apply(cfg, "faster")
    assert cfg["chunk_seconds"] == 7.0
    cli.chat_apply(cfg, "slower")
    assert cfg["chunk_seconds"] == 12.0


def test_chat_switch_backend():
    cfg = s.resolve_profile("rtt")
    cli.chat_apply(cfg, "switch to local whisper")
    assert cfg["backend"] == "local"


def test_chat_add_mic_variants():
    for phrase in ["add my mic", "my mic", "include mic", "with mic"]:
        cfg = s.resolve_profile("rtt")
        cfg["include_mic"] = False
        cli.chat_apply(cfg, phrase)
        assert cfg["include_mic"] is True, f"failed for: {phrase}"


def test_chat_output_file():
    cfg = s.resolve_profile("rtt")
    cli.chat_apply(cfg, "save to /tmp/notes.txt")
    assert cfg["output_file"] == "/tmp/notes.txt"


def test_chat_intents():
    cfg = s.resolve_profile("rtt")
    assert cli.chat_apply(cfg, "launch") == "__launch__"
    assert cli.chat_apply(cfg, "go") == "__launch__"
    assert cli.chat_apply(cfg, "wizard") == "__wizard__"
    assert cli.chat_apply(cfg, "quit") == "__quit__"


def test_chat_unhandled_returns_none():
    cfg = s.resolve_profile("rtt")
    assert cli.chat_apply(cfg, "make me a sandwich") is None


def test_chat_backend_switch_preserves_shared_fields():
    cfg = s.resolve_profile("rtt")
    cfg["include_mic"] = True
    cfg["sample_rate"] = 24000
    cli.chat_apply(cfg, "switch to local whisper")
    assert cfg["backend"] == "local"
    # shared audio fields carry over
    assert cfg["include_mic"] is True
    assert cfg["sample_rate"] == 24000
