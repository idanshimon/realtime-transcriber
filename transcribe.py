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
from typing import Iterable, List, Optional, TYPE_CHECKING

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

app = typer.Typer(add_completion=False, help="Stream live audio into local or Azure speech recognizers.")

BACKEND_LOCAL = "local"
BACKEND_AZURE = "azure"


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
        if file_path:
            file_path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            if self._file_path:
                with self._file_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")

    def snapshot(self) -> str:
        with self._lock:
            return "\n".join(self._lines)

    def _delta_snapshot_locked(self) -> str:
        baseline = max(0, min(self._delta_baseline, len(self._lines)))
        if baseline >= len(self._lines):
            return ""
        return "\n".join(self._lines[baseline:])

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
) -> sd.InputStream:
    blocksize = max(256, int(samplerate * block_duration))

    def callback(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        if status:
            typer.echo(f"PortAudio status: {status}", err=True)
        if paused_event.is_set():
            return
        capture_queue.put(indata.copy())

    stream = sd.InputStream(
        samplerate=samplerate,
        channels=1,
        dtype="float32",
        device=device_index,
        blocksize=blocksize,
        callback=callback,
    )
    stream.start()
    return stream


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
        key: str,
        region: Optional[str],
        endpoint: Optional[str],
        language: str,
        sample_rate: int,
        enable_speaker_labels: bool,
    ) -> None:
        if speechsdk is None:
            raise RuntimeError("azure-cognitiveservices-speech is not installed.")

        if endpoint:
            speech_config = speechsdk.SpeechConfig(subscription=key, endpoint=endpoint)
        else:
            if not region:
                raise ValueError("Azure Speech backend requires either region or endpoint.")
            speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        speech_config.speech_recognition_language = language
        if enable_speaker_labels:
            diarize_property = getattr(
                speechsdk.PropertyId,
                "SpeechServiceResponse_DiarizeIntermediateResults",
                None,
            )
            if diarize_property is not None:
                speech_config.set_property(property_id=diarize_property, value="true")
        stream_format = speechsdk.audio.AudioStreamFormat(samples_per_second=sample_rate, bits_per_sample=16, channels=1)
        self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)
        self._use_conversation = enable_speaker_labels
        self._conversation_transcriber = None
        self._speech_recognizer = None
        if enable_speaker_labels:
            self._conversation_transcriber = speechsdk.transcription.ConversationTranscriber(
                speech_config=speech_config,
                audio_config=audio_config,
            )
            self._conversation_transcriber.transcribed.connect(self._on_transcribed)
            self._conversation_transcriber.canceled.connect(self._on_canceled)
            self._conversation_transcriber.session_stopped.connect(self._on_session_stopped)
            self._conversation_transcriber.session_started.connect(self._on_session_started)
        else:
            self._speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
            self._speech_recognizer.recognized.connect(self._on_recognized)
            self._speech_recognizer.canceled.connect(self._on_canceled)
            self._speech_recognizer.session_stopped.connect(self._on_session_stopped)
            self._speech_recognizer.session_started.connect(self._on_session_started)
        self._text_queue: "queue.Queue[str]" = queue.Queue()
        self._stop_event = threading.Event()

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

        typer.echo(f"Azure canceled (reason={reason}, code={error_code}): {error_details}", err=True)

    def _on_session_stopped(self, evt) -> None:
        session_id = getattr(evt, "session_id", None) or getattr(evt, "sessionId", None)
        if session_id:
            typer.echo(f"Azure session stopped (id={session_id})", err=True)
        else:
            typer.echo("Azure session stopped", err=True)

    def start(self) -> None:
        if self._use_conversation:
            assert self._conversation_transcriber is not None
            self._conversation_transcriber.start_transcribing_async().get()
        else:
            assert self._speech_recognizer is not None
            self._speech_recognizer.start_continuous_recognition_async().get()

    def stop(self) -> None:
        self._stop_event.set()
        if self._use_conversation:
            assert self._conversation_transcriber is not None
            self._conversation_transcriber.stop_transcribing_async().get()
        else:
            assert self._speech_recognizer is not None
            self._speech_recognizer.stop_continuous_recognition_async().get()
        self.push_stream.close()

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
    except Exception as exc:
        typer.echo(f"Azure backend worker failed: {exc}", err=True)
        stop_event.set()
    finally:
        backend.stop()


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
    if not key or (not region and not endpoint):
        raise typer.BadParameter(
            "Azure backend requires AZURE_SPEECH_KEY and either AZURE_SPEECH_REGION or AZURE_SPEECH_ENDPOINT (env or CLI)."
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
    azure_speaker_labels: bool = typer.Option(
        False,
        "--azure-speaker-labels/--no-azure-speaker-labels",
        help="Enable Azure Conversation Transcriber speaker diarization (Azure backend only).",
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

    if backend not in {BACKEND_LOCAL, BACKEND_AZURE}:
        raise typer.BadParameter("--backend must be 'local' or 'azure'.")
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
            stream = audio_chunks_from_device(
                audio_queue, stop_event, paused_event, sample_rate, block_duration, device_index
            )

        if backend == BACKEND_LOCAL:
            typer.echo(f"Starting local Whisper backend ({model_size})")
            local_backend = LocalWhisperBackend(model_size, compute, language, beam_size)
            worker = threading.Thread(
                target=process_local_backend,
                args=(local_backend, audio_queue, stop_event, paused_event, emit, sample_rate, window_seconds),
                daemon=True,
            )
        else:
            key = azure_key or os.environ.get("AZURE_SPEECH_KEY")
            region = azure_region or os.environ.get("AZURE_SPEECH_REGION")
            endpoint = azure_endpoint or os.environ.get("AZURE_SPEECH_ENDPOINT")
            validate_azure_config(key, region, endpoint)
            assert key is not None
            typer.echo("Starting Azure Speech backend")
            try:
                azure_backend = AzureSpeechBackend(
                    key,
                    region,
                    endpoint,
                    language or "en-US",
                    sample_rate,
                    azure_speaker_labels,
                )
            except Exception as exc:
                typer.echo(f"Failed to initialize Azure Speech backend: {exc}", err=True)
                raise typer.Exit(code=1)
            worker = threading.Thread(
                target=process_azure_backend,
                args=(azure_backend, audio_queue, stop_event, paused_event, emit),
                daemon=True,
            )

        worker.start()
        typer.echo("Press Ctrl+C to stop.")
        while worker.is_alive():
            worker.join(timeout=0.5)
    finally:
        stop_event.set()
        if stream is not None:
            stream.stop()
            stream.close()
        if capture_thread and capture_thread.is_alive():
            capture_thread.join(timeout=1)
        hotkey_listener.join(timeout=1)


if __name__ == "__main__":
    app()
