#!/usr/bin/env python3
"""Assemble the curated facts.json for the speech-model field guide.

Every fact here is lifted from a source file on disk or an OpenAI/Microsoft
doc URL (verified 2026-10-14). Nothing is invented. Statuses are explicit:
  documented  = stated in a vendor doc, not measured here
  measured    = observed in this pilot's saved evidence
  not_tested  = neither measured nor independently confirmed here
  unknown     = value genuinely not recorded / not reverified

Run:  python3 build_facts.py   ->  writes facts.json next to this file
"""
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
VERIFIED = "2026-10-14"

SRC = {
    "pricing":        "https://developers.openai.com/api/docs/pricing",
    "gpt_live_1":     "https://developers.openai.com/models/gpt-live-1",
    "rt_21":          "https://developers.openai.com/models/gpt-realtime-2.1",
    "rt_21_mini":     "https://developers.openai.com/models/gpt-realtime-2.1-mini",
    "rt_transcribe":  "https://developers.openai.com/guides/realtime-transcription",
    "transcription":  "https://developers.openai.com/guides/transcription",
    "mai_azure":      "https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe",
    "mai_model":      "https://microsoft.ai/models/mai-transcribe-2/",
    "azure_diar":     "https://learn.microsoft.com/en-us/azure/ai-services/speech-service/get-started-stt-diarization",
    "cookbook_spkr":  "https://developers.openai.com/cookbook/examples/audio/speaker_aware_meeting_intelligence/speaker_aware_meeting_intelligence",
    "diart":          "https://diart.readthedocs.io/en/stable/",
    "azure_gpt_live": "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/gpt-live",
}

# --- audio token conversion (documented): 10 input tok/s, 20 output tok/s ---
IN_TOK_PER_S, OUT_TOK_PER_S = 10, 20

def audio_cost_per_min(in_rate_per_M, out_rate_per_M, which="input"):
    """Derive $/min of audio from per-token rates + documented conversion."""
    if which == "input":
        return round(IN_TOK_PER_S * 60 / 1_000_000 * in_rate_per_M, 4)
    return round(OUT_TOK_PER_S * 60 / 1_000_000 * out_rate_per_M, 4)

# ---------------------------------------------------------------------------
# PRICING families — raw public list rates (USD, per 1M tokens) 2026-10-14.
# NOT reconciled against any Azure bill. Marked status=documented.
# ---------------------------------------------------------------------------
pricing = {
    "realtime_full": {  # 2.1, 2, 1.5, base
        "audio_in_per_M": 32, "audio_out_per_M": 64,
        "text_in_per_M": 4,  "text_out_per_M": 24,
        "cache_audio_per_M": 0.4,
        "note": "2.1 text out $24/M; 1.5/base text out $16/M (see per-model).",
    },
    "realtime_mini": {
        "audio_in_per_M": 10, "audio_out_per_M": 20,
        "text_in_per_M": 0.6, "text_out_per_M": 2.4,
        "cache_audio_per_M": 0.3,
    },
}

def pricing_block(fam, text_out_override=None):
    p = pricing[fam]
    tout = text_out_override if text_out_override is not None else p["text_out_per_M"]
    return {
        "audio_in_per_M_usd": p["audio_in_per_M"],
        "audio_out_per_M_usd": p["audio_out_per_M"],
        "text_in_per_M_usd": p["text_in_per_M"],
        "text_out_per_M_usd": tout,
        "cache_audio_per_M_usd": p["cache_audio_per_M"],
        "audio_in_per_min_usd": audio_cost_per_min(p["audio_in_per_M"], p["audio_out_per_M"], "input"),
        "audio_out_per_min_usd": audio_cost_per_min(p["audio_in_per_M"], p["audio_out_per_M"], "output"),
        "source": SRC["pricing"], "status": "documented",
    }

# per-minute transcription list rates (documented, $/min)
TRANSCRIBE_RATES = {
    "gpt-live-transcribe":       (0.017, "no speaker labels for live-transcribe"),
    "gpt-realtime-whisper":      (0.017, None),
    "gpt-transcribe":            (0.0045, "file / committed-turn"),
    "gpt-4o-transcribe-diarize": (0.006,  "diarized file transcription"),
    "gpt-4o-mini-transcribe":    (0.003,  None),
}

