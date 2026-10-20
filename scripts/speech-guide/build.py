#!/usr/bin/env python3
"""Render the self-contained speech-model field guide HTML from facts.json.

Reads:  facts.json  (built by build_facts.py)
        <scratch>/mai2-meeting-benchmark/speaker-boundary-05m00s-05m25s.mp3
Writes: <repo>/docs/speech-model-field-guide.html  (single file, offline)

All CSS/JS/audio inlined. Core content is server-rendered into static HTML and
is fully visible with JavaScript disabled; JS only adds search/filter/calculator.
"""
import json, base64, pathlib, html as H

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
FACTS = json.load(open(HERE / "facts.json"))
OUT = REPO / "docs" / "speech-model-field-guide.html"
AUDIO = pathlib.Path.home() / ".hermes/cache/scratch/mai2-meeting-benchmark/speaker-boundary-05m00s-05m25s.mp3"

def esc(s):
    return H.escape(str(s)) if s is not None else ""

# ---- audio embed -----------------------------------------------------------
audio_b64 = base64.b64encode(AUDIO.read_bytes()).decode()
audio_uri = f"data:audio/mpeg;base64,{audio_b64}"

def usd(x):
    return f"${x:,.4f}".rstrip("0").rstrip(".") if isinstance(x,(int,float)) else esc(x)

# ---- cost rendering per card ----------------------------------------------
def render_cost(cost):
    if not cost:
        return '<span class="unknown">cost not recorded</span>'
    st = cost.get("status")
    if st == "unknown":
        return f'<span class="unknown">unknown — {esc(cost.get("note",""))}</span>'
    parts = []
    labels = {
        "connected_per_min_usd":"connected/min","connected_per_hour_usd":"connected/hr",
        "audio_in_per_M_usd":"audio in /1M tok","audio_out_per_M_usd":"audio out /1M tok",
        "text_in_per_M_usd":"text in /1M","text_out_per_M_usd":"text out /1M",
        "cache_audio_per_M_usd":"cache audio /1M","audio_in_per_min_usd":"audio in/min",
        "audio_out_per_min_usd":"audio out/min","per_min_usd":"/min","per_hour_usd":"/hr",
        "intro_per_audio_hour_usd":"intro /audio-hr","stream_per_hour_usd":"stream /hr",
        "per_min_estimate_usd":"est. /min",
    }
    for k,label in labels.items():
        if k in cost and cost[k] is not None:
            parts.append(f'<span class="price"><b>{usd(cost[k])}</b> {esc(label)}</span>')
    body = " ".join(parts)
    note = cost.get("note")
    badge = ""
    if st == "documented_introductory":
        badge = '<span class="tag tag-intro">introductory</span>'
    elif st == "documented":
        badge = '<span class="tag tag-doc">list rate</span>'
    note_html = f'<div class="cost-note">{esc(note)}</div>' if note else ""
    return f'<div class="costrow">{body} {badge}</div>{note_html}'

TESTED_LABEL = {
    "measured":("measured","Observed in this pilot's saved evidence"),
    "documented":("documented","Stated in a vendor doc; not measured here"),
    "not_tested":("not tested","Neither measured nor confirmed here"),
    "functional":("functional test","Functional voice path confirmed (separate from the six-model benchmark)"),
}

CAT_LABEL = {
    "voice-agent":"Voice agent","realtime-conversational":"Realtime conversational",
    "transcription":"Transcription","audio":"Audio in/out","tts":"Text-to-speech",
    "azure-speech":"Azure / MAI",
}

