"""Data model for the deterministic layer.

These objects are the single source of truth that both renderers and the
drafting prompt read from. They are plain dataclasses with explicit
``to_dict``: the JSON shape is part of the public contract, so it is written
out by hand rather than inferred from field names.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from piia.version import ANALYSIS_SCHEMA_VERSION

#: Relatedness bands. The boundaries are deterministic (see compare.py) so two
#: runs over the same commits always land in the same band.
RELATEDNESS_HIGH = "high"
RELATEDNESS_MODERATE = "moderate"
RELATEDNESS_LOW = "low"
RELATEDNESS_NONE = "none"


@dataclass
class RepoRef:
    """Where a repository is, and how we got to it."""

    name: str
    url: str | None = None
    host: str | None = None
    owner: str | None = None
    path: str | None = None
    #: ``local-discovery`` (found an existing checkout), ``local-path`` (the
    #: user passed a path), or ``clone`` (fetched into the workspace).
    source: str = "local-path"
    remote_url: str | None = None
    role: str = "prior"  # prior | target

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}" if self.owner else self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "url": self.url,
            "host": self.host,
            "owner": self.owner,
            "path": self.path,
            "source": self.source,
            "remote_url": self.remote_url,
            "role": self.role,
        }


@dataclass
class GitFacts:
    """Facts read straight out of ``git``. No inference, no model."""

    commit: str | None = None
    branch: str | None = None
    first_commit_date: str | None = None
    last_commit_date: str | None = None
    commit_count: int = 0
    contributors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    is_git_repo: bool = True
    is_shallow: bool = False

    @property
    def short_commit(self) -> str | None:
        return self.commit[:12] if self.commit else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "short_commit": self.short_commit,
            "branch": self.branch,
            "first_commit_date": self.first_commit_date,
            "last_commit_date": self.last_commit_date,
            "commit_count": self.commit_count,
            "contributors": self.contributors,
            "contributor_count": len(self.contributors),
            "tags": self.tags,
            "is_git_repo": self.is_git_repo,
            "is_shallow": self.is_shallow,
        }


@dataclass
class Dependency:
    name: str
    ecosystem: str
    version: str | None = None
    manifest: str | None = None

    @property
    def key(self) -> str:
        return f"{self.ecosystem}:{self.name.lower()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ecosystem": self.ecosystem,
            "version": self.version,
            "manifest": self.manifest,
        }


@dataclass
class LanguageStat:
    name: str
    files: int
    bytes: int
    share: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "files": self.files,
            "bytes": self.bytes,
            "share": round(self.share, 4),
        }


@dataclass
class TechInventory:
    """What a repository is built out of.

    ``technology_set`` is the normalised, deduplicated set that comparison
    operates on; every other list is kept for human readability and for the
    drafting prompt.
    """

    primary_language: str | None = None
    languages: list[LanguageStat] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    datastores: list[str] = field(default_factory=list)
    infrastructure: list[str] = field(default_factory=list)
    ci: list[str] = field(default_factory=list)
    cloud: list[str] = field(default_factory=list)
    ml_ai: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    testing: list[str] = field(default_factory=list)
    frontend: list[str] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)

    CATEGORIES = (
        "languages",
        "frameworks",
        "datastores",
        "infrastructure",
        "cloud",
        "ml_ai",
        "protocols",
        "frontend",
        "testing",
        "package_managers",
        "ci",
    )

    def categorized(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for cat in self.CATEGORIES:
            if cat == "languages":
                out[cat] = [lang.name for lang in self.languages]
            else:
                out[cat] = list(getattr(self, cat))
        return out

    @property
    def technology_set(self) -> set[str]:
        """Normalised ``category/name`` tokens used for overlap scoring."""
        tokens: set[str] = set()
        for cat, values in self.categorized().items():
            tokens.update(f"{cat}/{v.lower()}" for v in values)
        return tokens

    @property
    def dependency_keys(self) -> set[str]:
        return {d.key for d in self.dependencies}

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_language": self.primary_language,
            "languages": [lang.to_dict() for lang in self.languages],
            "frameworks": self.frameworks,
            "package_managers": self.package_managers,
            "datastores": self.datastores,
            "infrastructure": self.infrastructure,
            "ci": self.ci,
            "cloud": self.cloud,
            "ml_ai": self.ml_ai,
            "protocols": self.protocols,
            "testing": self.testing,
            "frontend": self.frontend,
            "manifests": self.manifests,
            "entrypoints": self.entrypoints,
            "dependency_count": len(self.dependencies),
            "dependencies": [d.to_dict() for d in self.dependencies],
        }


@dataclass
class RepoAnalysis:
    """Everything the deterministic layer knows about one repository."""

    ref: RepoRef
    git: GitFacts = field(default_factory=GitFacts)
    technology: TechInventory = field(default_factory=TechInventory)
    metrics: dict[str, Any] = field(default_factory=dict)
    license: str | None = None
    description: str | None = None
    readme_excerpt: str | None = None
    repowise: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.ref.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.ref.name,
            "url": self.ref.url,
            "slug": self.ref.slug,
            "repository": self.ref.to_dict(),
            "git": self.git.to_dict(),
            "technology": self.technology.to_dict(),
            "metrics": self.metrics,
            "license": self.license,
            "description": self.description,
            "readme_excerpt": self.readme_excerpt,
            "repowise": self.repowise,
            "notes": self.notes,
        }


@dataclass
class Overlap:
    """How one prior work relates to the target work."""

    repository: str
    url: str | None
    overlap_score: float
    jaccard: float
    dependency_overlap: float
    shared_technologies: list[str]
    unique_technologies: list[str]
    target_only_technologies: list[str]
    shared_dependencies: list[str]
    relatedness: str
    rationale: list[str]
    recommendation: str
    predates_target: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "url": self.url,
            "overlap_score": round(self.overlap_score, 4),
            "jaccard": round(self.jaccard, 4),
            "dependency_overlap": round(self.dependency_overlap, 4),
            "relatedness": self.relatedness,
            "predates_target": self.predates_target,
            "shared_technologies": self.shared_technologies,
            "unique_technologies": self.unique_technologies,
            "target_only_technologies": self.target_only_technologies,
            "shared_dependencies": self.shared_dependencies,
            "rationale": self.rationale,
            "recommendation": self.recommendation,
        }


@dataclass
class AnalysisBundle:
    """The complete deterministic result: inputs, per-repo facts, comparison."""

    target: RepoAnalysis
    prior_works: list[RepoAnalysis] = field(default_factory=list)
    overlaps: list[Overlap] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)
    tool_versions: dict[str, Any] = field(default_factory=dict)
    generated_at: str | None = None
    #: Everything that happened, including routine resolution decisions.
    notes: list[str] = field(default_factory=list)
    #: The subset of notes a caller should be told about: clones, skips,
    #: ambiguous matches, degraded enrichment. Kept separate so a 40-repo run
    #: does not bury one real problem under forty "found it locally" lines.
    warnings: list[str] = field(default_factory=list)
    schema_version: str = ANALYSIS_SCHEMA_VERSION

    def overlap_for(self, name: str) -> Overlap | None:
        return next((o for o in self.overlaps if o.repository == name), None)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "target": self.target.to_dict(),
            "prior_works": [p.to_dict() for p in self.prior_works],
            "overlaps": [o.to_dict() for o in self.overlaps],
            "aggregate": self.aggregate,
            "tool_versions": self.tool_versions,
            "notes": self.notes,
            "warnings": self.warnings,
        }
        payload["digest"] = self.digest(payload)
        return payload

    @staticmethod
    def digest(payload: dict[str, Any]) -> str:
        """Content hash over everything except volatile fields.

        Two runs at the same commits produce the same digest, which is what
        makes a generated agreement auditable: the digest is printed in the
        document's evidence exhibit.
        """
        volatile = {"generated_at", "digest", "tool_versions"}
        stable = {k: v for k, v in payload.items() if k not in volatile}
        blob = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
