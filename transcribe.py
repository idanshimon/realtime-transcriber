#!/usr/bin/env python3
"""Real-time Teams transcription MVP with local Whisper and optional Azure backends."""
from __future__ import annotations

import os
import platform
import queue
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple, Union, TYPE_CHECKING

from datetime import datetime

import numpy as np
import sounddevice as sd
import soundfile as sf
import typer
import av
from av.audio.resampler import AudioResampler
from faster_whisper import WhisperModel

if TYPE_CHECKING:  # pragma: no cover - typing aid
    import azure.cognitiveservices.speech as speechsdk  # type: ignore
else:  # pragma: no cover - runtime import
    try:  # Optional dependency for Azure backend
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        speechsdk = None  # type: ignore

try:
    from azure.identity import DefaultAzureCredential
except ImportError:
    DefaultAzureCredential = None  # type: ignore

from chunked_backends import (
    OpenAITranscribeBackend,
    LLMSpeechBackend,
    process_chunked_backend,
)

app = typer.Typer(add_completion=False, help="Stream live audio into local or Azure speech recognizers.")

BACKEND_LOCAL = "local"
BACKEND_AZURE = "azure"
BACKEND_OPENAI = "openai"        # Azure OpenAI gpt-4o-transcribe-diarize (chunked)
BACKEND_LLMSPEECH = "llmspeech"  # Azure Speech LLM Speech enhancedMode (chunked)


def default_transcript_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("transcripts") / f"transcript-{timestamp}.txt"