def card_html(c):
    t = c.get("tested","not_tested")
    tl, tdesc = TESTED_LABEL.get(t,(t,""))
    caps = "".join(f'<span class="cap">{esc(x)}</span>' for x in c.get("capabilities",[]))
    vers = ", ".join(c.get("versions",[]))
    srcs = "".join(f'<a href="{esc(u)}" class="src">source</a>' for u in c.get("sources",[]))
    detail = f'<p class="detail">{esc(c["tested_detail"])}</p>' if c.get("tested_detail") else ""
    bench = ""
    if c.get("benchmark_key"):
        bench = f'<a href="#benchmark" class="benchlink">↓ benchmark evidence</a>'
    return f'''<article class="card" data-cat="{esc(c['category'])}" data-tested="{esc(t)}" data-name="{esc(c['name'].lower())} {esc(' '.join(c.get('capabilities',[])).lower())} {esc(c.get('family','').lower())}">
  <header class="card-head">
    <div>
      <h3>{esc(c['name'])}</h3>
      <div class="meta">{esc(CAT_LABEL.get(c['category'],c['category']))} · {esc(c.get('family',''))} · v {esc(vers)}</div>
    </div>
    <span class="status status-{esc(t)}" title="{esc(tdesc)}">{esc(tl)}</span>
  </header>
  <p class="headline">{esc(c['headline'])}</p>
  <p class="what">{esc(c['what'])}</p>
  {detail}
  <div class="caps">{caps}</div>
  <div class="diar"><span class="k">Diarization</span> {esc(c.get('diarization',''))}</div>
  <div class="cost">{render_cost(c.get('cost'))}</div>
  <footer class="card-foot">{bench}{srcs}</footer>
</article>'''

cards_html = "\n".join(card_html(c) for c in FACTS["cards"])

# ---- benchmark table -------------------------------------------------------
def bench_rows():
    rows = []
    for m in FACTS["benchmark"]["models"]:
        rng = m["first_text_after_commit_range_s"]
        outlier = ' class="outlier"' if (rng[1]-rng[0])>5 else ''
        rep = m.get("representative_output")
        rep_html = f'<details><summary>representative completed output</summary><pre>{esc(rep)}</pre><p class="repnote">{esc(m.get("representative_note",""))}</p></details>' if rep else '<span class="unknown">no completed run with output</span>'
        rows.append(f'''<tr>
      <td class="mname">{esc(m['model'])}</td>
      <td class="num">{m['completed']}/{m['attempts']}</td>
      <td class="num">{m['setup_failed']}</td>
      <td class="num">{m['frank_continuity_passes']}/{m['completed']}</td>
      <td class="num">{m['three_longer_voices_distinct_passes']}/{m['completed']}</td>
      <td class="num">{m['first_text_after_commit_median_s']:.3f}s</td>
      <td class="num"{outlier}>{rng[0]:.3f}–{rng[1]:.3f}s</td>
      <td>{rep_html}</td>
    </tr>''')
    return "\n".join(rows)

b = FACTS["benchmark"]
meth = b["methodology"]
meth_html = "\n".join(f'<li><b>{esc(k.replace("_"," "))}:</b> {esc(v)}</li>' for k,v in meth.items())

# ---- MAI batch -------------------------------------------------------------
mai = FACTS["mai_batch"]
hc = FACTS["human_check"]
hc_annot = "\n".join(f'<li>{esc(a.get("clip_interval_seconds_approx") or a.get("clip_time_seconds"))}s — <b>{esc(a["speaker"])}</b>: {esc(a.get("event",""))} <span class="prec">({esc(a["precision"])})</span></li>' for a in hc["annotations"])

# ---- cost calculator data --------------------------------------------------
cc = FACTS["cost_calc"]
cc_json = json.dumps(cc)

# ---- concepts & solutions --------------------------------------------------
con = FACTS["concepts"]
concepts_html = "\n".join(f'<div class="concept"><h3>{esc(k.replace("_"," ").title())}</h3><p>{esc(v)}</p></div>' for k,v in con.items())
def sol_li(s):
    src = f' <a class="src" href="{esc(s["source"])}">source</a>' if s.get("source") else ""
    return f'<li><b>{esc(s["name"])}</b> <span class="tag tag-prop">{esc(s["status"])}</span><br>{esc(s["detail"])}{src}</li>'
sol_html = "\n".join(sol_li(s) for s in FACTS["solutions"])

dfl = FACTS["defaults"]
dfl_html = "\n".join(f'<li><b>{esc(k)}:</b> {esc(v)}</li>' for k,v in dfl.items() if k!="note")

src_html = "\n".join(f'<li><a href="{esc(u)}">{esc(u)}</a> <span class="skey">{esc(k)}</span></li>' for k,u in FACTS["sources"].items())

meta = FACTS["meta"]

