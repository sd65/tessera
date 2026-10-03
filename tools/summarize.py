"""Run the `prompt` file through the local Ollama models and write a
side-by-side HTML comparison.

This is an offline experiment over `tessera.db`, not part of the `tessera`
package proper: it only reads transcripts, never writes the corpus, and lives
outside `tessera/` so the shipped UI and its test suites are untouched.

Four variants are cached, per transcript:

    e4b         gemma4:e4b-mlx   thinking off
    12b         gemma4:12b-mlx   thinking off
    e4b-think   gemma4:e4b-mlx   thinking on
    12b-think   gemma4:12b-mlx   thinking on

Usage, from the repo root:

    uv run python tools/summarize.py pilot [--think]   # shortest transcript
    uv run python tools/summarize.py run [--think]     # every transcript
    uv run python tools/summarize.py render            # rebuild HTML from cache

`run` and `pilot` default to the thinking-off variants; `--think` runs the
thinking-on ones. Results are cached per (video, variant) under `summaries/raw/`,
so a re-run and `render` are cheap and resumable. `--force` re-runs regardless.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# `tools/` is not a package; put it on the path so `normalize` is importable
# however the script is launched.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import normalize as normalizer  # noqa: E402

from tessera import store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompt"
OUT_DIR = ROOT / "summaries"
RAW_DIR = OUT_DIR / "raw"

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

# Deterministic, comparable runs.
TEMPERATURE = 0.0
SEED = 42
NUM_PREDICT = 2048
# Thinking spends output tokens on the reasoning before the answer, so it gets a
# much larger budget; the context is sized from whichever budget applies. 12b
# under thinking can burn 8k tokens on reasoning alone before it starts writing.
NUM_PREDICT_THINK = 32768
KEEP_ALIVE = "10m"

# Context window: sized per call from the transcript length, kept well above the
# largest transcript but below either model's ceiling. `// 3` over-estimates
# tokens for English on purpose. The ceiling is generous because a thinking
# variant's prompt plus its reasoning budget can exceed 64k.
MIN_CTX = 32768
MAX_CTX = 131072
CTX_STEP = 8192

TIMEOUT = 3600  # seconds; a 134k-char transcript under thinking is the slow one


@dataclass(frozen=True)
class Variant:
    key: str          # stable id, used as the <select> value and in the filename
    tag: str          # Ollama model tag
    think: bool
    label: str        # shown in the UI


VARIANTS: list[Variant] = [
    Variant("e4b", "gemma4:e4b-mlx", False, "gemma4:e4b-mlx"),
    Variant("12b", "gemma4:12b-mlx", False, "gemma4:12b-mlx"),
    Variant("e4b-think", "gemma4:e4b-mlx", True, "gemma4:e4b-mlx · thinking"),
    Variant("12b-think", "gemma4:12b-mlx", True, "gemma4:12b-mlx · thinking"),
]

OFF = [variant for variant in VARIANTS if not variant.think]
THINK = [variant for variant in VARIANTS if variant.think]

DEFAULT_LEFT = "e4b"
DEFAULT_RIGHT = "12b"

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; background: #f7f6f4; color: #1b1a17;
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: #0b5cad; }
.masthead { padding: 18px 24px 12px; border-bottom: 1px solid #e2dfda; background: #fff; }
.masthead h1 { margin: 6px 0 4px; font-size: 20px; line-height: 1.25; }
.masthead .sub { margin: 0; color: #6b675f; font-size: 13px; }
.masthead .norm {
  margin: 10px 0 0; padding: 8px 10px; background: #fbfaf8; border: 1px solid #eceae5;
  border-radius: 8px; color: #49463f; font-size: 13px;
}
.masthead .norm code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.back { font-size: 13px; text-decoration: none; color: #6b675f; }
.controls { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; }
.controls label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #49463f; }
.controls select {
  font: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid #d8d4cc;
  border-radius: 6px; background: #fff; color: #1b1a17;
}
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px; align-items: start; }
.col { background: #fff; border: 1px solid #e2dfda; border-radius: 10px; overflow: hidden; }
.colhead {
  position: sticky; top: 0; z-index: 1; display: flex; flex-wrap: wrap; gap: 8px;
  align-items: baseline; justify-content: space-between;
  padding: 10px 14px; background: #fbfaf8; border-bottom: 1px solid #eceae5;
}
.model { font-weight: 600; font-size: 14px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; }
.badge { font-size: 12px; padding: 2px 7px; border-radius: 999px; background: #f0eee9; color: #49463f; white-space: nowrap; }
.badge.time { background: #e4efe3; color: #1f5c22; font-weight: 600; }
.out { margin: 0; padding: 14px; white-space: pre-wrap; overflow-wrap: break-word; font: inherit; }
.colfoot { padding: 8px 14px 12px; color: #8a857b; font-size: 12px; }
.empty, .error { padding: 14px; }
.error { color: #a11; background: #fdf0f0; }
.muted { color: #8a857b; }
.err { color: #a11; }
.foot { padding: 12px 24px 32px; color: #8a857b; font-size: 12px; }
.indexwrap { padding: 16px; }
.card { background: #fff; border: 1px solid #e2dfda; border-radius: 10px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #eceae5; vertical-align: top; }
tr:last-child td { border-bottom: 0; }
th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ch { display: block; color: #8a857b; font-size: 11px; font-weight: 400; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
"""

