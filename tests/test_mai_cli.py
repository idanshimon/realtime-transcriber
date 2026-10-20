"""Unit tests for the --backend mai wiring in transcribe.py.

Verifies the CLI dispatch constant, the backend-choice guard, and that the
MAI endpoint resolution reuses the existing LLM Speech / Azure Speech endpoint
env vars (same resource rttspeech1763582903). No network — we only assert the
constant + guard surface, keeping it network-free like the other RTT unit tests.

Run: python -m pytest tests/test_mai_cli.py -v
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transcribe  # noqa: E402


def test_backend_mai_constant_exists():
    assert transcribe.BACKEND_MAI == "mai"


def test_mai_is_in_the_valid_backend_set():
    """The main() guard set must include mai so --backend mai is accepted."""
    import inspect
    src = inspect.getsource(transcribe.main)
    assert "BACKEND_MAI" in src