# filter controls
cats = sorted({c["category"] for c in FACTS["cards"]})
cat_btns = "".join(f'<button class="chip" data-filter-cat="{esc(c)}">{esc(CAT_LABEL.get(c,c))}</button>' for c in cats)

HTML = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{esc(meta['title'])}</title>
<style>
:root{{
  --paper:#f7f6f2; --panel:#ffffff; --ink:#1a2230; --muted:#5b6676; --faint:#8793a4;
  --line:#e2e0d8; --nav:#15202e; --nav-ink:#e8edf2; --teal:#0f766e; --teal-soft:#d7eae7;
  --accent:#0f766e; --warn:#9a5b00; --warn-bg:#fdf3e3; --meas:#0f766e; --doc:#3a5a8c;
  --nottest:#8793a4; --unknown:#9a5b00;
  --shadow:0 1px 2px rgba(20,32,46,.06),0 1px 1px rgba(20,32,46,.04);
}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--paper);color:var(--ink);
  font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;text-wrap:pretty;}}
a{{color:var(--teal);text-decoration:none}}
a:hover{{text-decoration:underline}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px}}
/* nav */
.topnav{{position:sticky;top:0;z-index:30;background:var(--nav);color:var(--nav-ink);
  border-bottom:1px solid #0a121b}}
.topnav .wrap{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 20px;min-height:52px}}
.topnav .brand{{font-weight:700;letter-spacing:.2px;margin-right:auto;font-size:15px}}
.topnav a{{color:var(--nav-ink);opacity:.82;font-size:13px;padding:8px 10px;border-radius:7px;min-height:40px;display:inline-flex;align-items:center}}
.topnav a:hover{{opacity:1;background:rgba(255,255,255,.08);text-decoration:none}}
/* hero */
.hero{{padding:48px 0 28px;border-bottom:1px solid var(--line)}}
.hero h1{{font-size:clamp(26px,4.5vw,40px);line-height:1.1;margin:0 0 10px;letter-spacing:-.5px}}
.hero .sub{{font-size:18px;color:var(--muted);max-width:68ch;margin:0 0 18px}}
.badges{{display:flex;gap:8px;flex-wrap:wrap}}
.pill{{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:5px 12px;font-size:13px;color:var(--muted);box-shadow:var(--shadow)}}
.pill b{{color:var(--ink)}}
.disclaimer{{margin-top:18px;background:var(--warn-bg);border:1px solid #f0dcb8;border-left:3px solid var(--warn);
  padding:12px 14px;border-radius:8px;font-size:14px;color:#6b4a12;max-width:80ch}}
section{{padding:40px 0;border-bottom:1px solid var(--line)}}
section > .wrap > h2{{font-size:24px;margin:0 0 6px;letter-spacing:-.3px}}
.lead{{color:var(--muted);margin:0 0 22px;max-width:74ch}}
/* concepts */
.concepts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}
.concept{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;box-shadow:var(--shadow)}}
.concept h3{{margin:0 0 6px;font-size:15px;color:var(--teal)}}
.concept p{{margin:0;font-size:14px;color:var(--muted)}}
/* catalog controls */
.controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:20px}}
.controls input[type=search]{{flex:1;min-width:200px;padding:11px 14px;border:1px solid var(--line);border-radius:9px;font-size:15px;background:var(--panel)}}
.chips{{display:flex;gap:7px;flex-wrap:wrap}}
.chip{{border:1px solid var(--line);background:var(--panel);color:var(--muted);border-radius:999px;
  padding:9px 13px;font-size:13px;cursor:pointer;min-height:40px}}
