"""Atom feed parsing for a YouTube channel.

GET https://www.youtube.com/feeds/videos.xml?channel_id=<id>

Public RSS. No key, no auth, not rate limited. Returns the 15 most recent
uploads — that cap is the main structural limit of this whole approach, see
README "Known limits".
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from .config import WINDOW_DAYS

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
USER_AGENT = "tessera/0.1 (local; reads public YouTube RSS)"
TIMEOUT = 30

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
YT = "{http://www.youtube.com/xml/schemas/2015}"


class FeedError(RuntimeError):
    """The channel's feed could not be fetched or parsed."""


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _entry_text(entry: ET.Element, path: str) -> str:
    node = entry.find(path)
    return (node.text or "").strip() if node is not None else ""


def _parse_entry(entry: ET.Element, channel_id: str) -> dict | None:
    video_id = _entry_text(entry, f"{YT}videoId")
    if not video_id:
        return None

    link = entry.find(f"{ATOM}link[@rel='alternate']")
    url = link.get("href") if link is not None else f"https://www.youtube.com/watch?v={video_id}"

    return {
        "video_id": video_id,
        "channel_id": channel_id,
        "channel_name": _entry_text(entry, f"{ATOM}author/{ATOM}name") or None,
        "title": _entry_text(entry, f"{ATOM}title") or video_id,
        "description": _entry_text(entry, f"{MEDIA}group/{MEDIA}description") or None,
        "published_at": _iso_utc(_parse_timestamp(_entry_text(entry, f"{ATOM}published"))),
        "url": url,
    }


def _is_short(url: str) -> bool:
    """Shorts arrive in the feed like any other upload, tagged only by the URL."""
    return "/shorts/" in url


def fetch_feed(channel_id: str) -> list[dict]:
    """The channel's 15 most recent uploads, newest first. No date filtering.

    Shorts are dropped. They are vertical clips, usually cut from a video that is
    also in the feed, so keeping them double-counts content and spends a
    transcript request each — and the feed only ever holds 15 entries, so a
    Shorts entry displaces a real video.
    """
    url = FEED_URL.format(channel_id=urllib.parse.quote(channel_id, safe=""))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise FeedError(f"feed returned HTTP {exc.code} for {channel_id}") from exc
    except urllib.error.URLError as exc:
        raise FeedError(f"could not reach the feed for {channel_id}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FeedError(f"feed for {channel_id} timed out") from exc

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FeedError(f"feed for {channel_id} was not valid XML: {exc}") from exc

    entries = (_parse_entry(e, channel_id) for e in root.iter(f"{ATOM}entry"))
    videos = [v for v in entries if v and not _is_short(v["url"])]
    return sorted(videos, key=lambda v: v["published_at"], reverse=True)


def fetch_recent(channel_id: str, window_days: int = WINDOW_DAYS) -> list[dict]:
    """Feed entries published within the last `window_days`.

    The RSS cap means a wide window can still come back short — that is expected,
    not a bug. See README "Known limits".
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    return [v for v in fetch_feed(channel_id) if _parse_timestamp(v["published_at"]) >= cutoff]