def transcribe_cost(model):
    rate, note = TRANSCRIBE_RATES[model]
    return {
        "per_min_usd": rate,
        "per_hour_usd": round(rate * 60, 2),
        "note": note,
        "source": SRC["transcription"], "status": "documented",
    }

# ---------------------------------------------------------------------------
# MODEL CARDS — 22 total (18 GPT from catalog + 4 named Azure/MAI)
# ---------------------------------------------------------------------------
cards = []

def card(**kw):
    cards.append(kw)

# ---- Voice agent -----------------------------------------------------------
card(
    id="gpt-live-1", name="gpt-live-1", versions=["2026-09-10"],
    category="voice-agent", family="GPT-Live",
    headline="Full-duplex voice agent frontend — listens while it speaks.",
    what=("A speech-to-speech voice agent that can listen while speaking "
          "(full duplex). It delegates reasoning and tool calls to a separate "
          "backend model, which is billed separately."),
    capabilities=["speech-in", "speech-out", "full-duplex", "delegates reasoning/tools to backend"],
    tested="functional",
    tested_detail=("Caldova's voice path explicitly uses gpt-live-1. That functional "
                   "voice test is SEPARATE from the six-model transcription benchmark; "
                   "gpt-live-1 was not one of the six transcription models."),
    diarization="not a transcription model; no meeting-diarization claim",
    cost={"connected_per_min_usd": 0.05, "connected_per_hour_usd": 3.0,
          "note": "Plus separate backend reasoning + tool charges — not included.",
          "source": SRC["pricing"], "status": "documented"},
    sources=[SRC["gpt_live_1"], SRC["azure_gpt_live"]],
)

# ---- Realtime conversational (six benchmarked) -----------------------------
RT = {
    "gpt-realtime":        ("2025-08-28",  "base",      "realtime_full", 16),
    "gpt-realtime-1.5":    ("2026-02-23",  "1.5",       "realtime_full", 16),
    "gpt-realtime-2":      ("2026-05-06",  "2",         "realtime_full", 24),
    "gpt-realtime-2.1":    ("2026-07-07",  "2.1",       "realtime_full", 24),
    "gpt-realtime-2.1-mini":("2026-07-07", "2.1 mini",  "realtime_mini", None),
    "gpt-realtime-mini":   (["2025-10-06","2025-12-15"], "mini", "realtime_mini", None),
}
RT_CAPS = ["streaming voice", "input text", "image input"]
for model, (ver, short, fam, tout) in RT.items():
    versions = ver if isinstance(ver, list) else [ver]
    reasoning = model in ("gpt-realtime-2.1", "gpt-realtime-2.1-mini")
    card(
        id=model, name=model, versions=versions,
        category="realtime-conversational", family="GPT-Realtime",
        headline=f"Conversational realtime model ({short}), prompted to transcribe.",
        what=("A streaming conversational voice model (voice / input-text / image). "
              "In this pilot it was PROMPTED to emit a transcript with textual "
              "Speaker A/B labels — it is NOT a dedicated transcription model and "
              "has NO native meeting diarization."),
        capabilities=RT_CAPS + (["reasoning", "tool use (documented for newest 2.1/full+mini)"] if reasoning else []),
        tested="measured",
        benchmark_key=model,
        diarization="none native; speaker labels are prompted generation",
        cost=pricing_block(fam, text_out_override=tout),
        sources=[SRC["rt_21"] if "2.1" in model and "mini" not in model else
                 SRC["rt_21_mini"] if model=="gpt-realtime-2.1-mini" else SRC["rt_transcribe"],
                 SRC["rt_transcribe"]],
    )

# ---- Other realtime / transcription / audio / tts --------------------------
card(id="gpt-realtime-translate", name="gpt-realtime-translate", versions=["2026-05-06"],
     category="realtime-conversational", family="GPT-Realtime",
     headline="Realtime speech translation variant.",
     what="Realtime translation variant in the catalog. Not tested in this pilot.",
     capabilities=["streaming", "translation"], tested="not_tested",
     diarization="not tested", cost={"status":"unknown","note":"not reverified"},
     sources=[SRC["rt_transcribe"]])

