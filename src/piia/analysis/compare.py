"""Prior-work vs target-work comparison.

The comparison answers the one question a PIIA turns on: *does this earlier
work relate to the company's business, and does it therefore have to be
disclosed and carved out?*

Scoring is deliberately simple and inspectable, because a founder may have to
defend it years later:

``overlap_score = 0.65 * weighted_technology_coverage + 0.35 * dependency_overlap``

* **weighted_technology_coverage** -- how much of the *target's* technology
  surface the prior work already covers, weighted by category
  (:data:`~piia.analysis.signatures.CATEGORY_WEIGHTS`), with ubiquitous
  technologies discounted so "both use NumPy" cannot carry a finding.
* **dependency_overlap** -- the share of the target's third-party dependencies
  the prior work also uses. Concrete, and hard to argue with.

Bands: ``high`` >= 0.55, ``moderate`` >= 0.30, ``low`` >= 0.12, else ``none``.
Every score ships with the rationale that produced it.
"""

from __future__ import annotations

from typing import Any

from piia.analysis.models import (
    RELATEDNESS_HIGH,
    RELATEDNESS_LOW,
    RELATEDNESS_MODERATE,
    RELATEDNESS_NONE,
    Overlap,
    RepoAnalysis,
)
from piia.analysis.signatures import CATEGORY_WEIGHTS, UBIQUITOUS

#: Weight multiplier applied to technologies in :data:`UBIQUITOUS`.
UBIQUITOUS_DISCOUNT = 0.25

TECHNOLOGY_WEIGHT = 0.65
DEPENDENCY_WEIGHT = 0.35

BAND_HIGH = 0.55
BAND_MODERATE = 0.30
BAND_LOW = 0.12
SCORE_PRECISION = 4

RECOMMENDATIONS = {
    RELATEDNESS_HIGH: (
        "Disclose in Exhibit A and carve out explicitly; overlaps the company's "
        "field of business, so the boundary must be stated in writing."
    ),
    RELATEDNESS_MODERATE: (
        "Disclose in Exhibit A; related enough that silence could later be read "
        "as an implied assignment."
    ),
    RELATEDNESS_LOW: (
        "Disclose in Exhibit A for completeness; limited technical relationship "
        "to the company's business."
    ),
    RELATEDNESS_NONE: (
        "Optional disclosure; no material technical relationship detected. "
        "Listing it is still the safer default."
    ),
}


def _classify(score: float) -> str:
    if score >= BAND_HIGH:
        return RELATEDNESS_HIGH
    if score >= BAND_MODERATE:
        return RELATEDNESS_MODERATE
    if score >= BAND_LOW:
        return RELATEDNESS_LOW
    return RELATEDNESS_NONE


def _weight(token: str) -> float:
    category = token.split("/", 1)[0]
    base = CATEGORY_WEIGHTS.get(category, 1.0)
    if token in UBIQUITOUS:
        base *= UBIQUITOUS_DISCOUNT
    return base


def _weighted(tokens: set[str]) -> float:
    return sum(_weight(t) for t in tokens)


def _label(token: str) -> str:
    return token.split("/", 1)[1] if "/" in token else token


def display_names(*analyses: RepoAnalysis) -> dict[str, str]:
    """Map normalised tokens back to their canonical display names.

    ``technology_set`` lowercases so that matching is case-insensitive, but a
    Prior Inventions exhibit has to read "PyTorch", not "pytorch". Later
    analyses win, so passing the target last makes its casing authoritative.
    """
    names: dict[str, str] = {}
    for analysis in analyses:
        for category, values in analysis.technology.categorized().items():
            for value in values:
                names[f"{category}/{value.lower()}"] = value
    return names


def _labels(tokens: set[str], names: dict[str, str]) -> list[str]:
    return sorted({names.get(token, _label(token)) for token in tokens}, key=str.lower)


