# Ported from NCAR/DAVINCI origin/develop, davinci_monet/plots/titles.py (Apache-2.0).
"""Helpers for plot titles and date subtitles."""

from __future__ import annotations

import re
from typing import Any

_TITLE_RULES = {
    "dates or years": re.compile(r"\b(?:19|20)\d{2}\b"),
    "comparison wording": re.compile(r"\b(?:vs\.?|versus)\b", re.IGNORECASE),
    "statistics": re.compile(r"\b(?:mean|std|rmse|nmb|nme|MB)\b|\b(?:N|R)\s*=|±", re.IGNORECASE),
    "raw variable/configuration keys": re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b"),
    "internal configuration labels": re.compile(
        r"\b(?:gls|rho|task\s*\d|phase\s*\d)\b|ρ", re.IGNORECASE
    ),
    "pairing-axis placeholders": re.compile(r"\b[xy]\b", re.IGNORECASE),
    "plot-type wording": re.compile(r"\b(?:scatter|histogram|time[- ]series)\b", re.IGNORECASE),
}


def title_violations(title: str) -> list[str]:
    """Return semantic title violations for the plotting protocol to report."""
    return [reason for reason, pattern in _TITLE_RULES.items() if pattern.search(title)]


def validate_title(title: str) -> None:
    """Reject dates, raw keys, statistics, or implementation details in a title."""
    failures = title_violations(title)
    if failures:
        raise ValueError(f"Invalid plot title {title!r}: {', '.join(failures)}")


TRAILING_DATE_TITLE_RE = re.compile(
    r"\s+(?:-|--|\u2013|\u2014)\s+"
    r"(?:"
    r"\d{4}-\d{2}-\d{2}(?:\s+(?:-|--|\u2013|\u2014|to)\s+\d{4}-\d{2}-\d{2})?"
    r"|(?:\d{1,2}\s+)?"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?,?\s+\d{4}"
    r")\s*$",
    re.IGNORECASE,
)

DATE_LABEL_RE = re.compile(
    r"^\s*(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|(?:\d{1,2}\s+)?"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?,?\s+\d{4}"
    r")\s*$",
    re.IGNORECASE,
)


def strip_trailing_date_title(title: str) -> str:
    """Remove a trailing date suffix from a plot title."""
    return TRAILING_DATE_TITLE_RE.sub("", title).rstrip()


def is_date_label(value: Any) -> bool:
    """Return whether a label is a display date."""
    return bool(DATE_LABEL_RE.match(str(value)))


def title_for_labeled_subset(
    base_title: str | None,
    label: Any,
    *,
    label_prefix: str = "",
    separator: str = " - ",
) -> tuple[str, str | None]:
    """Return ``(title, subtitle)`` for a labeled subset such as a flight."""
    from sarb_aod.evaluation.regions import REGION_DISPLAY_NAMES
    from sarb_aod.plots.labeling import source_display_name

    label_text = str(label)
    prefix = f"{label_prefix} " if label_prefix else ""
    fallback_title = label_prefix or "Subset"
    if is_date_label(label_text):
        return base_title or fallback_title, label_text
    label_text = REGION_DISPLAY_NAMES.get(label_text, source_display_name(label_text))
    label_title = f"{prefix}{label_text}"
    return (f"{base_title}{separator}{label_title}" if base_title else label_title), None