card(id="gpt-realtime-whisper", name="gpt-realtime-whisper", versions=["2026-05-06"],
     category="transcription", family="GPT-Realtime",
     headline="Realtime Whisper transcription endpoint.",
     what="Realtime Whisper-based transcription. Documented rate only; not tested here.",
     capabilities=["streaming transcription"], tested="not_tested",
     diarization="no speaker labels", cost=transcribe_cost("gpt-realtime-whisper"),
     sources=[SRC["rt_transcribe"]])

card(id="gpt-live-transcribe", name="gpt-live-transcribe", versions=["2026-07-28"],
     category="transcription", family="GPT-Live",
     headline="Live streaming transcription — no speaker labels.",
     what="Streaming transcription endpoint. Documented: no speaker labels.",
     capabilities=["streaming transcription"], tested="not_tested",
     diarization="none (documented: no speakers)", cost=transcribe_cost("gpt-live-transcribe"),
     sources=[SRC["transcription"]])

card(id="gpt-transcribe", name="gpt-transcribe", versions=["2026-07-28"],
     category="transcription", family="GPT-Transcribe",
     headline="File / committed-turn transcription.",
     what="File and committed-turn transcription. Documented rate only; not tested here.",
     capabilities=["file transcription", "committed-turn"], tested="not_tested",
     diarization="not tested", cost=transcribe_cost("gpt-transcribe"),
     sources=[SRC["transcription"]])

card(id="gpt-4o-transcribe", name="gpt-4o-transcribe", versions=["2025-03-20"],
     category="transcription", family="GPT-4o",
     headline="GPT-4o file transcription.",
     what="GPT-4o transcription endpoint. Not tested in this pilot.",
     capabilities=["file transcription"], tested="not_tested",
     diarization="not tested", cost={"status":"unknown","note":"rate not reverified for this card"},
     sources=[SRC["transcription"]])

card(id="gpt-4o-mini-transcribe", name="gpt-4o-mini-transcribe", versions=["2025-03-20","2025-12-15"],
     category="transcription", family="GPT-4o",
     headline="Cheapest GPT-4o-family transcription.",
     what="GPT-4o-mini transcription. Documented rate only; not tested here.",
     capabilities=["file transcription"], tested="not_tested",
     diarization="not tested", cost=transcribe_cost("gpt-4o-mini-transcribe"),
     sources=[SRC["transcription"]])

card(id="gpt-4o-transcribe-diarize", name="gpt-4o-transcribe-diarize", versions=["2025-10-15"],
     category="transcription", family="GPT-4o",
     headline="GPT-4o transcription WITH diarization — the only deployment left standing.",
     what=("GPT-4o transcription with diarization. This is the original deployment on the "
           "RTT resource; all five temporary benchmark deployments were removed and this "
           "one was verified by readback as the sole remaining speech deployment."),
     capabilities=["file transcription", "diarization"], tested="not_tested",
     tested_detail="Present/retained, but not part of the six-model realtime comparison.",
     diarization="documented diarization (not measured in this pilot)",
     cost=transcribe_cost("gpt-4o-transcribe-diarize"),
     sources=[SRC["transcription"]])

card(id="gpt-4o-mini-tts", name="gpt-4o-mini-tts", versions=["2025-03-20","2025-12-15"],
     category="tts", family="GPT-4o",
     headline="Text-to-speech output.",
     what="GPT-4o-mini TTS. Priced $12/M output audio tokens; text in $0.6/M.",
     capabilities=["text-to-speech"], tested="not_tested", diarization="n/a (TTS)",
     cost={"audio_out_per_M_usd":12, "text_in_per_M_usd":0.6,
           "per_min_estimate_usd":0.015,
           "note":"~$0.015/min est. per OpenAI conversion; est. only — omit if unchecked.",
           "source":SRC["pricing"], "status":"documented"},
     sources=[SRC["pricing"]])

card(id="gpt-audio", name="gpt-audio", versions=["2025-08-28"],
     category="audio", family="GPT-Audio",
     headline="Audio-in/out model (base).",
     what="GPT-Audio base. Audio $32/M in, $64/M out. Not tested in this pilot.",
     capabilities=["audio in", "audio out"], tested="not_tested", diarization="not tested",
     cost={"audio_in_per_M_usd":32,"audio_out_per_M_usd":64,"source":SRC["pricing"],"status":"documented"},
     sources=[SRC["pricing"]])

