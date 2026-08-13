"""Drafting: deterministic evidence in, document content out.

Three model tasks, each with a deterministic fallback that produces a complete,
usable draft on its own:

1. Characterise the company's business and technical field.
2. Write the Exhibit A entry for each prior work (batched).
3. Produce a counsel review checklist, license notes and supplemental clauses.

If the endpoint is unreachable, the model misbehaves, or the user passes
``--no-llm``, every field falls back and the run still succeeds -- it is
recorded as ``deterministic`` in the provenance so a reader can tell which
sentences a model wrote.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from piia.analysis.models import (
    RELATEDNESS_HIGH,
    RELATEDNESS_MODERATE,
    RELATEDNESS_NONE,
    AnalysisBundle,
    Overlap,
    RepoAnalysis,
)
from piia.errors import LLMError
from piia.llm.client import ChatClient
from piia.llm.prompts import (
    FIELD_OF_BUSINESS_KEYS,
    PRIOR_INVENTION_KEYS,
    REVIEW_KEYS,
    field_of_business_messages,
    prior_inventions_messages,
    review_messages,
)

Progress = Callable[[str], None]

#: Prior works per model call. Small enough that a 16k-context model copes,
#: large enough that the model can see related works together.
BATCH_SIZE = 4

COPYLEFT = ("gpl", "agpl", "lgpl", "mpl", "osl", "epl", "cc-by-sa")


@dataclass
class DraftContent:
    """Everything the document template needs, plus where each part came from."""

    company_business: str
    technical_field: str
    executive_summary: str
    prior_inventions: list[dict[str, Any]] = field(default_factory=list)
    review_checklist: list[dict[str, Any]] = field(default_factory=list)
    open_source_notes: list[str] = field(default_factory=list)
    supplemental_clauses: list[dict[str, Any]] = field(default_factory=list)
    generated_fields: list[str] = field(default_factory=list)
    deterministic_fields: list[str] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    @property
    def used_model(self) -> bool:
        return bool(self.generated_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_business": self.company_business,
            "technical_field": self.technical_field,
            "executive_summary": self.executive_summary,
            "prior_inventions": self.prior_inventions,
            "review_checklist": self.review_checklist,
            "open_source_notes": self.open_source_notes,
            "supplemental_clauses": self.supplemental_clauses,
            "generated_fields": self.generated_fields,
            "deterministic_fields": self.deterministic_fields,
            "model_calls": self.model_calls,
            "degraded": self.degraded,
        }


def draft(
    bundle: AnalysisBundle,
    *,
    company_name: str,
    signatory_name: str,
    governing_law: str | None = None,
    corporate_excerpt: str | None = None,
    client: ChatClient | None = None,
    system_override: str | None = None,
    progress: Progress | None = None,
    batch_size: int = BATCH_SIZE,
) -> DraftContent:
    """Produce document content, with the model where available."""
    say = progress or (lambda _m: None)

    content = DraftContent(
        company_business=_fallback_company_business(bundle, company_name),
        technical_field=_fallback_technical_field(bundle),
        executive_summary=_fallback_summary(bundle, company_name, signatory_name),
    )
    content.prior_inventions = [
        _fallback_invention(p, bundle.overlap_for(p.name), signatory_name)
        for p in bundle.prior_works
    ]
    content.review_checklist = _fallback_checklist(bundle)
    content.open_source_notes = _fallback_license_notes(bundle)
    content.deterministic_fields = [
        "company_business",
        "technical_field",
        "executive_summary",
        "prior_inventions",
        "review_checklist",
        "open_source_notes",
    ]

    if client is None:
        content.degraded.append("No model was used; all content is deterministic.")
        return content

    # -- Task 1 -----------------------------------------------------------
    say("Drafting the company's field of business")
    try:
        data, response = client.complete_json(
            field_of_business_messages(
                bundle,
                company_name=company_name,
                corporate_excerpt=corporate_excerpt,
                system_override=system_override,
            ),
            required_keys=FIELD_OF_BUSINESS_KEYS,
        )
        content.company_business = _text(data.get("company_business"), content.company_business)
        content.technical_field = _text(data.get("technical_field"), content.technical_field)
        content.executive_summary = _text(data.get("executive_summary"), content.executive_summary)
        _mark(content, "company_business", "technical_field", "executive_summary")
        content.model_calls.append({"task": "field_of_business", **response.to_dict()})
    except LLMError as exc:
        content.degraded.append(f"field_of_business fell back to deterministic text: {exc.message}")
        say(f"! field_of_business failed ({exc.code}); using deterministic text")

    # -- Task 2 -----------------------------------------------------------
    batches = [
        bundle.prior_works[i : i + batch_size]
        for i in range(0, len(bundle.prior_works), max(1, batch_size))
    ]
    drafted: dict[str, dict[str, Any]] = {}
    for number, batch in enumerate(batches, start=1):
        names = ", ".join(p.name for p in batch)
        say(f"Drafting Exhibit A entries {number}/{len(batches)}: {names}")
        try:
            data, response = client.complete_json(
                prior_inventions_messages(
                    bundle,
                    batch,
                    company_name=company_name,
                    signatory_name=signatory_name,
                    company_business=content.company_business,
                    system_override=system_override,
                ),
                required_keys=PRIOR_INVENTION_KEYS,
            )
            entries = data.get("prior_inventions") or []
            matched = _match_entries(entries, batch)
            drafted.update(matched)
            content.model_calls.append(
                {"task": f"prior_inventions[{number}]", "repositories": [p.name for p in batch],
                 **response.to_dict()}
            )
            missing = [p.name for p in batch if p.name not in matched]
            if missing:
                content.degraded.append(
                    "Model omitted Exhibit A entries for: " + ", ".join(missing)
                )
        except LLMError as exc:
            content.degraded.append(
                f"Exhibit A batch {number} ({names}) fell back to deterministic text: {exc.message}"
            )
            say(f"! Exhibit A batch {number} failed ({exc.code}); using deterministic entries")

    if drafted:
        merged: list[dict[str, Any]] = []
        for prior in bundle.prior_works:
            fallback = _fallback_invention(
                prior, bundle.overlap_for(prior.name), signatory_name
            )
            entry = drafted.get(prior.name)
            merged.append(
                _merge_invention(fallback, entry, _verified_technologies(prior))
                if entry
                else fallback
            )
        content.prior_inventions = merged
        _mark(content, "prior_inventions")

    # -- Task 3 -----------------------------------------------------------
    say("Drafting the counsel review checklist")
    try:
        data, response = client.complete_json(
            review_messages(
                bundle,
                company_name=company_name,
                signatory_name=signatory_name,
                governing_law=governing_law,
                company_business=content.company_business,
                system_override=system_override,
            ),
            required_keys=REVIEW_KEYS,
        )
        checklist = [
            {
                "item": _text(item.get("item"), ""),
                "why": _text(item.get("why"), ""),
                "severity": str(item.get("severity", "medium")).lower(),
                "source": "model",
            }
            for item in (data.get("review_checklist") or [])
            if isinstance(item, dict) and item.get("item")
        ]
        if checklist:
            # Deterministic findings are load-bearing, so they are kept and the
            # model's items are added after them.
            content.review_checklist = _dedupe_checklist(content.review_checklist + checklist)
            _mark(content, "review_checklist")
        notes = [str(n).strip() for n in (data.get("open_source_notes") or []) if str(n).strip()]
        if notes:
            content.open_source_notes = _dedupe(content.open_source_notes + notes)
            _mark(content, "open_source_notes")
        content.supplemental_clauses = [
            {
                "heading": _text(c.get("heading"), "Supplemental clause"),
                "text": _text(c.get("text"), ""),
                "rationale": _text(c.get("rationale"), ""),
                "source": "model",
            }
            for c in (data.get("supplemental_clauses") or [])
            if isinstance(c, dict) and c.get("text")
        ][:3]
        if content.supplemental_clauses:
            _mark(content, "supplemental_clauses")
        content.model_calls.append({"task": "review", **response.to_dict()})
    except LLMError as exc:
        content.degraded.append(f"review checklist fell back to deterministic items: {exc.message}")
        say(f"! review checklist failed ({exc.code}); using deterministic items")

    content.deterministic_fields = [
        f for f in content.deterministic_fields if f not in content.generated_fields
    ]
    return content


# ---------------------------------------------------------------------------
# Merging and normalisation
# ---------------------------------------------------------------------------
def _mark(content: DraftContent, *fields: str) -> None:
    for name in fields:
        if name not in content.generated_fields:
            content.generated_fields.append(name)


def _text(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    text = str(value).strip()
    return text or fallback


def _match_entries(
    entries: list[Any], batch: list[RepoAnalysis]
) -> dict[str, dict[str, Any]]:
    """Match model entries to repositories by name, then by position.

    Small models sometimes rename or reorder; positional fallback keeps their
    output usable without letting an entry attach to the wrong repository when
    the name is present and wrong.
    """
    by_name = {p.name.lower(): p.name for p in batch}
    matched: dict[str, dict[str, Any]] = {}
    unclaimed = [p.name for p in batch]
    positional: list[dict[str, Any]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("repository", "")).strip()
        key = raw.lower().rsplit("/", 1)[-1]
        name = by_name.get(key)
        if name and name in unclaimed:
            matched[name] = entry
            unclaimed.remove(name)
        else:
            positional.append(entry)

    for name, entry in zip(unclaimed, positional, strict=False):
        matched[name] = entry
    return matched


def _verified_technologies(analysis: RepoAnalysis) -> dict[str, str]:
    """Every technology the scan actually found, keyed by lowercase name.

    This is the full inventory, not the shortened display list: the prompt shows
    the model every category, so quarantining a name it read from that evidence
    would flag its own correct answers.
    """
    verified: dict[str, str] = {}
    for values in analysis.technology.categorized().values():
        for value in values:
            verified[value.lower()] = value
    for dep in analysis.technology.dependencies:
        verified.setdefault(dep.name.lower(), dep.name)
    if analysis.technology.primary_language:
        verified.setdefault(
            analysis.technology.primary_language.lower(), analysis.technology.primary_language
        )
    return verified


def _merge_invention(
    fallback: dict[str, Any], entry: dict[str, Any], verified: dict[str, str]
) -> dict[str, Any]:
    """Take the model's prose, keep the deterministic facts authoritative."""
    merged = dict(fallback)
    merged.update(
        {
            "title": _text(entry.get("title"), fallback["title"]),
            "description": _text(entry.get("description"), fallback["description"]),
            "relation_to_company_business": _text(
                entry.get("relation_to_company_business"),
                fallback["relation_to_company_business"],
            ),
            "carve_out_language": _text(
                entry.get("carve_out_language"), fallback["carve_out_language"]
            ),
            "notes": _text(entry.get("notes"), fallback.get("notes", "")),
            "incorporation_risk": str(
                entry.get("incorporation_risk") or fallback["incorporation_risk"]
            ).lower(),
            "source": "model",
        }
    )
    declared = entry.get("technologies")
    if isinstance(declared, list) and declared:
        # Names the scan found are normalised to its canonical casing; names it
        # did not find are kept, but quarantined so a reader can check them.
        kept = [verified.get(str(t).lower(), str(t)) for t in declared][:10]
        merged["technologies"] = kept or fallback["technologies"]
        unsupported = [str(t) for t in declared if str(t).lower() not in verified]
        if unsupported:
            merged["unverified_technologies"] = unsupported[:10]
    if merged["incorporation_risk"] not in {"none", "possible", "likely"}:
        merged["incorporation_risk"] = fallback["incorporation_risk"]
    return merged


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _dedupe_checklist(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"high": 0, "medium": 1, "low": 2}
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("item", "")).strip().lower()[:80]
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return sorted(out, key=lambda i: order.get(str(i.get("severity", "medium")), 1))