class TranscriptBuffer:
    def __init__(self, file_path: Optional[Path]) -> None:
        self._lines: List[str] = []
        self._file_path = file_path
        self._lock = threading.Lock()
        # Index into `_lines` used for Ctrl+E delta copies (since last Ctrl+S).
        # Starts at 0 so Ctrl+E before the first Ctrl+S returns the full transcript.
        self._delta_baseline: int = 0
        # Speaker rename map: "Guest-1" -> "Hawk Ticehurst". Applied at write/render time.
        self._speaker_map: dict = {}
        if file_path:
            file_path.parent.mkdir(parents=True, exist_ok=True)

    def _apply_speaker_map(self, line: str) -> str:
        if not self._speaker_map:
            return line
        out = line
        for src, dst in self._speaker_map.items():
            # Match "Speaker Guest-2:" or "Speaker Guest-2 " patterns.
            out = out.replace(f"Speaker {src}:", f"Speaker {dst}:")
            out = out.replace(f"Speaker {src} ", f"Speaker {dst} ")
        return out

    def add(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            if self._file_path:
                with self._file_path.open("a", encoding="utf-8") as handle:
                    handle.write(self._apply_speaker_map(line) + "\n")

    def known_speakers(self) -> List[str]:
        """Return distinct speaker labels seen so far (e.g. ['Guest-1', 'Guest-2'])."""
        speakers: List[str] = []
        seen = set()
        with self._lock:
            for ln in self._lines:
                # Format: "[HH:MM:SS] Speaker XXX: text"
                idx = ln.find("Speaker ")
                if idx < 0:
                    continue
                tail = ln[idx + len("Speaker "):]
                colon = tail.find(":")
                if colon < 0:
                    continue
                name = tail[:colon].strip()
                if name and name not in seen:
                    seen.add(name)
                    speakers.append(name)
        return speakers

    def rename_speaker(self, src: str, dst: str) -> int:
        """Map src->dst going forward AND rewrite the file with substitutions applied.
        Returns the number of past lines updated."""
        if not src or not dst:
            return 0
        with self._lock:
            self._speaker_map[src] = dst
            updated = 0
            for ln in self._lines:
                if f"Speaker {src}:" in ln or f"Speaker {src} " in ln:
                    updated += 1
            if self._file_path and self._file_path.exists():
                rendered = [self._apply_speaker_map(ln) for ln in self._lines]
                # Rewrite atomically.
                tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
                tmp.write_text("\n".join(rendered) + ("\n" if rendered else ""), encoding="utf-8")
                tmp.replace(self._file_path)
            return updated

    def snapshot(self) -> str:
        with self._lock:
            return "\n".join(self._apply_speaker_map(ln) for ln in self._lines)

    def _delta_snapshot_locked(self) -> str:
        baseline = max(0, min(self._delta_baseline, len(self._lines)))
        if baseline >= len(self._lines):
            return ""
        return "\n".join(self._apply_speaker_map(ln) for ln in self._lines[baseline:])

    def copy_full_to_clipboard(self) -> None:
        text = self.snapshot()
        if not text.strip():
            typer.echo("No transcript available to copy yet.")
            return
        if copy_text_to_clipboard(text):
            with self._lock:
                # Mark baseline AFTER copying full transcript: deltas begin from here.
                self._delta_baseline = len(self._lines)
            typer.echo("Transcript copied to clipboard (full, Ctrl+S).")
        else:
            typer.echo("Clipboard copy failed. Install pbcopy/xclip/clip or disable the hotkey.", err=True)

    def copy_delta_to_clipboard(self) -> None:
        with self._lock:
            text = self._delta_snapshot_locked()
        if not text.strip():
            typer.echo("No new transcript since last Ctrl+S.")
            return

        if copy_text_to_clipboard(text):
            typer.echo("Transcript delta copied to clipboard (Ctrl+E).")
        else:
            typer.echo("Clipboard copy failed. Install pbcopy/xclip/clip or disable the hotkey.", err=True)


class ClipboardHotkeyListener(threading.Thread):
    def __init__(
        self,
        buffer: TranscriptBuffer,
        stop_event: threading.Event,
        paused_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self._buffer = buffer
        self._stop_event = stop_event
        self._paused_event = paused_event
        self._stdin_fd: int = sys.stdin.fileno() if sys.stdin.isatty() else -1
        self._orig_termios = None

    def run(self) -> None:
        if not sys.stdin.isatty():
            typer.echo("Clipboard hotkey disabled: stdin is not attached to a TTY.", err=True)
            return
        if os.name == "nt":
            self._run_windows()
        else:
            self._run_posix()

    def _prompt_rename(self) -> None:
        """Pause raw-mode input, prompt user for src=dst, apply, resume."""
        speakers = self._buffer.known_speakers()
        is_windows = os.name == "nt"
        # Restore canonical mode on POSIX so input() works normally.
        if not is_windows and self._orig_termios is not None:
            try:
                import termios
                termios.tcsetattr(self._stdin_fd, termios.TCSANOW, self._orig_termios)
            except Exception:
                pass
        try:
            sys.stdout.write("\n\u270f\ufe0f  Rename speaker. ")
            if speakers:
                sys.stdout.write(f"Known: {', '.join(speakers)}\n")
            else:
                sys.stdout.write("(No speakers labeled yet.)\n")
            sys.stdout.write('src=dst (e.g. "Guest-2=Hawk Ticehurst") or blank to cancel: ')
            sys.stdout.flush()
            try:
                raw = sys.stdin.readline().rstrip("\n")
            except (EOFError, KeyboardInterrupt):
                raw = ""
            if not raw.strip():
                typer.echo("Rename cancelled.")
                return
            if "=" not in raw:
                typer.echo("\u26a0\ufe0f  Invalid format. Use src=dst (e.g. Guest-2=Hawk Ticehurst).", err=True)
                return
            src, _, dst = raw.partition("=")
            src = src.strip()
            dst = dst.strip()
            if not src or not dst:
                typer.echo("\u26a0\ufe0f  Both src and dst are required.", err=True)
                return
            if speakers and src not in speakers:
                typer.echo(f"\u26a0\ufe0f  '{src}' not in known speakers ({', '.join(speakers)}). Mapping anyway.")
            count = self._buffer.rename_speaker(src, dst)
            typer.echo(f"\u2705 Renamed {src} \u2192 {dst} ({count} past lines updated, future lines auto-mapped).")
        finally:
            # Re-enter raw mode on POSIX.
            if not is_windows and self._orig_termios is not None:
                try:
                    import termios
                    new_attrs = termios.tcgetattr(self._stdin_fd)
                    new_attrs[3] &= ~(termios.ECHO | termios.ICANON)
                    new_attrs[0] &= ~termios.IXON
                    termios.tcsetattr(self._stdin_fd, termios.TCSANOW, new_attrs)
                except Exception:
                    pass

    def _run_windows(self) -> None:
        try:
            import msvcrt  # type: ignore
        except ImportError:
            typer.echo("Clipboard hotkey disabled: msvcrt unavailable on this platform.", err=True)
            return
        while not self._stop_event.is_set():
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                ch = msvcrt.getwch()  # type: ignore[attr-defined]
                if ch == "\x05":  # Ctrl+E
                    self._buffer.copy_delta_to_clipboard()
                elif ch == "\x13":  # Ctrl+S
                    self._buffer.copy_full_to_clipboard()
                elif ch == "\x10":  # Ctrl+P
                    if self._paused_event.is_set():
                        self._paused_event.clear()
                        typer.echo("Transcription resumed (Ctrl+P).")
                    else:
                        self._paused_event.set()
                        typer.echo("Transcription paused (Ctrl+P).")
                elif ch == "\x12":  # Ctrl+R
                    self._prompt_rename()
            time.sleep(0.05)

    def _run_posix(self) -> None:
        import termios
        import tty

        if self._stdin_fd < 0:
            typer.echo("Clipboard hotkey disabled: stdin file descriptor unavailable.", err=True)
            return
        try:
            self._orig_termios = termios.tcgetattr(self._stdin_fd)
        except termios.error as exc:  # pragma: no cover - environment specific
            typer.echo(f"Clipboard hotkey disabled: cannot access terminal attributes ({exc}).", err=True)
            return

        new_attrs = termios.tcgetattr(self._stdin_fd)
        new_attrs[3] &= ~(termios.ECHO | termios.ICANON)
        new_attrs[0] &= ~termios.IXON  # disable software flow control so Ctrl+S is delivered
        termios.tcsetattr(self._stdin_fd, termios.TCSANOW, new_attrs)

        try:
            while not self._stop_event.is_set():
                rlist, _, _ = select.select([self._stdin_fd], [], [], 0.1)
                if self._stdin_fd in rlist:
                    ch = os.read(self._stdin_fd, 1)
                    if ch == b"\x05":
                        self._buffer.copy_delta_to_clipboard()
                    elif ch == b"\x13":
                        self._buffer.copy_full_to_clipboard()
                    elif ch == b"\x10":
                        if self._paused_event.is_set():
                            self._paused_event.clear()
                            typer.echo("Transcription resumed (Ctrl+P).")
                        else:
                            self._paused_event.set()
                            typer.echo("Transcription paused (Ctrl+P).")
                    elif ch == b"\x12":  # Ctrl+R
                        self._prompt_rename()
        finally:
            if self._orig_termios is not None:
                termios.tcsetattr(self._stdin_fd, termios.TCSANOW, self._orig_termios)


def copy_text_to_clipboard(text: str) -> bool:
    if not text:
        return False
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        if os.name == "nt":
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
            return True
        for cmd in ("wl-copy", "xclip", "xsel"):
            if shutil.which(cmd):
                subprocess.run([cmd], input=text.encode("utf-8"), check=True)
                return True
        typer.echo("Clipboard copy not available: install wl-copy or xclip/xsel.", err=True)
    except Exception as exc:
        typer.echo(f"Clipboard copy failed: {exc}", err=True)
    return False

def list_devices() -> None:
    devices = sd.query_devices()
    default_input = sd.default.device[0]
    typer.echo("Available CoreAudio input devices:\n")
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] < 1:
            continue
        marker = "*" if idx == default_input else " "
        typer.echo(f"{marker} [{idx:>2}] {dev['name']} (max {dev['max_input_channels']} ch)")


def resolve_device(device_option: Optional[str]) -> Optional[int]:
    if device_option is None:
        return None
    devices = sd.query_devices()
    try:
        idx = int(device_option)
        if idx < 0 or idx >= len(devices):
            raise ValueError
        return idx
    except ValueError:
        matches = [i for i, dev in enumerate(devices) if device_option.lower() in dev["name"].lower()]
        if not matches:
            raise typer.BadParameter(f"No device matches '{device_option}'.")
        if len(matches) > 1:
            names = ", ".join(devices[i]["name"] for i in matches)
            raise typer.BadParameter(f"Multiple matches for '{device_option}': {names}. Use an index.")
        return matches[0]


def audio_chunks_from_device(
    capture_queue: queue.Queue[np.ndarray],
    stop_event: threading.Event,
    paused_event: threading.Event,
    samplerate: int,
    block_duration: float,
    device_index: Optional[int],
    use_loopback: bool = False,
) -> sd.InputStream:
    blocksize = max(256, int(samplerate * block_duration))

    def callback(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        if status:
            typer.echo(f"PortAudio status: {status}", err=True)
        if paused_event.is_set():
            return
        capture_queue.put(indata.copy())

    stream_kwargs: dict = dict(
        samplerate=samplerate,
        channels=1,
        dtype="float32",
        device=device_index,
        blocksize=blocksize,
        callback=callback,
    )

    # WASAPI loopback (Windows): captures the OUTPUT of a device as if it were an input.
    # Lets us record what's playing through the speakers without virtual cables/drivers.
    if use_loopback:
        try:
            stream_kwargs["extra_settings"] = sd.WasapiSettings(loopback=True)
        except AttributeError:
            typer.echo(
                "Warning: WASAPI loopback requested but unavailable on this platform. "
                "Falling back to direct device capture.",
                err=True,
            )

    stream = sd.InputStream(**stream_kwargs)
    stream.start()
    return stream


class MixedDeviceCapture:
    """Captures from a primary device (system audio) and a mic, mixes them in real time.

    Each stream pushes into its own ring buffer. A consumer thread pulls equal-sized
    blocks from both and sums them (with simple clipping protection) into capture_queue.
    If one stream falls behind, the other plays through alone for that block, so the
    transcript never stalls waiting on a silent device.
    """

    def __init__(
        self,
        capture_queue: "queue.Queue[np.ndarray]",
        stop_event: threading.Event,
        paused_event: threading.Event,
        samplerate: int,
        block_duration: float,
        primary_device: Optional[int],
        mic_device: Optional[int],
        use_loopback: bool = False,
        mic_gain: float = 1.0,
    ) -> None:
        self.capture_queue = capture_queue
        self.stop_event = stop_event
        self.paused_event = paused_event
        self.samplerate = samplerate
        self.blocksize = max(256, int(samplerate * block_duration))
        self.mic_gain = mic_gain

        self._primary_buf: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=64)
        self._mic_buf: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=64)

        primary_kwargs = dict(
            samplerate=samplerate, channels=1, dtype="float32",
            device=primary_device, blocksize=self.blocksize,
            callback=self._make_cb(self._primary_buf),
        )
        if use_loopback:
            try:
                primary_kwargs["extra_settings"] = sd.WasapiSettings(loopback=True)
            except AttributeError:
                typer.echo("Warning: WASAPI loopback unavailable; using direct capture.", err=True)

        self._primary_stream = sd.InputStream(**primary_kwargs)
        self._mic_stream = sd.InputStream(
            samplerate=samplerate, channels=1, dtype="float32",
            device=mic_device, blocksize=self.blocksize,
            callback=self._make_cb(self._mic_buf),
        )

        self._mixer_thread = threading.Thread(target=self._mixer_loop, daemon=True)

    def _make_cb(self, buf: "queue.Queue[np.ndarray]"):
        def cb(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
            if status:
                typer.echo(f"PortAudio status: {status}", err=True)
            if self.paused_event.is_set():
                return
            try:
                buf.put_nowait(indata.copy())
            except queue.Full:
                # Drop oldest block to keep latency bounded.
                try:
                    buf.get_nowait()
                    buf.put_nowait(indata.copy())
                except queue.Empty:
                    pass
        return cb

    def _drain_block(self, buf: "queue.Queue[np.ndarray]") -> Optional[np.ndarray]:
        try:
            return buf.get(timeout=0.5)
        except queue.Empty:
            return None

    def _mixer_loop(self) -> None:
        zeros = np.zeros((self.blocksize, 1), dtype=np.float32)
        while not self.stop_event.is_set():
            primary = self._drain_block(self._primary_buf)
            mic = self._drain_block(self._mic_buf)
            if primary is None and mic is None:
                continue
            if primary is None:
                primary = zeros
            if mic is None:
                mic = zeros
            # Align block lengths defensively
            n = min(primary.shape[0], mic.shape[0])
            mixed = primary[:n] + (mic[:n] * self.mic_gain)
            np.clip(mixed, -1.0, 1.0, out=mixed)
            self.capture_queue.put(mixed)

    def start(self) -> None:
        self._primary_stream.start()
        self._mic_stream.start()
        self._mixer_thread.start()

    def stop(self) -> None:
        for s in (self._primary_stream, self._mic_stream):
            try:
                s.stop()
                s.close()
            except Exception:
                pass


SOUND_FILE_EXTS = {".wav", ".flac", ".ogg", ".oga", ".aiff", ".aif", ".aifc"}


def _audio_chunks_soundfile(path: Path, samplerate: int, block_duration: float) -> Iterable[np.ndarray]:
    frames_per_block = max(256, int(samplerate * block_duration))
    with sf.SoundFile(path, mode="r") as handle:
        if handle.samplerate != samplerate:
            raise typer.BadParameter(
                f"Input file sample rate {handle.samplerate} Hz does not match target {samplerate} Hz."
            )
        while True:
            data = handle.read(frames_per_block, dtype="float32", always_2d=True)
            if not len(data):
                break
            if data.shape[1] > 1:
                data = np.mean(data, axis=1, keepdims=True)
            yield data


def _audio_chunks_av(path: Path, samplerate: int, block_duration: float) -> Iterable[np.ndarray]:
    frames_per_block = max(256, int(samplerate * block_duration))
    buffer = np.empty((0,), dtype=np.float32)
    try:
        with av.open(str(path)) as container:
            audio_stream = next((s for s in container.streams if s.type == "audio"), None)
            if audio_stream is None:
                raise typer.BadParameter(f"No audio stream found in {path}.")
            audio_stream.thread_type = "AUTO"
            resampler = AudioResampler(format="flt", layout="mono", rate=samplerate)
            for frame in container.decode(audio_stream):
                resampled_frames = resampler.resample(frame)
                if not resampled_frames:
                    continue
                if not isinstance(resampled_frames, list):
                    resampled_frames = [resampled_frames]
                for resampled in resampled_frames:
                    arr = resampled.to_ndarray()
                    if arr.ndim > 1:
                        arr = np.mean(arr, axis=0)
                    arr = arr.astype(np.float32)
                    buffer = np.concatenate((buffer, arr))
                    while buffer.shape[0] >= frames_per_block:
                        chunk = buffer[:frames_per_block]
                        buffer = buffer[frames_per_block:]
                        yield chunk.reshape(-1, 1)
    except av.AVError as exc:
        raise typer.BadParameter(f"Failed to decode {path.name}: {exc}") from exc
    if buffer.size:
        yield buffer.reshape(-1, 1)


def audio_chunks_from_file(path: Path, samplerate: int, block_duration: float) -> Iterable[np.ndarray]:
    suffix = path.suffix.lower()
    if suffix in SOUND_FILE_EXTS:
        try:
            yield from _audio_chunks_soundfile(path, samplerate, block_duration)
            return
        except Exception as exc:  # pragma: no cover - fallback path
            typer.echo(f"soundfile could not read {path.name}: {exc}. Falling back to PyAV.", err=True)
    yield from _audio_chunks_av(path, samplerate, block_duration)


class LocalWhisperBackend:
    def __init__(
        self,
        model_size: str,
        compute: str,
        language: Optional[str],
        beam_size: int,
    ) -> None:
        self.model = WhisperModel(model_size, device="auto", compute_type=compute)
        self.language = language
        self.beam_size = beam_size

    def transcribe_window(self, audio_window: np.ndarray, sample_rate: int) -> List[str]:
        audio_window = audio_window.astype(np.float32)
        if np.max(np.abs(audio_window)) < 1e-5:
            return []
        kwargs = dict(
            beam_size=self.beam_size,
            temperature=0.0,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 250},
        )
        lang = self.language
        try:
            segments, _ = self.model.transcribe(audio_window, language=lang, **kwargs)
        except ValueError as exc:
            if lang is None and "max() arg is an empty sequence" in str(exc):
                segments, _ = self.model.transcribe(audio_window, language="en", **kwargs)
            else:
                raise
        lines: List[str] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                lines.append(text)
        return lines