def compare(target: RepoAnalysis, prior: RepoAnalysis) -> Overlap:
    """Score one prior work against the target work."""
    t_tokens = target.technology.technology_set
    p_tokens = prior.technology.technology_set
    shared = t_tokens & p_tokens
    union = t_tokens | p_tokens

    jaccard = len(shared) / len(union) if union else 0.0
    coverage = _weighted(shared) / _weighted(t_tokens) if t_tokens else 0.0

    t_deps = target.technology.dependency_keys
    p_deps = prior.technology.dependency_keys
    shared_deps = t_deps & p_deps
    dep_overlap = len(shared_deps) / len(t_deps) if t_deps else 0.0

    score = TECHNOLOGY_WEIGHT * coverage + DEPENDENCY_WEIGHT * dep_overlap
    # Classify the same four-decimal score that is published in the evidence.
    # Without quantization, binary floating-point noise can display as 0.5500
    # while falling microscopically below the documented high-risk boundary.
    score = round(max(0.0, min(1.0, score)), SCORE_PRECISION)

    band = _classify(score)

    names = display_names(prior, target)
    predates = _predates(prior, target)
    rationale = _rationale(
        names=names,
        target=target,
        prior=prior,
        shared=shared,
        shared_deps=shared_deps,
        coverage=coverage,
        dep_overlap=dep_overlap,
        predates=predates,
    )

    return Overlap(
        repository=prior.name,
        url=prior.ref.url,
        overlap_score=score,
        jaccard=jaccard,
        dependency_overlap=dep_overlap,
        shared_technologies=_labels(shared, names),
        unique_technologies=_labels(p_tokens - t_tokens, names),
        target_only_technologies=_labels(t_tokens - p_tokens, names),
        shared_dependencies=sorted({k.split(":", 1)[1] for k in shared_deps})[:80],
        relatedness=band,
        rationale=rationale,
        recommendation=RECOMMENDATIONS[band],
        predates_target=predates,
    )


def _predates(prior: RepoAnalysis, target: RepoAnalysis) -> bool | None:
    """Whether the prior work's first commit precedes the target's.

    This is the temporal half of a Prior Invention: it has to have existed
    before the work for the company began.
    """
    p_first = prior.git.first_commit_date
    t_first = target.git.first_commit_date
    if not p_first or not t_first:
        return None
    return p_first < t_first


def _rationale(
    *,
    names: dict[str, str],
    target: RepoAnalysis,
    prior: RepoAnalysis,
    shared: set[str],
    shared_deps: set[str],
    coverage: float,
    dep_overlap: float,
    predates: bool | None,
) -> list[str]:
    lines: list[str] = []
    substantive = _labels(
        {
            t
            for t in shared
            if t.split("/", 1)[0] in {"frameworks", "ml_ai", "datastores", "protocols", "frontend"}
            and t not in UBIQUITOUS
        },
        names,
    )
    if substantive:
        lines.append(
            "Shares substantive technology with the target work: "
            + ", ".join(substantive[:12])
            + ("..." if len(substantive) > 12 else "")
        )
    else:
        lines.append("No substantive framework, datastore or AI/ML technology in common.")

    lines.append(f"Covers {coverage * 100:.0f}% of the target work's weighted technology surface.")
    if shared_deps:
        lines.append(
            f"Shares {len(shared_deps)} third-party dependencies with the target work "
            f"({dep_overlap * 100:.0f}% of the target's declared dependencies)."
        )
    else:
        lines.append("Shares no declared third-party dependencies with the target work.")

    if predates is True:
        lines.append(
            f"First commit {prior.git.first_commit_date} precedes the target work's first commit "
            f"{target.git.first_commit_date}, consistent with a pre-existing invention."
        )
    elif predates is False:
        lines.append(
            f"First commit {prior.git.first_commit_date} does not precede the target work's "
            f"{target.git.first_commit_date}; confirm whether this is genuinely prior work."
        )
    else:
        lines.append("Commit dates unavailable, so temporal precedence was not verified.")

    if prior.license:
        lines.append(f"Declared license: {prior.license}.")
    else:
        lines.append("No license file detected; ownership terms should be stated expressly.")
    return lines


def summarize(
    target: RepoAnalysis, priors: list[RepoAnalysis], overlaps: list[Overlap]
) -> dict[str, Any]:
    """Aggregate view across every prior work."""
    t_tokens = target.technology.technology_set
    prior_union: set[str] = set()
    for prior in priors:
        prior_union |= prior.technology.technology_set
    names = display_names(*priors, target)

    shared_any = _labels(t_tokens & prior_union, names)
    target_only = _labels(t_tokens - prior_union, names)
    by_band: dict[str, list[str]] = {}
    for overlap in overlaps:
        by_band.setdefault(overlap.relatedness, []).append(overlap.repository)

    carve_out = [
        o.repository
        for o in sorted(overlaps, key=lambda o: -o.overlap_score)
        if o.relatedness in {RELATEDNESS_HIGH, RELATEDNESS_MODERATE}
    ]
    scores = [o.overlap_score for o in overlaps]
    return {
        "prior_work_count": len(priors),
        "shared_technologies": shared_any,
        "target_only_technologies": target_only,
        "carve_out_candidates": carve_out,
        "relatedness_breakdown": {band: sorted(names) for band, names in sorted(by_band.items())},
        "highest_overlap": max(scores) if scores else 0.0,
        "mean_overlap": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "target_technology_count": len(t_tokens),
        "prior_technology_count": len(prior_union),
        "technology_universe": _labels(t_tokens | prior_union, names),
    }