# ---------------------------------------------------------------------------
# Deterministic fallbacks. These are what runs with --no-llm, so they have to
# stand on their own as a usable draft.
# ---------------------------------------------------------------------------
def _humanize(name: str) -> str:
    cleaned = name.replace("_", " ").replace("-", " ").strip()
    words = [w if w.isupper() else w.capitalize() for w in cleaned.split()]
    return " ".join(words) or name


def _top_tech(analysis: RepoAnalysis, limit: int = 8) -> list[str]:
    tech = analysis.technology
    ordered: list[str] = []
    for group in (tech.ml_ai, tech.frameworks, tech.datastores, tech.protocols, tech.cloud,
                  tech.frontend, tech.infrastructure):
        for item in group:
            if item not in ordered:
                ordered.append(item)
    if tech.primary_language and tech.primary_language not in ordered:
        ordered.insert(0, tech.primary_language)
    return ordered[:limit]


def _fallback_company_business(bundle: AnalysisBundle, company_name: str) -> str:
    tech = bundle.target.technology
    stack = ", ".join(_top_tech(bundle.target, 10)) or "software"
    described = bundle.target.description or (
        f"the {bundle.target.name} codebase"
    )
    return (
        f"{company_name} develops and operates software in the field represented by "
        f"{described}. The company's current codebase is implemented primarily in "
        f"{tech.primary_language or 'multiple languages'} and is built on {stack}. "
        "For the purposes of this Agreement, the Company's field of business comprises "
        "the design, development, operation and commercialisation of software systems "
        "of this kind, together with their natural extensions and improvements."
    )