class AzureSpeechBackend:
    def __init__(
        self,
        region: Optional[str],
        endpoint: Optional[str],
        language: str,
        sample_rate: int,
        enable_speaker_labels: bool,
        key: Optional[str] = None,
        auth_token: Optional[str] = None,
        auto_detect_languages: Optional[List[str]] = None,
        token_provider=None,
    ) -> None:
        if speechsdk is None:
            raise RuntimeError("azure-cognitiveservices-speech is not installed.")
        if not key and not auth_token:
            raise ValueError("Azure Speech backend requires either a subscription key or an auth token.")

        if key:
            if endpoint:
                speech_config = speechsdk.SpeechConfig(subscription=key, endpoint=endpoint)
            else:
                if not region:
                    raise ValueError("Azure Speech backend requires either region or endpoint.")
                speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        else:
            if endpoint:
                speech_config = speechsdk.SpeechConfig(endpoint=endpoint)
            else:
                if not region:
                    raise ValueError("Azure Speech backend requires either region or endpoint.")
                speech_config = speechsdk.SpeechConfig(auth_token=auth_token, region=region)
            speech_config.authorization_token = auth_token

        # Language: use auto-detect if multiple languages requested, else single language
        self._auto_detect_config: Optional[speechsdk.languageconfig.AutoDetectSourceLanguageConfig] = None
        if auto_detect_languages and len(auto_detect_languages) > 1:
            self._auto_detect_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=auto_detect_languages
            )
            # Enable CONTINUOUS language detection (re-detects per segment, not just at start)
            # This is the key setting for Hebrew/English code-switching mid-sentence
            try:
                speech_config.set_property(
                    property_id=speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode,
                    value="Continuous",
                )
                typer.echo("Azure language ID mode: Continuous (best for mixed Hebrew/English)", err=True)
            except AttributeError:
                # Older SDK versions may not have this PropertyId — falls back to AtStart detection
                typer.echo("Azure language ID mode: AtStart (upgrade SDK for Continuous mode)", err=True)
            typer.echo(f"Azure auto-detect languages: {', '.join(auto_detect_languages)}", err=True)
        else:
            speech_config.speech_recognition_language = language
        if enable_speaker_labels:
            diarize_property = getattr(
                speechsdk.PropertyId,
                "SpeechServiceResponse_DiarizeIntermediateResults",
                None,
            )
            if diarize_property is not None:
                speech_config.set_property(property_id=diarize_property, value="true")
        self._enable_speaker_labels = enable_speaker_labels
        self._use_conversation = enable_speaker_labels
        self._conversation_transcriber = None
        self._speech_recognizer = None
        self._speech_config = speech_config
        self._token_provider = token_provider
        self._sample_rate = sample_rate
        self._build_recognizer()
        self._text_queue: "queue.Queue[str]" = queue.Queue()
        self._stop_event = threading.Event()
        self._session_stopped_event = threading.Event()
        self._auth_error_event = threading.Event()
        # --- Token auto-refresh (AAD) ---
        # AAD tokens expire ~60-90 min. The Speech SDK silently rotates its
        # WebSocket connection mid-session; if the authorization_token is stale
        # at that moment the upgrade fails with HTTP 401 and the session is
        # canceled. We proactively refresh authorization_token on the live
        # speech_config every few minutes so a fresh token is always present.
        self._speech_config = speech_config
        self._token_provider = token_provider
        self._sample_rate = sample_rate
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_interval = 540  # seconds (9 min — well under the ~60 min TTL)

    def _on_session_started(self, evt) -> None:
        session_id = getattr(evt, "session_id", None) or getattr(evt, "sessionId", None)
        if session_id:
            typer.echo(f"Azure session started (id={session_id})", err=True)
        else:
            typer.echo("Azure session started", err=True)

    def _on_recognized(self, evt) -> None:
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
            self._text_queue.put(evt.result.text)

    def _on_transcribed(self, evt) -> None:
        text = evt.result.text.strip()
        if text:
            speaker = evt.result.speaker_id
            if speaker:
                self._text_queue.put(f"Speaker {speaker}: {text}")
            else:
                self._text_queue.put(text)

    def _on_canceled(self, evt) -> None:
        reason = getattr(evt, "reason", None)
        error_details = getattr(evt, "error_details", None) or getattr(evt, "errorDetails", None)
        error_code = getattr(evt, "error_code", None) or getattr(evt, "errorCode", None)

        if hasattr(evt, "result"):
            try:
                details = speechsdk.CancellationDetails(evt.result)
                reason = getattr(details, "reason", reason)
                error_code = getattr(details, "error_code", error_code)
                error_details = getattr(details, "error_details", error_details)
            except Exception:
                pass

        # Detect auth/connection failures (e.g. expired AAD token → 401 on
        # WebSocket upgrade) so the worker loop can transparently re-auth and
        # resume instead of leaving the session permanently deaf.
        details_str = str(error_details or "")
        is_auth_error = (
            "401" in details_str
            or "Authentication" in details_str
            or "Forbidden" in details_str
            or "403" in details_str
        )
        if is_auth_error:
            self._auth_error_event.set()
            # Refresh the token immediately so the next start() uses a valid one.
            self._refresh_token(force=True)

        typer.echo(f"Azure canceled (reason={reason}, code={error_code}): {error_details}", err=True)

    def _on_session_stopped(self, evt) -> None:
        session_id = getattr(evt, "session_id", None) or getattr(evt, "sessionId", None)
        if session_id:
            typer.echo(f"Azure session stopped (id={session_id})", err=True)
        else:
            typer.echo("Azure session stopped", err=True)
        self._session_stopped_event.set()

    def _build_recognizer(self) -> None:
        """(Re)build the push stream + recognizer/transcriber and wire events.

        Called once at init and again by restart() after an auth-error cancel.
        A PushAudioInputStream cannot be reused after the session is canceled,
        so we always create a fresh one here."""
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=self._sample_rate, bits_per_sample=16, channels=1
        )
        self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)
        self._conversation_transcriber = None
        self._speech_recognizer = None
        if self._enable_speaker_labels:
            if self._auto_detect_config:
                self._conversation_transcriber = speechsdk.transcription.ConversationTranscriber(
                    speech_config=self._speech_config,
                    audio_config=audio_config,
                    auto_detect_source_language_config=self._auto_detect_config,
                )
            else:
                self._conversation_transcriber = speechsdk.transcription.ConversationTranscriber(
                    speech_config=self._speech_config,
                    audio_config=audio_config,
                )
            self._conversation_transcriber.transcribed.connect(self._on_transcribed)
            self._conversation_transcriber.canceled.connect(self._on_canceled)
            self._conversation_transcriber.session_stopped.connect(self._on_session_stopped)
            self._conversation_transcriber.session_started.connect(self._on_session_started)
        else:
            if self._auto_detect_config:
                self._speech_recognizer = speechsdk.SpeechRecognizer(
                    speech_config=self._speech_config,
                    audio_config=audio_config,
                    auto_detect_source_language_config=self._auto_detect_config,
                )
            else:
                self._speech_recognizer = speechsdk.SpeechRecognizer(
                    speech_config=self._speech_config, audio_config=audio_config
                )
            self._speech_recognizer.recognized.connect(self._on_recognized)
            self._speech_recognizer.canceled.connect(self._on_canceled)
            self._speech_recognizer.session_stopped.connect(self._on_session_stopped)
            self._speech_recognizer.session_started.connect(self._on_session_started)

    def restart(self) -> bool:
        """Tear down the canceled recognizer and stand up a fresh one with a
        valid token. Used by the worker to auto-recover from an auth-error
        cancel without losing the rest of the meeting. Returns True on success."""
        # Token was already refreshed in _on_canceled; refresh once more to be
        # safe (handles the case where the cancel-time refresh failed).
        self._refresh_token(force=True)
        # Best-effort teardown of the dead recognizer.
        try:
            if self._use_conversation and self._conversation_transcriber is not None:
                self._conversation_transcriber.stop_transcribing_async().get()
            elif self._speech_recognizer is not None:
                self._speech_recognizer.stop_continuous_recognition_async().get()
        except Exception:
            pass
        try:
            self.push_stream.close()
        except Exception:
            pass
        # Reset the stopped flag and rebuild from scratch.
        self._session_stopped_event.clear()
        try:
            self._build_recognizer()
            if self._use_conversation:
                assert self._conversation_transcriber is not None
                self._conversation_transcriber.start_transcribing_async().get()
            else:
                assert self._speech_recognizer is not None
                self._speech_recognizer.start_continuous_recognition_async().get()
            return True
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"Azure restart failed: {exc}", err=True)
            return False

    def _refresh_token(self, force: bool = False) -> bool:
        """Fetch a fresh AAD token and apply it to the live speech_config.

        Returns True if a new token was applied. Safe to call repeatedly; on
        key-auth (no token_provider) it is a no-op.
        """
        if self._token_provider is None:
            return False
        try:
            new_token = self._token_provider()
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"Azure token refresh failed: {exc}", err=True)
            return False
        if not new_token:
            return False
        try:
            self._speech_config.authorization_token = new_token
            if force:
                typer.echo("Azure token refreshed (post-auth-error).", err=True)
            else:
                typer.echo("Azure token refreshed (proactive).", err=True)
            return True
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"Failed to apply refreshed Azure token: {exc}", err=True)
            return False

    def _refresh_loop(self) -> None:
        """Background thread: proactively refresh the AAD token on an interval
        so the SDK's silent WebSocket reconnects always see a valid token."""
        while not self._stop_event.wait(self._refresh_interval):
            self._refresh_token(force=False)

    def start(self) -> None:
        # Apply a fresh token before connecting, then keep it fresh in the
        # background so mid-session reconnects never present an expired token.
        self._refresh_token(force=False)
        if self._token_provider is not None and self._refresh_thread is None:
            self._refresh_thread = threading.Thread(
                target=self._refresh_loop, daemon=True, name="azure-token-refresh"
            )
            self._refresh_thread.start()
        if self._use_conversation:
            assert self._conversation_transcriber is not None
            self._conversation_transcriber.start_transcribing_async().get()
        else:
            assert self._speech_recognizer is not None
            self._speech_recognizer.start_continuous_recognition_async().get()

    def stop(self) -> None:
        self._stop_event.set()
        # Close the push stream to signal end-of-audio, giving Azure a
        # chance to finalize any pending utterances before we stop the recognizer.
        try:
            self.push_stream.close()
        except Exception:
            pass  # already closed
        if self._use_conversation:
            assert self._conversation_transcriber is not None
            self._conversation_transcriber.stop_transcribing_async().get()
        else:
            assert self._speech_recognizer is not None
            self._speech_recognizer.stop_continuous_recognition_async().get()

    def push_audio(self, audio: np.ndarray) -> None:
        audio = np.squeeze(audio)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        self.push_stream.write(pcm.tobytes())

    def drain_text(self) -> List[str]:
        lines: List[str] = []
        while True:
            try:
                lines.append(self._text_queue.get_nowait())
            except queue.Empty:
                break
        return lines


