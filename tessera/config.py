"""Paths and tuning constants. Everything tunable lives here."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = ROOT / "tessera.db"
CHANNELS_PATH = ROOT / "channels.json"

# How far back the RSS ingest path looks. Widening this is the backfill
# mechanism: set it to 90, run fetch, set it back.
WINDOW_DAYS = 7

# A transcript row is retried while attempts < MAX_ATTEMPTS. Past that it stops
# retrying and shows up in `status` as needing attention.
MAX_ATTEMPTS = 3

# Seconds to wait between transcript requests.
#
# Each transcript costs one full YouTube watch-page download, not a light API
# call. 1.0s was a guess and was too hot: a 28-video run at that pace walked
# straight into an IP ban. 5s puts a 28-video backfill at ~2.5 minutes, which is
# nothing for a manual command.
POLITE_DELAY = 5.0

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

# The prompt is user-owned and lives beside the database, not inside the code.
PROMPT_PATH = ROOT / "prompt"

# The local model server. Summarisation happens inside `fetch`, after the
# transcript is stored, so a machine without Ollama still fetches transcripts;
# the summary is recorded as `error` and retried on a later run.
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

# The production configuration the benchmark settled on: the e4b model with
# thinking off. Deterministic (temperature 0, fixed seed) so a given transcript
# always produces the same summary.
SUMMARY_MODEL = "gemma4:e4b-mlx"
SUMMARY_TEMPERATURE = 0.0
SUMMARY_SEED = 42
SUMMARY_NUM_PREDICT = 2048
SUMMARY_KEEP_ALIVE = "10m"

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