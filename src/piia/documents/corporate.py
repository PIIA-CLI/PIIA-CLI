"""Deterministic extraction from an existing corporate agreement.

A PIIA never stands alone: it hangs off a founders' agreement, an operating
agreement, or an employment contract that already fixes the company name, the
governing law and the parties. Rather than asking the user to retype those, this
module reads them out of the document they already have -- with regexes, so the
result is inspectable and no document text is sent anywhere to get it.

The model only ever sees the excerpt selected by :meth:`CorporateContext.excerpt_for_model`,
which is limited to intellectual-property, confidentiality and governing-law
sections.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from piia.errors import DocumentError

#: Sections worth showing a drafting model, by keyword in the heading.
RELEVANT_HEADINGS = (
    "intellectual property",
    "prior invention",
    "invention",
    "confidential",
    "non-disclosure",
    "proprietary",
    "assignment",
    "governing law",
    "representations",
    "non-compete",
    "work product",
    "moral rights",
)

_ENTITY_SUFFIX = (
    r"(?:Inc\.?|Incorporated|Corp\.?|Corporation|LLC|L\.L\.C\.|Ltd\.?|Limited|"
    r"GmbH|PLC|P\.L\.C\.|LP|LLP|AB|AS|A/S|Oy|BV|B\.V\.|NV|SA|S\.A\.|Pty)"
)

_GOVERNING_LAW = re.compile(
    r"governed\s+by\s+and\s+construed\s+in\s+accordance\s+with\s+the\s+laws\s+of\s+"
    r"(?:the\s+)?(?P<law>[A-Z][^.,;]{2,80})",
    re.IGNORECASE,
)
_GOVERNING_LAW_SHORT = re.compile(
    r"(?:laws?|law)\s+of\s+(?:the\s+)?(?P<law>(?:State|Commonwealth|Province|Republic)\s+of\s+"
    r"[A-Z][A-Za-z ]{2,40}|[A-Z][A-Za-z ]{2,40})",
)
_COMPANY_AS = re.compile(
    r"^(?P<name>[^\n]{2,120}?)\s*\n+\s*as\s+(?:the\s+)?Company\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_COMPANY_LABEL = re.compile(
    r"(?:^|\n)\s*(?:\*\*)?Company(?:\*\*)?\s*[:\-]\s*(?P<name>[^\n]{2,120})",
    re.IGNORECASE,
)
_COMPANY_ENTITY = re.compile(rf"\b(?P<name>[A-Z][\w&.,'’\- ]{{2,60}}?\s{_ENTITY_SUFFIX})\b")
_PARTY_AS = re.compile(
    r"^(?P<name>[A-Z][^\n]{1,80}?)\s*\n+\s*as\s+(?P<role>[^\n]{2,120}?)\s*$",
    re.MULTILINE,
)
_PARTY_NUMBERED = re.compile(
    r"^\s*\d+\.\s+(?P<name>[A-Z][A-Za-z.'’\- ]{2,60}),\s+(?:residing|of|located)\b",
    re.MULTILINE,
)
_DATE_PATTERNS = (
    re.compile(
        r"\b(?P<date>(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},\s+\d{4})\b"
    ),
    re.compile(r"\b(?P<date>\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"\b(?P<date>\d{1,2}\s+(?:January|February|March|April|May|June|July|"
               r"August|September|October|November|December)\s+\d{4})\b"),
)
_HEADING = re.compile(
    r"^(?:#{1,4}\s*(?P<atx>.+?)\s*$"
    r"|\*\*(?P<bold>\d+\\?\.\s*[^*]+?)\*\*\s*$"
    r"|(?P<num>\d{1,2}\.\s+[A-Z][^\n]{3,80})$)",
    re.MULTILINE,
)

MAX_SECTION_CHARS = 2500
MAX_EXCERPT_CHARS = 7000


@dataclass
class Section:
    heading: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"heading": self.heading, "characters": len(self.text)}


@dataclass
class CorporateContext:
    """What a corporate document tells us, and nothing more."""

    path: str | None = None
    company_name: str | None = None
    governing_law: str | None = None
    effective_date: str | None = None
    parties: list[dict[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    digest: str | None = None
    characters: int = 0
    warnings: list[str] = field(default_factory=list)

    def party_role(self, name: str) -> str | None:
        """Best-effort role lookup for a signatory named on the command line."""
        needle = name.strip().lower()
        for party in self.parties:
            if party["name"].strip().lower() == needle:
                return party.get("role")
        for party in self.parties:
            if needle and needle in party["name"].strip().lower():
                return party.get("role")
        return None

    def excerpt_for_model(self, limit: int = MAX_EXCERPT_CHARS) -> str | None:
        """IP / confidentiality / governing-law sections only, truncated."""
        if not self.sections:
            return None
        chunks: list[str] = []
        used = 0
        for section in self.sections:
            block = f"## {section.heading}\n{section.text[:MAX_SECTION_CHARS]}"
            if used + len(block) > limit:
                break
            chunks.append(block)
            used += len(block)
        return "\n\n".join(chunks) or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "company_name": self.company_name,
            "governing_law": self.governing_law,
            "effective_date": self.effective_date,
            "parties": self.parties,
            "relevant_sections": [s.to_dict() for s in self.sections],
            "digest": self.digest,
            "characters": self.characters,
            "warnings": self.warnings,
        }


def parse_corporate_document(path: str | Path) -> CorporateContext:
    """Read a corporate agreement (Markdown, text, or exported DOCX-as-text)."""
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise DocumentError(
            f"Corporate document not found: {file_path}",
            details=[{"field": "corporate_document", "issue": "file does not exist"}],
            remediation="Pass a readable .md or .txt path with --corporate-document.",
        )
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise DocumentError(f"Could not read {file_path}: {exc}") from exc

    text = raw.decode("utf-8", errors="replace")
    context = CorporateContext(
        path=str(file_path),
        digest="sha256:" + hashlib.sha256(raw).hexdigest()[:32],
        characters=len(text),
    )
    normalized = text.replace("\r\n", "\n").replace("\\.", ".")

    context.company_name = _find_company(normalized)
    context.governing_law = _find_governing_law(normalized)
    context.effective_date = _find_date(normalized)
    context.parties = _find_parties(normalized)
    context.sections = _find_sections(normalized)

    if not context.company_name:
        context.warnings.append(
            "Could not identify the company name in the corporate document; "
            "pass --company to set it."
        )
    if not context.governing_law:
        context.warnings.append(
            "Could not identify a governing law clause; pass --governing-law to set it."
        )
    if not context.sections:
        context.warnings.append(
            "No intellectual-property or confidentiality sections were recognised; "
            "the drafting model will work from the repository analysis alone."
        )
    return context


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------
def _clean(value: str) -> str:
    """Normalise whitespace and drop markdown noise.

    Trailing periods are deliberately kept: a company name is very often
    "Acme Robotics, Inc." and stripping the period renames the party.
    """
    return re.sub(r"\s+", " ", value.replace("*", "").replace("\\", "")).strip(" ,;:\t")


def _find_company(text: str) -> str | None:
    match = _COMPANY_AS.search(text)
    if match:
        return _clean(match.group("name"))
    match = _COMPANY_LABEL.search(text)
    if match:
        return _clean(match.group("name"))
    # Fall back to the most frequent corporate entity name in the first pages.
    head = text[:20000]
    counts: dict[str, int] = {}
    for hit in _COMPANY_ENTITY.finditer(head):
        name = _clean(hit.group("name"))
        if len(name) > 3:
            counts[name] = counts.get(name, 0) + 1
    if counts:
        return max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
    return None


def _find_governing_law(text: str) -> str | None:
    match = _GOVERNING_LAW.search(text)
    if match:
        return _clean(match.group("law"))
    for hit in _GOVERNING_LAW_SHORT.finditer(text):
        candidate = _clean(hit.group("law"))
        if candidate.lower().startswith(("state of", "commonwealth of", "province of",
                                         "republic of")):
            return candidate
    return None


def _find_date(text: str) -> str | None:
    head = text[:8000]
    for pattern in _DATE_PATTERNS:
        match = pattern.search(head)
        if match:
            return match.group("date")
    return None


def _find_parties(text: str) -> list[dict[str, str]]:
    parties: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _PARTY_AS.finditer(text):
        name = _clean(match.group("name"))
        role = _clean(match.group("role"))
        if not name or name.lower().startswith(("and", "between", "table of")):
            continue
        if role.lower() in {"company", "the company"}:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            parties.append({"name": name, "role": role})
    for match in _PARTY_NUMBERED.finditer(text):
        name = _clean(match.group("name"))
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            parties.append({"name": name, "role": ""})
    return parties[:20]


def _find_sections(text: str) -> list[Section]:
    """Split on headings, keep the ones a PIIA drafter cares about."""
    matches = list(_HEADING.finditer(text))
    sections: list[Section] = []
    for index, match in enumerate(matches):
        heading = _clean(
            match.group("atx") or match.group("bold") or match.group("num") or ""
        )
        if not heading:
            continue
        lowered = heading.lower()
        if not any(keyword in lowered for keyword in RELEVANT_HEADINGS):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(body) < 40:  # a table-of-contents line, not a section
            continue
        sections.append(Section(heading=heading, text=body))
    # Deduplicate by heading, keeping the longest body (the real section beats
    # any table-of-contents echo).
    best: dict[str, Section] = {}
    for section in sections:
        key = section.heading.lower()
        if key not in best or len(section.text) > len(best[key].text):
            best[key] = section
    return sorted(best.values(), key=lambda s: -len(s.text))[:8]
