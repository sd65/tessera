"""Paths and tuning constants. Everything tunable lives here."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = ROOT / "tessera.db"
CHANNELS_PATH = ROOT / "channels.json"

# How far back the RSS ingest path looks. Widening this is the backfill
# mechanism: set it to 90, run fetch, set it back.
WINDOW_DAYS = 5

# A transcript row is retried while attempts < MAX_ATTEMPTS. Past that it stops
# retrying and shows up in `status` as needing attention.
MAX_ATTEMPTS = 3

# Pacing for transcript requests — the ban-avoidance policy.
#
# Each transcript costs one full YouTube watch-page download, not a light API
# call. 1.0s was a guess and was too hot: a 28-video run at that pace walked
# straight into an IP ban. The interval between two request *starts* is drawn
# uniformly at random per request so the cadence is not metronomic, and the time
# spent fetching and summarising a video counts toward it — the loop waits only
# for the remainder. Applied whatever the inline summary did: success, failure,
# or Ollama unreachable.
FETCH_INTERVAL_MIN = 31.0
FETCH_INTERVAL_MAX = 40.0

# Hard ceiling: never more than this many transcript requests inside the rolling
# window. Counted across runs (see store.fetch_attempts), because the jittered
# interval on its own allows ~150-180 per hour. Hitting the ceiling stops the
# fetch loop and leaves the rest for a later run.
FETCH_HOURLY_LIMIT = 100
FETCH_HOURLY_WINDOW = 3600.0

# Seconds sqlite waits for a lock before giving up. The database runs in WAL, so
# a reader does not block a writer, but two writers (two `fetch` runs at once)
# still serialise. Long enough to outlast a normal pause, short enough to fail
# with a message instead of hanging.
BUSY_TIMEOUT = 10.0

# The browser UI's bind address. Explicitly loopback and never 0.0.0.0 — the
# corpus is private, and a server that answers on every interface is a server
# that answers on the office wifi.
UI_HOST = "127.0.0.1"

UI_PORT = 8787

# Page size for the list endpoint. `limit` is clamped to the max rather than
# refused, so a client cannot ask for the whole corpus by accident, while a
# client that asks for more than it should still gets an answer.
UI_DEFAULT_LIMIT = 200
UI_MAX_LIMIT = 500

# --- Summaries ------------------------------------------------------------

# Prompts are editable text files kept together beside the database.
PROMPT_DIR = ROOT / "prompts"
PROMPT_PATH = PROMPT_DIR / "summary.txt"
TITLE_CASE_PROMPT_PATH = PROMPT_DIR / "title_case.txt"
CHAT_PROMPT_PATH = PROMPT_DIR / "chat.txt"

# The local model server. Summaries run after transcript storage; title casing
# is best-effort, so a machine without Ollama still ingests videos normally.
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"

# Summaries use the larger model for better instruction/schema compliance.
# Title casing and interactive chat stay on the faster e4b model below.
SUMMARY_MODEL = "gemma4:12b-mlx"
FAST_MODEL = "gemma4:e4b-mlx"
SUMMARY_TEMPERATURE = 0.0
SUMMARY_SEED = 42
SUMMARY_NUM_PREDICT = 2048
SUMMARY_KEEP_ALIVE = "10m"
# Word counts in summary prose use one percentage tolerance above their target
# maxima. Minimums and non-word/structural rules remain strict.
SUMMARY_WORD_TOLERANCE_PERCENT = 20
SUMMARY_SUBJECT_MIN_WORDS = 1
SUMMARY_SUBJECT_TARGET_MAX_WORDS = 25
SUMMARY_CONCLUSION_MIN_WORDS = 1
SUMMARY_CONCLUSION_TARGET_MAX_WORDS = 40
SUMMARY_BODY_MIN_WORDS = 80
SUMMARY_BODY_TARGET_MAX_WORDS = 120
SUMMARY_TAKEAWAY_TARGET_MAX_WORDS = 20
SUMMARY_DECISION_TARGET_MAX_WORDS = 25
SUMMARY_CAVEAT_TARGET_MAX_WORDS = 15


def tolerated_summary_word_limit(target: int) -> int:
    """Return the ceiling after the configured percentage allowance."""
    return (target * (100 + SUMMARY_WORD_TOLERANCE_PERCENT) + 99) // 100

# Title casing is a short independent Ollama request. It never blocks ingestion:
# failures leave the display title pending and the UI uses its local fallback.
TITLE_CASE_MODEL = FAST_MODEL
TITLE_CASE_TEMPERATURE = 0.0
TITLE_CASE_SEED = SUMMARY_SEED
TITLE_CASE_NUM_CTX = 4096
TITLE_CASE_NUM_PREDICT = 192
TITLE_CASE_TIMEOUT = 120

# Interactive video chat is non-streaming and uses the clean transcript plus
# the conversation supplied by the browser on every turn.
CHAT_MODEL = FAST_MODEL
CHAT_TEMPERATURE = SUMMARY_TEMPERATURE
CHAT_SEED = SUMMARY_SEED
CHAT_NUM_PREDICT = 1024
CHAT_TIMEOUT = 120

# Context window, sized per call from the transcript length. `// 3`
# over-estimates tokens for English on purpose. Same floor/step/ceiling the
# benchmark used, well above the largest transcript and below the model ceiling.
SUMMARY_MIN_CTX = 32768
SUMMARY_MAX_CTX = 131072
SUMMARY_CTX_STEP = 8192

# Seconds before the model call gives up. A long transcript is slow; generous
# enough that a normal run never trips it.
SUMMARY_TIMEOUT = 3600

# A summary row is retried while attempts < SUMMARY_MAX_ATTEMPTS. A transport
# failure (Ollama not running) does not count as an attempt — see
# store.record_summary — so an outage never retires a video.
SUMMARY_MAX_ATTEMPTS = 3


class ConfigError(RuntimeError):
    """channels.json is missing, unreadable, or malformed."""


def load_channels() -> list[dict[str, str]]:
    """Read channels.json — the source of truth for the channel list.

    Not duplicated into the database: editing this file changes what `fetch`
    walks, immediately.
    """
    if not CHANNELS_PATH.exists():
        raise ConfigError(f"no channel list at {CHANNELS_PATH}")

    try:
        raw = json.loads(CHANNELS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {CHANNELS_PATH}: {exc}") from exc

    if not isinstance(raw, list):
        raise ConfigError(f"{CHANNELS_PATH} must hold a list of channels")

    channels: list[dict[str, str]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or not entry.get("id"):
            raise ConfigError(f"{CHANNELS_PATH}[{i}] needs an 'id'")
        channels.append({"id": str(entry["id"]), "name": str(entry.get("name") or entry["id"])})

    return channels
