"""Interactive table enhancement; no remote assets or dependencies."""
from pathlib import Path
import json, re, html
WEIGHTS={'continuity':45,'separation':35,'completion':20}
def enhance(page,facts,bench,here):
 rows=[]
 for b in bench['models']:
  k=b['completed'];v=45*b['frank_continuity_passes']/k+35*b['three_longer_voices_distinct_passes']/k+20*k/b['attempts']
  rows.append({'model':b['model'],'score':round(v,1),'unrounded':v})
 for r in rows:r['rank']=1+sum(s['unrounded']>r['unrounded']+1e-9 for s in rows)
 (here/'comparison-scores.json').write_text(json.dumps({'weights':WEIGHTS,'scope':'Provisional post-hoc pilot utility ranking; not DER or word accuracy. Weights chosen after seeing the pilot. Setup failures reflect observed Azure setup reliability, not intrinsic model quality.','models':rows},indent=2))
 scores={r['model']:r for r in rows};meta={}
 live={'gpt-live-1','gpt-live-transcribe','gpt-realtime-whisper','gpt-realtime-translate','mai-transcribe-2-streaming','azure-conversation-transcriber'}
 native={'mai-transcribe-2','azure-conversation-transcriber','gpt-4o-transcribe-diarize','azure-llm-speech'}
 for c in facts['cards']:
  n=c['id'];p=c['cost'];cat='voice-agent' if c['category'] in ['voice-agent','realtime-conversational'] else c['category']
  unit='unknown';value=None
  for field,u in [('connected_per_hour_usd','connected-hour'),('audio_in_per_M_usd','audio-token-in'),('per_hour_usd','audio-hour'),('intro_per_audio_hour_usd','audio-hour'),('stream_per_hour_usd','audio-hour'),('audio_out_per_M_usd','audio-token-out')]:
   if field in p:unit=u;value=p[field];break
  meta[n]={'model':n,'category':cat,'stream':'live' if n in live or n in scores else 'file','speaker':'native' if n in native else 'prompted' if n in scores else 'none' if n in ['gpt-live-transcribe','gpt-realtime-whisper','mai-transcribe-2-streaming'] else 'other','test':'pilot' if n in scores else 'measured' if c['tested'] in ['functional','measured'] else 'untested','costUnit':unit,'cost':value,'score':scores.get(n,{}).get('score'),'rank':scores.get(n,{}).get('rank')}
 def insert_score(m):
  n=html.unescape(m[1]);s=scores.get(n);content=f'<strong>#{s["rank"]} · {s["score"]:.1f}/100</strong><small>Provisional pilot score</small>' if s else 'Unranked<small>No comparable pilot</small>'
  return m[0].replace('</th>',f'</th><td>{content}</td>',1)
 page=re.sub(r'<tr class="[^"]*" data-model="([^"]+)"><th scope="row">.*?</tr>',insert_score,page)
 page=page.replace('colspan="8"','colspan="9"').replace('<th>Model</th><th>Main job</th>','<th>Model</th><th>Overall score / rank</th><th>Main job</th>')
 def benchmark_score(m):
  n=html.unescape(m[1]);s=scores[n]
  return m[0].replace('</th>',f'</th><td><strong>#{s["rank"]} · {s["score"]:.1f}/100</strong></td>',1)
 start=page.index('<table id="result-table">');end=page.index('</table>',start)
 block=page[start:end]
 block=re.sub(r'<tr class="[^"]*"><th scope="row">([^<]+)</th>.*?</tr>',benchmark_score,block)
 block=block.replace('<th>Model</th><th>Completed','<th>Model</th><th>Overall score / rank</th><th>Completed')
 page=page[:start]+block+page[end:]
 model_controls='''<div class="filters" aria-label="Model filters"><label>Search models<input id="model-search" type="search" placeholder="Name, capability, finding…"></label><label>Family<select id="category-filter"><option value="">All families</option><option value="voice-agent">Voice / Realtime</option><option value="transcription">Transcription</option><option value="audio">Audio chat</option><option value="tts">Text to speech</option><option value="azure-speech">Azure / MAI</option></select></label><label>Live-input path<select id="stream-filter"><option value="">All paths</option><option value="live">Live / continuous</option><option value="file">File / request-based</option></select></label><label>Speaker support<select id="speaker-filter"><option value="">Any speaker support</option><option value="native">Native speaker IDs</option><option value="prompted">Prompted labels</option><option value="none">No speaker labels</option><option value="other">Other / unverified</option></select></label><label>Test evidence<select id="tested-filter"><option value="">All evidence</option><option value="pilot">Six-model pilot</option><option value="measured">Other functional test</option><option value="untested">No current test</option></select></label><label>Cost billing unit<select id="cost-unit-filter"><option value="">All units (no cost sorting)</option><option value="audio-hour">Per audio hour</option><option value="connected-hour">Per connected hour</option><option value="audio-token-in">Per 1M audio-input tokens</option><option value="audio-token-out">Per 1M audio-output tokens</option><option value="unknown">Not reverified</option></select></label><button id="model-reset" type="button">Reset model table</button></div><p id="model-count" class="count" aria-live="polite">22 of 22 models shown</p><p id="sort-notice" class="notice" role="status" hidden>Choose a specific cost billing unit before sorting costs. Different units cannot be compared as one price.</p><p id="model-empty" class="notice" hidden>No models match. Adjust filters or reset.</p>'''
 bench_controls='''<div class="filters bench-filters"><label>Search test results<input id="bench-search" type="search" placeholder="Model or result…"></label><button id="bench-reset" type="button">Reset results table</button></div><p id="bench-count" class="count" aria-live="polite">6 of 6 results shown</p>'''
 page=page.replace('<div class="table-wrap" tabindex="0" aria-label="All 22',model_controls+'<div class="table-wrap" tabindex="0" aria-label="All 22',1)
 page=page.replace('<div class="table-wrap" tabindex="0" aria-label="Six realtime',bench_controls+'<div class="table-wrap" tabindex="0" aria-label="Six realtime',1)
 rank_note='''<div class="note"><strong>My provisional overall score — not an accuracy benchmark.</strong> 45% Frank continuity + 35% main-turn label separation + 20% completed-attempt rate. Weights were chosen after reviewing this pilot. Quality checks use completed runs; completion uses all attempts. Azure setup failures affect this operational score but do not establish intrinsic model quality. Word accuracy, DER and long-meeting behavior are not scored. Equal scores share a rank; untested models remain unranked.</div>'''
 page=page.replace('<section id="results">','<section id="results">'+rank_note,1)
 styles='''[hidden]{display:none!important}.filters{display:flex;flex-wrap:wrap;align-items:end;gap:12px;padding:16px;background:#e8eff1;border:1px solid #c9d6db;border-radius:6px;margin-top:18px}.filters label{display:flex;flex:1 1 165px;min-width:150px;gap:5px;flex-direction:column;font-size:12px;font-weight:700;color:#23424e}.filters input,.filters select,.filters button{min-height:44px;padding:10px;border:1px solid #9badb4;border-radius:5px;background:white;color:#17313a;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:100%}.filters button{cursor:pointer;background:#183d48;color:white;font-weight:650}.filters input:focus,.filters select:focus{outline:3px solid #8dcbb9;outline-offset:1px}.count{font-size:13px;color:#37535f;font-weight:600;margin:9px 0}.notice{background:#fff4de;border:1px solid #cba85c;padding:12px;border-radius:5px}.sort-button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;text-align:left;padding:4px 0;min-height:44px;width:100%}.sort-button:hover{text-decoration:underline}.sort-button:focus-visible{outline:3px solid #8ed1bb;outline-offset:3px}thead th[aria-sort="ascending"] .sort-button:after{content:" ↑"}thead th[aria-sort="descending"] .sort-button:after{content:" ↓"}thead th:not([aria-sort]) .sort-button:after{content:" ↕"}table{min-width:1380px}nav a{min-height:44px}@media(max-width:650px){.filters{padding:12px;gap:10px}.filters label{flex:1 1 145px;min-width:130px}.filters button{width:100%}}@media print{.filters,.count,.notice{display:none!important}.sort-button{min-height:0}table{min-width:0}}'''
 page=page.replace('</style>',styles+'</style>',1)
 js=(here/'comparison_interactive.js').read_text()
 payload=json.dumps({'models':meta,'benchmark':bench['models']},ensure_ascii=False).replace('</','<\\/')
 page=page.replace('</body>',f'<script id="comparison-data" type="application/json">{payload}</script><script>{js}</script></body>')
 page=page.replace('All rows remain visible.','Use search and filters below; click column headings to sort.').replace('No hidden cards or expandable rows.','Search, filter and sort either table. No expandable cards.')
 return page