def output_writer(buffer: TranscriptBuffer):
    lock = threading.Lock()

    def emit(text: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}"
        with lock:
            typer.echo(line)
            buffer.add(line)

    return emit


def process_local_backend(
    backend: LocalWhisperBackend,
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    paused_event: threading.Event,
    emit,
    samplerate: int,
    window_seconds: float,
) -> None:
    samples_per_window = max(1024, int(samplerate * window_seconds))
    buffer = np.empty((0,), dtype=np.float32)
    while not stop_event.is_set() or not audio_queue.empty():
        if paused_event.is_set():
            buffer = np.empty((0,), dtype=np.float32)
            while True:
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    break
            time.sleep(0.1)
            continue
        try:
            chunk = audio_queue.get(timeout=0.2)
            chunk = np.squeeze(chunk)
            buffer = np.concatenate((buffer, chunk))
        except queue.Empty:
            continue
        while buffer.shape[0] >= samples_per_window:
            window = buffer[:samples_per_window]
            buffer = buffer[samples_per_window:]
            lines = backend.transcribe_window(window, samplerate)
            for line in lines:
                emit(line)
    if buffer.size:
        lines = backend.transcribe_window(buffer, samplerate)
        for line in lines:
            emit(line)


def process_azure_backend(
    backend: AzureSpeechBackend,
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    paused_event: threading.Event,
    emit,
) -> None:
    backend.start()
    try:
        while not stop_event.is_set() or not audio_queue.empty():
            # If the session was canceled by an auth error (e.g. expired AAD
            # token → 401 on WebSocket reconnect), transparently re-establish
            # the recognizer instead of going permanently deaf. The token was
            # already refreshed in _on_canceled; restart the recognizer here.
            if backend._auth_error_event.is_set() and not stop_event.is_set():
                typer.echo("Azure: auth error detected — re-establishing session...", err=True)
                resumed = backend.restart()
                if resumed:
                    emit("[RTT: session auto-recovered after auth error]")
                    backend._auth_error_event.clear()
                else:
                    typer.echo("Azure: auto-recovery failed, retrying in 3s...", err=True)
                    time.sleep(3)
                    continue
            if paused_event.is_set():
                while True:
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        break
                backend.drain_text()
                time.sleep(0.1)
                continue
            try:
                chunk = audio_queue.get(timeout=0.2)
                backend.push_audio(chunk)
            except queue.Empty:
                pass
            for line in backend.drain_text():
                emit(line)
        # All audio has been pushed. Close the push stream to signal end-of-audio
        # so Azure can finalize any pending utterances, then keep draining results
        # until the session fully stops.
        backend.push_stream.close()
        while not backend._session_stopped_event.wait(timeout=0.2):
            for line in backend.drain_text():
                emit(line)
        # Final drain after session has stopped
        for line in backend.drain_text():
            emit(line)
    except Exception as exc:
        typer.echo(f"Azure backend worker failed: {exc}", err=True)
        stop_event.set()
    finally:
        backend.stop()
        # Drain any results produced during stop()
        for line in backend.drain_text():
            emit(line)


