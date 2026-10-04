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
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config, normalize

OK = "ok"
ERROR = "error"
MAX_PASSAGE_DURATION_MS = 120_000


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
    processing_seconds: float | None = None
    tokens_per_second: float | None = None
    eval_count: int | None = None
    passages: tuple[dict, ...] = ()


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


def output_language_instruction(language_code: str | None) -> str:
    """Make the transcript's stored language an explicit output constraint."""
    code = (language_code or "").strip()
    primary = code.split("-", 1)[0].casefold()
    if primary == "fr":
        return (
            f"LANGUE DE SORTIE OBLIGATOIRE : français (code `{code}`). "
            "Rédige toute la réponse en français, y compris les titres de sections, "
            "les libellés de verdict et les justifications. Garde les citations "
            "exactement dans leur langue d'origine. Dans la valeur JSON `summary`, "
            "n'ajoute aucun titre général, introduction, préambule ou section "
            "supplémentaire. Utilise exactement ces titres, dans cet ordre : `1. SUJET`, "
            "`2. CONCLUSION`, `3. RÉSUMÉ`, `4. POINTS CLÉS`, `5. DÉCISION DE VISIONNAGE`, "
            "`6. RÉSERVES`. Dans l'objet JSON de la section 5, utilise les clés fixes "
            "`visual_value` et `watch_recommendation`; rédige leurs choix et leurs "
            "justifications en français. `visual_value.choice` utilise `probablement important`, "
            "`peu indiqué` ou `indéterminé`; `watch_recommendation.choice` utilise "
            "`résumé suffisant`, `regarder les passages sélectionnés`, `visionnage "
            "complet recommandé` ou `indéterminé`. Le reste de la réponse doit "
            "respecter l'enveloppe JSON demandée : écris toujours `passages` en premier "
            "(même si la liste est vide), puis `summary`."
        )
    if primary == "en":
        return (
            f"MANDATORY OUTPUT LANGUAGE: English (track language code `{code}`). "
            "Write the entire response in English, including section headings, "
            "verdict labels and justifications. Keep quotations exactly in their "
            "original language. Use exactly these section headings in order: "
            "`1. SUBJECT`, `2. CONCLUSION`, `3. SUMMARY`, `4. KEY TAKEAWAYS`, "
            "`5. WATCHING DECISION`, `6. CAVEATS`. In the JSON `summary` value, "
            "do not add a general title, introduction, preamble or extra section. "
            "The JSON object must always include `passages` first (even if empty), "
            "then `summary`."
        )
    if code:
        return (
            f"MANDATORY OUTPUT LANGUAGE: the transcript language identified by "
            f"BCP-47 code `{code}`. Write every section heading, label, verdict, "
            "justification, prose, and passage labels in that language. Keep "
            "quotations exactly in their original language. In the JSON `summary` "
            "value, do not add a general title, introduction, preamble or extra "
            "section. The JSON object must always include `passages` first (even "
            "if empty), then `summary`."
        )
    return (
        "MANDATORY OUTPUT LANGUAGE: use the transcript's language for every "
        "section heading, verdict, justification, prose and passage label. Keep "
        "quotations exactly in their original language. In the JSON `summary` "
        "value, do not add a general title, introduction, preamble or extra "
        "section. The JSON object must always include `passages` first (even if "
        "empty), then `summary`."
    )


def passage_output_instruction(entry: dict) -> str:
    """Align the model's recommendation with whether this transcript can link."""
    cues = entry.get("_cues")
    if not cues:
        return (
            "No timed transcript cues are available to the application. Set the "
            "top-level `passages` value to `[]`, and do not recommend watching "
            "selected passages."
        )
    return (
        f"The application has {len(cues)} timed subtitle cues for this transcript. "
        "It is long enough to support two excerpts: provide exactly two distinct, "
        "useful, exact quote anchors unless a second one genuinely cannot be found. "
        "Always put the `passages` array first in the JSON object."
    )


def _ctx_for(instructions: str, transcript: str) -> int:
    """Size the context window to this call, floor/step/ceiling from config."""
    need = (len(instructions) + len(transcript)) // 3 + config.SUMMARY_NUM_PREDICT + 2048
    ctx = config.SUMMARY_MIN_CTX
    while ctx < need:
        ctx += config.SUMMARY_CTX_STEP
    return min(ctx, config.SUMMARY_MAX_CTX)


