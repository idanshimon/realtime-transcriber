#!/usr/bin/env python3
"""
RTT smoke test — plays a tone and verifies the capture device receives audio.
Used by install.sh to confirm the audio routing is working before declaring success.

Usage:  python3 smoke_test.py --device "BlackHole"
Exit:   0 = audio detected, 1 = silent (routing broken), 2 = setup error
"""
from __future__ import annotations
import argparse
import sys
import time
import threading
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("ERROR: sounddevice not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(2)


def find_input_device(name_substring: str) -> int | None:
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0 and name_substring.lower() in dev["name"].lower():
            return idx
    return None


def play_tone(duration_s: float = 2.0, freq: float = 880.0, sample_rate: int = 44100):
    """Play a tone via the default output device (which on a working setup goes through Multi-Output → BlackHole → us)."""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    tone = 0.3 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    sd.play(tone, samplerate=sample_rate, blocking=False)


def capture_rms(device_idx: int, duration_s: float = 2.5) -> float:
    """Capture from the given input device and return peak RMS energy."""
    sample_rate = 16000
    chunks: list[np.ndarray] = []
    done = threading.Event()

    def cb(indata, frames, time_info, status):
        chunks.append(indata.copy())

    with sd.InputStream(device=device_idx, channels=1, samplerate=sample_rate, callback=cb):
        time.sleep(duration_s)

    if not chunks:
        return 0.0
    audio = np.concatenate(chunks, axis=0).flatten()
    return float(np.sqrt(np.mean(audio**2)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True, help="Substring of input device name to capture from")
    ap.add_argument("--threshold", type=float, default=0.001, help="RMS threshold to declare success")
    args = ap.parse_args()

    idx = find_input_device(args.device)
    if idx is None:
        print(f"ERROR: No input device matches '{args.device}'.", file=sys.stderr)
        print("Available input devices:", file=sys.stderr)
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"  [{i}] {d['name']}", file=sys.stderr)
        sys.exit(2)

    dev = sd.query_devices(idx)
    print(f"→ Capturing from: [{idx}] {dev['name']}")
    print(f"→ Playing 880Hz tone for 2 seconds…")

    # Start capture slightly before tone so we don't miss it
    capture_thread_result: dict = {}

    def capture_worker():
        capture_thread_result["rms"] = capture_rms(idx, duration_s=2.5)

    t = threading.Thread(target=capture_worker)
    t.start()
    time.sleep(0.3)
    play_tone(duration_s=2.0)
    t.join()

    rms = capture_thread_result.get("rms", 0.0)
    print(f"→ Captured RMS: {rms:.4f} (threshold: {args.threshold})")

    if rms >= args.threshold:
        print("✅ Audio capture is working!")
        sys.exit(0)
    else:
        print("✖  Silent capture — audio is not reaching the chosen device.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Likely causes:", file=sys.stderr)
        print("  • macOS default output is not set to the Multi-Output Device", file=sys.stderr)
        print("  • The Multi-Output Device doesn't include BlackHole as a member", file=sys.stderr)
        print("  • System volume is muted", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
