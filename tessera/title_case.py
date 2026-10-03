"""Use the local model to sentence-case titles without rewriting their words."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class TitleCaseResult:
    display_title: str | None
    error: str | None = None


def build_body(title: str) -> dict:
    """Build a small, deterministic Ollama request for one title."""
    prompt = config.TITLE_CASE_PROMPT_PATH.read_text(encoding="utf-8").format(title=title)
    return {
        "model": config.TITLE_CASE_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": config.SUMMARY_KEEP_ALIVE,
        "options": {
            "temperature": config.TITLE_CASE_TEMPERATURE,
            "seed": config.TITLE_CASE_SEED,
            "num_ctx": config.TITLE_CASE_NUM_CTX,
            "num_predict": config.TITLE_CASE_NUM_PREDICT,
        },
    }


def _post(body: dict) -> dict:
    request = urllib.request.Request(
        config.OLLAMA_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.TITLE_CASE_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise ConnectionError(str(exc)) from exc


def case_title(title: str) -> TitleCaseResult:
    """Return the model's casing-only version, or an error for a safe fallback.

    Unicode case-fold equality ensures that the model cannot silently translate,
    correct, add, or remove words. It does not guarantee proper-noun casing; the
    prompt's examples and the caller's review remain important.
    """
    try:
        payload = _post(build_body(title))
    except (ConnectionError, OSError, ValueError) as exc:
        return TitleCaseResult(None, f"title model unavailable: {exc}")

    if not isinstance(payload, dict) or not isinstance(payload.get("response"), str):
        return TitleCaseResult(None, "title model returned an unreadable response")

    result = payload["response"].strip("\r\n")
    if not result or result != result.strip():
        return TitleCaseResult(None, "title model returned an empty or padded title")
    if result.casefold() != title.casefold():
        return TitleCaseResult(None, "title model changed more than letter casing")
    return TitleCaseResult(result)