# Client-side re-render of the two columns. Kept as a plain string (no f-string)
# so its braces are literal; DATA and DEFAULTS are prepended in render_video.
JS = """
const $ = (id) => document.getElementById(id);
function fmtTime(s) {
  if (s == null) return "\\u2014";
  if (s < 60) return s.toFixed(1) + " s";
  const m = Math.floor(s / 60), r = s - 60 * m;
  if (m < 60) return m + " m " + r.toFixed(1).padStart(4, "0") + " s";
  const h = Math.floor(m / 60);
  return h + " h " + String(m % 60).padStart(2, "0") + " m";
}
function tokRate(d) {
  if (!d.output_tokens || !d.eval_ns) return "";
  return (d.output_tokens / (d.eval_ns / 1e9)).toFixed(1) + " tok/s";
}
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}
function details(d) {
  if (!d || d.missing || d.error) return "";
  const parts = [];
  if (d.num_ctx) parts.push("ctx " + d.num_ctx.toLocaleString());
  if (d.load_ns) parts.push("load " + fmtTime(d.load_ns / 1e9));
  if (d.prompt_ns) parts.push("prefill " + fmtTime(d.prompt_ns / 1e9));
  if (d.think) parts.push("thinking on");
  if (d.thinking) parts.push("reasoning hidden");
  if (d.finished) parts.push(String(d.finished).replace("T", " ").slice(0, 19));
  return parts.join(" \\u00b7 ");
}
function renderCol(host, key) {
  host.textContent = "";
  const d = DATA[key] || { missing: true, label: key };
  const head = el("div", "colhead");
  head.appendChild(el("span", "model", d.label));
  const badges = el("span", "badges");
  if (!d.missing && d.wall_seconds != null) {
    badges.appendChild(el("span", "badge time", fmtTime(d.wall_seconds)));
  }
  if (d.error) {
    badges.appendChild(el("span", "badge", "error"));
  } else if (!d.missing) {
    const rate = tokRate(d);
    if (rate) badges.appendChild(el("span", "badge", rate));
    if (d.prompt_tokens != null) {
      badges.appendChild(el("span", "badge",
        d.prompt_tokens.toLocaleString() + " \\u2192 " + (d.output_tokens || 0).toLocaleString() + " tok"));
    }
  }
  head.appendChild(badges);
  host.appendChild(head);

  if (d.missing) host.appendChild(el("div", "empty", "not run yet"));
  else if (d.error) host.appendChild(el("div", "error", "\\u26a0 " + d.error));
  else if (!d.response) host.appendChild(el("div", "error",
    "\\u26a0 no answer — the reasoning used the whole output budget"));
  else host.appendChild(el("pre", "out", d.response));
  host.appendChild(el("div", "colfoot", details(d)));
}
function renderAll() {
  renderCol($("col-left"), $("left").value);
  renderCol($("col-right"), $("right").value);
}
$("left").value = DEFAULTS.left;
$("right").value = DEFAULTS.right;
$("left").addEventListener("change", renderAll);
$("right").addEventListener("change", renderAll);
renderAll();
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)} m {rest:04.1f} s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours} h {minutes:02d} m"


def _tok_rate(result: dict) -> str:
    tokens, nanos = result.get("output_tokens"), result.get("eval_ns")
    if not tokens or not nanos:
        return ""
    return f"{tokens / (nanos / 1e9):.1f} tok/s"


def _slug(tag: str) -> str:
    return tag.replace(":", "_").replace("/", "_")


def _raw_path(video_id: str, variant: Variant) -> Path:
    suffix = "__think" if variant.think else ""
    return RAW_DIR / f"{video_id}__{_slug(variant.tag)}{suffix}.json"


def _ctx_for(prompt_text: str, transcript: str, num_predict: int) -> int:
    need = (len(prompt_text) + len(transcript)) // 3 + num_predict + 2048
    ctx = MIN_CTX
    while ctx < need:
        ctx += CTX_STEP
    return min(ctx, MAX_CTX)


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").rstrip()


def load_entries() -> list[dict]:
    """Every transcript that has text, shortest first."""
    conn = store.connect()
    try:
        rows = conn.execute(
            """
            SELECT v.video_id, v.title, v.channel_name, v.description, v.published_at, v.url,
                   t.lang, t.kind, t.text
            FROM transcripts t
            JOIN videos v ON v.video_id = t.video_id
            WHERE t.status = 'ok' AND t.text IS NOT NULL AND t.text <> ''
            ORDER BY length(t.text) ASC
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def call_model(variant: Variant, instructions: str, transcript: str) -> dict:
    num_predict = NUM_PREDICT_THINK if variant.think else NUM_PREDICT
    num_ctx = _ctx_for(instructions, transcript, num_predict)
    body = {
        "model": variant.tag,
        "prompt": f"{instructions}\n\nTRANSCRIPT:\n{transcript}",
        "stream": False,
        "think": variant.think,
        "keep_alive": KEEP_ALIVE,
        "options": {
            "temperature": TEMPERATURE,
            "seed": SEED,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"model": variant.tag, "error": str(exc), "wall_seconds": time.monotonic() - started}

    wall = time.monotonic() - started
    response = payload.get("response") or ""
    output_tokens = payload.get("eval_count")
    return {
        "model": variant.tag,
        "variant": variant.key,
        "think": variant.think,
        "response": response,
        "thinking": payload.get("thinking") or "",
        "truncated": not response and output_tokens is not None and output_tokens >= num_predict,
        "error": None,
        "wall_seconds": wall,
        "total_ns": payload.get("total_duration"),
        "load_ns": payload.get("load_duration"),
        "prompt_tokens": payload.get("prompt_eval_count"),
        "prompt_ns": payload.get("prompt_eval_duration"),
        "output_tokens": output_tokens,
        "eval_ns": payload.get("eval_duration"),
        "num_ctx": num_ctx,
        "finished": _now(),
    }


def _metadata_block(entry: dict) -> str:
    """The METADATA block handed to the model (recommendation 2)."""
    lines = [
        f"title: {entry.get('title') or ''}",
        f"channel: {entry.get('channel_name') or ''}",
    ]
    description = normalizer.clean_metadata(entry.get("description"))
    if description:
        lines.append("description:")
        lines.append(description)
    return "METADATA:\n" + "\n".join(lines)


def _norm_note(result: dict) -> str:
    """One line of grounding provenance for the page header, or "" for old runs."""
    if "normalize" not in result and "metadata" not in result:
        return ""
    parts = ["metadata " + ("on" if result.get("metadata") else "off")]
    info = result.get("normalize") or {}
    if not info.get("applied"):
        parts.append("normalization off")
    else:
        summary = info.get("summary") or []
        if not summary:
            parts.append("normalization: metadata glossary, no corrections")
        else:
            chips = ", ".join(
                f"<code>{html.escape(item['source'])} \u2192 {html.escape(item['target'])}</code>"
                f" \u00d7{item['count']}"
                for item in summary[:8]
            )
            if len(summary) > 8:
                chips += f" (+{len(summary) - 8} more)"
            parts.append("normalization: " + chips)
    return "ASR grounding \u2014 " + " \u00b7 ".join(parts)


def generate(
    entry: dict,
    variant: Variant,
    force: bool,
    normalize_on: bool = True,
    metadata_on: bool = True,
) -> dict:
    """Return a cached result, or run the variant and cache a fresh one."""
    path = _raw_path(entry["video_id"], variant)
    if path.exists() and not force:
        result = json.loads(path.read_text(encoding="utf-8"))
        print(f"    {variant.label:<28} cached  {_fmt_time(result.get('wall_seconds'))}")
        return result

    # Recommendation 1: repair only names the video's own metadata already
    # spells, deterministically and before the model ever sees the text. The
    # stored transcript is never touched; this is a derived copy.
    transcript = entry["text"]
    normalize_info: dict = {"applied": False, "summary": []}
    if normalize_on:
        transcript, edits = normalizer.normalize(transcript, entry)
        normalize_info = {
            "applied": True,
            "summary": normalizer.summarize(edits),
        }

    # Recommendation 2: the video's own metadata, for the PROPER NOUNS rule that
    # now lives in `prompt`. Only the block is appended here.
    instructions = load_prompt()
    if metadata_on:
        instructions = f"{instructions}\n\n{_metadata_block(entry)}"

    print(f"    {variant.label:<28} running …", flush=True)
    result = call_model(variant, instructions, transcript)
    result["video_id"] = entry["video_id"]
    result["title"] = entry["title"]
    result["metadata"] = metadata_on
    result["normalize"] = normalize_info
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    state = "error" if result.get("error") else "ok"
    detail = result["error"] if result.get("error") else _tok_rate(result)
    print(f"    {variant.label:<28} {state:<6} {_fmt_time(result.get('wall_seconds'))}  {detail}")
    return result


def load_results(entry: dict) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for variant in VARIANTS:
        path = _raw_path(entry["video_id"], variant)
        if path.exists():
            results[variant.key] = json.loads(path.read_text(encoding="utf-8"))
    return results


def render_video(entry: dict, results: dict[str, dict]) -> str:
    data: dict[str, dict] = {}
    for variant in VARIANTS:
        result = results.get(variant.key)
        item: dict = {"label": variant.label, "think": variant.think}
        if result is None:
            item["missing"] = True
        else:
            for field in (
                "wall_seconds", "error", "truncated", "num_ctx", "load_ns", "prompt_ns",
                "prompt_tokens", "output_tokens", "eval_ns", "thinking", "finished",
            ):
                item[field] = result.get(field)
            item["response"] = result.get("response") or ""
        data[variant.key] = item

    payload = json.dumps(data, ensure_ascii=False)
    # A literal "</script>" (or a line separator) in a model's output must not
    # break out of the inline script.
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    defaults = json.dumps({"left": DEFAULT_LEFT, "right": DEFAULT_RIGHT})

    options = "".join(
        f'<option value="{html.escape(variant.key)}">{html.escape(variant.label)}</option>'
        for variant in VARIANTS
    )
    script = f"<script>const DATA = {payload};\nconst DEFAULTS = {defaults};\n{JS}</script>"

    lang = f'{entry.get("lang") or "?"}/{entry.get("kind") or "?"}'
    published = (entry.get("published_at") or "")[:10]
    sample = next((results[variant.key] for variant in VARIANTS if variant.key in results), {})
    note = _norm_note(sample)
    note_html = f'\n  <p class="norm">{note}</p>' if note else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(entry["title"])} — model comparison</title>
