# tessera

Local corpus of YouTube videos + transcripts. Runs locally on macOS. No API key,
no OAuth, no quota. Nothing leaves the machine.

V1 produces NO summaries and NO recommendations — it is the data layer that V2
(rank + summarize with a local LLM) will sit on top of.

## Setup

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
```

Both take no options. No scheduling yet — V1 is manual.

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
| `config.py` | DB path, `WINDOW_DAYS`, `MAX_ATTEMPTS`, politeness delay. |
| `rss.py` | Fetch + parse the Atom feed -> list of video dicts. |
| `transcript.py` | Fetch one video's transcript, classify failures, clean text. |
| `store.py` | Schema, upserts, selection queries, everything `status` prints. |

## How it works

`fetch` runs two selection paths, then works through whatever they return.

**Path 1 — ingest.** Fetch each channel's RSS, keep entries within `WINDOW_DAYS`,
skip Shorts, insert any video not already in `videos`.

**Path 2 — retry.** Select transcripts where `status` is `blocked` or `error` and
`attempts < MAX_ATTEMPTS`. This ignores publish date on purpose — a video that
came back `blocked` days ago would otherwise never be selected again, becoming
permanently unreachable.

Both paths mean `fetch` is idempotent *and* self-healing. There is no separate
retry command to remember.

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

## Known limits

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

## Out of scope for V1

Summaries or any LLM involvement; ranking, scoring, recommendations;
scheduled/daily runs (`launchd`); tracking which videos you've watched; search
over transcripts; yt-dlp as a transcript fallback or for feed pagination;
backfill as a CLI flag.