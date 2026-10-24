# tessera

Local corpus of YouTube videos + transcripts. Runs locally on macOS. No API key,
no OAuth, no quota. Summaries, title casing, video chat, and optional speech
recognition run locally; transcript audio is never sent to an AI service. The
optional ASR worker downloads audio from YouTube and its model from Hugging Face
once. The UI's `youtube-nocookie.com` embed is the only external connection
while browsing.

`fetch` also summarises: after each transcript is stored, it is normalised with
metadata from the video's own title, channel and description, then sent whole to
a local Ollama model. The summary and the normalised text are stored beside the
raw transcript, and the UI shows the summary first with the full transcript
behind a disclosure. The summary can also include a few quote-verified, clickable
passages; ranking videos remains out of scope.

## Setup

Transcripts need nothing but the Python dependencies. Summaries and the other AI
features additionally need [Ollama](https://ollama.com) running locally with both
models pulled:

```bash
ollama pull gemma4:12b-mlx  # summaries
ollama pull gemma4:e4b-mlx # chat and title casing
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

For videos without usable YouTube captions, install the optional local speech
recognition worker and its audio decoder:

```bash
brew install ffmpeg
uv sync --extra asr
```

The fallback downloads audio with `yt-dlp` and transcribes it locally with
MLX Whisper on Apple Silicon. Its model is downloaded from Hugging Face the
first time it is used; video audio is not uploaded to a transcription service.
The fallback runs only when YouTube has no captions or no track in the original
audio language. ASR results are marked `asr` and keep their segment timings.
Videos longer than three hours and audio downloads over 500 MiB are rejected.

## Commands

```
uv run tessera fetch
uv run tessera title-case
uv run tessera status
uv run tessera doctor
uv run tessera ui
```

`title-case` backfills AI-cased display titles that are missing; use
`uv run tessera title-case --force` to regenerate every cached display title.
`doctor` reports transcript and summary failures without changing the corpus.
Use `uv run tessera doctor --retry` to retry recoverable transcript failures
(with the normal request pacing and hourly limit) and failed summaries from
stored transcripts. `disabled` transcript errors are retried on later fetches;
`doctor --retry` can retry them even after the normal attempt limit. Permanent
transcript failures are reported but not retried.
No scheduling yet — commands are manual.

`ui` serves the corpus at <http://127.0.0.1:8787> and opens a browser.
It is the only command that contacts YouTube for playback: the embed is
`youtube-nocookie.com` and the page sends it an origin and
nothing more — never a path, never the `?v=` deep link. That is
`Referrer-Policy: origin-when-cross-origin`, and it is not decoration: YouTube
refuses to configure its embed when the request carries no `Referer` at all, and
says so on the video as "Error 153". The stricter `no-referrer` this used to send
breaks the player outright. The server binds loopback only, and the database is
opened with SQLite's `mode=ro`, so there is no code path through it that can
write anything but a read marker — see [The browser view](#the-browser-view).
There is no "run fetch" button; `fetch` stays a command you type, because
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
| `asr.py` | Optional local audio download and MLX Whisper fallback. |
| `config.py` | DB path, `WINDOW_DAYS`, `MAX_ATTEMPTS`, politeness delay, UI host/port, and the model, prompt path and context constants. |
| `rss.py` | Fetch + parse the Atom feed -> list of video dicts. |
| `transcript.py` | Fetch one video's transcript, classify failures, clean text. |
| `normalize.py` | Metadata-grounded ASR name repair. Pure and deterministic. |
| `summarize.py` | Normalise then summarise one transcript via the local Ollama model. |
| `chat.py` | Ground per-video questions in the clean transcript via local Ollama. |
| `title_case.py` | Ask local Ollama for a casing-only display title. |
| `store.py` | Schema, upserts, selection queries, everything `status` prints, and UI read-state writes. |
| `web.py` | Local HTTP for `ui`: read-only corpus routes, the chat proxy, and read-state writes. |
| `ui/` | The browser view: Web Components, one stylesheet, no build step. |

## The browser view

Three panes — channel picker and videos on the left, the YouTube embed and video
chat in the middle, the summary and its transcript on the right. The embedded
player receives `autoplay=0`, so selecting a video or passage never starts
playback on its own. Chat starts with
“Any questions?” and keeps a separate conversation per video in browser memory;
it is cleared on reload. Each question sends the full conversation with the
clean transcript to local Ollama. When asked for a specific excerpt, the chat
can return clickable quotes; the server verifies each quote and derives its
timestamp from stored cues. Dark theme is scheduled for 8:30 pm–7:00 am
local time; the sun button toggles manually until the next scheduled change.

The list shows the videos that have a transcript — 14 today. A video with no
transcript (blocked, disabled, …) is not a row at all: the sidebar asks the server
for transcripts only, so it never arrives and the count and pager agree. Narrow
the list by typing in the channel box: it is a native searchable combo, so the
browser owns the typeahead and the keyboard, and an empty box means all channels.
Rows carry a title, the channel, and how long ago it was published ("12 days ago")
rather than a date; the caption source (`auto` or `manual`), video duration, and
caption language live in the transcript header, not on the row. A color-only dot
by the title shows the watch recommendation from summary section 5b; its meaning
is available on hover and to assistive technology.

A video you have not read is shown in **bold**. It stops being bold one second after its
summary is on screen, and only while the tab is visible: a tab left open in the
background does not mark what it happened to be showing. Hovering a row — or
tabbing into it — reveals a small button on the right that toggles the marker by
hand, which is also the way to put one back after you have read it. A video with
no summary has nothing to have read, so its row stays bold however long it sits
selected. The checkmark in the pager marks all successful transcripts in the
current channel filter as read, including videos on other pages.

The marker is per-video state, not corpus content, and it lives in its own table
so it can be deleted wholesale without touching anything else:

```sql
CREATE TABLE read_state (
  video_id TEXT PRIMARY KEY REFERENCES videos(video_id) ON DELETE CASCADE,
  read_at  TEXT NOT NULL
);
```

Absence of a row **is** "unread" — there is no boolean column and nothing to
backfill, so an existing corpus comes up entirely unread the first time you open
it. `read_at` is kept so the question "when did I read this" is answerable
without a second source.

The right pane shows the summary under a **Summary** heading, parsed into section
headings, paragraphs and bullet lists, with quote-verified passages embedded under
section 5b. The model's output is never interpolated as HTML: it is built from text
nodes, which is what keeps the CSP (no inline script, no inline style) meaningful.
A collapsed **Transcript** drawer stays at the bottom of the pane; opening it covers
the summary and fills the available height. Search highlights every match and lets
you move between occurrences. The transcript is the *normalised* text — the same
text the model was fed — and arrives
as one unbroken run, rendered without paragraphing or sentence splitting. Cue start
times and character offsets are stored separately in
`transcript_cues`; the UI exposes only validated passage links, not the cue index.
Each passage shows its timestamp and an approximate watch duration. Clicking one
reloads the existing YouTube embed at that cue's approximate start.

When Ollama has not answered yet, the pane says so, with the stored reason, and
still offers the transcript.

The corpus endpoints are `GET`, except for two `POST`s:

| route | returns |
|---|---|
| `/api/videos?channel=&status=&limit=&offset=` | `{rows, meta}` — one page of metadata, including each video's `watch_recommendation` code and `read_at` |
| `/api/videos/<id>` | one video, including `text`, the summary, `normalized_text`, validated `passages`, `duration_seconds`, `watch_recommendation`, and `read_at` |
| `/api/chat` (`POST`) | `{answer, citations}` — accepts `video_id` and the full user/assistant `messages` history; citations contain verified quotes and server-derived timings |
| `/api/read` (`POST`) | `{video_id, read, read_at}` — accepts `video_id` and a boolean `read`; marks one summary read or unread |

`read_at` travels with the row rather than in a separate endpoint, so the sidebar
answers "which of these have I read" from the page it is already holding, joined
on the primary key. A `video_id` that is not in the corpus is a 404 rather than a
foreign-key error, and `read` must be a real boolean — `{"read": "false"}` is
refused rather than coerced.

Chat history is not stored in the corpus. The server uses the cached normalized
transcript (or normalizes the stored transcript if no cache exists); the browser
sends only the video ID and conversation, never transcript text. The server then
forwards the clean transcript and conversation to Ollama with a 120-second
timeout. Citation quotes are matched uniquely against the normalized transcript
and mapped to cue timings on the server; ambiguous or unmatched quotes produce no
link. The page gives that local request a little extra time to return a timeout
response.

On initialization, existing successful summaries are backfilled into the
`watch_recommendation` field by reading their stored section 5b; unknown choices
are marked undetermined without rerunning the model.

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

Read-only is enforced by the database rather than by the routing — with one
deliberate exception, which is the read marker. `PUT`, `PATCH` and `DELETE` are
all the same 405, and `store.connect_readonly()` opens the file with SQLite's
`mode=ro`, so a bug in a handler cannot mutate `tessera.db`: transcripts,
summaries, passages and metadata are refused by the engine. The exception is
`read_state`, which is viewer state rather than corpus content and has to be
written by the server for a marker to survive a reload. It is reachable through
two narrow functions: `store.set_read_state(conn, video_id, read)` marks one
validated video, while `store.mark_all_read(conn, channel_id)` marks unread,
successful transcripts in the selected channel (or all channels when `null`).
Neither accepts SQL or arbitrary writes. The bulk operation spans all pages and
preserves timestamps for videos already marked read. So the accurate form of the
guarantee is narrower than it used to be, and it is narrower by exactly one table.

The CSP header is the other half: it names
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
immediately normalises and summarises it — not in a second pass. The point of the
ordering is pacing: the interval between two transcript requests is drawn at
random each time from `FETCH_INTERVAL_MIN`–`FETCH_INTERVAL_MAX` (20–25 s), and
the time spent fetching and summarising a video counts toward it, so the loop
only waits the remainder. That holds whatever the summary did — success, failure,
or Ollama unreachable. Each summary line reports Ollama's total processing time
and generated-token throughput when the response includes those metrics.
Transcript work comes first; the summary-only backlog runs after, and still runs
when a block or the hourly ceiling stopped the transcript loop, because it
touches no network but Ollama.

**The rate-limit policy**, applied whatever the summary did:

- **20–25 s of jitter between request starts**, drawn fresh per request so the
  cadence is not metronomic. On its own this allows ~150–180 requests/hour.
- **At most 100 transcript requests per rolling hour**, counted in the
  `fetch_attempts` table rather than in memory, so it holds across runs and
  restarts. Hitting the ceiling stops the fetch loop and leaves the rest for a
  later run; the local summary backlog still runs.
- **An HTTP 429/IP-level block stops the run outright**, backlog included, and
  exits with status 1. Continuing would only spend the next request to learn the
  same thing.

**Claiming happens up front.** Every selected video is inserted into `transcripts`
as `pending` before the first request, not one at a time as its turn comes. If a
video were reserved lazily and the run stopped early, it would sit in `videos`
with no `transcripts` row — and since path 1 filters on `videos` while path 2
needs that row, neither query would ever return it again. Leftovers stay `pending`
at `attempts = 0`, which path 2 picks up on the next run.

**A bot-check block stops the fetch loop.** `blocked` is a video-level challenge,
distinct from an HTTP 429 or IP-level CAPTCHA block (`rate_limited`). The loop
stops rather than spend the rest of the queue proving that further requests will
also be blocked; unlike an IP-level block, the local summary backlog may still
run.

**Blocks are not video attempts.** `attempts` counts tries attributable to the
video, so `blocked` and `rate_limited` leave that counter alone. The separate
`fetch_attempts` log still counts every request for the hourly ceiling.

## `status` values

| value | meaning | retried? |
|---|---|---|
| `ok` | transcript stored | — |
| `no_captions` | video genuinely has no captions (without local ASR) | no, permanent |
| `disabled` | YouTube returned no caption tracks (without local ASR) | yes, retryable |
| `unavailable` | private, removed, or age/region blocked | no, permanent |
| `rate_limited` | HTTP 429 or IP-level CAPTCHA block | **yes**, stops the run; does not count against the video's attempts |
| `blocked` | YouTube challenged the request (bot check) | **yes**, stops the fetch loop; does not count against the video's attempts |
| `error` | anything else, including no caption track matching the original audio language | **yes** |

Transcript selection follows YouTube's original audio language, not the current
playback default or the first caption track in YouTube's list (the playback
default can be an auto-dub). Manual captions are preferred over
auto-generated captions in that language. If the original language cannot be
determined or no matching caption exists, the fetch records an `error` instead
of silently using captions in another language.

When the optional ASR worker is installed, `disabled`, `no_captions`, and a
missing original-language caption track trigger local audio recognition. A
successful fallback is stored as `kind = 'asr'`; if the worker fails, the row
becomes a retryable `error` with both the caption and ASR failure reasons.

The permanent/retryable split is load-bearing — it is literally the WHERE clause
for path 2.

## Summaries

After a transcript is stored, `fetch`:

1. loads the video's own `title`, `channel_name` and `description`;
2. runs `normalize()` — a pure, deterministic pass that repairs only names the
   video's metadata already spells, and never writes `transcripts.text`;
3. sends the normalised text whole, with `prompts/summary.txt` and a `METADATA`
   block, to the local model: `gemma4:12b-mlx`, thinking off, `temperature 0`,
   `seed 42`;
4. asks for the six-section summary plus up to two exact-quote passage anchors;
   the server resolves each unique quote against normalized cue offsets and stores
   only verified `{label, quote, start_ms, duration_ms}` rows in `summary_passages`.

Successful transcript fetches also save each subtitle cue's start/duration in
integer milliseconds and its half-open Unicode character range into the cleaned
`transcripts.text`. Cue text is not duplicated. If cue-by-cue cleanup cannot
reproduce the established flattened transcript exactly, the text is retained but
timestamps for that fetch are omitted. Normalization edits are carried through
the offsets; an edit spanning cue boundaries is attributed to the first cue it
overlaps. YouTube seeking remains approximate (integer-second start and nearby
keyframe), not word-level timing. Passage watch duration is estimated from the
matched cues' displayed time span (cue end minus first cue start); YouTube cue
display duration is not exact speech duration, so the UI marks it approximate.
No backfill is performed for existing rows.

That configuration is the one the benchmark settled on: fastest, every run
complete, and its one real weakness — copying ASR spellings — is exactly what the
metadata grounding fixes. Thinking was slower, made naming worse, and on the
larger model silently spent the whole output budget on reasoning.

The `summaries` table holds one row per video: `status` (`ok` or `error`), the
model output in `text`, the `normalized_text` the model was fed (and the UI shows),
the `model` tag, `attempts`, the stored `error`, and `generated_at`.
`normalized_text` is written even when the model call fails, so the grounded
transcript stays readable while the summary is retried.

Editing `prompts/summary.txt` does not rewrite history: existing summaries are
kept, and new wording applies only to summaries generated afterwards.

## Video title casing

The RSS title in `videos.title` is kept exactly as received and remains the
metadata source for summaries. A separate nullable `display_title` is generated
with the local model using `prompts/title_case.txt`; it changes letter casing
only, and is shown in the video list and player. New titles are processed during
`fetch` on a best-effort basis, so an unavailable Ollama does not stop ingestion.
The UI uses a deterministic sentence-case fallback until a model result is
available. Run `uv run tessera title-case` to backfill pending titles or add
`--force` to regenerate the existing cache. Model outputs are rejected if their
Unicode case-folded text differs from the source, though proper-name casing
still merits review.

## Known limits

- **Summaries need Ollama.** Without it, `fetch` still stores transcripts, but
  every summary is recorded as `error`. They are retried on the next run, the
  reason is printed per video, and the UI shows "Summary unavailable" with the
  transcript still readable behind the disclosure. The 20–25 s jittered interval
  between transcript requests still applies; processing time counts toward it.
- **Read markers have no undo, and the first open marks nothing read.** Every
  existing video arrives unread, so the first pass through a 198-video sidebar is
  entirely bold even if you have read most of them; `DELETE FROM read_state` is
  the only way back, and it forgets the whole set rather than one video. A
  summary that fails to save is left unmarked rather than reported — the row stays
  bold and the next selection retries — because the only notice the sidebar has
  replaces the rows themselves, which is the wrong thing to spend on one rejected
  write. The per-row toggle's label is English-only, unlike the bilingual rest of
  the pane.
- **One second is a guess, and a tight one.** Nothing here measures how long a
  summary takes to read. 1 s is enough to tell a skim of the sidebar from a stop
  on a summary, but it is barely more than the time it takes to click through to
  the next row, so pausing on a row counts as reading it. It is also wrong in
  both directions regardless of length: a 200-word summary read properly in ten
  seconds is marked at one, and a 2,000-word one skimmed is marked at the same
  moment. The number is one constant in `components/app.js` if it needs moving.
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

  The dependency, the module, and the design risk are all gone. If YouTube
  continues to block requests despite the pacing and hourly ceiling, a proxy via
  `ProxyConfig` is the remaining lever; it is not free.
- **One transcript costs one full watch-page download.** Innertube keys are
  scraped from the page HTML first, so this is not a light API call. That is why
  transcript requests use 20–25 s of random spacing and a hard ceiling of 100
  starts per rolling hour. A 429 stops the run immediately; there is no extra
  backoff or retry in that run.
- **Local ASR is optional and requires audio downloads.** It adds `yt-dlp`,
  `mlx-whisper`, and `ffmpeg`; the Whisper model is fetched from Hugging Face on
  first use. Recognition may take several minutes for a long video and is only
  available on Apple Silicon with the configured MLX backend.

## Out of scope for now

Ranking, scoring, recommendations; scheduled/daily runs (`launchd`); tracking
which videos you've *watched* (read markers track the summary, not playback);
filtering the list to unread only; search over transcripts; yt-dlp for feed
pagination; backfill as a CLI flag.