def _fallback_technical_field(bundle: AnalysisBundle) -> str:
    tech = bundle.target.technology
    if tech.ml_ai:
        return f"Applied artificial intelligence and machine learning systems ({', '.join(tech.ml_ai[:4])})."
    if tech.frameworks:
        return f"Software engineering using {', '.join(tech.frameworks[:4])}."
    return f"Software engineering in {tech.primary_language or 'multiple languages'}."


def _fallback_summary(bundle: AnalysisBundle, company_name: str, signatory_name: str) -> str:
    close = [
        o for o in bundle.overlaps if o.relatedness in {RELATEDNESS_HIGH, RELATEDNESS_MODERATE}
    ]
    return (
        f"This Agreement records the proprietary information and invention assignment "
        f"obligations between {company_name} and {signatory_name}, and carves out "
        f"{signatory_name}'s pre-existing works. A deterministic analysis compared "
        f"{len(bundle.prior_works)} prior repositories against the Company's "
        f"{bundle.target.name} repository. Of those, {len(close)} show a moderate or high "
        f"technical relationship to the Company's field of business and are disclosed in "
        f"Exhibit A with express carve-out language. The analysis is reproducible from the "
        f"commits recorded in Exhibit B."
    )


def _fallback_invention(
    analysis: RepoAnalysis, overlap: Overlap | None, signatory_name: str
) -> dict[str, Any]:
    tech = _top_tech(analysis, 10)
    first = analysis.git.first_commit_date or "an unrecorded date"
    last = analysis.git.last_commit_date or "an unrecorded date"
    description = (
        f"{_humanize(analysis.name)} is a "
        f"{analysis.technology.primary_language or 'software'} work "
        f"first committed on {first} and last updated on {last}, comprising "
        f"{analysis.metrics.get('analyzed_files', 0)} source files across "
        f"{analysis.git.commit_count} commits."
    )
    if analysis.description:
        description += f" Its stated purpose is: {analysis.description.rstrip('.')}."
    if tech:
        description += f" It is built on {', '.join(tech)}."

    relatedness = overlap.relatedness if overlap else RELATEDNESS_NONE
    if overlap and overlap.shared_technologies:
        relation = (
            f"Measured relatedness to the Company's field of business is "
            f"{relatedness} (score {overlap.overlap_score:.2f}), based on shared use of "
            f"{', '.join(overlap.shared_technologies[:6])}."
        )
    else:
        relation = (
            f"Measured relatedness to the Company's field of business is {relatedness}; "
            "no substantive shared technology was detected."
        )

    risk = {
        RELATEDNESS_HIGH: "likely",
        RELATEDNESS_MODERATE: "possible",
    }.get(relatedness, "none")

    carve_out = (
        f"{signatory_name} retains all right, title and interest in "
        f"{_humanize(analysis.name)} as it exists at the commit recorded in Exhibit B, "
        "together with its subsequent independent development. Nothing in this Agreement "
        "assigns that work to the Company. To the extent it is incorporated into a Company "
        "product, process or service, the licence granted under this Agreement applies to "
        "that incorporated portion only."
    )

    notes: list[str] = []
    if not analysis.license:
        notes.append("No license file was detected; ownership terms should be stated expressly.")
    elif any(marker in (analysis.license or "").lower() for marker in COPYLEFT):
        notes.append(
            f"Declared license {analysis.license} is copyleft; incorporation into a "
            "proprietary Company product may trigger source-disclosure obligations."
        )
    if len(analysis.git.contributors) > 1:
        notes.append(
            f"{len(analysis.git.contributors)} contributors appear in the commit history "
            f"({', '.join(analysis.git.contributors[:3])}...); joint authorship must be confirmed."
        )
    if overlap and overlap.predates_target is False:
        notes.append(
            "Commit history does not show this work predating the Company's repository; "
            "confirm it qualifies as prior work."
        )

    return {
        "repository": analysis.name,
        "url": analysis.ref.url,
        "title": _humanize(analysis.name),
        "description": description,
        "technologies": tech,
        "relation_to_company_business": relation,
        "carve_out_language": carve_out,
        "incorporation_risk": risk,
        "relatedness": relatedness,
        "overlap_score": round(overlap.overlap_score, 4) if overlap else 0.0,
        "ownership": f"{signatory_name} (asserted)",
        "license": analysis.license,
        "commit": analysis.git.commit,
        "first_commit_date": analysis.git.first_commit_date,
        "last_commit_date": analysis.git.last_commit_date,
        "contributors": analysis.git.contributors[:10],
        "notes": " ".join(notes),
        "source": "deterministic",
    }


