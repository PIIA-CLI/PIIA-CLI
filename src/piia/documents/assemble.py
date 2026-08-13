"""Assembly: turn analysis + draft content into one document object.

The document object is the single payload that every renderer reads -- Markdown,
JSON, HTML and DOCX are four views of it, so they cannot disagree. Section
numbering is assigned here, once, which is what lets the fixed clause text in
``clauses.py`` refer to "Section 4.3" and be right.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from piia.analysis.models import AnalysisBundle
from piia.config import Settings
from piia.documents.clauses import DISCLAIMER, EXHIBITS, RECITALS, SECTIONS
from piia.documents.corporate import CorporateContext
from piia.envelope import utc_now_iso
from piia.errors import UsageError
from piia.llm.drafting import DraftContent
from piia.version import DOCUMENT_SCHEMA_VERSION

OMITTED = "[Intentionally omitted.]"


@dataclass
class PiiaDocument:
    """A complete, renderable PIIA."""

    title: str
    company: dict[str, Any]
    signatory: dict[str, Any]
    effective_date: str
    governing_law: str
    recitals: list[str]
    sections: list[dict[str, Any]]
    exhibits: list[dict[str, Any]]
    prior_inventions: list[dict[str, Any]]
    review_checklist: list[dict[str, Any]]
    open_source_notes: list[str]
    supplemental_clauses: list[dict[str, Any]]
    executive_summary: str
    company_business: str
    technical_field: str
    evidence: dict[str, Any]
    provenance: dict[str, Any]
    disclaimer: str = DISCLAIMER
    schema_version: str = DOCUMENT_SCHEMA_VERSION
    generated_at: str = field(default_factory=utc_now_iso)

    @property
    def slug(self) -> str:
        base = f"{self.signatory.get('full_name', 'signatory')}-{self.company.get('name', 'company')}"
        return "".join(c if c.isalnum() else "-" for c in base.lower()).strip("-")[:80]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "title": self.title,
            "company": self.company,
            "signatory": self.signatory,
            "effective_date": self.effective_date,
            "governing_law": self.governing_law,
            "executive_summary": self.executive_summary,
            "company_business": self.company_business,
            "technical_field": self.technical_field,
            "recitals": self.recitals,
            "sections": self.sections,
            "prior_inventions": self.prior_inventions,
            "exhibits": self.exhibits,
            "review_checklist": self.review_checklist,
            "open_source_notes": self.open_source_notes,
            "supplemental_clauses": self.supplemental_clauses,
            "evidence": self.evidence,
            "provenance": self.provenance,
            "disclaimer": self.disclaimer,
        }


def assemble(
    *,
    bundle: AnalysisBundle,
    content: DraftContent,
    settings: Settings,
    corporate: CorporateContext | None = None,
    include_non_solicitation: bool = False,
) -> PiiaDocument:
    """Build the document. Every fact traces back to the analysis or the config."""
    doc_cfg = settings.document
    company_name = doc_cfg.company_name or (corporate.company_name if corporate else None)
    if not company_name:
        raise UsageError(
            "The company name is not known.",
            details=[{"field": "company", "issue": "not given and not found in any document"}],
            remediation=(
                "Pass --company \"Acme, Inc.\", set PIIA_COMPANY_NAME, or supply "
                "--corporate-document pointing at an agreement that names the company."
            ),
        )
    signatory_name = doc_cfg.signatory_name
    if not signatory_name:
        raise UsageError(
            "The signatory name is not known.",
            details=[{"field": "signatory", "issue": "required"}],
            remediation='Pass --signatory "First Last" or set PIIA_SIGNATORY_NAME.',
        )

    governing_law = (
        doc_cfg.governing_law
        or (corporate.governing_law if corporate else None)
        or "[GOVERNING LAW TO BE SPECIFIED]"
    )
    effective_date = (
        doc_cfg.effective_date
        or (corporate.effective_date if corporate else None)
        or date.today().isoformat()
    )
    signatory_title = doc_cfg.signatory_title or (
        corporate.party_role(signatory_name) if corporate else None
    )

    fields = {
        "company": company_name,
        "signatory": signatory_name,
        "company_business": content.company_business,
        "governing_law": governing_law,
        "effective_date": effective_date,
        "prior_invention_count": len(content.prior_inventions),
    }

    sections: list[dict[str, Any]] = []
    for number, spec in enumerate(SECTIONS, start=1):
        omitted = spec["id"] == "restrictive-covenants" and not include_non_solicitation
        clauses = [
            {
                "number": f"{number}.{index}",
                "text": OMITTED if omitted else text.format(**fields),
            }
            for index, text in enumerate(spec["clauses"], start=1)
        ]
        sections.append(
            {
                "id": spec["id"],
                "number": number,
                "heading": spec["heading"] + (" (Intentionally Omitted)" if omitted else ""),
                "clauses": clauses,
                "omitted": omitted,
                "jurisdiction_sensitive": bool(spec.get("jurisdiction_sensitive")),
                "drafting_note": (
                    spec["drafting_note"].format(**fields) if spec.get("drafting_note") else None
                ),
            }
        )

    if content.supplemental_clauses:
        number = len(sections) + 1
        sections.append(
            {
                "id": "supplemental",
                "number": number,
                "heading": "Supplemental Provisions",
                "clauses": [
                    {
                        "number": f"{number}.{index}",
                        "text": f"{clause['heading']}. {clause['text']}",
                        "rationale": clause.get("rationale"),
                        "source": clause.get("source", "model"),
                    }
                    for index, clause in enumerate(content.supplemental_clauses, start=1)
                ],
                "omitted": False,
                "jurisdiction_sensitive": True,
                "drafting_note": (
                    "These provisions were proposed by a language model in response to specific "
                    "findings in the analysis. Review each one before retaining it."
                ),
            }
        )

    evidence = _evidence(bundle, corporate)
    exhibits = [
        {**spec, "preamble": spec["preamble"].format(**fields)} for spec in EXHIBITS
    ]

    provenance = {
        "generated_at": utc_now_iso(),
        "tool": "piia-cli",
        "tool_version": bundle.tool_versions.get("piia"),
        "analysis_digest": evidence["analysis_digest"],
        "corporate_document_digest": corporate.digest if corporate else None,
        "model": settings.llm.model if content.used_model else None,
        "base_url": settings.llm.base_url if content.used_model else None,
        "temperature": settings.llm.temperature if content.used_model else None,
        "api_style": settings.llm.api_style if content.used_model else None,
        "model_generated_fields": content.generated_fields,
        "deterministic_fields": content.deterministic_fields,
        "model_calls": content.model_calls,
        "degraded": content.degraded,
        "tool_versions": bundle.tool_versions,
        "repowise_used": any(
            (p.repowise or {}).get("indexed") for p in [bundle.target, *bundle.prior_works]
        ),
    }

    return PiiaDocument(
        title=f"Proprietary Information and Inventions Agreement -- {company_name}",
        company={
            "name": company_name,
            "jurisdiction": doc_cfg.company_jurisdiction,
            "repository": bundle.target.ref.url or bundle.target.ref.path,
        },
        signatory={
            "full_name": signatory_name,
            "title": signatory_title,
            "prior_work_count": len(bundle.prior_works),
        },
        effective_date=effective_date,
        governing_law=governing_law,
        recitals=[r.format(**fields) for r in RECITALS],
        sections=sections,
        exhibits=exhibits,
        prior_inventions=content.prior_inventions,
        review_checklist=content.review_checklist,
        open_source_notes=content.open_source_notes,
        supplemental_clauses=content.supplemental_clauses,
        executive_summary=content.executive_summary,
        company_business=content.company_business,
        technical_field=content.technical_field,
        evidence=evidence,
        provenance=provenance,
    )


def _evidence(bundle: AnalysisBundle, corporate: CorporateContext | None) -> dict[str, Any]:
    """Exhibit B content: the reproducible record behind Exhibit A."""
    payload = bundle.to_dict()

    def row(analysis_dict: dict[str, Any]) -> dict[str, Any]:
        git = analysis_dict["git"]
        return {
            "repository": analysis_dict["name"],
            "url": analysis_dict["url"],
            "resolved_from": analysis_dict["repository"]["source"],
            "local_path": analysis_dict["repository"]["path"],
            "commit": git["commit"],
            "short_commit": git["short_commit"],
            "branch": git["branch"],
            "first_commit_date": git["first_commit_date"],
            "last_commit_date": git["last_commit_date"],
            "commit_count": git["commit_count"],
            "contributor_count": git["contributor_count"],
            "license": analysis_dict["license"],
            "source_files": analysis_dict["metrics"].get("analyzed_files"),
            "primary_language": analysis_dict["technology"]["primary_language"],
            "dependency_count": analysis_dict["technology"]["dependency_count"],
        }

    return {
        "analysis_digest": payload["digest"],
        "analysis_generated_at": payload["generated_at"],
        "analysis_schema_version": payload["schema_version"],
        "method": (
            "Each repository was walked on disk; languages, manifests, declared dependencies "
            "and infrastructure files were inventoried against a fixed signature table; git "
            "history supplied commit dates, counts and contributors. Prior works were then "
            "scored against the target work by weighted technology coverage (65%) and shared "
            "declared dependencies (35%). No model was involved in any figure in this Exhibit."
        ),
        "target": row(payload["target"]),
        "prior_works": [row(p) for p in payload["prior_works"]],
        "comparisons": [
            {
                "repository": o["repository"],
                "overlap_score": o["overlap_score"],
                "jaccard": o["jaccard"],
                "dependency_overlap": o["dependency_overlap"],
                "relatedness": o["relatedness"],
                "predates_target": o["predates_target"],
                "shared_technologies": o["shared_technologies"],
                "rationale": o["rationale"],
            }
            for o in payload["overlaps"]
        ],
        "aggregate": payload["aggregate"],
        "tool_versions": payload["tool_versions"],
        "analysis_notes": payload["notes"],
        "corporate_document": corporate.to_dict() if corporate else None,
    }
