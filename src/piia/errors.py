"""Structured errors.

Every failure an agent can hit is a :class:`PiiaError` with a stable machine
code, a human message, optional per-field details and a remediation string that
tells the caller what to run next. The CLI turns these into both the human and
the JSON error rendering, and maps ``exit_code`` to the process exit status.
"""

from __future__ import annotations

from typing import Any


class PiiaError(Exception):
    """Base class for all expected failures.

    Attributes:
        code: Stable ``SCREAMING_SNAKE_CASE`` identifier. Never reworded once
            released -- agents branch on it.
        message: One-sentence human explanation.
        details: List of ``{"field": ..., "issue": ...}`` style records.
        remediation: A concrete next action, ideally a runnable command.
        exit_code: Process exit status to use.
    """

    code = "PIIA_ERROR"
    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        remediation: str | None = None,
        code: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []
        self.remediation = remediation
        if code is not None:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "remediation": self.remediation,
        }


class ConfigError(PiiaError):
    """Missing or contradictory configuration."""

    code = "CONFIG_INVALID"
    exit_code = 2


class UsageError(PiiaError):
    """The invocation itself was wrong (bad flag combination, missing input)."""

    code = "USAGE_INVALID"
    exit_code = 2


class RepositoryError(PiiaError):
    """A repository could not be resolved, cloned or read."""

    code = "REPOSITORY_UNAVAILABLE"
    exit_code = 3


class GitNotFoundError(PiiaError):
    """``git`` is not on ``PATH``."""

    code = "GIT_NOT_FOUND"
    exit_code = 4

    def __init__(self) -> None:
        super().__init__(
            "The 'git' executable was not found on PATH.",
            remediation="Install git (https://git-scm.com/downloads) and re-run 'piia doctor'.",
        )


class LLMError(PiiaError):
    """The model endpoint failed, timed out, or returned an unusable body."""

    code = "LLM_REQUEST_FAILED"
    exit_code = 6


class LLMResponseError(LLMError):
    """The model answered, but not in the contracted structure."""

    code = "LLM_RESPONSE_INVALID"
    exit_code = 6


class DocumentError(PiiaError):
    """A document could not be assembled or rendered."""

    code = "DOCUMENT_RENDER_FAILED"
    exit_code = 7


class DependencyMissingError(PiiaError):
    """An optional extra is required for the requested output format."""

    code = "OPTIONAL_DEPENDENCY_MISSING"
    exit_code = 8

    def __init__(self, package: str, extra: str, purpose: str) -> None:
        super().__init__(
            f"The optional dependency '{package}' is required to {purpose}.",
            details=[{"field": "dependency", "issue": f"{package} is not installed"}],
            remediation=f"Install it with 'pip install piia-cli[{extra}]'.",
        )
