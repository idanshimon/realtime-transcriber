#!/usr/bin/env python3
"""Build the table-first companion from saved facts and benchmark evidence."""
from pathlib import Path
import json, html
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
FACTS=json.loads((HERE/'facts.json').read_text())
BENCH=json.loads((ROOT/'docs/benchmarks/2026-10-14-realtime-pilot/realtime-comparison-summary.json').read_text())
OUT=ROOT/'docs/speech-model-comparison.html'
E=lambda x:html.escape(str(x))
GROUPS=[('Voice agents & conversational Realtime','voice-agent'),('Dedicated transcription','transcription'),('Audio chat','audio'),('Text to speech','tts'),('Azure Speech & MAI','azure-speech')]
NOTES={
'gpt-live-1':'Works in Caldova voice flow; not in this transcription comparison.',
'gpt-realtime-1.5':'Shortlist: strongest repeated result on this clip.',
'gpt-realtime-2.1':'Shortlist: promising, only one completed comparison run.',
'gpt-realtime-2':'Promising: only one completed comparison run.',
'gpt-realtime':'Kept Frank; merged the following longer voice with him.',
'gpt-realtime-mini':'Kept Frank; confused the preceding speaker.',
'gpt-realtime-2.1-mini':'Changed Frank’s label in one completed run; merged voices.',
'gpt-4o-transcribe-diarize':'Current rtt backend. Not rerun in the six-model pilot.',
'mai-transcribe-2':'10s and 15s chunks both reset speaker IDs.',
'mai-transcribe-2-streaming':'Access worked; earlier 15s clip finalized at ~34.6s. Not a latency win yet.',
'azure-conversation-transcriber':'rttold backup. Best continuity observed; some brief-turn and word errors.',
'azure-llm-speech':'Existing rttheb integration; no new comparison result.'}
def group(c):
 if c['category'] in ['voice-agent','realtime-conversational']:return 'voice-agent'
 return c['category']
def price(c):
 p=c['cost']
 if 'connected_per_hour_usd' in p:return f"${p['connected_per_hour_usd']:g}/connected hour<small>+ backend model / tools</small>"
 if 'audio_in_per_M_usd' in p:return f"${p['audio_in_per_M_usd']:g} in / ${p['audio_out_per_M_usd']:g} out<small>per 1M audio tokens; text extra</small>"
 if 'per_hour_usd' in p:return f"~${p['per_hour_usd']:g}/audio hour<small>${p['per_min_usd']:g}/audio minute</small>"
 if 'intro_per_audio_hour_usd' in p:return f"${p['intro_per_audio_hour_usd']:g}/audio hour<small>advertised introductory rate</small>"
 if 'stream_per_hour_usd' in p:return f"${p['stream_per_hour_usd']:g}/audio hour<small>advertised introductory rate</small>"
 if 'audio_out_per_M_usd' in p:return f"${p['audio_out_per_M_usd']:g}/1M audio out<small>+ ${p.get('text_in_per_M_usd',0):g}/1M text in</small>"
 return '<span class="muted">Not reverified</span><small>Not $0 / not free</small>'
