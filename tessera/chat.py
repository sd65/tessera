"""Answer video questions with the local Ollama model and a clean transcript."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config, normalize, summarize


class ChatError(Exception):
    """A request error with an HTTP status suitable for the UI API."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class TransportError(ChatError):
    """Ollama could not complete the model request."""

    def __init__(self, message: str):
        super().__init__(f"Ollama request failed: {message}", status=503)


@dataclass(frozen=True)
class ChatResult:
    answer: str
    citations: tuple[dict, ...] = ()


def _transcript_context(video: dict) -> tuple[str, list, bool]:
    """Return normalized text, its edits, and whether cue mapping is aligned."""
    cached = video.get("normalized_text")
    source = video.get("text") or ""
    if not source.strip():
        if isinstance(cached, str) and cached.strip():
            return cached, [], False
        raise ChatError("This video has no transcript to ask about.", status=409)

    cleaned, edits = normalize.normalize(source, video)
    if not cleaned.strip():
        raise ChatError("This video's transcript is empty after cleanup.", status=409)
    if isinstance(cached, str) and cached.strip():
        # Cue offsets refer to the source transcript. Only project them through
        # edits when the cached summary text is exactly the same normalization.
        return cached, edits, cached == cleaned
    return cleaned, edits, True


def clean_transcript(video: dict) -> str:
    """Return the same normalized transcript used by summaries and the UI."""
    return _transcript_context(video)[0]


def validate_messages(messages: object) -> list[dict[str, str]]:
    """Accept only the user/assistant conversation; system context stays server-side."""
    if not isinstance(messages, list) or not messages:
        raise ChatError("messages must be a non-empty array", status=400)

    checked: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ChatError(f"messages[{index}] must be an object", status=400)
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant"):
            raise ChatError(f"messages[{index}] has an invalid role", status=400)
        if not isinstance(content, str) or not content.strip():
            raise ChatError(f"messages[{index}] content must be non-empty text", status=400)
        checked.append({"role": role, "content": content})

    if checked[-1]["role"] != "user":
        raise ChatError("the last message must be a user question", status=400)
    return checked


def build_body(transcript: str, messages: list[dict[str, str]]) -> dict:
    """Build a deterministic Ollama chat request with server-owned grounding."""
    system = config.CHAT_PROMPT_PATH.read_text(encoding="utf-8").format(transcript=transcript)
    context_text = system + "\n" + "\n".join(message["content"] for message in messages)
    needed = (len(context_text) // 3) + config.CHAT_NUM_PREDICT + 2048
    context = config.SUMMARY_MIN_CTX
    while context < needed and context < config.SUMMARY_MAX_CTX:
        context += config.SUMMARY_CTX_STEP

    return {
        "model": config.CHAT_MODEL,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": False,
        "think": False,
        "keep_alive": config.SUMMARY_KEEP_ALIVE,
        "options": {
            "temperature": config.CHAT_TEMPERATURE,
            "seed": config.CHAT_SEED,
            "num_ctx": min(context, config.SUMMARY_MAX_CTX),
            "num_predict": config.CHAT_NUM_PREDICT,
        },
    }


def _post(body: dict) -> dict:
    request = urllib.request.Request(
        config.OLLAMA_CHAT_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.CHAT_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise TransportError(str(exc)) from exc


def _unpack_response(content: str) -> tuple[str, list]:
    """Read the model's JSON answer; fall back to plain text without citations."""
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate[3:-3].strip()
        if candidate.startswith("json"):
            candidate = candidate[4:].lstrip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return content.strip(), []
    if not isinstance(payload, dict):
        return content.strip(), []
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return content.strip(), []
    citations = payload.get("citations")
    return answer.strip(), citations if isinstance(citations, list) else []


def answer(video: dict, messages: object) -> ChatResult:
    """Validate a conversation, ground it in the video's clean transcript, and answer."""
    checked = validate_messages(messages)
    transcript, edits, cue_mapping_aligned = _transcript_context(video)
    try:
        payload = _post(build_body(transcript, checked))
    except TransportError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ChatError(f"Unreadable Ollama response: {exc}") from exc

    if not isinstance(payload, dict):
        raise ChatError("Unreadable Ollama response: expected an object")
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ChatError("Ollama returned an empty answer")
    response, candidates = _unpack_response(content)
    if not response:
        raise ChatError("Ollama returned an empty answer")

    passage_candidates = [
        {"label": "Chat excerpt", "quote": candidate["quote"]}
        for candidate in candidates[:2]
        if isinstance(candidate, dict) and isinstance(candidate.get("quote"), str)
    ]
    resolved = summarize.resolve_passages(
        transcript,
        video.get("_cues") or [],
        edits,
        passage_candidates,
    ) if cue_mapping_aligned else ()
    citations = tuple(
        {
            "quote": item["quote"],
            "start_ms": item["start_ms"],
            "duration_ms": item["duration_ms"],
        }
        for item in resolved
    )
    return ChatResult(response, citations)
