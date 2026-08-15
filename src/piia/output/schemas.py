"""JSON Schemas for the output contract.

Every command answers ``--schema`` with the schema of what it emits, so an agent
can discover the contract instead of inferring it from one sample. Schemas are
JSON Schema 2020-12 and are versioned by
:data:`piia.version.ENVELOPE_VERSION`.
"""

from __future__ import annotations

from typing import Any

from piia.envelope import ENVELOPE_SCHEMA_URL, SCHEMA_BASE_URL
from piia.version import ENVELOPE_VERSION

DRAFT = "https://json-schema.org/draft/2020-12/schema"

_ERROR = {
    "type": ["object", "null"],
    "properties": {
        "code": {"type": "string", "description": "Stable machine-readable error code."},
        "message": {"type": "string"},
        "details": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"field": {"type": "string"}, "issue": {"type": "string"}},
            },
        },
        "remediation": {"type": ["string", "null"], "description": "Concrete next action."},
    },
    "required": ["code", "message"],
}

_WARNING = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "remediation": {"type": ["string", "null"]},
    },
    "required": ["code", "message"],
}

_TECHNOLOGY = {
    "type": "object",
    "properties": {
        "primary_language": {"type": ["string", "null"]},
        "languages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "files": {"type": "integer"},
                    "bytes": {"type": "integer"},
                    "share": {"type": "number"},
                },
                "required": ["name", "files", "bytes", "share"],
            },
        },
        "frameworks": {"type": "array", "items": {"type": "string"}},
        "datastores": {"type": "array", "items": {"type": "string"}},
        "infrastructure": {"type": "array", "items": {"type": "string"}},
        "cloud": {"type": "array", "items": {"type": "string"}},
        "ml_ai": {"type": "array", "items": {"type": "string"}},
        "protocols": {"type": "array", "items": {"type": "string"}},
        "frontend": {"type": "array", "items": {"type": "string"}},
        "testing": {"type": "array", "items": {"type": "string"}},
        "package_managers": {"type": "array", "items": {"type": "string"}},
        "ci": {"type": "array", "items": {"type": "string"}},
        "dependency_count": {"type": "integer"},
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ecosystem": {"type": "string"},
                    "version": {"type": ["string", "null"]},
                    "manifest": {"type": ["string", "null"]},
                },
                "required": ["name", "ecosystem"],
            },
        },
    },
    "required": ["primary_language", "languages", "dependencies"],
}

_REPO_ANALYSIS = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "slug": {"type": "string"},
        "repository": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "url": {"type": ["string", "null"]},
                "owner": {"type": ["string", "null"]},
                "path": {"type": ["string", "null"]},
                "source": {
                    "type": "string",
                    "enum": ["local-path", "local-discovery", "clone", "pending"],
                    "description": "How the checkout was obtained.",
                },
                "role": {"type": "string", "enum": ["target", "prior"]},
            },
            "required": ["name", "source", "role"],
        },
        "git": {
            "type": "object",
            "properties": {
                "commit": {"type": ["string", "null"]},
                "short_commit": {"type": ["string", "null"]},
                "branch": {"type": ["string", "null"]},
                "first_commit_date": {"type": ["string", "null"]},
                "last_commit_date": {"type": ["string", "null"]},
                "commit_count": {"type": "integer"},
                "contributors": {"type": "array", "items": {"type": "string"}},
                "contributor_count": {"type": "integer"},
                "is_git_repo": {"type": "boolean"},
                "is_shallow": {"type": "boolean"},
            },
            "required": ["commit", "commit_count", "is_git_repo"],
        },
        "technology": _TECHNOLOGY,
        "metrics": {"type": "object"},
        "license": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "repowise": {"type": ["object", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "repository", "git", "technology", "metrics"],
}

_OVERLAP = {
    "type": "object",
    "properties": {
        "repository": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "overlap_score": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "0.65 * weighted technology coverage + 0.35 * dependency overlap.",
        },
        "jaccard": {"type": "number", "minimum": 0, "maximum": 1},
        "dependency_overlap": {"type": "number", "minimum": 0, "maximum": 1},
        "relatedness": {"type": "string", "enum": ["high", "moderate", "low", "none"]},
        "predates_target": {"type": ["boolean", "null"]},
        "shared_technologies": {"type": "array", "items": {"type": "string"}},
        "unique_technologies": {"type": "array", "items": {"type": "string"}},
        "target_only_technologies": {"type": "array", "items": {"type": "string"}},
        "shared_dependencies": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string"},
    },
    "required": ["repository", "overlap_score", "relatedness", "rationale", "recommendation"],
}