card(id="gpt-audio-1.5", name="gpt-audio-1.5", versions=["2026-02-23"],
     category="audio", family="GPT-Audio",
     headline="Audio-in/out model (1.5).",
     what="GPT-Audio 1.5. Audio $32/M in, $64/M out. Not tested in this pilot.",
     capabilities=["audio in", "audio out"], tested="not_tested", diarization="not tested",
     cost={"audio_in_per_M_usd":32,"audio_out_per_M_usd":64,"source":SRC["pricing"],"status":"documented"},
     sources=[SRC["pricing"]])

card(id="gpt-audio-mini", name="gpt-audio-mini", versions=["2025-10-06","2025-12-15"],
     category="audio", family="GPT-Audio",
     headline="Audio-in/out model (mini).",
     what="GPT-Audio mini. Audio $10/M in, $20/M out. Not tested in this pilot.",
     capabilities=["audio in", "audio out"], tested="not_tested", diarization="not tested",
     cost={"audio_in_per_M_usd":10,"audio_out_per_M_usd":20,"source":SRC["pricing"],"status":"documented"},
     sources=[SRC["pricing"]])

# ---- 4 named Azure / MAI additions -----------------------------------------
card(id="mai-transcribe-2", name="MAI-Transcribe-2", versions=["batch (rich-config tested)"],
     category="azure-speech", family="MAI",
     headline="Microsoft batch transcription — full-file speaker IDs, per-request only.",
     what=("Azure MAI batch transcription. Full 28-min file returned HTTP 200 in 20.6s with "
           "92 phrases / 4486 words and 8 unique speaker IDs (0–7) — NOT verified against 8 "
           "real people. Rich config (word timestamps + clean style + phrase-list hints + "
           "en-US) returned HTTP 200; default is verbatim. maxSpeakers was rejected (400); "
           "confidence 0 returned, not usable. No documented cross-request session ID / "
           "references / reusable embeddings."),
     capabilities=["batch transcription", "diarization (per-file)", "word timestamps", "phrase-list hints"],
     tested="measured", benchmark_key="mai-batch",
     diarization="per-file speaker IDs; IDs restart every request (no cross-request matching)",
     cost={"intro_per_audio_hour_usd":0.10,
           "note":"Advertised introductory rate on the Microsoft model page — NOT an Azure bill.",
           "source":SRC["mai_model"],"status":"documented_introductory"},
     sources=[SRC["mai_azure"], SRC["mai_model"]])

card(id="mai-transcribe-2-streaming", name="MAI-Transcribe-2-Streaming", versions=["Sweden SDK 1.52"],
     category="azure-speech", family="MAI",
     headline="MAI streaming — works, but no diarization.",
     what=("MAI streaming via Sweden SDK 1.52. Works, but NO diarization. An earlier 15s "
           "audio took 34.6s to finalize — the advertised ~120ms was not verified."),
     capabilities=["streaming transcription"], tested="measured",
     diarization="none (streaming)",
     cost={"stream_per_hour_usd":0.54,
           "note":"Advertised introductory stream rate (Microsoft model page) — NOT an Azure bill.",
           "source":SRC["mai_model"],"status":"documented_introductory"},
     sources=[SRC["mai_azure"], SRC["mai_model"]])

card(id="azure-conversation-transcriber", name="Azure ConversationTranscriber (rttold)",
     versions=["classic continuous"],
     category="azure-speech", family="Azure Speech",
     headline="Classic continuous diarization — best observed continuity.",
     what=("Azure classic continuous ConversationTranscriber, the `rttold` default. Produced "
           "the best observed speaker continuity in this pilot, with brief turn mistakes, and "
           "its own wording quirks (e.g. 'chatbot Brad' where MAI said 'RAG'). Like the 30s "
           "MAI request, it placed the introduction under one speaker. Default `rttold` = "
           "classic continuous, left UNCHANGED."),
     capabilities=["continuous diarization", "streaming speaker labels"], tested="measured",
     benchmark_key="rttold",
     diarization="continuous speaker labels (best continuity observed here)",
     cost={"status":"unknown","note":"Azure rate NOT reverified — verify Azure quote; not $0/free."},
     sources=[SRC["azure_diar"]])

