# tessera

Local corpus of YouTube videos + transcripts. Runs locally on macOS. No API key,
no OAuth, no quota. Nothing leaves the machine, with one deliberate exception:
`tessera ui` plays videos in a `youtube-nocookie.com` embed.

`fetch` also summarises: after each transcript is stored, it is normalised with
metadata from the video's own title, channel and description, then sent whole to
a local Ollama model. The summary and the normalised text are stored beside the
raw transcript, and the UI shows the summary first with the full transcript
behind a disclosure. Ranking and recommendations are still out of scope.

## Setup

Transcripts need nothing but the Python dependencies. Summaries additionally need
[Ollama](https://ollama.com) running locally with the production model pulled:

```bash
ollama pull gemma4:e4b-mlx
```

Without Ollama, `fetch` still stores transcripts; it records the summary as
`error` and retries it on a later run. See "Summaries" below.

```bash
uv venv --python 3.14
uv sync
```

`uv venv --python 3.14` finds the Homebrew interpreter already on the machine and
symlinks it, rather than downloading its own managed CPython. If `uv sync`
complains about a missing `cp314` wheel for a C extension, fall back:

```bash
uv venv --python 3.13 --clear && uv sync
```

Nothing here needs 3.14 specifically.

## Commands

```
uv run tessera fetch
uv run tessera status
uv run tessera ui
```

All take no options. No scheduling yet — it is manual.

`ui` serves the corpus read-only at <http://127.0.0.1:8787> and opens a browser.
It is the only command that touches the network in any way, and only to play a
video: the embed is `youtube-nocookie.com` and the page sends it an origin and
nothing more — never a path, never the `?v=` deep link. That is
`Referrer-Policy: origin-when-cross-origin`, and it is not decoration: YouTube
refuses to configure its embed when the request carries no `Referer` at all, and
says so on the video as "Error 153". The stricter `no-referrer` this used to send
breaks the player outright. The server binds loopback only, and the database is
opened with SQLite's `mode=ro`, so there is no code path through it that can
write. There is no "run fetch" button; `fetch` stays a command you type, because
a run can take minutes and should not be something a stray click starts.

## Channels

`channels.json` is a plain list of YouTube channel IDs, edited by hand:

```json
[
  { "id": "UCODHrzPMGbNv67e84WDZhQQ", "name": "fern" },
  { "id": "UCxr2d4As312LulcajAkKJYw", "name": "Townsends" }
]
```

`name` is cosmetic, for display in `status`. It is not duplicated into the
database — this file is the source of truth.

## First run

```bash
uv sync
# set WINDOW_DAYS = 90 in tessera/config.py
uv run tessera fetch
# set it back to 7
uv run tessera status
```

Expect roughly **15 from Townsends and ~13 from fern**. The RSS cap means the
Townsends count is incomplete — a 90-day backfill returns 15 videos instead of the
~41 that exist in that window.

## Modules

| module | does |
|---|---|
| `cli.py` | argparse, dispatch, printing. No logic beyond wiring. |
| `config.py` | DB path, `WINDOW_DAYS`, `MAX_ATTEMPTS`, politeness delay, UI host/port, and the model, prompt path and context constants. |
| `rss.py` | Fetch + parse the Atom feed -> list of video dicts. |
| `transcript.py` | Fetch one video's transcript, classify failures, clean text. |
| `normalize.py` | Metadata-grounded ASR name repair. Pure and deterministic. |
| `summarize.py` | Normalise then summarise one transcript via the local Ollama model. |
| `store.py` | Schema, upserts, selection queries, everything `status` prints. |
| `web.py` | Read-only HTTP for `ui`. Routing and JSON, nothing else. |
| `ui/` | The browser view: four Web Components, one stylesheet, no build step. |

## The browser view

Three panes — channel picker and videos on the left, the YouTube embed in the
middle, the summary and its transcript on the right. The theme is light, and only
light.

The list shows the videos that have a transcript — 14 today. A video with no
transcript (blocked, disabled, …) is not a row at all: the sidebar asks the server
for transcripts only, so it never arrives and the count and pager agree. Narrow
the list by typing in the channel box: it is a native searchable combo, so the
browser owns the typeahead and the keyboard, and an empty box means all channels.
Rows carry a title, the channel, and how long ago it was published ("12 days ago")
rather than a date; the caption source (`auto` or `manual`) lives in the transcript
header next to the language, not on the row.

The right pane shows the summary first, parsed into section headings, paragraphs
and bullet lists. The model's output is never interpolated as HTML: it is built
from text nodes, which is what keeps the CSP (no inline script, no inline style)
meaningful. Below the summary is a collapsed **Transcript** disclosure; opening it
reveals the *normalised* transcript — the same text the model was fed, so a quote
in the summary is searchable in what you expand. The transcript arrives as one
unbroken run of text and is rendered as received: no paragraphing, no sentence
splitting, no timestamps to sync and no cue table. It is real DOM text, so browser
find-in-page is the search feature.

When Ollama has not answered yet, the pane says so, with the stored reason, and
still offers the transcript.

Two endpoints, both `GET`:

| route | returns |
|---|---|
| `/api/videos?channel=&status=&limit=&offset=` | `{rows, meta}` — one page of metadata |
| `/api/videos/<id>` | one video, including `text`, the summary, and `normalized_text` |

The list payload carries no `text` and no `description`. The longest transcript
here is 134,632 characters and a stored RSS blurb runs to 4KB, so a list of 200
rows would otherwise ship megabytes of payload for metadata.

`meta` publishes `total`, `limit`, `offset`, `has_more`, and the channel list. The
client hardcodes none of it: `total` counts the *filtered* set, and without it a
client holding one page cannot know whether more pages exist or what "showing
21–40 of 63" means. The one exception is the `ok` status itself, which the client
writes down rather than fetching and sends as the sidebar's `?status=ok` — it is
the only filter value the UI needs before it has a response, and publishing it cost
a request per page load to learn a literal.

Read-only is enforced by the database rather than by the routing. There are no
write verbs and no write routes, and `store.connect_readonly()` opens the file
with SQLite's `mode=ro`, so a bug in a handler cannot mutate `tessera.db` — the
write would be refused by the engine. The CSP header is the other half: it names
`youtube-nocookie.com` as the only reachable origin, and forbids inline script
and inline style, which is why all CSS lives in one module and every component
in its own file.

## How it works

`fetch` runs three selection paths, then works through whatever they return.

**Path 1 — ingest.** Fetch each channel's RSS, keep entries within `WINDOW_DAYS`,
skip Shorts, insert any video not already in `videos`.

**Path 2 — retry.** Select transcripts whose `status` is retryable — `blocked`,
`error`, or `pending` — and whose `attempts < MAX_ATTEMPTS`. This ignores publish
date on purpose — a video that came back `blocked` days ago would otherwise never
be selected again, becoming permanently unreachable.

**Path 3 — summarise.** Select transcripts that are `ok` and have no usable
summary yet: the first run after upgrading an old database, and the repair path
when Ollama was down for an earlier one. These need no fetch. A summary row is
retried while its `attempts < SUMMARY_MAX_ATTEMPTS`; a transport failure does not
count, so an outage never retires a video.

All three paths mean `fetch` is idempotent *and* self-healing. There is no separate
retry command to remember.

**Summary runs inline, one video at a time.** After a transcript is stored, `fetch`
immediately normalises and summarises it — not in a second pass. That is
deliberate: the model call takes tens of seconds, and the time it spends is extra
spacing between two YouTube requests on top of `POLITE_DELAY`. Transcript work
comes first; the summary-only backlog runs after, and still runs even if a block
stopped the transcript loop, because it touches no network but Ollama.

**Claiming happens up front.** Every selected video is inserted into `transcripts`
as `pending` before the first request, not one at a time as its turn comes. If a
video were reserved lazily and the run stopped early, it would sit in `videos`
with no `transcripts` row — and since path 1 filters on `videos` while path 2
needs that row, neither query would ever return it again. Leftovers stay `pending`
at `attempts = 0`, which path 2 picks up on the next run.

**A block stops the run.** The first `IpBlocked` ends the fetch immediately.
Every subsequent request would block too, so continuing just burns the queue.
Measured ceiling: 19 transcripts in 109s at a 5s interval, then a hard block.

**Blocks are not attempts.** `attempts` counts real tries, so a `blocked` result
leaves the counter alone. YouTube refusing to answer says nothing about the video,
and counting it let a ban drive good videos to `MAX_ATTEMPTS` and retire them
permanently. Only `error` — a failure that is actually about the video — now
exhausts.

## `status` values

| value | meaning | retried? |
|---|---|---|
| `ok` | transcript stored | — |
| `no_captions` | video genuinely has no captions | no, permanent |
| `disabled` | captions exist but the author turned them off | no, permanent |
| `unavailable` | private, removed, or age/region blocked | no, permanent |
| `blocked` | YouTube rate-limited or challenged us | **yes**, and does not count as an attempt |
| `error` | anything else, including a bug in our cleanup code | **yes** |

The split between the last two and the first three is load-bearing — it is
literally the WHERE clause for path 2.

## Summaries

After a transcript is stored, `fetch`:

1. loads the video's own `title`, `channel_name` and `description`;
2. runs `normalize()` — a pure, deterministic pass that repairs only names the
   video's metadata already spells, and never writes `transcripts.text`;
3. sends the normalised text whole, with the `prompt` file and a `METADATA`
   block, to the local model: `gemma4:e4b-mlx`, thinking off, `temperature 0`,
   `seed 42`;
4. stores the answer in `summaries`, beside the raw transcript.

That configuration is the one the benchmark settled on: fastest, every run
complete, and its one real weakness — copying ASR spellings — is exactly what the
metadata grounding fixes. Thinking was slower, made naming worse, and on the
larger model silently spent the whole output budget on reasoning.

The `summaries` table holds one row per video: `status` (`ok` or `error`), the
model output in `text`, the `normalized_text` the model was fed (and the UI shows),
the `model` tag, `attempts`, the stored `error`, and `generated_at`.
`normalized_text` is written even when the model call fails, so the grounded
transcript stays readable while the summary is retried.

Editing `prompt` does not rewrite history: existing summaries are kept, and a new
wording applies only to summaries generated afterwards.

## Known limits

- **Summaries need Ollama.** Without it, `fetch` still stores transcripts, but
  every summary is recorded as `error`. They are retried on the next run, the
  reason is printed per video, and the UI shows "Summary unavailable" with the
  transcript still readable behind the disclosure.
- **The RSS endpoint 404s, intermittently, for everyone.** Observed 2026-10-03:
  both channels, plus Google Developers and other large channels, all returned
  `HTTP 404` with `server: YouTube RSS Feeds server` — so the feed server is
  reached and is refusing the request itself. Channel pages return 200 from the
  same IP, so this is not our block. Not new: open reports on newsboat#3269,
  n8n, and r/youtube run from Dec 2025 onward, and it comes and goes —
  reporters describe it clearing on its own hours later, sometimes time-of-day
  correlated. `curl` with a browser UA makes no difference.

  **Only new-video discovery is affected.** Path 2 reads the `transcripts` table
  and never touches the network for discovery, so retries of known videos keep
  working while the feeds are down — `cmd_fetch` logs `!channel: ...` and
  continues to the retry path.

  **Nothing is permanently lost.** Feeds carry the ~15 most recent entries, so a
  multi-hour outage just means the next successful fetch sees a slightly longer
  list. Re-discovery is automatic once the endpoint recovers.
- **RSS returns 15 entries max.** For fern (weekly) that covers 15 weeks. For
  Townsends (~3/week) it covers only **33 days**, so a 90-day backfill silently
  misses ~26 older videos. Steady-state daily runs are unaffected. The fix, if
  the missed backlog matters, is paginating via yt-dlp's flat playlist listing.
  Out of scope for V1.
- **A fixed window never reconsiders old videos.** To seed a backlog,
  temporarily widen `WINDOW_DAYS`.
- **Cookies do not work, and did not need to.** An earlier draft of this project
  read Firefox `cookies.sqlite` and passed the cookies in, on the theory that
  looking like a logged-in browser would prevent blocking. That is not possible
  with `youtube-transcript-api` as of **v1.1.0**: the library switched to
  YouTube's innertube API and the maintainer disabled cookie auth deliberately,
  noting innertube authentication was unimplemented. It remains disabled in
  v1.2.4.

  Innertube authenticates with an `Authorization` header. The library sets no
  `Authorization`, no `SAPISIDHASH`, no visitor data — it impersonates an
  anonymous `ANDROID` client — so cookies cannot substitute. They could at best
  have influenced the intermediate watch-page fetch, never the call that returns
  captions. The library also warns that authenticating with cookies gets **the
  account permanently banned**.

  The dependency, the module, and the design risk are all gone. If `blocked`
  ever dominates in normal use, the remaining levers are `POLITE_DELAY` and a
  proxy via `ProxyConfig`; neither is free.
- **One transcript costs one full watch-page download.** Innertube keys are
  scraped from the page HTML first, so this is not a light API call. That is why
  `POLITE_DELAY` is 5s — 1.0s was a guess, and a 28-video run at that pace
  walked into an IP ban.

## Out of scope for now

Ranking, scoring, recommendations; scheduled/daily runs (`launchd`); tracking
which videos you've watched; search over transcripts; yt-dlp as a transcript
fallback or for feed pagination; backfill as a CLI flag.