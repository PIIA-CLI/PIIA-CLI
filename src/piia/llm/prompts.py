"""Prompts and the context compaction that feeds them.

Design rules for this module:

* **The model never invents facts.** Every prompt hands the model the
  deterministic evidence and instructs it to characterise only what it was
  given. Repository names, dates, licenses and technology lists come from the
  analysis, not from the model.
* **The model never writes the skeleton.** Operative clauses live in the
  template (``piia/templates``). The model fills named slots -- descriptions,
  the field-of-business characterisation, per-invention carve-out language --
  which is where prose judgement actually helps.
* **Everything is budgeted.** Contexts are compacted to a predictable size so a
  16k-context local model and a 1M-context hosted model both work.
"""

from __future__ import annotations

import json
from typing import Any

from piia.analysis.models import AnalysisBundle, Overlap, RepoAnalysis
from piia.llm.client import Message

DEFAULT_SYSTEM_PROMPT = """\
You are a technology transactions attorney's drafting assistant. You prepare the \
factual and descriptive portions of a Proprietary Information and Inventions \
Agreement (PIIA), also called a CIIAA or an Invention Assignment Agreement.

Your role and its limits:
- You characterise software repositories that a person built BEFORE joining a \
company, so those works can be disclosed and carved out as "Prior Inventions".
- You write plain, precise, professional English. Short sentences. No marketing \
language, no hedging, no filler.
- You state only what the supplied evidence supports. If the evidence does not \
establish something, say so explicitly rather than guessing. Never invent \
repository names, dates, authors, licenses, customers, or revenue.
- You do not give legal advice, do not assert legal conclusions about ownership \
or validity, and do not opine on whether an assignment is enforceable. You \
describe technical scope so that counsel can make those determinations.
- When asked for JSON, you return exactly one JSON object and nothing else: no \
prose before or after, no markdown fences.
"""

#: Hard caps that keep a prompt within a small model's context window.
MAX_DEPENDENCIES = 45
MAX_README_CHARS = 700
MAX_SHARED = 30
MAX_CORPORATE_CHARS = 6000


