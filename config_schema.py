"""Declarative configuration schema for RTT — the single source of truth.

Everything in rtt_cli.py (wizard, chat parser, validation, launch-command
builder, help) reads from the `FIELDS` registry below. To add a new
configuration option, add ONE `Field(...)` entry here — it then automatically
appears in the wizard, is understood by the chat parser (via `aliases`), is
validated, and is emitted into the launch command. No UI code changes needed.

This is the abstraction layer that keeps the CLI scalable.

Nothing here imports the heavy transcribe.py modules, so it's cheap to load and
trivial to unit-test (no network, no audio, no Azure SDK).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

BACKENDS = ["local", "azure", "openai", "llmspeech"]

BACKEND_HELP = {
    "local": "Offline faster-whisper. No cloud, no diarization. Private.",
    "azure": "Classic Speech SDK (ConversationTranscriber). Real-time streaming + Guest-N diarization + Hebrew continuous-LID.",
    "openai": "Azure OpenAI gpt-4o-transcribe-diarize (chunked). Best WER, Speaker A/B labels, ~1/3 cost. Default for `rtt`.",
    "llmspeech": "Azure Speech LLM Speech (chunked). Multilingual auto-detect (Hebrew/English), Speaker 1/2 labels. Default for `rttheb`.",
}


# --------------------------------------------------------------------------
# Field definition
# --------------------------------------------------------------------------

@dataclass
class Field:
    """One configurable option. The atomic unit the whole CLI is built from."""
    name: str                                   # config key / python arg name
    flag: str                                   # CLI flag, e.g. "--chunk-seconds"
    type: str                                   # str|int|float|bool|choice|path
    default: Any = None
    choices: Optional[List[str]] = None
    help: str = ""
    group: str = "general"                      # menu grouping
    applies_to: Optional[List[str]] = None      # backends; None = all
    aliases: List[str] = field(default_factory=list)   # NL keywords for chat
    env_var: Optional[str] = None               # env fallback for default
    secret: bool = False                        # mask value in display
    min: Optional[float] = None
    max: Optional[float] = None
    flag_false: Optional[str] = None            # for --x/--no-x bool flags

    def applies(self, backend: str) -> bool:
        return self.applies_to is None or backend in self.applies_to

    def effective_default(self) -> Any:
        if self.env_var:
            v = os.environ.get(self.env_var)
            if v:
                return v
        return self.default

    def coerce(self, raw: Any) -> Any:
        """Turn a raw string/user value into the correct python type."""
        if raw is None:
            return None
        if self.type == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")
        if self.type == "int":
            return int(raw)
        if self.type == "float":
            return float(raw)
        # str / choice / path
        return str(raw)

    def validate(self, value: Any) -> Optional[str]:
        """Return an error string if invalid, else None."""
        if value is None:
            return None
        if self.type in ("int", "float"):
            try:
                num = float(value)
            except (TypeError, ValueError):
                return f"{self.name}: '{value}' is not a number"
            if self.min is not None and num < self.min:
                return f"{self.name}: {value} is below min {self.min}"
            if self.max is not None and num > self.max:
                return f"{self.name}: {value} is above max {self.max}"
        if self.type == "choice" and self.choices and str(value) not in self.choices:
            return f"{self.name}: '{value}' not in {self.choices}"
        return None


# --------------------------------------------------------------------------
# The registry — add a Field here and it flows everywhere.
# --------------------------------------------------------------------------

FIELDS: List[Field] = [
    # ---- backend selection ----
    Field(
        "backend", "--backend", "choice", default="openai", choices=BACKENDS,
        help="Which transcription engine to use.", group="backend",
        aliases=["backend", "engine", "model"],
    ),

    # ---- audio capture (all backends) ----
    Field(
        "input_device", "--input-device", "str", default=None,
        env_var="RTT_INPUT_DEVICE",
        help="Audio input device name or index (e.g. 'BlackHole 16ch').",
        group="audio", aliases=["input device", "capture device", "source device", "source"],
    ),
    Field(
        "include_mic", "--include-mic", "bool", default=False,
        flag_false="--no-include-mic", env_var="RTT_INCLUDE_MIC",
        help="Mix your microphone into the captured stream (your voice appears too).",
        group="audio", aliases=["include mic", "with mic", "my voice", "mix mic", "my mic", "add mic", "microphone", "voice"],
    ),
    Field(
        "mic_device", "--mic-device", "str", default=None, env_var="RTT_MIC_DEVICE",
        help="Microphone device when --include-mic is on.",
        group="audio", aliases=["mic device", "mic name"],
    ),
    Field(
        "mic_gain", "--mic-gain", "float", default=1.0, min=0.0, max=2.0,
        help="Mic gain when mixing (0.0-2.0).",
        group="audio", aliases=["mic gain", "gain", "mic volume"],
    ),
    Field(
        "sample_rate", "--sample-rate", "int", default=16000, min=8000, max=48000,
        help="Capture sample rate in Hz.",
        group="audio", aliases=["sample rate", "hz", "samplerate"],
    ),

    # ---- output (all backends) ----
    Field(
        "output_file", "--output-file", "path", default=None,
        help="Append transcript to this file (default: transcripts/transcript-<ts>.txt).",
        group="output", aliases=["output", "output file", "save to", "transcript file"],
    ),

    # ---- whisper (local only) ----
    Field(
        "model_size", "--model-size", "choice", default="base",
        choices=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
        help="Whisper model size (local backend).",
        group="whisper", applies_to=["local"],
        aliases=["model size", "whisper model", "size"],
    ),
    Field(
        "compute", "--compute", "choice", default="auto",
        choices=["auto", "cpu", "metal", "cuda"],
        help="Whisper compute type.",
        group="whisper", applies_to=["local"],
        aliases=["compute", "device type", "acceleration"],
    ),
    Field(
        "beam_size", "--beam-size", "int", default=1, min=1, max=5,
        help="Whisper decoding beam size.",
        group="whisper", applies_to=["local"],
        aliases=["beam", "beam size"],
    ),
    Field(
        "window_seconds", "--window", "float", default=2.5, min=0.5, max=30.0,
        help="Whisper transcription window (seconds).",
        group="whisper", applies_to=["local"],
        aliases=["window", "whisper window"],
    ),
    Field(
        "language", "--language", "str", default=None,
        help="Language hint like 'en' or 'en-US' (local/openai).",
        group="whisper", applies_to=["local", "openai"],
        aliases=["language hint", "lang hint"],
    ),

    # ---- azure classic (azure backend) ----
    Field(
        "azure_speaker_labels", "--azure-speaker-labels", "bool", default=True,
        flag_false="--no-azure-speaker-labels",
        help="Speaker diarization (Guest-N labels) via ConversationTranscriber.",
        group="azure", applies_to=["azure"],
        aliases=["speaker labels", "diarization", "diarize", "speakers"],
    ),
    Field(
        "azure_languages", "--azure-languages", "str", default=None,
        help="Comma-separated auto-detect languages, e.g. 'en-US,he-IL' (Hebrew/English).",
        group="azure", applies_to=["azure"],
        aliases=["languages", "auto detect", "hebrew english", "continuous language"],
    ),
    Field(
        "azure_region", "--azure-region", "str", default=None, env_var="AZURE_SPEECH_REGION",
        help="Azure Speech region.", group="azure", applies_to=["azure"],
        aliases=["region", "azure region"],
    ),
    Field(
        "azure_resource_id", "--azure-resource-id", "str", default=None,
        env_var="AZURE_SPEECH_RESOURCE_ID", secret=True,
        help="Azure Speech ARM resource ID (for AAD auth).",
        group="azure", applies_to=["azure"],
        aliases=["resource id"],
    ),

    # ---- chunked (openai + llmspeech) ----
    Field(
        "chunk_seconds", "--chunk-seconds", "float", default=10.0, min=3.0, max=60.0,
        help="Chunk window (s). Smaller=more live, larger=more accurate.",
        group="chunked", applies_to=["openai", "llmspeech"],
        aliases=["chunk", "chunk seconds", "chunk size", "window", "latency"],
    ),
    Field(
        "openai_endpoint", "--openai-endpoint", "str", default=None,
        env_var="RTT_OPENAI_ENDPOINT",
        help="Azure OpenAI resource endpoint (openai backend).",
        group="chunked", applies_to=["openai"],
        aliases=["openai endpoint", "endpoint"],
    ),
    Field(
        "openai_deployment", "--openai-deployment", "str", default="gpt-4o-transcribe-diarize",
        help="Azure OpenAI deployment name (openai backend).",
        group="chunked", applies_to=["openai"],
        aliases=["deployment", "openai model"],
    ),
    Field(
        "llmspeech_endpoint", "--llmspeech-endpoint", "str", default=None,
        env_var="RTT_LLMSPEECH_ENDPOINT",
        help="Azure Speech resource endpoint (llmspeech backend).",
        group="chunked", applies_to=["llmspeech"],
        aliases=["llmspeech endpoint"],
    ),
    Field(
        "llmspeech_locales", "--llmspeech-locales", "str", default=None,
        help="Comma-separated locales to bias, e.g. 'en-US,he-IL'. Omit for auto-detect.",
        group="chunked", applies_to=["llmspeech"],
        aliases=["locales", "hebrew english", "languages"],
    ),

    # ---- file-mode testing (all backends) ----
    Field(
        "input_file", "--input-file", "path", default=None,
        help="Transcribe a WAV/FLAC/MP3 file instead of live capture.",
        group="testing", aliases=["input file", "file", "from file", "test file"],
    ),
    Field(
        "max_seconds", "--max-seconds", "float", default=None, min=0.1,
        help="Limit file playback to N seconds (with --input-file).",
        group="testing", aliases=["max seconds", "limit"],
    ),
    Field(
        "skip_seconds", "--skip-seconds", "float", default=0.0, min=0.0,
        help="Skip N seconds into the file (with --input-file).",
        group="testing", aliases=["skip seconds", "skip"],
    ),
]

# Menu groups in display order.
GROUPS = ["backend", "audio", "chunked", "azure", "whisper", "output", "testing"]

GROUP_LABELS = {
    "backend": "Backend selection",
    "audio": "Audio capture",
    "chunked": "Chunked backends (openai / llmspeech)",
    "azure": "Classic Azure Speech",
    "whisper": "Local Whisper",
    "output": "Output",
    "testing": "File-mode testing",
}


# --------------------------------------------------------------------------
# Named profiles — the starting presets, built FROM the schema.
# Mirror the shell aliases so the CLI and aliases stay consistent.
# --------------------------------------------------------------------------

PROFILES: Dict[str, Dict[str, Any]] = {
    "rtt": {"backend": "openai"},
    "rttheb": {"backend": "llmspeech", "llmspeech_locales": "en-US,he-IL"},
    "rttold": {"backend": "azure", "azure_speaker_labels": True},
    "rtthebold": {"backend": "azure", "azure_speaker_labels": True, "azure_languages": "en-US,he-IL"},
    "local": {"backend": "local", "model_size": "base"},
}

PROFILE_HELP = {
    "rtt": "Default — Azure OpenAI diarize (English, best quality).",
    "rttheb": "Hebrew/English — LLM Speech multilingual.",
    "rttold": "Classic Speech SDK diarize (fallback / A-B baseline).",
    "rtthebold": "Classic Hebrew continuous-LID (true-realtime fallback).",
    "local": "Offline Whisper (private, no cloud).",
}


# --------------------------------------------------------------------------
# Lookup + build helpers — the API the UI layer consumes.
# --------------------------------------------------------------------------

def field_by_name(name: str) -> Optional[Field]:
    for f in FIELDS:
        if f.name == name:
            return f
    return None


def fields_for_backend(backend: str) -> List[Field]:
    """All fields applicable to a backend, minus the backend selector itself."""
    return [f for f in FIELDS if f.name != "backend" and f.applies(backend)]


def fields_in_group(group: str, backend: Optional[str] = None) -> List[Field]:
    out = [f for f in FIELDS if f.group == group]
    if backend is not None:
        out = [f for f in out if f.applies(backend)]
    return out


def default_config(backend: str = "openai") -> Dict[str, Any]:
    """A fresh config dict populated with effective defaults for a backend."""
    cfg: Dict[str, Any] = {"backend": backend}
    for f in fields_for_backend(backend):
        cfg[f.name] = f.effective_default()
    return cfg


def resolve_profile(name: str) -> Dict[str, Any]:
    """Turn a named profile into a full config dict (defaults + overrides)."""
    if name not in PROFILES:
        raise KeyError(f"unknown profile '{name}'")
    overrides = PROFILES[name]
    cfg = default_config(overrides.get("backend", "openai"))
    cfg.update(overrides)
    return cfg


def validate_config(cfg: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable validation errors (empty = valid)."""
    errors: List[str] = []
    backend = cfg.get("backend")
    if backend not in BACKENDS:
        errors.append(f"backend: '{backend}' must be one of {BACKENDS}")
        return errors

    for name, value in cfg.items():
        f = field_by_name(name)
        if f is None:
            continue
        err = f.validate(value)
        if err:
            errors.append(err)

    # Required-endpoint checks (mirror transcribe.py's guards, surfaced early).
    if backend == "openai" and not cfg.get("openai_endpoint"):
        errors.append("openai backend needs an endpoint (--openai-endpoint or RTT_OPENAI_ENDPOINT).")
    if backend == "llmspeech" and not cfg.get("llmspeech_endpoint"):
        errors.append("llmspeech backend needs an endpoint (--llmspeech-endpoint or RTT_LLMSPEECH_ENDPOINT).")
    return errors