.chip[aria-pressed=true]{{background:var(--nav);color:#fff;border-color:var(--nav)}}
.chip.reset{{margin-left:auto;color:var(--teal);border-color:var(--teal)}}
.count{{font-size:13px;color:var(--faint);margin-bottom:12px}}
/* cards */
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:9px}}
.card-head{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}}
.card-head h3{{margin:0;font-size:17px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:-.3px}}
.meta{{font-size:12px;color:var(--faint);margin-top:3px}}
.status{{font-size:11px;font-weight:600;padding:4px 9px;border-radius:999px;white-space:nowrap;border:1px solid transparent}}
.status-measured{{background:#e2f0ee;color:var(--meas);border-color:#bfe0da}}
.status-functional{{background:#e2f0ee;color:var(--meas);border-color:#bfe0da}}
.status-documented{{background:#e7eef7;color:var(--doc);border-color:#cdddf0}}
.status-not_tested{{background:#eef0f2;color:var(--nottest);border-color:#dde1e5}}
.headline{{margin:0;font-weight:600;font-size:14.5px}}
.what{{margin:0;font-size:13.5px;color:var(--muted)}}
.detail{{margin:0;font-size:13px;color:var(--muted);background:#f4f6f5;border-left:2px solid var(--teal);padding:8px 10px;border-radius:0 6px 6px 0}}
.caps{{display:flex;gap:6px;flex-wrap:wrap}}
.cap{{font-size:11.5px;background:#eef2f1;color:#39524e;border-radius:6px;padding:3px 8px}}
.diar{{font-size:13px;color:var(--muted)}}
.diar .k,.cost .k{{display:inline-block;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--faint);margin-right:6px}}
.costrow{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:13px}}
.price{{color:var(--ink)}}
.cost-note{{font-size:12px;color:var(--faint);margin-top:4px}}
.unknown{{color:var(--unknown);font-size:13px}}
.tag{{font-size:10.5px;padding:2px 7px;border-radius:999px;font-weight:600}}
.tag-intro{{background:var(--warn-bg);color:var(--warn)}}
.tag-doc{{background:#e7eef7;color:var(--doc)}}
.tag-prop{{background:#efeafb;color:#6b4bb0}}
.card-foot{{margin-top:auto;display:flex;gap:12px;flex-wrap:wrap;padding-top:6px;border-top:1px solid var(--line);font-size:12px}}
.src,.benchlink{{font-size:12px}}
/* benchmark table */
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel)}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;min-width:720px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{background:#f0f2f1;font-size:12px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted)}}
td.num{{font-variant-numeric:tabular-nums;white-space:nowrap}}
td.mname{{font-family:ui-monospace,Menlo,monospace;font-size:13px}}
td.outlier{{color:var(--warn);font-weight:600}}
details summary{{cursor:pointer;color:var(--teal);font-size:13px}}
details pre{{white-space:pre-wrap;background:#f4f6f5;border-radius:7px;padding:10px;font-size:12.5px;margin:8px 0 4px;border:1px solid var(--line)}}
.repnote{{font-size:11.5px;color:var(--faint);margin:0}}
.methodology{{margin-top:18px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 20px}}
.methodology ul{{margin:8px 0 0;padding-left:18px}}
.methodology li{{font-size:13.5px;color:var(--muted);margin-bottom:6px}}
.callout{{background:#fdf3e3;border:1px solid #f0dcb8;border-left:3px solid var(--warn);border-radius:8px;padding:12px 14px;font-size:13.5px;color:#6b4a12;margin:14px 0}}
/* audio */
.audio-panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;box-shadow:var(--shadow)}}
.audio-panel audio{{width:100%;margin:12px 0}}
.audio-panel ul{{padding-left:18px;margin:10px 0 0}}
.prec{{color:var(--faint);font-size:12px}}
/* calculator */
.calc{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;box-shadow:var(--shadow);max-width:640px}}
.calc label{{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}}
.calc select,.calc input{{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;font-size:15px;background:var(--paper)}}
.calc .row{{display:flex;gap:14px}}
.calc .row>div{{flex:1}}
.calc-out{{margin-top:16px;font-size:22px;font-weight:700;color:var(--teal)}}
.calc-out small{{display:block;font-size:12px;font-weight:400;color:var(--faint);margin-top:4px}}
.nojs{{font-size:13px;color:var(--muted);background:#f4f6f5;padding:10px 12px;border-radius:8px;margin-top:12px}}
/* generic list panels */
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 20px;box-shadow:var(--shadow)}}
.panel ul{{margin:0;padding-left:18px}}
.panel li{{font-size:14px;color:var(--muted);margin-bottom:8px}}
.skey{{font-size:11px;color:var(--faint);font-family:ui-monospace,Menlo,monospace}}
.panel li a{{word-break:break-all;overflow-wrap:anywhere}}
footer.pagefoot{{padding:30px 0;color:var(--faint);font-size:13px}}
@media (max-width:560px){{
  .cards{{grid-template-columns:1fr}}
  .hero{{padding:32px 0 20px}}
  .calc .row{{flex-direction:column;gap:0}}
}}
@media print{{
  .topnav,.controls,.chip{{display:none}}
  section{{break-inside:avoid;padding:18px 0}}
  .card,.panel,.concept{{break-inside:avoid;box-shadow:none}}
  body{{background:#fff}}
  details[open] pre,details pre{{display:block}}
  details summary{{display:none}}
}}
</style>
</head>
<body>
<nav class="topnav">
  <div class="wrap">
    <span class="brand">Speech · Voice · Realtime — Field Guide</span>
    <a href="#concepts">Concepts</a>
    <a href="#catalog">Catalog</a>
    <a href="#benchmark">Benchmark</a>
    <a href="#mai">MAI batch</a>
    <a href="#audio">Audio</a>
    <a href="#calc">Cost</a>
    <a href="#solutions">Solutions</a>
    <a href="#sources">Sources</a>
  </div>
</nav>

<header class="hero">
  <div class="wrap">
    <h1>{esc(meta['title'])}</h1>
    <p class="sub">{esc(meta['subtitle'])}</p>
    <div class="badges">
      <span class="pill"><b>{meta['card_count']}</b> models catalogued</span>
      <span class="pill"><b>6</b> realtime models benchmarked</span>
      <span class="pill"><b>{b['attempts_total']}</b> attempts · <b>{b['completed_total']}</b> completed</span>
      <span class="pill">verified <b>{esc(meta['verified_date'])}</b></span>
    </div>
    <div class="disclaimer">{esc(meta['disclaimer'])}</div>
  </div>
</header>

<section id="concepts">
  <div class="wrap">
    <h2>What each thing is</h2>
    <p class="lead">Voice agent vs ASR vs TTS, and streamed file output vs continuous audio — the distinctions that decide which model fits a workload.</p>
    <div class="concepts">{concepts_html}</div>
  </div>
</section>

<section id="catalog">
  <div class="wrap">
    <h2>Model catalog — {meta['card_count']} cards</h2>
    <p class="lead">18 GPT speech/voice/realtime models from the deduplicated catalog, plus 4 named Azure/MAI entries. Each card marks whether a fact was <b>measured</b> here, <b>documented</b> by the vendor, or <b>not tested</b>. Costs are OpenAI public list rates unless noted; Azure rates shown as <b>unknown</b> where not reverified.</p>
    <div class="controls">
      <input type="search" id="search" placeholder="Search models, capabilities, families…" aria-label="Search models">
      <div class="chips" id="chips">{cat_btns}<button class="chip reset" id="reset">Reset</button></div>
    </div>
    <div class="count" id="count"></div>
    <div class="cards" id="cards">
{cards_html}
    </div>
  </div>
</section>

<section id="benchmark">
  <div class="wrap">
    <h2>Six-model realtime transcription pilot</h2>
    <p class="lead">{esc(b['scope'])}</p>
    <div class="callout"><b>Read the denominators carefully.</b> {esc(b['denominator_rule'])} Passes are shown over <b>completed</b> runs. These are targeted diagnostics — not WER/DER, and there is no whole-meeting winner.</div>
    <div class="tablewrap">
    <table>
      <thead><tr>
        <th>Model</th><th>Completed / attempts</th><th>Setup-failed</th>
        <th>Frank continuity</th><th>3 voices distinct</th>
        <th>First-text median</th><th>First-text range</th><th>Representative output</th>
      </tr></thead>
      <tbody>
{bench_rows()}
      </tbody>
    </table>
    </div>
    <div class="callout">{esc(meth['base_outlier'])}</div>
    <div class="methodology">
      <h3 style="margin:0 0 4px;font-size:15px">Methodology &amp; caveats</h3>
      <ul>{meth_html}</ul>
      <p style="font-size:12px;color:var(--faint);margin:10px 0 0">Sources: <a href="#sources">rubric</a> and summary JSON under <code>docs/benchmarks/2026-10-14-realtime-pilot/</code>. Shortlist (1.5, 2.1) is tentative; full-meeting validation pending.</p>
    </div>
  </div>
</section>

<section id="mai">
  <div class="wrap">
    <h2>MAI batch &amp; the cross-request label problem</h2>
    <p class="lead">Azure MAI-Transcribe-2 and the classic continuous path, from saved evidence. The core finding: streamed/chunked requests reset speaker IDs every call, so the same person is relabeled across chunks.</p>
    <div class="panel">
      <ul>
        <li><b>Full file:</b> {mai['full_file']['audio_seconds']}s audio → HTTP 200 in {mai['full_file']['request_seconds']}s · {mai['full_file']['phrases']} phrases · {mai['full_file']['words']} words · {len(mai['full_file']['speaker_ids'])} unique speaker IDs ({esc(mai['full_file']['caveat'])})</li>
        <li><b>Chunked excerpt (s{mai['chunked_excerpt']['source_start_seconds']}–{mai['chunked_excerpt']['source_end_seconds']}, {mai['chunked_excerpt']['chunk_seconds']}s chunks):</b> {mai['chunked_excerpt']['lines']} lines, {mai['chunked_excerpt']['words']} words. {esc(mai['chunked_excerpt']['issue'])}</li>
        <li><b>15s vs 10s chunks:</b> {esc(mai['chunk_15_vs_10']['note'])}</li>
        <li><b>Rich config:</b> HTTP {mai['rich_config']['http']} with {esc(mai['rich_config']['options'])}; default is {esc(mai['rich_config']['default'])}. maxSpeakers {esc(mai['rich_config']['maxSpeakers'])}; confidence {esc(mai['rich_config']['confidence'])}.</li>
        <li><b>Quality metrics:</b> WER / DER = <span class="unknown">unknown</span> — {esc(mai['quality_metrics']['reason'])}</li>
        <li><b>CLI bug:</b> {esc(mai['cli_bug']['fixed'])} (was {esc(mai['cli_bug']['regression_before'])}); {esc(mai['cli_bug']['tests_after'])}.</li>
      </ul>
    </div>
  </div>
</section>

<section id="audio">
  <div class="wrap">
    <h2>Speaker-boundary clip (25s)</h2>
    <p class="lead">A 25-second excerpt beginning at source second 300, near the Frank boundary. User-confirmed annotations only — this is not full diarization ground truth. Private meeting audio, embedded for offline portability.</p>
    <div class="audio-panel">
      <audio controls preload="none" src="{audio_uri}">Your browser does not support embedded audio.</audio>
      <div><b>Clip:</b> {esc(hc['clip'])} · starts at source second {hc['source_start_seconds']}</div>
      <ul>{hc_annot}</ul>
      <p class="prec" style="margin-top:10px">{esc(hc['scope'])} At clip ~15s, Frank continues "You probably" → "worked with John"; the MAI request-local label flips 1→0 at the chunk boundary even though the speaker does not change.</p>
    </div>
  </div>
</section>

<section id="calc">
  <div class="wrap">
    <h2>Cost calculator</h2>
    <p class="lead">{esc(cc['note'])}</p>
    <div class="calc">
      <label for="calc-model">Model family</label>
      <select id="calc-model"></select>
      <div class="row">
        <div><label for="calc-in">Incoming audio (min)</label><input id="calc-in" type="number" value="30" min="0" step="1"></div>
        <div><label for="calc-out">Outgoing audio (min)</label><input id="calc-out" type="number" value="30" min="0" step="1"></div>
      </div>
      <div class="calc-out" id="calc-out-val">—<small>Excludes history text, reasoning, transcription, and tools.</small></div>
      <div class="nojs">Worked example (JS off): {esc(cc['worked_example']['scenario'])} — GPT-Realtime full ≈ <b>${cc['worked_example']['rt_full_usd']}</b>, mini ≈ <b>${cc['worked_example']['rt_mini_usd']}</b>. Based on documented {cc['conversion']['input_tok_per_s']} input / {cc['conversion']['output_tok_per_s']} output audio tokens per second.</div>
    </div>
  </div>
</section>

<section id="solutions">
  <div class="wrap">
    <h2>Proposed solutions &amp; defaults</h2>
    <p class="lead">Cross-request speaker persistence is unsolved here. These approaches are proposed, none implemented. User defaults were left unchanged.</p>
    <div class="concepts">
      <div class="panel"><h3 style="margin:0 0 8px;font-size:15px">Candidate approaches</h3><ul>{sol_html}</ul></div>
      <div class="panel"><h3 style="margin:0 0 8px;font-size:15px">Defaults (unchanged) &amp; cleanup</h3><ul>{dfl_html}</ul></div>
    </div>
  </div>
</section>

<section id="sources">
  <div class="wrap">
    <h2>Sources &amp; methodology</h2>
    <p class="lead">Every pricing/claim traces to one of these vendor docs (dates checked {esc(meta['verified_date'])}, not assumed published) or to a saved evidence file in the repo. Clickable:</p>
    <div class="panel"><ul>{src_html}</ul></div>
    <p class="prec" style="margin-top:12px">Benchmark evidence files live under <code>docs/benchmarks/2026-10-14-realtime-pilot/</code> (summary, rubric, all-results, fixture, human-speaker-check, cleanup). Raw transcript quotes are machine-labeled; only selected outputs are shown, not full raw network events.</p>
  </div>
</section>

<footer class="pagefoot"><div class="wrap">Generated by <code>{esc(meta['generated_by'])}</code> · verified {esc(meta['verified_date'])} · private/internal artifact · core content renders with JavaScript disabled.</div></footer>

<script>
(function(){{
  // ---- catalog filter ----
  var search=document.getElementById('search');
  var cards=Array.prototype.slice.call(document.querySelectorAll('#cards .card'));
  var count=document.getElementById('count');
  var chips=document.getElementById('chips');
  var activeCat=null;
  function apply(){{
    var q=(search.value||'').toLowerCase().trim();
    var shown=0;
    cards.forEach(function(c){{
      var okCat=!activeCat||c.getAttribute('data-cat')===activeCat;
      var okQ=!q||c.getAttribute('data-name').indexOf(q)>-1||c.textContent.toLowerCase().indexOf(q)>-1;
      var vis=okCat&&okQ; c.style.display=vis?'':'none'; if(vis)shown++;
    }});
    count.textContent=shown+' of '+cards.length+' models shown';
  }}
  search.addEventListener('input',apply);
  chips.addEventListener('click',function(e){{
    var b=e.target.closest('button'); if(!b)return;
    if(b.id==='reset'){{activeCat=null;search.value='';chips.querySelectorAll('.chip').forEach(function(x){{x.setAttribute('aria-pressed','false')}});apply();return;}}
    var cat=b.getAttribute('data-filter-cat'); if(!cat)return;
    if(activeCat===cat){{activeCat=null;b.setAttribute('aria-pressed','false');}}
    else{{activeCat=cat;chips.querySelectorAll('.chip').forEach(function(x){{x.setAttribute('aria-pressed','false')}});b.setAttribute('aria-pressed','true');}}
    apply();
  }});
  apply();

  // ---- cost calculator ----
  var CC={cc_json};
  var sel=document.getElementById('calc-model');
  CC.options.forEach(function(o,i){{var op=document.createElement('option');op.value=i;op.textContent=o.label;sel.appendChild(op);}});
  var inp=document.getElementById('calc-in'),outp=document.getElementById('calc-out'),outv=document.getElementById('calc-out-val');
  function calc(){{
    var o=CC.options[sel.value||0];
    var mi=parseFloat(inp.value)||0, mo=parseFloat(outp.value)||0;
    var total=mi*o.audio_in_per_min_usd+mo*o.audio_out_per_min_usd;
    outv.innerHTML='$'+total.toFixed(2)+'<small>'+mi+' min in ($'+o.audio_in_per_min_usd+'/min) + '+mo+' min out ($'+o.audio_out_per_min_usd+'/min). Excludes history text, reasoning, transcription, tools. List rates, not an Azure bill.</small>';
  }}
  sel.addEventListener('change',calc);inp.addEventListener('input',calc);outp.addEventListener('input',calc);
  calc();
}})();
</script>
</body>
</html>'''

OUT.write_text(HTML)
print(f"wrote {OUT} ({len(HTML):,} bytes, audio {len(audio_b64):,} b64 chars)")