card(id="azure-llm-speech", name="Azure LLM Speech (rttheb)", versions=["LLM speech path"],
     category="azure-speech", family="Azure Speech",
     headline="Azure LLM-backed speech path (rttheb alias).",
     what="Azure LLM Speech, the `rttheb` alias. Rate not reverified; render Azure quote, not free.",
     capabilities=["llm speech"], tested="not_tested",
     diarization="not tested",
     cost={"status":"unknown","note":"Azure rate NOT reverified — verify Azure quote; not $0/free."},
     sources=[SRC["azure_diar"]])

# ---------------------------------------------------------------------------
# BENCHMARK (six realtime models) — exact from saved summary JSON
# ---------------------------------------------------------------------------
summary = json.load(open(HERE.parent.parent / "docs/benchmarks/2026-10-14-realtime-pilot/realtime-comparison-summary.json"))

# representative completed outputs (verbatim, machine-labeled)
rep_outputs = {
    "gpt-realtime-1.5": "Speaker A: Yeah, you know me already, Marcus.\nSpeaker B: Yeah.\nSpeaker A: Frank, have you been plugged in already?\nSpeaker B: I think we've had a brief introduction before. So yeah, if you don't know me, Frank Tomasello, I'm the Solutions Engineer working alongside Steven.",
    "gpt-realtime-2.1": "Speaker A: Yeah, you know me already, Marcus. Frank, have you been plugged in already?\nSpeaker B: I think we've had a brief introduction before. So yeah, if you don't know me, Frank Tomasello, I'm the solutions engineer working alongside Steven. You probably...",
    "gpt-realtime-2": "Speaker A: Yeah, you know me already, Marcus. Frank, have you been plugged in already?\nSpeaker B: I think we've had a brief introduction before. So yeah, if you don't know me, Frank Tomasello, I'm the solutions engineer working alongside Stephen. You probably",
    "gpt-realtime": "Speaker A: Yeah, you know me already, Marcus.\nSpeaker B: Yeah.\nSpeaker A: Frank, have you been plugged in already?\nSpeaker B: I think we've had a brief introduction before. So yeah, if you don't know me, Frank Tomasello, I'm the solutions engineer working alongside Steven.",
    "gpt-realtime-mini": "Speaker A: Yeah, you know me already, Marcus.\nSpeaker B: Frank, have you been plugged in already?\nSpeaker A: I think we've had a brief introduction before. So yeah, if you don't know me, Frank Tomasello, I'm the solutions engineer working alongside Steven.",
    "gpt-realtime-2.1-mini": "Speaker A: Yeah, do you know me already, Marcus? Frank, have you been plugged in already?\nSpeaker B: I think we've had a brief introduction before, so yeah. If you don't know me, Frank Tomasello, I'm the solutions engineer working alongside Steven. You got it.",
}
for m in summary["models"]:
    m["representative_output"] = rep_outputs.get(m["model"])
    m["representative_note"] = "Machine-labeled Speaker A/B; one selected completed run, not full raw events."

