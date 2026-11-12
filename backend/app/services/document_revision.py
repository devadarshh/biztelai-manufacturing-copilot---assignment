from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

REVISION_LABEL = re.compile(
    r"\b(?:revision|rev\.?|version|ver\.?)\s*(?:no\.?|number)?\s*[:#._-]?\s*([a-z]|\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)
FILENAME_REVISION = re.compile(
    r"(?:^|[\s._-])(?:revision|rev|version|ver|r)[\s._-]*([a-z]|\d+(?:\.\d+)*)(?=$|[\s._-])",
    re.IGNORECASE,
)
EFFECTIVE_LABEL = re.compile(r"\beffective\s+(?:date|from)\b", re.IGNORECASE)
ISO_DATE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
TEXT_DATE = re.compile(
    r"\b(\d{1,2})[\s-]+([A-Za-z]{3,9})\.?,?\s+(20\d{2})\b"
    r"|\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(20\d{2})\b"
)
FILENAME_DATE = re.compile(
    r"(?<!\d)20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?!\d)"
    r"|(?<!\d)\d{1,2}[\s-]+[A-Za-z]{3,9}\.?,?\s+20\d{2}(?!\d)"
    r"|(?<![A-Za-z])[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+20\d{2}(?!\d)"
)
FAMILY_REVISION = re.compile(
    r"(?:^|[\s._-])(?:revision|rev|version|ver|r)[\s._-]*[a-z]?\d+(?:\.\d+)*(?=$|[\s._-])",
    re.IGNORECASE,
)


def _parse_date(value: str) -> date | None:
    value = value.strip().rstrip(".,")
    iso_match = ISO_DATE.search(value)
    if iso_match:
        try:
            return date(*(int(part) for part in iso_match.groups()))
        except ValueError:
            return None

    for pattern, formats in (
        (TEXT_DATE, ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y")),
    ):
        match = pattern.search(value)
        if match:
            normalized = " ".join(group for group in match.groups() if group)
            for date_format in formats:
                try:
                    return datetime.strptime(normalized, date_format).date()
                except ValueError:
                    continue
    return None


def _effective_date(text: str) -> date | None:
    for line in text.splitlines():
        label = EFFECTIVE_LABEL.search(line)
        if label:
            parsed = _parse_date(line[label.end():])
            if parsed:
                return parsed
    return None


def _revision_from_text(text: str) -> str | None:
    for line in text.splitlines():
        match = REVISION_LABEL.search(line)
        if match:
            return match.group(1).upper()
    return None


def _revision_from_filename(filename: str) -> str | None:
    match = FILENAME_REVISION.search(Path(filename).stem)
    return match.group(1).upper() if match else None


def _family_name(filename: str) -> str:
    stem = Path(filename).stem
    stem = FILENAME_REVISION.sub(" ", stem)
    stem = FAMILY_REVISION.sub(" ", stem)
    stem = FILENAME_DATE.sub(" ", stem)
    family = re.sub(r"[^a-zA-Z0-9]+", "", stem).upper()
    return family or re.sub(r"[^a-zA-Z0-9]+", "", Path(filename).stem).upper()


def _revision_key(revision: str | None) -> tuple[tuple[int, int | str], ...]:
    if not revision:
        return ()
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.lower())
        for part in re.findall(r"\d+|[A-Za-z]+", revision)
    )


def inspect_revision(filename: str, text: str) -> dict:
    """Extract only explicitly labeled revisions and effective dates."""
    revision = _revision_from_text(text) or _revision_from_filename(filename)
    effective_date = _effective_date(text)
    if effective_date is None:
        for line in text.splitlines():
            if REVISION_LABEL.search(line):
                effective_date = _parse_date(line)
                if effective_date:
                    break
    if effective_date is None:
        for match in FILENAME_DATE.finditer(Path(filename).stem):
            effective_date = _parse_date(match.group())
            if effective_date:
                break

    return {
        "document_family": _family_name(filename),
        "revision": revision,
        "effective_date": effective_date.isoformat() if effective_date else None,
    }


def classify_revisions(
    documents: list[dict],
    effective_today: date | None = None,
) -> list[dict]:
    """Mark a unique latest effective revision; leave ties and unknown revisions explicit."""
    today = effective_today or date.today()
    classified = [dict(document) for document in documents]
    families: dict[str, list[int]] = {}

    for index, document in enumerate(classified):
        families.setdefault(document["document_family"], []).append(index)
        document["revision_status"] = "revision_unknown"
        document["superseded_by"] = None

    for indices in families.values():
        eligible = []
        for index in indices:
            effective = classified[index].get("effective_date")
            parsed = date.fromisoformat(effective) if effective else None
            if parsed and parsed > today:
                classified[index]["revision_status"] = "not_yet_effective"
            else:
                eligible.append(index)

        if not eligible:
            continue
        if len(indices) == 1:
            document = classified[eligible[0]]
            if document.get("revision") or document.get("effective_date"):
                document["revision_status"] = "current"
            continue

        def rank_by_effective_date(index: int) -> tuple[date, tuple[tuple[int, int | str], ...]]:
            document = classified[index]
            effective = document.get("effective_date")
            return (
                date.fromisoformat(effective) if effective else date.min,
                _revision_key(document.get("revision")),
            )

        def rank_by_revision(index: int) -> tuple[tuple[tuple[int, int | str], ...], date]:
            document = classified[index]
            effective = document.get("effective_date")
            return (
                _revision_key(document.get("revision")),
                date.fromisoformat(effective) if effective else date.min,
            )

        have_all_dates = all(classified[index].get("effective_date") for index in eligible)
        have_all_revisions = all(classified[index].get("revision") for index in eligible)
        if have_all_dates:
            rank = rank_by_effective_date
        elif have_all_revisions:
            rank = rank_by_revision
        else:
            continue

        best_rank = max(rank(index) for index in eligible)
        winners = [index for index in eligible if rank(index) == best_rank]
        if len(winners) != 1:
            continue

        winner = winners[0]
        classified[winner]["revision_status"] = "current"
        winner_name = classified[winner]["name"]
        for index in eligible:
            if index == winner:
                continue
            if rank(index) < best_rank:
                classified[index]["revision_status"] = "superseded"
                classified[index]["superseded_by"] = winner_name

    return classified
