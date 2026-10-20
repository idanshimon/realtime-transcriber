# Realtime comparison — predeclared pilot rubric

Written before the six-model comparison. Earlier Realtime 2.1 pilot informed the fixture; this is not blind held-out evaluation.

Models: gpt-realtime, gpt-realtime-mini (latest catalog version), gpt-realtime-1.5, gpt-realtime-2, gpt-realtime-2.1, gpt-realtime-2.1-mini. Three repetitions per model. Same source test.mp3 seconds 300–330, decoded once to mono PCM16 24kHz. Two 15-second commits in one retained conversational session, same transcription-only instructions, text output, VAD disabled. No vocabulary hints, corrections, references, or custom reasoning setting.

Required checks, independently reported:
1. Both responses complete without provider errors and contain nonempty transcript text.
2. User-confirmed boundary: Frank's introduction ending 'You probably' and its continuation 'worked with John' must carry the same speaker label across commits. Unclear text or missing label = unresolved, not a pass.
3. Distinct longer turns: introduction-before-Frank, Frank, and following 'Awesome' speaker receive distinct labels. Brief 'yeah' interjections remain human-review-only; the user has not supplied exact full reference labels.
4. Wording anchors: Frank Tomasello, solutions engineer, Steven, GitHub/developer use-case context where spoken. Compare actual returned text; name spelling is diagnostic, not established WER.
5. No participant-like answer or summary; no invented content. Flag textual issues manually against earlier evidence; no exact human transcript exists.
6. Latency: first text AFTER each commit, response completion AFTER commit, plus overall wall time. Audio is paced at source speed; time to first text includes deliberate 15-second accumulation if reported from session start. Do not call it minimum model latency.
7. Persist final response status and usage as observed; no Azure billed-cost claim from token counts alone.

Pick a tentative candidate only if it passes the continuity checks in all three completed runs. Report provider-blocked runs and all failures. N=3 and 30 seconds of audio establish only this fixture's behavior, not whole-meeting reliability. Native speaker metadata is not expected: labels are conversational-model generation.

Existing 2.1 runs on Caldova, others on temporary pay-as-you-go deployments in the dedicated RTT account; both are East US 2. This account difference is a confound for timing. Keep all defaults and rttold unchanged; remove only newly created benchmark deployments after testing.
