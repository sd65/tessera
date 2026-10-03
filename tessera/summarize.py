"""Normalise a transcript, then summarise it with the local Ollama model.

The production cut of the benchmark that lived in ``tools/summarize.py``. One
call per transcript, whole transcript, no chunking, deterministic: the model is
the e4b tag with thinking off, at temperature 0 and a fixed seed, exactly the
configuration the benchmark recommended.

The order matters and is the whole point of V2:

    normalize(text, metadata) -> send the normalised text -> store the answer

The metadata-grounded normaliser runs *first*, so the model reads names the
video's own title/channel/description already spell and does not have to guess.
The text the model sees is also the text the UI shows, so a quote it copies
stays searchable in what the reader is looking at.

Failure is a value, not an exception. ``summarize()`` returns a SummaryResult
for every outcome, and distinguishes a *transport* failure — Ollama is not
running — from a model answer that came back empty. ``store.record_summary``
uses that distinction: an absent Ollama is not the video's fault and must not
retire it, the same way a `blocked` transcript does not count as an attempt.

Nothing here writes the corpus or touches the network beyond the loopback
Ollama call.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config, normalize

OK = "ok"
ERROR = "error"


class TransportError(RuntimeError):
    """Ollama could not be reached, or answered with an error status."""


@dataclass(frozen=True)
class SummaryResult:
    """Outcome of one summarisation attempt.

    ``transport`` is True only when the model was never actually asked — a
    connection that was refused or timed out. It is what keeps an Ollama outage
    from exhausting a video's retries.
    """

    status: str
    normalized_text: str
    text: str | None = None
    error: str | None = None
    transport: bool = False


def load_prompt() -> str:
    return config.PROMPT_PATH.read_text(encoding="utf-8").rstrip()


def build_metadata_block(entry: dict) -> str:
    """The METADATA block the prompt's PROPER NOUNS rule refers to."""
    lines = [
        f"title: {entry.get('title') or ''}",
        f"channel: {entry.get('channel_name') or ''}",
    ]
    description = normalize.clean_metadata(entry.get("description"))
    if description:
        lines.append("description:")
        lines.append(description)
    return "METADATA:\n" + "\n".join(lines)


def _ctx_for(instructions: str, transcript: str) -> int:
    """Size the context window to this call, floor/step/ceiling from config."""
    need = (len(instructions) + len(transcript)) // 3 + config.SUMMARY_NUM_PREDICT + 2048
    ctx = config.SUMMARY_MIN_CTX
    while ctx < need:
        ctx += config.SUMMARY_CTX_STEP
    return min(ctx, config.SUMMARY_MAX_CTX)


def build_body(instructions: str, transcript: str) -> dict:
    return {
        "model": config.SUMMARY_MODEL,
        "prompt": f"{instructions}\n\nTRANSCRIPT:\n{transcript}",
        "stream": False,
        # Thinking stays off: it was slower, made naming worse, and on the 12b
        # model silently burned the whole output budget on reasoning.
        "think": False,
        "keep_alive": config.SUMMARY_KEEP_ALIVE,
        "options": {
            "temperature": config.SUMMARY_TEMPERATURE,
            "seed": config.SUMMARY_SEED,
            "num_ctx": _ctx_for(instructions, transcript),
            "num_predict": config.SUMMARY_NUM_PREDICT,
        },
    }


def _post(body: dict) -> dict:
    """The one network call. Tests stub this, so nothing else touches Ollama."""
    request = urllib.request.Request(
        config.OLLAMA_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.SUMMARY_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise TransportError(str(exc)) from exc


def call_model(instructions: str, transcript: str) -> dict:
    return _post(build_body(instructions, transcript))


def summarize(entry: dict) -> SummaryResult:
    """Normalise ``entry['text']`` and ask the model for its summary.

    ``entry`` is a video row with ``title``, ``channel_name``, ``description``
    and ``text``. Never raises: every failure comes back as a SummaryResult.
    """
    text = entry.get("text") or ""
    if not text:
        return SummaryResult(ERROR, "", error="no transcript text to summarise")

    normalized, _edits = normalize.normalize(text, entry)
    if not normalized:
        return SummaryResult(ERROR, normalized, error="the normalised transcript is empty")

    instructions = f"{load_prompt()}\n\n{build_metadata_block(entry)}"
    try:
        payload = call_model(instructions, normalized)
    except TransportError as exc:
        return SummaryResult(ERROR, normalized, error=str(exc), transport=True)
    except (ValueError, KeyError, TypeError) as exc:
        return SummaryResult(ERROR, normalized, error=f"unreadable model response: {exc}")

    response = (payload.get("response") or "").strip()
    if response:
        return SummaryResult(OK, normalized, text=response)

    output_tokens = payload.get("eval_count")
    if output_tokens is not None and output_tokens >= config.SUMMARY_NUM_PREDICT:
        error = "no answer — the output budget was exhausted"
    else:
        error = "the model returned an empty answer"
    return SummaryResult(ERROR, normalized, error=error)