benchmark = {
    "scope": summary["scope"],
    "attempts_total": summary["attempts"],
    "completed_total": summary["completed"],
    "setup_failed_total": summary["attempts"] - summary["completed"],
    "models": summary["models"],
    "methodology": {
        "fixture": "Same source test.mp3 seconds 300–330 (30s), decoded once to mono PCM16 24kHz.",
        "commits": "Two 15-second commits in ONE retained conversational session.",
        "pacing": "Audio paced at source speed (100ms frames); English; text output.",
        "settings": "Same transcription-only instructions, VAD disabled, provider-default reasoning, no vocabulary hints / corrections / references.",
        "reps": "Three repetitions per model; six models × 6 attempts (two auth routes × 3).",
        "auth": "Both auth routes attempted SEPARATELY: ephemeral minted key vs direct AAD. 26 of 36 attempts were setup failures (timeouts / HTTP 408, including mint + WS).",
        "scoring": "Targeted diagnostics (Frank continuity, three-voices-distinct, latency), NOT WER/DER. No whole-meeting winner.",
        "latency_caveat": "'First text after commit' includes deliberate 15s accumulation; it is NOT minimum model latency.",
        "base_outlier": "gpt-realtime median 0.597s but range reaches 18.597s — the outlier MUST stay visible; do not rank on median alone.",
        "confound": "Earlier 2.1 runs were on Caldova; others on temporary PAYG deployments. Both East US 2, but the account difference is a timing confound.",
        "earlier_pilot": "An earlier 2.1 initial-pilot success is NOT included in this 36-attempt aggregate.",
        "shortlist": "1.5 and 2.1 are TENTATIVE shortlist candidates only — full-meeting validation pending.",
    },
    "denominator_rule": "Passes are over COMPLETED runs, not attempts. No quality scores exist for setup-failed runs.",
    "rubric_source": "docs/benchmarks/2026-10-14-realtime-pilot/realtime-comparison-rubric.md",
    "summary_source": "docs/benchmarks/2026-10-14-realtime-pilot/realtime-comparison-summary.json",
}

# ---------------------------------------------------------------------------
# MAI batch + chunk + human-speaker-check evidence (exact from JSON)
# ---------------------------------------------------------------------------
mai_batch = {
    "full_file": {"audio_seconds": 1695.79, "request_seconds": 20.636,
                  "phrases": 92, "words": 4486, "speaker_ids": [0,1,2,3,4,5,6,7],
                  "caveat": "8 unique IDs — NOT verified as 8 distinct people."},
    "chunked_excerpt": {"source_start_seconds": 270, "source_end_seconds": 450,
                        "chunk_seconds": 10, "lines": 26, "words": 535, "speaker_ids": ["0","1"],
                        "issue": "Speaker IDs restart per request. Speaker 0 assigned to multiple distinct self-introductions and both sides of dialogue. No cross-chunk matching."},
    "chunk_15_vs_10": {"10s": {"lines":26,"words":535,"speaker_ids":["0","1"]},
                       "15s": {"lines":19,"words":530,"speaker_ids":["0","1","2"]},
                       "note": "15s chunks do NOT solve cross-request label reset."},
    "rich_config": {"http":200, "options":"word timestamps + clean style + phrase-list hints + en-US",
                    "default":"verbatim", "maxSpeakers":"rejected (HTTP 400)", "confidence":"0 returned, not usable"},
    "quality_metrics": {"word_error_rate": None, "diarization_error_rate": None,
                        "reason": "No human-checked transcript/speaker reference exists."},
    "cli_bug": {"fixed":"EOF drained only two buffered chunks; now all full chunks + tail drain",
                "regression_before":"2 requests instead of 19", "tests_after":"99 passed, 1 existing LibreSSL warning"},
    "source": "docs/benchmarks/2026-10-14-realtime-pilot/verified-results.json",
}

human_check = json.load(open(HERE.parent.parent / "docs/benchmarks/2026-10-14-realtime-pilot/human-speaker-check.json"))

# ---------------------------------------------------------------------------
# COST CALCULATOR inputs (real rates; computed in JS from these)
# ---------------------------------------------------------------------------
cost_calc = {
    "note": "Illustrative only. OpenAI public list rates 2026-10-14, NOT a verified Azure bill. Excludes history text, reasoning, transcription, and tools.",
    "conversion": {"input_tok_per_s": IN_TOK_PER_S, "output_tok_per_s": OUT_TOK_PER_S,
                   "doc": "Documented: 10 audio tokens/s input, 20 tokens/s output."},
    "options": [
        {"id":"rt-full","label":"GPT-Realtime full (2.1 / 2 / 1.5 / base)",
         "audio_in_per_min_usd": audio_cost_per_min(32,64,"input"),
         "audio_out_per_min_usd": audio_cost_per_min(32,64,"output")},
        {"id":"rt-mini","label":"GPT-Realtime mini / 2.1-mini",
         "audio_in_per_min_usd": audio_cost_per_min(10,20,"input"),
         "audio_out_per_min_usd": audio_cost_per_min(10,20,"output")},
    ],
    "worked_example": {
        "scenario":"30 min incoming + 30 min outgoing audio",
        "rt_full_usd": round(30*audio_cost_per_min(32,64,"input") + 30*audio_cost_per_min(32,64,"output"), 2),
        "rt_mini_usd": round(30*audio_cost_per_min(10,20,"input") + 30*audio_cost_per_min(10,20,"output"), 2),
        "excludes":"history text, reasoning, transcription, tools",
    },
    "transcription_rates_per_min_usd": {k:v[0] for k,v in TRANSCRIBE_RATES.items()},
    "gpt_live_connected_per_hour_usd": 3.0,
}