def build_body(
    instructions: str,
    transcript: str,
    final_instruction: str = "",
) -> dict:
    prompt = f"{instructions}\n\nTRANSCRIPT:\n{transcript}"
    if final_instruction:
        prompt += f"\n\nFINAL OUTPUT REQUIREMENTS:\n{final_instruction}"
    return {
        "model": config.SUMMARY_MODEL,
        "prompt": prompt,
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


def call_model(
    instructions: str,
    transcript: str,
    final_instruction: str = "",
) -> dict:
    return _post(build_body(instructions, transcript, final_instruction))


def _edit_value(edit, name: str):
    return edit[name] if isinstance(edit, dict) else getattr(edit, name)


def map_cue_offsets(cues: list[dict], edits: list[normalize.Edit]) -> list[dict]:
    """Project source cue ranges through normalization edits.

    A source boundary strictly inside an edit maps after the replacement. This
    assigns an edit crossing cue boundaries to the first cue it overlaps; cue
    boundaries at the edit's exact start/end remain ordinary half-open edges.
    """
    ordered = sorted(edits, key=lambda edit: _edit_value(edit, "start"))
    owners = {}
    for edit in ordered:
        start, end = _edit_value(edit, "start"), _edit_value(edit, "end")
        overlapping = [
            cue for cue in cues
            if start < int(cue["char_end"]) and end > int(cue["char_start"])
        ]
        if overlapping:
            owners[id(edit)] = min(overlapping, key=lambda cue: int(cue["cue_index"]))["cue_index"]

    def boundary(position: int, cue: dict, edge: str) -> int:
        delta = 0
        for edit in ordered:
            start = _edit_value(edit, "start")
            end = _edit_value(edit, "end")
            target = _edit_value(edit, "target")
            if start < position < end:
                replacement_start = start + delta
                if owners.get(id(edit)) == cue["cue_index"]:
                    return replacement_start if edge == "start" else replacement_start + len(target)
                return replacement_start + len(target)
            if end <= position:
                delta += len(target) - len(_edit_value(edit, "source"))
            elif start >= position:
                break
        return position + delta

    mapped = []
    for cue in cues:
        item = dict(cue)
        item["char_start"] = boundary(int(cue["char_start"]), cue, "start")
        item["char_end"] = boundary(int(cue["char_end"]), cue, "end")
        mapped.append(item)
    return mapped


def resolve_passages(
    normalized: str,
    cues: list[dict],
    edits: list[normalize.Edit],
    candidates,
) -> tuple[dict, ...]:
    """Validate quotes and derive an approximate watch duration from cue timing."""
    mapped_cues = map_cue_offsets(cues, edits)
    resolved = []
    if not isinstance(candidates, list):
        return ()

    for candidate in candidates[:2]:
        if not isinstance(candidate, dict):
            continue
        label, quote = candidate.get("label"), candidate.get("quote")
        if (
            not isinstance(label, str)
            or not isinstance(quote, str)
            or not label.strip()
            or not quote.strip()
            or len(label) > 60
            or not 2 <= len(label.split()) <= 6
            or len(quote) > 280
            or not 8 <= len(quote.split()) <= 45
        ):
            continue
        label, quote = label.strip(), quote.strip()
        first = normalized.find(quote)
        if first < 0 or normalized.find(quote, first + 1) >= 0:
            continue
        quote_end = first + len(quote)
        matching = [
            cue for cue in mapped_cues
            if int(cue["char_start"]) < quote_end
            and int(cue["char_end"]) > first
        ]
        if not matching:
            continue
        cue = min(matching, key=lambda item: int(item["cue_index"]))
        start_ms = int(cue["start_ms"])
        # Caption duration is how long a cue stays visible, not exact speech
        # duration. Treat the range as an estimate and reject implausible spans.
        end_ms = max(
            int(item["start_ms"]) + int(item["duration_ms"])
            for item in matching
        )
        duration_ms = end_ms - start_ms
        if duration_ms <= 0 or duration_ms > MAX_PASSAGE_DURATION_MS:
            continue
        if any(item["start_ms"] == start_ms for item in resolved):
            continue
        resolved.append({
            "label": label,
            "quote": quote,
            "start_ms": start_ms,
            "duration_ms": duration_ms,
        })
    return tuple(resolved)


def _format_summary_object(summary: dict) -> str:
    """Validate the six structured sections and render the UI's safe text format."""
    sections = list(summary.items())
    if len(sections) != 6:
        raise ValueError("summary must contain exactly six numbered sections")

    lines: list[str] = []
    for number, (heading, content) in enumerate(sections, start=1):
        if not isinstance(heading, str) or not re.match(rf"^{number}\.\s+\S", heading):
            raise ValueError(f"section {number} has an invalid heading")
        lines.append(heading.strip())

        if number in (1, 2, 3):
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"section {number} must be non-empty text")
            words = len(content.split())
            limits = {1: (1, 25), 2: (1, 40), 3: (80, 120)}
            minimum, maximum = limits[number]
            if not minimum <= words <= maximum:
                raise ValueError(
                    f"section {number} has {words} words; expected {minimum}–{maximum}"
                )
            lines.append(content.strip())
        elif number in (4, 6):
            minimum, maximum = (2, 3) if number == 4 else (0, 2)
            if not isinstance(content, list) or not minimum <= len(content) <= maximum:
                raise ValueError(f"section {number} has an invalid number of bullets")
            for item in content:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"section {number} contains a malformed bullet")
                word_limit = 20 if number == 4 else 15
                if len(item.split()) > word_limit:
                    raise ValueError(f"section {number} bullet exceeds {word_limit} words")
                lines.append(f"- {item.strip()}")
        else:
            if not isinstance(content, dict) or set(content) != {
                "visual_value", "watch_recommendation"
            }:
                raise ValueError("section 5 must contain visual_value and watch_recommendation")
            for marker, key in (("a)", "visual_value"), ("b)", "watch_recommendation")):
                decision = content[key]
                if (
                    not isinstance(decision, dict)
                    or set(decision) != {"choice", "justification"}
                    or not isinstance(decision["choice"], str)
                    or not isinstance(decision["justification"], str)
                    or not decision["choice"].strip()
                    or not decision["justification"].strip()
                ):
                    raise ValueError(f"section 5 {key} is malformed")
                combined = f"{decision['choice']} {decision['justification']}"
                if len(combined.split()) > 25:
                    raise ValueError(f"section 5 {key} exceeds 25 words")
                lines.append(
                    f"{marker} {decision['choice'].strip()} — "
                    f"{decision['justification'].strip()}"
                )
        if number < 6:
            lines.append("")
    return "\n".join(lines)