<style>{CSS}</style>
</head>
<body>
<header class="masthead">
  <a class="back" href="index.html">← all summaries</a>
  <h1>{html.escape(entry["title"])}</h1>
  <p class="sub">{html.escape(entry.get("channel_name") or "")} · {html.escape(lang)} ·
    {html.escape(published)} ·
    <a href="{html.escape(entry.get("url") or "")}" target="_blank" rel="noopener">youtube</a></p>
  <div class="controls">
    <label>left <select id="left">{options}</select></label>
    <label>right <select id="right">{options}</select></label>
  </div>{note_html}
</header>
<main class="grid">
  <section class="col" id="col-left"></section>
  <section class="col" id="col-right"></section>
</main>
<footer class="foot">generated {html.escape(_now())}</footer>
{script}
</body>
</html>
"""


def write_video_page(entry: dict, results: dict[str, dict]) -> Path:
    path = OUT_DIR / f"{entry['video_id']}.html"
    path.write_text(render_video(entry, results), encoding="utf-8")
    return path


def render_index(entries: list[dict]) -> str:
    rows: list[str] = []
    for entry in entries:
        results = load_results(entry)
        cells: list[str] = []
        for variant in VARIANTS:
            result = results.get(variant.key)
            if result is None:
                cells.append('<td class="num muted">—</td>')
            elif result.get("error"):
                cells.append('<td class="num err">error</td>')
            else:
                cells.append(f'<td class="num">{_fmt_time(result.get("wall_seconds"))}</td>')
        rows.append(
            f'<tr><td><a href="{entry["video_id"]}.html">{html.escape(entry["title"])}</a>'
            f'<span class="ch">{html.escape(entry.get("channel_name") or "")} · '
            f'{len(entry.get("text") or ""):,} chars</span></td>{"".join(cells)}</tr>'
        )
    header = "".join(
        f'<th class="num">{html.escape(variant.label)}<br><span class="ch">time</span></th>'
        for variant in VARIANTS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Summaries — model comparison</title>
<style>{CSS}</style>
</head>
<body>
<header class="masthead">
  <h1>Summaries — model comparison</h1>
  <p class="sub">{len(entries)} transcripts · prompt <code>prompt</code> ·
    temperature {TEMPERATURE} · seed {SEED}</p>
</header>
<div class="indexwrap">
  <div class="card">
    <table>
      <thead><tr><th>video</th>{header}</tr></thead>
      <tbody>
        {"".join(rows)}
      </tbody>
    </table>
  </div>
</div>
<footer class="foot">generated {html.escape(_now())}</footer>
</body>
</html>
"""