# ---------------------------------------------------------------------------
# PROPOSED SOLUTIONS (none implemented)
# ---------------------------------------------------------------------------
solutions = [
    {"name":"Hybrid: Azure continuous diarization + MAI word timestamps",
     "status":"proposed (not implemented)",
     "detail":"Use classic continuous diarization for speaker continuity, MAI for word-level timing."},
    {"name":"Local diarization via voice embeddings (diart)",
     "status":"proposed (not implemented)",
     "detail":"Local speaker embeddings to persist identity across requests.", "source":SRC["diart"]},
    {"name":"OpenAI known-speaker references",
     "status":"proposed (not implemented)",
     "detail":"Up to 4 known voices, 2–10s each, attached per request. OpenAI direct is DOCUMENTED; the Azure path is UNVERIFIED.", "source":SRC["cookbook_spkr"]},
]

defaults = {
    "note":"User defaults UNCHANGED by this work.",
    "rtt":"openai, 10s chunks", "rttold":"classic continuous", "rttheb":"Azure LLM Speech",
    "sdk":"upgraded to 1.52.0",
    "cleanup":"All five temporary PAYG deployments removed; only original gpt-4o-transcribe-diarize remains on the RTT resource (verified by readback).",
}

concepts = {
    "voice_agent":"A voice agent (gpt-live-1) holds a spoken conversation — it listens while it speaks (full duplex) and delegates reasoning/tools to a backend. It is not a transcriber.",
    "asr":"ASR / transcription turns speech into text. Diarization additionally labels WHO spoke. Some endpoints do neither speaker labeling (e.g. gpt-live-transcribe).",
    "tts":"TTS turns text into spoken audio (e.g. gpt-4o-mini-tts). The reverse direction of ASR.",
    "streamed_vs_continuous":"Streamed file / committed-turn output processes a bounded chunk and RESETS speaker labels each request. Continuous audio (classic ConversationTranscriber) keeps one session and carries speaker identity across the stream — which is why it showed the best continuity here.",
    "realtime_prompted":"The six GPT-Realtime models are conversational models PROMPTED to emit Speaker A/B text. That is generated labeling, not native diarization.",
    "aliases":"rtt (openai 10s chunks), rttold (classic continuous), rttheb (Azure LLM Speech). Tested paths vs unverified paths are marked per card.",
}

facts = {
    "meta": {
        "title": "Speech, Voice & Realtime Model Field Guide",
        "subtitle": "GPT speech/voice/realtime catalog + a grounded six-model realtime transcription pilot",
        "verified_date": VERIFIED,
        "generated_by": "scripts/speech-guide/build_facts.py + build.py",
        "card_count": len(cards),
        "card_count_expected": 22,
        "disclaimer": "Private/internal artifact. Costs are OpenAI public list rates (2026-10-14), NOT a verified Azure bill. Measured facts come from this pilot's saved evidence; documented facts come from vendor docs; not_tested / unknown are shown as such.",
    },
    "sources": SRC,
    "cards": cards,
    "benchmark": benchmark,
    "mai_batch": mai_batch,
    "human_check": human_check,
    "cost_calc": cost_calc,
    "solutions": solutions,
    "defaults": defaults,
    "concepts": concepts,
}

assert len(cards) == 22, f"expected 22 cards, got {len(cards)}"
out = HERE / "facts.json"
out.write_text(json.dumps(facts, indent=2))
print(f"wrote {out} with {len(cards)} cards")
print("categories:", sorted({c['category'] for c in cards}))
print("tested states:", sorted({c['tested'] for c in cards}))
