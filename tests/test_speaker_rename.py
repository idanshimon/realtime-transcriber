"""Unit tests for TranscriptBuffer speaker rename functionality.

Run with: python -m pytest tests/test_speaker_rename.py -v
(or `pip install pytest` first)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe import TranscriptBuffer  # noqa: E402


def test_apply_speaker_map_no_map():
    buf = TranscriptBuffer(file_path=None)
    line = "[13:43:56] Speaker Guest-1: hello"
    assert buf._apply_speaker_map(line) == line


def test_apply_speaker_map_with_colon():
    buf = TranscriptBuffer(file_path=None)
    buf._speaker_map["Guest-1"] = "Hawk"
    out = buf._apply_speaker_map("[13:43:56] Speaker Guest-1: hello")
    assert out == "[13:43:56] Speaker Hawk: hello"


def test_apply_speaker_map_no_collision_substring():
    """Renaming Guest-1 must not match Guest-10."""
    buf = TranscriptBuffer(file_path=None)
    buf._speaker_map["Guest-1"] = "Hawk"
    out = buf._apply_speaker_map("[13:43:56] Speaker Guest-10: hello")
    assert "Guest-10" in out
    assert "Hawk" not in out


def test_known_speakers_dedup_order():
    buf = TranscriptBuffer(file_path=None)
    buf._lines = [
        "[1] Speaker Guest-1: a",
        "[2] Speaker Guest-2: b",
        "[3] Speaker Guest-1: c",
        "[4] Speaker Guest-3: d",
    ]
    assert buf.known_speakers() == ["Guest-1", "Guest-2", "Guest-3"]


def test_rename_speaker_rewrites_file(tmp_path: Path):
    file = tmp_path / "transcript.txt"
    buf = TranscriptBuffer(file_path=file)
    buf.add("[1] Speaker Guest-1: alpha")
    buf.add("[2] Speaker Guest-2: bravo")
    buf.add("[3] Speaker Guest-1: charlie")

    count = buf.rename_speaker("Guest-1", "Hawk")
    assert count == 2

    contents = file.read_text(encoding="utf-8")
    assert "Speaker Hawk: alpha" in contents
    assert "Speaker Hawk: charlie" in contents
    assert "Speaker Guest-2: bravo" in contents
    assert "Guest-1" not in contents


def test_rename_future_lines_auto_mapped(tmp_path: Path):
    file = tmp_path / "transcript.txt"
    buf = TranscriptBuffer(file_path=file)
    buf.add("[1] Speaker Guest-1: alpha")
    buf.rename_speaker("Guest-1", "Hawk")
    buf.add("[2] Speaker Guest-1: beta")  # raw input still has Guest-1

    contents = file.read_text(encoding="utf-8")
    assert "Speaker Hawk: alpha" in contents
    assert "Speaker Hawk: beta" in contents
    assert "Guest-1" not in contents


def test_rename_empty_inputs_rejected():
    buf = TranscriptBuffer(file_path=None)
    assert buf.rename_speaker("", "Hawk") == 0
    assert buf.rename_speaker("Guest-1", "") == 0
    assert buf._speaker_map == {}


def test_snapshot_applies_map():
    buf = TranscriptBuffer(file_path=None)
    buf.add("[1] Speaker Guest-1: hello")
    buf.add("[2] Speaker Guest-2: world")
    buf._speaker_map["Guest-1"] = "Hawk"
    snap = buf.snapshot()
    assert "Speaker Hawk: hello" in snap
    assert "Speaker Guest-2: world" in snap


if __name__ == "__main__":
    # Allow running as a plain script for environments without pytest.
    import tempfile
    failures = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            if "tmp_path" in fn.__code__.co_varnames:
                with tempfile.TemporaryDirectory() as tmp:
                    fn(Path(tmp))
            else:
                fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
            failures += 1
        except Exception as e:
            print(f"ERROR {name}: {e!r}")
            failures += 1
    sys.exit(1 if failures else 0)