_PRIOR_INVENTION = {
    "type": "object",
    "properties": {
        "repository": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "technologies": {"type": "array", "items": {"type": "string"}},
        "unverified_technologies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Technologies the model named that the deterministic scan did not find.",
        },
        "relation_to_company_business": {"type": "string"},
        "carve_out_language": {"type": "string"},
        "incorporation_risk": {"type": "string", "enum": ["none", "possible", "likely"]},
        "relatedness": {"type": "string", "enum": ["high", "moderate", "low", "none"]},
        "overlap_score": {"type": "number"},
        "ownership": {"type": "string"},
        "license": {"type": ["string", "null"]},
        "commit": {"type": ["string", "null"]},
        "first_commit_date": {"type": ["string", "null"]},
        "last_commit_date": {"type": ["string", "null"]},
        "contributors": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "source": {
            "type": "string",
            "enum": ["model", "deterministic"],
            "description": "Whether the prose was model-drafted or template-generated.",
        },
    },
    "required": ["repository", "title", "description", "carve_out_language", "source"],
}

_ARTIFACT = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["agreement", "exhibit-a", "analysis"]},
        "path": {"type": "string"},
        "bytes": {"type": "integer"},
        "format": {"type": "string", "enum": ["md", "json", "html", "docx", "pdf"]},
    },
    "required": ["kind", "path", "bytes", "format"],
}

#: ``data`` payload schema per command.
DATA_SCHEMAS: dict[str, dict[str, Any]] = {
    "analyze": {
        "title": "AnalyzeData",
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "generated_at": {"type": "string"},
            "digest": {
                "type": "string",
                "description": "Content hash of the analysis; stable across runs at the same commits.",
            },
            "target": _REPO_ANALYSIS,
            "prior_works": {"type": "array", "items": _REPO_ANALYSIS},
            "overlaps": {"type": "array", "items": _OVERLAP},
            "aggregate": {
                "type": "object",
                "properties": {
                    "prior_work_count": {"type": "integer"},
                    "shared_technologies": {"type": "array", "items": {"type": "string"}},
                    "target_only_technologies": {"type": "array", "items": {"type": "string"}},
                    "carve_out_candidates": {"type": "array", "items": {"type": "string"}},
                    "relatedness_breakdown": {"type": "object"},
                    "highest_overlap": {"type": "number"},
                    "mean_overlap": {"type": "number"},
                },
            },
            "tool_versions": {"type": "object"},
            "notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Every resolution decision, including routine ones.",
            },
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The subset of notes worth surfacing: clones, skips, degradations.",
            },
        },
        "required": ["target", "prior_works", "overlaps", "aggregate", "digest"],
    },
    "generate": {
        "title": "GenerateData",
        "type": "object",
        "properties": {
            "document": {
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "title": {"type": "string"},
                    "company": {"type": "object"},
                    "signatory": {"type": "object"},
                    "effective_date": {"type": "string"},
                    "governing_law": {"type": "string"},
                    "executive_summary": {"type": "string"},
                    "company_business": {"type": "string"},
                    "recitals": {"type": "array", "items": {"type": "string"}},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "number": {"type": "integer"},
                                "heading": {"type": "string"},
                                "omitted": {"type": "boolean"},
                                "jurisdiction_sensitive": {"type": "boolean"},
                                "drafting_note": {"type": ["string", "null"]},
                                "clauses": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "number": {"type": "string"},
                                            "text": {"type": "string"},
                                        },
                                        "required": ["number", "text"],
                                    },
                                },
                            },
                            "required": ["id", "number", "heading", "clauses"],
                        },
                    },
                    "prior_inventions": {"type": "array", "items": _PRIOR_INVENTION},
                    "review_checklist": {"type": "array", "items": {"type": "object"}},
                    "open_source_notes": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "object"},
                    "provenance": {"type": "object"},
                    "disclaimer": {"type": "string"},
                },
                "required": ["title", "sections", "prior_inventions", "evidence", "provenance"],
            },
            "artifacts": {"type": "array", "items": _ARTIFACT},
            "provenance": {"type": "object"},
            "analysis_digest": {"type": "string"},
        },
        "required": ["document", "artifacts"],
    },
    "render": {
        "title": "RenderData",
        "type": "object",
        "properties": {
            "artifacts": {"type": "array", "items": _ARTIFACT},
            "source": {"type": "string"},
        },
        "required": ["artifacts"],
    },
    "doctor": {
        "title": "DoctorData",
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "status": {"type": "string", "enum": ["ok", "warn", "error", "skipped"]},
                        "detail": {"type": "string"},
                        "remediation": {"type": ["string", "null"]},
                    },
                    "required": ["name", "status", "detail"],
                },
            },
            "summary": {
                "type": "object",
                "properties": {
                    "passed": {"type": "integer"},
                    "warnings": {"type": "integer"},
                    "failures": {"type": "integer"},
                },
            },
        },
        "required": ["checks", "summary"],
    },
    "config show": {
        "title": "ConfigShowData",
        "type": "object",
        "properties": {
            "settings": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "value": {},
                        "source": {
                            "type": "string",
                            "enum": [
                                "cli-flag",
                                "environment",
                                "dotenv",
                                "config-file",
                                "default",
                                "default (parent of working directory)",
                                "dotenv (file)",
                                "environment (file)",
                                "config-file (file)",
                            ],
                        },
                        "env_var": {"type": "string"},
                    },
                },
            },
            "config_file": {"type": ["string", "null"]},
            "env_file": {"type": ["string", "null"]},
        },
        "required": ["settings"],
    },
    "version": {
        "title": "VersionData",
        "type": "object",
        "properties": {
            "version": {"type": "string"},
            "envelope_version": {"type": "string"},
            "python": {"type": "string"},
            "platform": {"type": "string"},
            "git": {"type": ["string", "null"]},
            "repowise": {"type": ["string", "null"]},
            "license": {"type": "string"},
            "source": {"type": "string"},
        },
        "required": ["version", "envelope_version", "license"],
    },
    "init": {
        "title": "InitData",
        "type": "object",
        "properties": {
            "written": {"type": "array", "items": {"type": "string"}},
            "skipped": {"type": "array", "items": {"type": "string"}},
            "next_steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["written", "skipped"],
    },
}