def write_index(entries: list[dict]) -> Path:
    path = OUT_DIR / "index.html"
    path.write_text(render_index(entries), encoding="utf-8")
    return path


def render_all(entries: list[dict]) -> int:
    rendered = 0
    for entry in entries:
        results = load_results(entry)
        if results:
            write_video_page(entry, results)
            rendered += 1
    write_index(entries)
    return rendered


def cmd_pilot(
    entries: list[dict], force: bool, think: bool, normalize_on: bool, metadata_on: bool
) -> int:
    variants = THINK if think else OFF
    entry = min(entries, key=lambda item: len(item.get("text") or ""))
    print(f"\npilot: {entry['video_id']}  {entry['title']}  "
          f"({len(entry['text']):,} chars)")

    for variant in variants:
        generate(entry, variant, force, normalize_on, metadata_on)

    page = write_video_page(entry, load_results(entry))
    write_index(entries)
    print(f"\n  {page}")
    return 0


def cmd_run(
    entries: list[dict], force: bool, think: bool, normalize_on: bool, metadata_on: bool
) -> int:
    variants = THINK if think else OFF
    # Variant-major: all transcripts through one variant, then the next. Only one
    # model is resident at a time, which is what the machine's memory wants.
    for variant in variants:
        print(f"\n{variant.label}  —  {len(entries)} transcript(s)")
        for index, entry in enumerate(entries, start=1):
            print(f"  [{index}/{len(entries)}] {entry['video_id']}  "
                  f"{len(entry['text']):,} chars")
            generate(entry, variant, force, normalize_on, metadata_on)

    rendered = render_all(entries)
    print(f"\nrendered {rendered} page(s) + index into {OUT_DIR}")
    return 0