def _fallback_checklist(bundle: AnalysisBundle) -> list[dict[str, Any]]:
    """Rule-based findings. Each one points at a specific piece of evidence."""
    items: list[dict[str, Any]] = []

    for overlap in bundle.overlaps:
        prior = next((p for p in bundle.prior_works if p.name == overlap.repository), None)
        if prior is None:
            continue
        if overlap.relatedness == RELATEDNESS_HIGH:
            items.append(
                {
                    "item": f"Confirm the carve-out boundary for {prior.name}.",
                    "why": (
                        f"Overlap score {overlap.overlap_score:.2f} (high) with the target work; "
                        f"shares {', '.join(overlap.shared_technologies[:5])}. A boundary this "
                        "close needs an express statement of what is and is not assigned."
                    ),
                    "severity": "high",
                    "source": "deterministic",
                }
            )
        if prior.license and any(m in prior.license.lower() for m in COPYLEFT):
            items.append(
                {
                    "item": f"Review the {prior.license} license on {prior.name}.",
                    "why": (
                        "Copyleft terms can require source disclosure if the work is "
                        "incorporated into a proprietary Company product."
                    ),
                    "severity": "high" if overlap.relatedness == RELATEDNESS_HIGH else "medium",
                    "source": "deterministic",
                }
            )
        if not prior.license:
            items.append(
                {
                    "item": f"Establish the license and ownership basis for {prior.name}.",
                    "why": "No license file was detected in the repository.",
                    "severity": "medium",
                    "source": "deterministic",
                }
            )
        if len(prior.git.contributors) > 1:
            items.append(
                {
                    "item": f"Confirm sole or joint authorship of {prior.name}.",
                    "why": (
                        f"The commit history shows {len(prior.git.contributors)} contributors: "
                        f"{', '.join(prior.git.contributors[:4])}."
                    ),
                    "severity": "medium",
                    "source": "deterministic",
                }
            )
        if overlap.predates_target is False:
            items.append(
                {
                    "item": f"Verify that {prior.name} is genuinely prior work.",
                    "why": (
                        f"Its first commit ({prior.git.first_commit_date}) does not precede the "
                        f"target work's ({bundle.target.git.first_commit_date})."
                    ),
                    "severity": "high",
                    "source": "deterministic",
                }
            )
        if prior.git.is_shallow:
            items.append(
                {
                    "item": f"Re-run the analysis on a full clone of {prior.name}.",
                    "why": "The checkout was shallow, so the first-commit date is not reliable.",
                    "severity": "low",
                    "source": "deterministic",
                }
            )

    if not bundle.target.git.first_commit_date:
        items.append(
            {
                "item": "Establish the start date of the Company's work.",
                "why": "No commit dates were available for the target repository.",
                "severity": "medium",
                "source": "deterministic",
            }
        )
    return _dedupe_checklist(items)


def _fallback_license_notes(bundle: AnalysisBundle) -> list[str]:
    notes: list[str] = []
    for prior in bundle.prior_works:
        if prior.license and any(m in prior.license.lower() for m in COPYLEFT):
            notes.append(
                f"{prior.name} is licensed under {prior.license}. If any part of it is "
                "incorporated into a Company product, the Company may be obliged to make "
                "corresponding source available under the same terms."
            )
        elif prior.license and prior.license.upper() == "PROPRIETARY":
            notes.append(
                f"{prior.name} carries an all-rights-reserved notice; confirm who holds "
                "those rights before relying on the carve-out."
            )
    return notes