def fields(c):
 n=c['id'];cat=c['category']
 if n=='gpt-live-1':return 'Natural voice conversation','Yes · full duplex','Yes','No verified multi-human diarization'
 if n=='gpt-realtime-translate':return 'Live translation','Yes','Translation model','Not verified'
 if cat=='realtime-conversational':return 'Conversation, reasoning / tools','Yes · continuous session','Yes','Prompted labels only<small>Not native diarization metadata</small>'
 if n in ['gpt-live-transcribe','gpt-realtime-whisper']:return 'Live speech → text','Yes','No','No speaker labels'
 if n=='gpt-transcribe':return 'Speech → text','File / committed turn','No','No native speaker labeling documented here'
 if n=='gpt-4o-transcribe-diarize':return 'Speech → text + speakers','10s chunks in rtt<small>Azure realtime path not tested</small>','No','Yes · per request<small>Global identity not established</small>'
 if cat=='transcription':return 'Speech → text','File; realtime path not tested here','No','Not evaluated here'
 if cat=='audio':return 'Audio-capable chat','Request / response<small>Not a persistent Realtime session</small>','Yes','Not evaluated here'
 if cat=='tts':return 'Text → speech','Generated audio output<small>Does not listen to live audio</small>','Yes','Not applicable'
 if n=='mai-transcribe-2':return 'Speech → text + speakers','No · file requests<small>Near-live via chunks</small>','No','Yes · per file<small>Resets between requests</small>'
 if n=='mai-transcribe-2-streaming':return 'Live speech → text','Yes','No','No'
 if n=='azure-conversation-transcriber':return 'Live speech + speakers','Yes · continuous session','No','Yes · session-local IDs'
 return 'LLM-enhanced speech → text','10s chunks in rttheb','No','Supported by integration<small>No fresh benchmark</small>'
rows=[]
for label,key in GROUPS:
 cards=[c for c in FACTS['cards'] if group(c)==key]
 rows.append(f'<tr class="group"><th colspan="8">{E(label)} <span>{len(cards)} options</span></th></tr>')
 for c in cards:
  purpose,stream,speaks,speakers=fields(c);n=c['id'];badge='shortlist' if n in ['gpt-realtime-1.5','gpt-realtime-2.1'] else ''
  metric=next((b for b in BENCH['models'] if b['model']==n),None)
  score='Not measured<small>No reference-scored DER</small>'
  if metric:
   k=metric['completed'];hits=metric['frank_continuity_passes']
   score=f'DER: not measured<small>Frank continuity: {hits}/{k} completed runs ({hits/k:.0%})</small>'
  source=c.get('sources',['https://developers.openai.com/api/docs/pricing'])[0]
  rows.append(f'<tr class="{badge}" data-model="{E(n)}"><th scope="row">{E(c["name"])}<small><a href="{E(source)}">Documentation ↗</a></small></th><td>{purpose}</td><td>{stream}</td><td>{speaks}</td><td>{speakers}</td><td>{score}</td><td class="cost">{price(c)}</td><td>{E(NOTES.get(n,"Not tested in this comparison."))}</td></tr>')
assert len(FACTS['cards'])==22
results=[]
for b in sorted(BENCH['models'],key=lambda x:['gpt-realtime-1.5','gpt-realtime-2.1','gpt-realtime-2','gpt-realtime','gpt-realtime-mini','gpt-realtime-2.1-mini'].index(x['model'])):
 n=b['model'];k=b['completed'];lo,hi=b['first_text_after_commit_range_s'];cls='shortlist' if n in ['gpt-realtime-1.5','gpt-realtime-2.1'] else ''
 results.append(f'<tr class="{cls}"><th scope="row">{E(n)}</th><td>{k} / {b["attempts"]}</td><td>{b["setup_failed"]}</td><td>{b["frank_continuity_passes"]} / {k}<small>{b["frank_continuity_passes"]/k:.0%} of completed runs</small></td><td>{b["three_longer_voices_distinct_passes"]} / {k}</td><td>{b["first_text_after_commit_median_s"]:.3f}s<small>range {lo:.3f}–{hi:.3f}s</small></td><td>{E(NOTES[n])}</td></tr>')