def _unpack_response(response: str) -> tuple[str, list]:
    """Read and validate the JSON envelope; plain text is not the new contract."""
    candidate = response.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate[3:-3].strip()
        if candidate.startswith("json"):
            candidate = candidate[4:].lstrip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("response must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")
    if set(payload) != {"passages", "summary"}:
        raise ValueError("JSON object must contain only passages and summary")
    passages = payload.get("passages")
    if not isinstance(passages, list):
        raise ValueError("JSON response must include a passages array")
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return _format_summary_object(summary), passages
    raise ValueError("JSON response summary must be a six-section object")


def _response_metrics(payload: dict, wall_seconds: float) -> dict:
    """Read Ollama's nanosecond timing/token fields, with a wall-time fallback."""
    def seconds(value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        return number / 1_000_000_000

    processing_seconds = seconds(payload.get("total_duration")) or wall_seconds

    raw_count = payload.get("eval_count")
    eval_count = raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) else None
    eval_seconds = seconds(payload.get("eval_duration"))
    tokens_per_second = (
        eval_count / eval_seconds
        if eval_count is not None and eval_count >= 0 and eval_seconds is not None
        else None
    )
    return {
        "processing_seconds": processing_seconds,
        "tokens_per_second": tokens_per_second,
        "eval_count": eval_count,
    }


def summarize(entry: dict) -> SummaryResult:
    """Normalise ``entry['text']`` and ask the model for its summary.

    ``entry`` is a video row with ``title``, ``channel_name``, ``description``
    and ``text``. Never raises: every failure comes back as a SummaryResult.
    """
    text = entry.get("text") or ""
    if not text:
        return SummaryResult(ERROR, "", error="no transcript text to summarise")

    normalized, edits = normalize.normalize(text, entry)
    if not normalized:
        return SummaryResult(ERROR, normalized, error="the normalised transcript is empty")

    instructions = f"{load_prompt()}\n\n{build_metadata_block(entry)}"
    final_instruction = "\n".join((
        output_language_instruction(entry.get("lang")),
        passage_output_instruction(entry),
    ))
    started = time.monotonic()
    try:
        payload = call_model(instructions, normalized, final_instruction)
    except TransportError as exc:
        elapsed = time.monotonic() - started
        return SummaryResult(
            ERROR, normalized, error=str(exc), transport=True,
            processing_seconds=elapsed,
        )
    except (ValueError, KeyError, TypeError) as exc:
        elapsed = time.monotonic() - started
        return SummaryResult(
            ERROR, normalized, error=f"unreadable model response: {exc}",
            processing_seconds=elapsed,
        )

    elapsed = time.monotonic() - started
    if not isinstance(payload, dict):
        return SummaryResult(
            ERROR, normalized, error="unreadable model response: expected an object",
            processing_seconds=elapsed,
        )
    metrics = _response_metrics(payload, elapsed)

    response = (payload.get("response") or "").strip()
    if response:
        try:
            summary_text, candidates = _unpack_response(response)
        except (TypeError, ValueError) as exc:
            return SummaryResult(
                ERROR,
                normalized,
                error=f"invalid model output: {exc}",
                **metrics,
            )
        passages = resolve_passages(
            normalized,
            entry.get("_cues") or [],
            edits,
            candidates,
        )
        if summary_text:
            return SummaryResult(
                OK, normalized, text=summary_text, passages=passages, **metrics
            )

    output_tokens = payload.get("eval_count")
    if output_tokens is not None and output_tokens >= config.SUMMARY_NUM_PREDICT:
        error = "no answer — the output budget was exhausted"
    else:
        error = "the model returned an empty answer"
    return SummaryResult(ERROR, normalized, error=error, **metrics)