def build_argv(cfg: Dict[str, Any]) -> List[str]:
    """Turn a config dict into transcribe.py CLI args.

    Only emits applicable, non-None fields whose value differs from the field
    default (keeps the command minimal and readable). Backend is always emitted.
    Bool flags emit their true/false variant. Env-backed values are emitted
    explicitly so the launch command is self-contained and reproducible.
    """
    backend = cfg.get("backend", "openai")
    argv: List[str] = ["--backend", str(backend)]

    # File-mode is mutually exclusive with live capture: when input_file is set,
    # suppress device/mic capture flags (transcribe.py rejects both together).
    file_mode = bool(cfg.get("input_file"))
    capture_fields = {"input_device", "include_mic", "mic_device", "mic_gain"}

    for f in fields_for_backend(backend):
        if f.name not in cfg:
            continue
        if file_mode and f.name in capture_fields:
            continue
        value = cfg[f.name]
        if value is None:
            continue

        if f.type == "bool":
            coerced = f.coerce(value)
            # Only emit when it differs from the field default, using the
            # matching --x / --no-x variant.
            if coerced == bool(f.default):
                continue
            argv.append(f.flag if coerced else (f.flag_false or f.flag))
            continue

        # Skip values equal to the plain default (not env-resolved) to keep
        # argv tight — EXCEPT endpoints/secrets which we always emit explicitly.
        if value == f.default and not f.env_var:
            continue
        argv.extend([f.flag, str(value)])

    return argv


def summarize_config(cfg: Dict[str, Any], mask_secrets: bool = True) -> List[Tuple[str, str, str]]:
    """Return (group, label, display_value) rows for a review screen."""
    backend = cfg.get("backend", "openai")
    rows: List[Tuple[str, str, str]] = []
    rows.append(("backend", "backend", str(backend)))
    for group in GROUPS:
        if group == "backend":
            continue
        for f in fields_in_group(group, backend):
            if f.name not in cfg:
                continue
            val = cfg[f.name]
            if val is None or val == "":
                display = "(unset)"
            elif f.secret and mask_secrets:
                display = "***set***"
            else:
                display = str(val)
            rows.append((group, f.name, display))
    return rows