assert len(results)==6 and sum(b['completed'] for b in BENCH['models'])==BENCH['completed']
CSS='''*{box-sizing:border-box}html{scroll-padding-top:30px}body{margin:0;background:#f5f7f8;color:#13242c;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}header{background:#142d35;color:white;padding:30px max(24px,calc((100vw - 1500px)/2))}header p{color:#c8d9df;max-width:1000px;margin:10px 0}h1{font-size:32px;line-height:1.2;margin:5px 0 12px;letter-spacing:-.6px}nav{display:flex;gap:12px;flex-wrap:wrap;margin-top:18px}nav a{color:white;text-decoration:none;border:1px solid #67848e;border-radius:5px;padding:10px 14px;min-height:44px}main{max-width:1550px;margin:auto;padding:26px 24px 50px}h2{font-size:25px;letter-spacing:-.3px;margin:36px 0 8px}p{max-width:1150px}a{color:#086356}a:focus-visible{outline:3px solid #d78a21;outline-offset:3px}.note{padding:15px 18px;border:1px solid #d3dfdf;background:white;border-radius:6px;margin:16px 0}.recommend{background:#e6f3ef;border-color:#a9cec2}.pill{font-weight:700;text-transform:uppercase;letter-spacing:1px;font-size:12px}.table-wrap{max-width:100%;overflow:auto;border:1px solid #cbd7da;border-radius:6px;background:white}table{border-collapse:separate;border-spacing:0;width:100%;min-width:1180px;font-size:14px;text-align:left}thead th{background:#183d48;color:white;font-size:12px;text-transform:uppercase;letter-spacing:.5px;padding:15px 12px;vertical-align:bottom}td,tbody th{padding:15px 12px;vertical-align:top;border-bottom:1px solid #dde4e6;line-height:1.45}tbody th{font-weight:650;min-width:205px;max-width:230px;overflow-wrap:anywhere}td{min-width:95px;max-width:235px}tbody tr:nth-child(even):not(.group){background:#f6f9fa}tbody tr.shortlist{background:#e6f3ef!important}.group th{background:#dfe9ed;padding:12px 14px;font-size:15px;letter-spacing:.3px}.group span{font-size:12px;color:#506570;font-weight:400;margin-left:10px}small{display:block;margin-top:5px;font-size:12px;line-height:1.45;color:#4c626c}small a{text-decoration:none}td.cost{min-width:165px;font-variant-numeric:tabular-nums}.muted{color:#5e7078}.scroll-hint{font-size:13px;color:#50656e}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}.two-col .note{margin:0}footer{margin-top:30px;font-size:13px;color:#4c626c}footer a{overflow-wrap:anywhere}@media(max-width:650px){header{padding:24px 18px}main{padding:20px 14px}h1{font-size:27px}h2{font-size:22px}.two-col{grid-template-columns:1fr}.table-wrap{border-radius:4px}nav{gap:8px}nav a{padding:10px;font-size:14px}}@media print{@page{size:A3 landscape;margin:12mm}body{background:white;font-size:11pt}header{background:white;color:#13242c;padding:0}header p{color:#334}nav,.scroll-hint{display:none}main{padding:0;max-width:none}.table-wrap{overflow:visible;border:0}table{font-size:9pt;min-width:0}thead{display:table-header-group}td,tbody th{padding:7px;min-width:0}section{break-before:auto}tr{break-inside:avoid}a{color:inherit}}'''
page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Voice & Speech — Clear Comparison Tables</title><style>{CSS}</style></head><body>
<header><div class="pill">Private working comparison · {E(FACTS['meta']['verified_date'])}</div><h1>Voice, speech & realtime — side by side</h1><p>Two tables. All 22 options first; our actual six-model results second. No hidden cards or expandable rows.</p><nav><a href="#models">All models</a><a href="#results">Actual test results</a><a href="#read">How to read the costs</a><a href="speech-model-field-guide.html">Detailed field guide ↗</a></nav></header><main>
<div class="note recommend"><strong>For RTT: shortlist Realtime 1.5 and 2.1 for a longer test.</strong> 1.5 had the strongest repeated result on our short clip. Keep <code>rttold</code> as the working backup. No default has been changed.</div>
<section id="models"><h2>1. All models — capabilities and cost</h2><p><strong>Native speaker IDs ≠ labels generated by a conversational model.</strong> MAI-2 resets IDs between separate requests; Realtime retained its session but generated the labels as text.</p><p class="scroll-hint">Wide comparison: on a phone, swipe the table sideways. All rows remain visible. Light green = tentative RTT shortlist.</p><div class="table-wrap" tabindex="0" aria-label="All 22 models comparison, horizontally scrollable"><table id="model-table"><thead><tr><th>Model</th><th>Main job</th><th>Live audio / streaming?</th><th>Speaks back?</th><th>Speaker support</th><th>Diarization score</th><th>Public list cost · USD</th><th>Our actual finding</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section id="results"><h2>2. Actual Realtime test — which did better?</h2><p>Same 30-second meeting excerpt. Two 15-second audio portions in one session. <strong>{BENCH['completed']} completed transcripts out of {BENCH['attempts']} attempts.</strong> Setup timeouts are not bad-transcript scores.</p><div class="table-wrap" tabindex="0" aria-label="Six realtime models measured comparison"><table id="result-table"><thead><tr><th>Model</th><th>Completed / attempts</th><th>Setup failures</th><th>Speaker continuity score³</th><th>3 main turns distinct¹</th><th>First text after commit²</th><th>Readout</th></tr></thead><tbody>{''.join(results)}</tbody></table></div><p><small>³ Speaker continuity score = completed runs that kept Frank’s label across this one boundary / completed runs. Setup failures remain in their own column. Not a formal DER score.</small><small>¹ Targeted label-separation diagnostic, not a human-scored diarization error rate. The user verified Frank’s continuity across the disputed boundary; no full word/speaker reference exists.</small><small>² Median and range from completed responses, after deliberately accumulating 15 seconds of audio. This is not minimum possible live latency. Authentication routes and Azure accounts differed; keep timing provisional.</small></p><div class="note"><strong>No whole-meeting winner yet.</strong> All tested conversational models made wording errors. Realtime 1.5: 3 completed runs; 2 and 2.1: only 1 each. The base model’s 18.597s delay is retained above, not hidden by its median. WER and DER: not measured. These percentages are narrow continuity diagnostics, not general speaker-identification accuracy.</div></section>
<section id="read"><h2>3. Compare costs without mixing units</h2><div class="two-col"><div class="note"><strong>Conversational Realtime</strong><p>Audio input and spoken output have separate token rates. Full models: about $0.0192 per incoming audio minute + $0.0768 per generated speech minute. Minis: $0.006 + $0.024 respectively.</p><p>Illustrative 30 minutes in + 30 minutes out: <strong>${30*.0192+30*.0768:.2f} full / ${30*.006+30*.024:.2f} mini</strong>. Excludes text/history, reasoning, input-transcription and tools. Our transcript-only pilot generated text, not spoken audio.</p></div><div class="note"><strong>Duration-based speech services</strong><p>GPT-Live-1 bills connected time: $3/hour + backend/tools. Transcribe rates use audio duration; token-metered 4o transcription rates are estimates. MAI prices are advertised introductory rates, not an Azure invoice.</p><p>All prices are public list references dated {E(FACTS['meta']['verified_date'])}, <strong>not a verified Azure subscription quote</strong>. “Not reverified” does not mean free.</p></div></div></section>
<footer><p>Generated from <code>scripts/speech-guide/facts.json</code> and the saved benchmark summary. Setup failures, unknowns and differing denominators are preserved. Private meeting evidence; not published.</p><p>Sources: <a href="https://developers.openai.com/api/docs/pricing">OpenAI pricing</a> · <a href="https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe">MAI API</a> · <a href="https://learn.microsoft.com/en-us/azure/ai-services/speech-service/get-started-stt-diarization">Azure diarization</a> · <a href="speech-model-field-guide.html">Full guide & audio</a></p></footer></main></body></html>'''
from comparison_interactive import enhance
page=enhance(page,FACTS,BENCH,HERE)
OUT.write_text(page)
print(f'Built {OUT}: {len(FACTS["cards"])} models, {len(results)} benchmark rows, {len(page.encode())} bytes')
