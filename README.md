# Real-Time Teams Transcriber (MVP)

Minimal Python tool that captures audio from a macOS input device (loopback or mic) and transcribes in near real time. It defaults to a fully local Whisper backend and can optionally stream to Azure Cognitive Services Speech for cloud transcription.

## Features
- Enumerate CoreAudio devices and pick one via CLI flag or environment variable.
- Local transcription powered by `faster-whisper` with configurable model size.
- Optional Azure backend using `azure-cognitiveservices-speech`; toggle via CLI flag/env, with speaker diarization support in Azure mode.
- File playback mode accepts WAV/FLAC/MP3 inputs (PyAV decoding) so you can test without a live call.
- Streams text to stdout, keeps an in-memory transcript buffer, and automatically writes to `transcripts/transcript-<timestamp>.txt`.
- While running in a TTY:
	- `Ctrl+S` copies the full transcript so far (from the beginning) to the clipboard, and sets the baseline for deltas.
	- `Ctrl+E` copies only the transcript since the last `Ctrl+S` (does not change the baseline).
	- `Ctrl+P` pauses/resumes transcribing.

## Prerequisites
1. **Audio routing**: To capture Teams audio, install a virtual loopback device such as [BlackHole](https://existential.audio/blackhole/) and select it as both the Teams output and this tool's input. For mic transcription, just pick the built-in input device.
2. **Python 3.10+** with `venv` available on macOS.
3. Optional Azure Speech resource (key + region) if you want the cloud backend.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Configure Azure credentials (if using Azure backend)
cp .env.example .env
# Edit .env with your Azure Speech Service key and region
```

## Usage
List devices:
```bash
source .venv/bin/activate
python transcribe.py --list-devices
```

Quick start (recommended):
```bash
./run.sh --list-devices
```

Local transcription from the default device:
```bash
source .venv/bin/activate
python transcribe.py --backend local --model-size base
```

Specify a device (index or name substring) and save output:
```bash
source .venv/bin/activate
python transcribe.py --input-device "BlackHole" --output-file transcripts.txt
```
That command continuously appends to `transcripts.txt` as lines arrive (overriding the default timestamped path under `transcripts/`).

Hotkeys summary:
- `Ctrl+S` copies full transcript so far.
- `Ctrl+E` copies transcript since last `Ctrl+S` (repeatable).
- `Ctrl+P` pauses/resumes transcribing.

Preview only part of a long recording (skip the first 30 seconds and process 60 seconds total):
```bash
source .venv/bin/activate
python transcribe.py --input-file test.mp3 --skip-seconds 30 --max-seconds 60
```

Azure backend (requires env vars `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`):
```bash
source .venv/bin/activate
# Fill values in .env or export here
export AZURE_SPEECH_KEY="<your-key>"
export AZURE_SPEECH_REGION="<your-region>"
python transcribe.py --backend azure
```

If you keep your Azure vars in `.env`, you can start the common Azure mode with:
```bash
./run-azure.sh
```

Add Azure speaker labels (Conversation Transcriber) to prefix lines with `Speaker <id>`:
```bash
python transcribe.py --backend azure --azure-speaker-labels
```
Speaker diarization currently requires the Azure backend; local Whisper mode continues to emit unlabeled lines.

## Testing with an audio file
You can skip live capture and feed a WAV/FLAC/MP3 file using `--input-file path`. Audio is resampled to 16 kHz mono on the fly with PyAV, which keeps the pipeline identical to live capture. Combine `--skip-seconds` and `--max-seconds` to preview a slice of a long meeting recording. Use `ffmpeg` or `sox` to convert other formats if needed.

## Troubleshooting
- **401 Authentication error (Azure)**: If your Azure Speech resource has `disableLocalAuth=true` (common in enterprise environments), you need to either:
  1. Add a `SecurityControl: Ignore` tag to the resource to bypass the policy
  2. Use Azure AD token authentication instead of API keys
  3. Enable local auth via: `az resource update --ids <resource-id> --set properties.disableLocalAuth=false`
- If you see `PortAudioError`, confirm macOS microphone permissions for the terminal and that the selected device supports the chosen sample rate (default 16 kHz).
- Azure mode requires the Speech SDK. If you only need local mode, you can omit the Azure package from `requirements.txt` and reinstall.
- Whisper models beyond `small` benefit from Apple Silicon acceleration; set `--compute cpu`, `--compute metal`, or `--compute auto` depending on your setup.
- **urllib3 OpenSSL warning**: This is a known compatibility issue with LibreSSL on macOS and doesn't affect functionality.
