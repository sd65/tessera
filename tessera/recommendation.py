"""Stable recommendation codes extracted from the summary's section 5b."""

from __future__ import annotations

import re

SUMMARY_SUFFICIENT = "summary_sufficient"
SELECTED_PASSAGES = "selected_passages"
FULL_VIDEO_RECOMMENDED = "full_video_recommended"
UNDETERMINED = "undetermined"

RECOMMENDATIONS = frozenset({
    SUMMARY_SUFFICIENT,
    SELECTED_PASSAGES,
    FULL_VIDEO_RECOMMENDED,
    UNDETERMINED,
})

_CHOICES = {
    "summary sufficient": SUMMARY_SUFFICIENT,
    "résumé suffisant": SUMMARY_SUFFICIENT,
    "watch selected passages": SELECTED_PASSAGES,
    "regarder les passages sélectionnés": SELECTED_PASSAGES,
    "full video recommended": FULL_VIDEO_RECOMMENDED,
    "visionnage complet recommandé": FULL_VIDEO_RECOMMENDED,
    "undetermined": UNDETERMINED,
    "indéterminé": UNDETERMINED,
    "indéterminée": UNDETERMINED,
}

_SECTION = re.compile(r"^\s*(\d{1,2})\.\s+")
_DECISION = re.compile(r"^\s*b\)\s*(.*?)\s+—\s+")


def from_choice(choice: str) -> str:
    """Map a known localized section-5b choice to its stable code."""
    return _CHOICES.get(" ".join(choice.split()).casefold(), UNDETERMINED)


def from_summary(summary: str | None) -> str:
    """Recover section 5b's choice from an already-rendered stored summary."""
    if not summary:
        return UNDETERMINED

    in_decision = False
    for line in summary.splitlines():
        heading = _SECTION.match(line)
        if heading:
            if in_decision:
                break
            in_decision = heading.group(1) == "5"
            continue
        if in_decision:
            decision = _DECISION.match(line)
            if decision:
                return from_choice(decision.group(1))
    return UNDETERMINED
