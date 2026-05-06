# Real-Time Teams Transcriber (MVP)

Minimal cross-platform Python tool that captures system audio (and optionally your mic) and transcribes in near real time. Defaults to a fully local Whisper backend; optionally streams to Azure Speech for cloud transcription with speaker diarization.

## Quick start (zero-friction)

**macOS / Linux:**
```bash
git clone https://github.com/idanshimon/realtime-transcriber.git
cd realtime-transcriber
./install.sh
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/idanshimon/realtime-transcriber.git
cd realtime-transcriber
.\install\windows.ps1
```

The installer:
- Detects your OS and Python version (offers to install if missing)
- **macOS:** Installs BlackHole, programmatically creates an `RTT Multi-Output` aggregate device, sets it as default output, configures the mix of system audio + mic
- **Linux:** Detects your PulseAudio/PipeWire monitor source
- **Windows:** Uses built-in WASAPI loopback (no drivers, no virtual cables) — *beta, not yet validated*
- Creates a `.venv`, installs all dependencies
- Runs an audio smoke test (plays a tone, verifies capture)

## Features
- Enumerate audio devices and pick one via CLI flag or env var
- Local transcription via `faster-whisper` (configurable model size)
- Azure Speech backend with speaker diarization (`--azure-speaker-labels`)
- **`--include-mic`** — mix your microphone into the captured audio so your own voice appears in the transcript (default ON after `install.sh` on macOS)
- File playback mode for WAV/FLAC/MP3 (PyAV decoding) — transcribe recordings, no live call needed
- Auto-saves to `transcripts/transcript-<timestamp>.txt`
- TTY hotkeys:
	- `Ctrl+S` — copy full transcript to clipboard, reset delta baseline
	- `Ctrl+E` — copy delta since last `Ctrl+S`
	- `Ctrl+P` — pause/resume

## Manual setup (if you skip `install.sh`)
1. **Audio routing** — install a virtual loopback device:
   - macOS: [BlackHole](https://existential.audio/blackhole/) + Multi-Output Device
   - Windows: nothing needed — use `--loopback`
   - Linux: PulseAudio/PipeWire monitor source (built in)
2. **Python 3.10+** with `venv`
3. (Optional) Azure Speech resource key + region for the cloud backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env   # then edit
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

### Azure AD authentication (no API key)

If you don't have (or prefer not to use) a subscription key, the tool falls back to Azure AD via `DefaultAzureCredential`. You must also set `AZURE_SPEECH_RESOURCE_ID` to the full ARM resource ID of your Speech resource:

```bash
# 1. Log in to Azure
az login

# 2. Find your Speech resource ID
az cognitiveservices account show --name <name> --resource-group <rg> --query id -o tsv

# 3. Set in .env (no AZURE_SPEECH_KEY)
AZURE_SPEECH_REGION=eastus
AZURE_SPEECH_RESOURCE_ID=/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name>

# 4. Run
./run-azure.sh
```

Your Azure AD identity needs the **Cognitive Services Speech User** (or broader) role on the Speech resource.

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
- **401 Authentication error (Azure)**: Check the following:
  1. **Stale `AZURE_SPEECH_KEY` in your shell**: If the key is commented out in `.env` but was previously exported in your terminal, it will still be used and may be invalid. Run `unset AZURE_SPEECH_KEY` and retry.
  2. **Azure AD token format**: When using Azure AD auth (no API key), `AZURE_SPEECH_RESOURCE_ID` must be set. The Speech SDK requires the token in `aad#<resource-id>#<token>` format; the tool handles this automatically when the resource ID is configured.
  3. **Missing RBAC role**: Your Azure AD identity needs **Cognitive Services Speech User** (or broader) on the Speech resource.
  4. **`disableLocalAuth=true`**: If your resource disables key auth, use Azure AD auth instead, or add a `SecurityControl: Ignore` tag to bypass the policy.
- If you see `PortAudioError`, confirm macOS microphone permissions for the terminal and that the selected device supports the chosen sample rate (default 16 kHz).
- Azure mode requires the Speech SDK. If you only need local mode, you can omit the Azure package from `requirements.txt` and reinstall.
- Whisper models beyond `small` benefit from Apple Silicon acceleration; set `--compute cpu`, `--compute metal`, or `--compute auto` depending on your setup.
- **urllib3 OpenSSL warning**: This is a known compatibility issue with LibreSSL on macOS and doesn't affect functionality.