def envelope_schema(command: str | None = None) -> dict[str, Any]:
    """The full envelope schema, with ``data`` specialised for ``command``."""
    data_schema: dict[str, Any] = (
        DATA_SCHEMAS.get(command or "", {"type": ["object", "null"]})
        if command
        else {"type": ["object", "null"]}
    )
    title = "".join(part.capitalize() for part in (command or "cli").split()) + "Response"
    return {
        "$schema": DRAFT,
        "$id": f"{SCHEMA_BASE_URL}/{(command or 'cli-envelope').replace(' ', '-')}.json",
        "title": title,
        "description": (
            f"Response envelope emitted by 'piia {command}' in JSON mode."
            if command
            else "Universal response envelope emitted by every piia command in JSON mode."
        ),
        "type": "object",
        "properties": {
            "$schema": {"type": "string", "const": ENVELOPE_SCHEMA_URL},
            "version": {"type": "string", "const": ENVELOPE_VERSION},
            "command": {"type": "string"},
            "status": {"type": "string", "enum": ["success", "error", "partial"]},
            "timestamp": {"type": "string", "format": "date-time"},
            "execution": {
                "type": "object",
                "properties": {
                    "duration_ms": {"type": "integer"},
                    "request_id": {"type": "string"},
                    "tool_version": {"type": "string"},
                },
                "required": ["duration_ms", "request_id", "tool_version"],
            },
            "data": data_schema if command else {"type": ["object", "null"]},
            "error": _ERROR,
            "warnings": {"type": "array", "items": _WARNING},
            "pagination": {"type": ["object", "null"]},
        },
        "required": ["version", "command", "status", "timestamp", "execution", "data", "error"],
        "additionalProperties": False,
    }


def all_schemas() -> dict[str, Any]:
    """Every command's schema, keyed by command name."""
    return {
        "$schema": DRAFT,
        "$id": f"{SCHEMA_BASE_URL}/all.json",
        "title": "PiiaSchemas",
        "envelope_version": ENVELOPE_VERSION,
        "commands": {command: envelope_schema(command) for command in sorted(DATA_SCHEMAS)},
    }