def system_message(override: str | None = None) -> Message:
    return Message(role="system", content=override or DEFAULT_SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# Context compaction
# ---------------------------------------------------------------------------
def compact_repo(analysis: RepoAnalysis, overlap: Overlap | None = None) -> dict[str, Any]:
    """Reduce one repository's analysis to the facts a drafter needs."""
    tech = analysis.technology
    payload: dict[str, Any] = {
        "repository": analysis.name,
        "url": analysis.ref.url,
        "owner": analysis.ref.owner,
        "description": analysis.description,
        "license": analysis.license or "none detected",
        "primary_language": tech.primary_language,
        "languages": [lang.name for lang in tech.languages[:6]],
        "frameworks": tech.frameworks[:15],
        "ai_ml": tech.ml_ai[:20],
        "datastores": tech.datastores[:12],
        "protocols": tech.protocols[:10],
        "cloud": tech.cloud[:10],
        "infrastructure": tech.infrastructure[:10],
        "frontend": tech.frontend[:10],
        "entrypoints": tech.entrypoints[:8],
        "top_level_directories": (analysis.metrics.get("top_level_dirs") or [])[:15],
        "dependencies": [d.name for d in tech.dependencies[:MAX_DEPENDENCIES]],
        "dependency_count": len(tech.dependencies),
        "source_files": analysis.metrics.get("analyzed_files"),
        "first_commit_date": analysis.git.first_commit_date,
        "last_commit_date": analysis.git.last_commit_date,
        "commit_count": analysis.git.commit_count,
        "contributors": analysis.git.contributors[:8],
        "readme_excerpt": (analysis.readme_excerpt or "")[:MAX_README_CHARS] or None,
    }
    if analysis.repowise and analysis.repowise.get("architecture"):
        arch = analysis.repowise["architecture"]
        payload["architecture"] = {
            k: arch[k] for k in ("layers", "subsystems", "entry_points") if k in arch
        }
    if overlap is not None:
        payload["comparison_to_target"] = {
            "overlap_score": round(overlap.overlap_score, 3),
            "relatedness": overlap.relatedness,
            "predates_target_work": overlap.predates_target,
            "shared_technologies": overlap.shared_technologies[:MAX_SHARED],
            "technologies_unique_to_this_work": overlap.unique_technologies[:MAX_SHARED],
            "deterministic_rationale": overlap.rationale,
        }
    return {k: v for k, v in payload.items() if v not in (None, [], {}, "")}


def compact_target(bundle: AnalysisBundle) -> dict[str, Any]:
    payload = compact_repo(bundle.target)
    payload["aggregate"] = {
        "prior_work_count": bundle.aggregate.get("prior_work_count"),
        "technologies_shared_with_prior_works": (
            bundle.aggregate.get("shared_technologies") or []
        )[:MAX_SHARED],
        "technologies_only_in_target": (
            bundle.aggregate.get("target_only_technologies") or []
        )[:MAX_SHARED],
    }
    return payload


def _dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=False, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Task 1: characterise the company's business and technical field
# ---------------------------------------------------------------------------
FIELD_OF_BUSINESS_KEYS = ("company_business", "technical_field", "executive_summary")


def field_of_business_messages(
    bundle: AnalysisBundle,
    *,
    company_name: str,
    corporate_excerpt: str | None = None,
    system_override: str | None = None,
) -> list[Message]:
    context = {
        "company_name": company_name,
        "target_work": compact_target(bundle),
        "prior_work_summary": [
            {
                "repository": o.repository,
                "relatedness": o.relatedness,
                "overlap_score": round(o.overlap_score, 3),
            }
            for o in bundle.overlaps
        ],
    }
    if corporate_excerpt:
        context["corporate_agreement_excerpt"] = corporate_excerpt[:MAX_CORPORATE_CHARS]

    instruction = f"""\
Characterise the company's business and technical field, for use in the \
definitions section of a PIIA.

Evidence (deterministic analysis of the company's repository, plus any excerpt \
from the company's own corporate agreement):

{_dumps(context)}

Return exactly one JSON object with these keys:

- "company_business": 2-4 sentences describing what {company_name} builds and \
the business it is in, grounded in the target repository's actual technology and \
purpose. This becomes the definition of the company's field of business, so it \
must be specific enough to draw a boundary and broad enough not to be evaded.
- "technical_field": one sentence naming the technical domain (for example \
"multi-agent orchestration for computer-vision pipelines").
- "executive_summary": 3-5 sentences summarising, for the signatory and for \
counsel, what this agreement does and what the accompanying analysis found. \
Mention the number of prior works reviewed and how many relate closely to the \
company's business.

Use only the evidence above. If the evidence is thin, say what could not be \
determined instead of inventing detail.
"""
    return [system_message(system_override), Message(role="user", content=instruction)]


# ---------------------------------------------------------------------------
# Task 2: the Prior Inventions exhibit
# ---------------------------------------------------------------------------
PRIOR_INVENTION_KEYS = ("prior_inventions",)


def prior_inventions_messages(
    bundle: AnalysisBundle,
    batch: list[RepoAnalysis],
    *,
    company_name: str,
    signatory_name: str,
    company_business: str,
    system_override: str | None = None,
) -> list[Message]:
    works = [compact_repo(p, bundle.overlap_for(p.name)) for p in batch]
    instruction = f"""\
Draft the Exhibit A "Prior Inventions" entries for {signatory_name}, who is \
entering into a PIIA with {company_name}.

The company's field of business: {company_business}

Each work below was built by or with {signatory_name} before, or independently \
of, the work for {company_name}. The deterministic analysis of each work, and its \
measured relationship to the company's repository, follows:

{_dumps(works)}

Return exactly one JSON object of the form:

{{"prior_inventions": [
  {{
    "repository": "<exact repository name from the evidence>",
    "title": "<short descriptive title of the invention, not the repo slug>",
    "description": "<3-5 sentences: what the work does, its architecture, and \
the specific technical contribution being reserved. Concrete and technical.>",
    "technologies": ["<up to 10 technologies actually listed in the evidence>"],
    "relation_to_company_business": "<1-3 sentences stating plainly how this \
work does or does not relate to the company's field of business, consistent with \
the measured relatedness>",
    "carve_out_language": "<1-2 sentences of operative contract language \
reserving this work to the signatory and defining the boundary against company \
work. Written to sit inside an exhibit, present tense, no headings.>",
    "incorporation_risk": "<one of: none | possible | likely -- how likely this \
work is to be incorporated into a company product, based on the overlap \
evidence>",
    "notes": "<anything counsel must verify, e.g. co-authors, unclear license, \
employer-owned code, third-party components. Empty string if nothing.>"
  }}
]}}

Rules:
- One entry per repository given above, in the same order. Use the exact \
repository names.
- Do not assert ownership as a legal conclusion; describe authorship and \
reserve rights.
- If a work has multiple contributors in the evidence, note that joint \
authorship must be confirmed.
- Never state a date, license or technology that is not in the evidence.
"""
    return [system_message(system_override), Message(role="user", content=instruction)]


# ---------------------------------------------------------------------------
# Task 3: counsel review checklist and supplemental clauses
# ---------------------------------------------------------------------------
REVIEW_KEYS = ("review_checklist",)


def review_messages(
    bundle: AnalysisBundle,
    *,
    company_name: str,
    signatory_name: str,
    governing_law: str | None,
    company_business: str,
    system_override: str | None = None,
) -> list[Message]:
    evidence = {
        "company_name": company_name,
        "signatory": signatory_name,
        "governing_law": governing_law or "not specified",
        "company_business": company_business,
        "aggregate": bundle.aggregate,
        "prior_works": [
            {
                "repository": o.repository,
                "relatedness": o.relatedness,
                "overlap_score": round(o.overlap_score, 3),
                "license": next(
                    (p.license for p in bundle.prior_works if p.name == o.repository), None
                ),
                "contributors": next(
                    (p.git.contributors[:5] for p in bundle.prior_works if p.name == o.repository),
                    [],
                ),
                "predates_target_work": o.predates_target,
            }
            for o in bundle.overlaps
        ],
    }
    instruction = f"""\
Review the analysis below as counsel would before this PIIA is signed.

{_dumps(evidence)}

Return exactly one JSON object:

{{
  "review_checklist": [
    {{"item": "<what must be verified or decided>",
      "why": "<why it matters, referencing the specific evidence>",
      "severity": "<high | medium | low>"}}
  ],
  "open_source_notes": [
    "<a note about any license in the evidence that constrains what the company \
may do with an incorporated prior invention -- copyleft, unclear, or missing>"
  ],
  "supplemental_clauses": [
    {{"heading": "<clause heading>",
      "text": "<operative clause text, present tense, no numbering>",
      "rationale": "<the specific fact in the evidence that makes this clause \
worth adding>"}}
  ]
}}

Rules:
- 4 to 8 checklist items, highest severity first. Each must point at a specific \
repository, license, contributor set, or date from the evidence. No generic \
advice.
- Supplemental clauses: at most 3, and only where the evidence justifies one \
(for example a copyleft license on a closely related prior work, or joint \
authorship). Return an empty list if nothing is justified.
- open_source_notes may be an empty list.
"""
    return [system_message(system_override), Message(role="user", content=instruction)]