def read_file_into_queue(
    input_file: Path,
    samplerate: int,
    block_duration: float,
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    paused_event: threading.Event,
    skip_seconds: float,
    max_seconds: Optional[float],
) -> None:
    delivered = 0.0
    skipped = 0.0
    blocks = iter(audio_chunks_from_file(input_file, samplerate, block_duration))
    while True:
        if stop_event.is_set():
            break
        if paused_event.is_set():
            time.sleep(0.1)
            continue
        try:
            block = next(blocks)
        except StopIteration:
            break
        block_duration_sec = block.shape[0] / samplerate
        if skip_seconds > 0 and skipped < skip_seconds:
            remaining_skip = skip_seconds - skipped
            if remaining_skip >= block_duration_sec:
                skipped += block_duration_sec
                continue
            start_index = int(remaining_skip * samplerate)
            block = block[start_index:]
            block_duration_sec = block.shape[0] / samplerate
            skipped = skip_seconds
            if block_duration_sec <= 0:
                continue
        if max_seconds is not None:
            remaining = max_seconds - delivered
            if remaining <= 0:
                break
            allowed_samples = int(remaining * samplerate)
            if block.shape[0] > allowed_samples:
                block = block[:allowed_samples]
                block_duration_sec = block.shape[0] / samplerate
        audio_queue.put(block)
        delivered += block_duration_sec
        if max_seconds is not None and delivered >= max_seconds:
            break
    typer.echo("Finished streaming file audio.")
    stop_event.set()


