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