def cmd_render(entries: list[dict]) -> int:
    rendered = render_all(entries)
    print(f"rendered {rendered} page(s) + index into {OUT_DIR}")
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--force", action="store_true", help="re-run even if a cached result exists")
    common.add_argument("--think", action="store_true", help="use the thinking-on variants")
    common.add_argument(
        "--no-normalize",
        dest="normalize",
        action="store_false",
        help="feed the transcript verbatim (skip the metadata-grounded normaliser)",
    )
    common.add_argument(
        "--no-metadata",
        dest="metadata",
        action="store_false",
        help="do not add the METADATA block to the prompt",
    )
    common.add_argument(
        "--video-id",
        dest="video_ids",
        action="append",
        default=None,
        metavar="ID",
        help="only this transcript; repeat for more than one",
    )

    parser = argparse.ArgumentParser(
        prog="summarize",
        description="Run the `prompt` through the local Ollama models over tessera.db.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pilot", parents=[common], help="run the shortest transcript through both models")
    sub.add_parser("run", parents=[common], help="run every transcript, one variant at a time")
    sub.add_parser("render", parents=[common], help="rebuild the HTML from cached JSON")

    args = parser.parse_args(argv)

    if not PROMPT_PATH.exists():
        print(f"error: no prompt file at {PROMPT_PATH}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    entries = load_entries()
    if not entries:
        print("error: no transcripts with text in the corpus", file=sys.stderr)
        return 1

    if args.video_ids:
        wanted = set(args.video_ids)
        entries = [entry for entry in entries if entry["video_id"] in wanted]
        missing = wanted - {entry["video_id"] for entry in entries}
        if missing:
            print(f"error: not in the corpus: {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    if args.command == "pilot":
        return cmd_pilot(entries, args.force, args.think, args.normalize, args.metadata)
    if args.command == "run":
        return cmd_run(entries, args.force, args.think, args.normalize, args.metadata)
    if args.command == "render":
        return cmd_render(entries)

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