def validate_azure_config(key: Optional[str], region: Optional[str], endpoint: Optional[str]) -> None:
    if not region and not endpoint:
        raise typer.BadParameter(
            "Azure backend requires either AZURE_SPEECH_REGION or AZURE_SPEECH_ENDPOINT (env or CLI)."
        )


@app.command()
def main(
    backend: str = typer.Option(BACKEND_LOCAL, "--backend", help="local (Whisper) or azure"),
    model_size: str = typer.Option("base", "--model-size", help="Whisper model size for local backend."),
    compute: str = typer.Option("auto", "--compute", help="Whisper compute type: auto, cpu, metal, cuda."),
    beam_size: int = typer.Option(1, "--beam-size", min=1, max=5, help="Beam size for decoding."),
    language: Optional[str] = typer.Option(None, "--language", help="Language hint like en, en-US."),
    input_device: Optional[str] = typer.Option(
        os.environ.get("RTT_INPUT_DEVICE"), "--input-device", help="Device index or name substring."
    ),
    list_devices_flag: bool = typer.Option(False, "--list-devices", help="Only list devices and exit."),
    use_loopback: bool = typer.Option(
        os.environ.get("RTT_USE_LOOPBACK", "") == "1",
        "--loopback/--no-loopback",
        help="WASAPI loopback (Windows): capture the OUTPUT of --input-device instead of its input. Lets you record system audio with zero virtual-cable setup.",
    ),
    include_mic: bool = typer.Option(
        os.environ.get("RTT_INCLUDE_MIC", "") == "1",
        "--include-mic/--no-include-mic",
        help="Mix the microphone into the captured audio so your own voice appears in the transcript. Honors RTT_INCLUDE_MIC=1.",
    ),
    mic_device: Optional[str] = typer.Option(
        os.environ.get("RTT_MIC_DEVICE"),
        "--mic-device",
        help="Mic device name/index for --include-mic. Defaults to the system default input.",
    ),
    mic_gain: float = typer.Option(
        float(os.environ.get("RTT_MIC_GAIN", "1.0")),
        "--mic-gain",
        help="Gain applied to the mic before mixing (0.0–2.0 sensible range).",
    ),
    sample_rate: int = typer.Option(16000, "--sample-rate", help="Capture sample rate (Hz)."),
    block_duration: float = typer.Option(0.5, "--block-duration", help="Capture block size in seconds."),
    window_seconds: float = typer.Option(2.5, "--window", help="Whisper window size in seconds."),
    input_file: Optional[Path] = typer.Option(None, "--input-file", exists=True, help="Stream audio from a WAV/FLAC/MP3 file."),
    skip_seconds: float = typer.Option(0.0, "--skip-seconds", min=0.0, help="Skip this many seconds when reading --input-file."),
    max_seconds: Optional[float] = typer.Option(None, "--max-seconds", min=0.1, help="Limit file playback to this many seconds."),
    output_file: Optional[Path] = typer.Option(None, "--output-file", help="Append transcripts to this file."),
    azure_key: Optional[str] = typer.Option(None, "--azure-key", help="Override AZURE_SPEECH_KEY."),
    azure_region: Optional[str] = typer.Option(None, "--azure-region", help="Override AZURE_SPEECH_REGION."),
    azure_endpoint: Optional[str] = typer.Option(None, "--azure-endpoint", help="Override AZURE_SPEECH_ENDPOINT."),
    azure_resource_id: Optional[str] = typer.Option(None, "--azure-resource-id", help="Override AZURE_SPEECH_RESOURCE_ID (required for Azure AD auth)."),
    azure_speaker_labels: bool = typer.Option(
        False,
        "--azure-speaker-labels/--no-azure-speaker-labels",
        help="Enable Azure Conversation Transcriber speaker diarization (Azure backend only).",
    ),
    azure_languages: Optional[str] = typer.Option(
        None,
        "--azure-languages",
        help="Comma-separated language codes for auto-detection, e.g. 'en-US,he-IL'. Overrides --language for Azure backend.",
    ),
    chunk_seconds: float = typer.Option(
        10.0,
        "--chunk-seconds",
        min=3.0,
        max=60.0,
        help="Chunk window (seconds) for openai/llmspeech backends. Larger = more accurate but higher latency.",
    ),
    openai_endpoint: Optional[str] = typer.Option(
        None,
        "--openai-endpoint",
        help="Azure OpenAI resource endpoint for the 'openai' backend (or set RTT_OPENAI_ENDPOINT).",
    ),
    openai_deployment: str = typer.Option(
        "gpt-4o-transcribe-diarize",
        "--openai-deployment",
        help="Deployment name for the 'openai' backend.",
    ),
    llmspeech_endpoint: Optional[str] = typer.Option(
        None,
        "--llmspeech-endpoint",
        help="Azure Speech resource endpoint for the 'llmspeech' backend (or set RTT_LLMSPEECH_ENDPOINT / AZURE_SPEECH_ENDPOINT).",
    ),
    llmspeech_locales: Optional[str] = typer.Option(
        None,
        "--llmspeech-locales",
        help="Comma-separated locales to bias the 'llmspeech' backend, e.g. 'en-US,he-IL'. Omit for multilingual auto-detect.",
    ),
) -> None:
    if list_devices_flag:
        list_devices()
        raise typer.Exit()

    if input_file and input_device:
        raise typer.BadParameter("Use either --input-file or --input-device, not both.")

    transcript_path = output_file or default_transcript_path()
    if output_file is None:
        typer.echo(f"Autosaving transcript to {transcript_path} (use --output-file to override).")
    transcript_buffer = TranscriptBuffer(transcript_path)
    emit = output_writer(transcript_buffer)
    audio_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=20)
    stop_event = threading.Event()
    paused_event = threading.Event()
    hotkey_listener = ClipboardHotkeyListener(transcript_buffer, stop_event, paused_event)
    hotkey_listener.start()

    if backend not in {BACKEND_LOCAL, BACKEND_AZURE, BACKEND_OPENAI, BACKEND_LLMSPEECH}:
        raise typer.BadParameter("--backend must be 'local', 'azure', 'openai', or 'llmspeech'.")
    if azure_speaker_labels and backend != BACKEND_AZURE:
        raise typer.BadParameter("--azure-speaker-labels is only valid with --backend azure.")

    capture_thread: Optional[threading.Thread] = None
    stream: Optional[sd.InputStream] = None

    def handle_interrupt(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)

    try:
        if input_file:
            capture_thread = threading.Thread(
                target=read_file_into_queue,
                args=(
                    input_file,
                    sample_rate,
                    block_duration,
                    audio_queue,
                    stop_event,
                    paused_event,
                    skip_seconds,
                    max_seconds,
                ),
                daemon=True,
            )
            capture_thread.start()
        else:
            device_index = resolve_device(input_device)
            if include_mic:
                mic_index = resolve_device(mic_device) if mic_device else None
                primary_name = sd.query_devices(device_index)["name"] if device_index is not None else "system default"
                mic_name = sd.query_devices(mic_index)["name"] if mic_index is not None else "system default"
                typer.echo(f"🎙️  Mixing: capture=[{primary_name}] + mic=[{mic_name}] (gain={mic_gain})")
                mixer = MixedDeviceCapture(
                    audio_queue, stop_event, paused_event, sample_rate, block_duration,
                    primary_device=device_index, mic_device=mic_index,
                    use_loopback=use_loopback, mic_gain=mic_gain,
                )
                mixer.start()
                stream = mixer  # has .stop() interface used during cleanup
            else:
                stream = audio_chunks_from_device(
                    audio_queue, stop_event, paused_event, sample_rate, block_duration, device_index, use_loopback=use_loopback
                )

        if backend == BACKEND_LOCAL:
            typer.echo(f"Starting local Whisper backend ({model_size})")
            local_backend = LocalWhisperBackend(model_size, compute, language, beam_size)
            worker = threading.Thread(
                target=process_local_backend,
                args=(local_backend, audio_queue, stop_event, paused_event, emit, sample_rate, window_seconds),
                daemon=True,
            )
        elif backend == BACKEND_AZURE:
            key = azure_key or os.environ.get("AZURE_SPEECH_KEY")
            region = azure_region or os.environ.get("AZURE_SPEECH_REGION")
            endpoint = azure_endpoint or os.environ.get("AZURE_SPEECH_ENDPOINT")
            validate_azure_config(key, region, endpoint)

            auth_token = None
            token_provider = None
            if not key:
                if DefaultAzureCredential is None:
                    raise typer.BadParameter(
                        "No AZURE_SPEECH_KEY set and azure-identity is not installed. "
                        "Install it with: pip install azure-identity"
                    )
                resource_id = azure_resource_id or os.environ.get("AZURE_SPEECH_RESOURCE_ID")
                if not resource_id:
                    raise typer.BadParameter(
                        "Azure AD auth requires AZURE_SPEECH_RESOURCE_ID (the full ARM resource ID). "
                        "Set it in .env or pass --azure-resource-id."
                    )
                typer.echo("No API key found — authenticating with Azure AD (DefaultAzureCredential)...")
                try:
                    credential = DefaultAzureCredential()

                    # Reusable closure so the backend can mint a FRESH token on
                    # demand (proactive refresh + post-401 recovery). AAD tokens
                    # expire ~60 min; without this the SDK's silent WebSocket
                    # reconnect presents a stale token and the session 401s.
                    def token_provider(_rid=resource_id, _cred=credential):
                        tok = _cred.get_token("https://cognitiveservices.azure.com/.default")
                        return f"aad#{_rid}#{tok.token}"

                    auth_token = token_provider()
                except Exception as exc:
                    typer.echo(f"Azure AD authentication failed: {exc}", err=True)
                    raise typer.Exit(code=1)
                typer.echo("Azure AD token acquired successfully.")

            typer.echo("Starting Azure Speech backend")
            auto_detect_langs: Optional[List[str]] = None
            if azure_languages:
                auto_detect_langs = [l.strip() for l in azure_languages.split(",") if l.strip()]
            try:
                azure_backend = AzureSpeechBackend(
                    region,
                    endpoint,
                    language or "en-US",
                    sample_rate,
                    azure_speaker_labels,
                    key=key,
                    auth_token=auth_token,
                    auto_detect_languages=auto_detect_langs,
                    token_provider=token_provider if not key else None,
                )
            except Exception as exc:
                typer.echo(f"Failed to initialize Azure Speech backend: {exc}", err=True)
                raise typer.Exit(code=1)
            worker = threading.Thread(
                target=process_azure_backend,
                args=(azure_backend, audio_queue, stop_event, paused_event, emit),
                daemon=True,
            )
        else:
            # Chunked HTTP backends (openai / llmspeech). Both micro-batch audio
            # into --chunk-seconds windows and POST each chunk. Auth reuses the
            # same AAD token_provider discipline as the Speech SDK backend: a
            # callable that mints a FRESH bearer token on demand (~60 min TTL).
            cog_key = azure_key or os.environ.get("AZURE_SPEECH_KEY")
            cog_token_provider: Optional[
                Callable[[], Union[str, Tuple[str, float]]]
            ] = None
            if not cog_key:
                if DefaultAzureCredential is None:
                    raise typer.BadParameter(
                        "No API key set and azure-identity is not installed. "
                        "Install it with: pip install azure-identity"
                    )
                typer.echo("No API key found — authenticating with Azure AD (DefaultAzureCredential)...")
                try:
                    _cred = DefaultAzureCredential()

                    # Return (token, expires_on) so the backend refreshes on the
                    # token's REAL expiry, not a blind wall-clock TTL. Critical
                    # because DefaultAzureCredential often falls through to the
                    # shared `az` CLI token, which can be handed over already
                    # aged (<30 min life) — a fixed 30-min TTL would then let it
                    # die mid-meeting before the scheduled refresh ever fires.
                    def _mint_cog_token(_c=_cred):
                        tok = _c.get_token("https://cognitiveservices.azure.com/.default")
                        return (tok.token, float(tok.expires_on))

                    _mint_cog_token()  # fail fast if credentials are unusable
                    cog_token_provider = _mint_cog_token
                except Exception as exc:
                    typer.echo(f"Azure AD authentication failed: {exc}", err=True)
                    raise typer.Exit(code=1)
                typer.echo("Azure AD token acquired successfully.")

            if backend == BACKEND_OPENAI:
                oai_endpoint = openai_endpoint or os.environ.get("RTT_OPENAI_ENDPOINT")
                if not oai_endpoint:
                    raise typer.BadParameter(
                        "The 'openai' backend requires --openai-endpoint or RTT_OPENAI_ENDPOINT "
                        "(the Azure OpenAI resource endpoint)."
                    )
                typer.echo(
                    f"Starting Azure OpenAI transcribe backend "
                    f"(deployment={openai_deployment}, chunk={chunk_seconds:.0f}s)"
                )
                try:
                    chunked_backend = OpenAITranscribeBackend(
                        endpoint=oai_endpoint,
                        deployment=openai_deployment,
                        sample_rate=sample_rate,
                        chunk_seconds=chunk_seconds,
                        language=language,
                        token_provider=cog_token_provider,
                        api_key=cog_key,
                    )
                except Exception as exc:
                    typer.echo(f"Failed to initialize Azure OpenAI backend: {exc}", err=True)
                    raise typer.Exit(code=1)
            else:  # BACKEND_LLMSPEECH
                lls_endpoint = (
                    llmspeech_endpoint
                    or os.environ.get("RTT_LLMSPEECH_ENDPOINT")
                    or os.environ.get("AZURE_SPEECH_ENDPOINT")
                )
                if not lls_endpoint:
                    raise typer.BadParameter(
                        "The 'llmspeech' backend requires --llmspeech-endpoint, "
                        "RTT_LLMSPEECH_ENDPOINT, or AZURE_SPEECH_ENDPOINT "
                        "(the Azure Speech resource endpoint)."
                    )
                locales_list: Optional[List[str]] = None
                if llmspeech_locales:
                    locales_list = [l.strip() for l in llmspeech_locales.split(",") if l.strip()]
                typer.echo(
                    f"Starting Azure LLM Speech backend "
                    f"(chunk={chunk_seconds:.0f}s, locales={locales_list or 'auto'})"
                )
                try:
                    chunked_backend = LLMSpeechBackend(
                        endpoint=lls_endpoint,
                        sample_rate=sample_rate,
                        chunk_seconds=chunk_seconds,
                        locales=locales_list,
                        token_provider=cog_token_provider,
                        api_key=cog_key,
                    )
                except Exception as exc:
                    typer.echo(f"Failed to initialize Azure LLM Speech backend: {exc}", err=True)
                    raise typer.Exit(code=1)

            worker = threading.Thread(
                target=process_chunked_backend,
                args=(chunked_backend, audio_queue, stop_event, paused_event, emit),
                daemon=True,
            )

        worker.start()
        typer.echo("Press Ctrl+C to stop.")
        while worker.is_alive():
            worker.join(timeout=0.5)
    finally:
        stop_event.set()
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if capture_thread and capture_thread.is_alive():
            capture_thread.join(timeout=1)
        hotkey_listener.join(timeout=1)


if __name__ == "__main__":
    app()
