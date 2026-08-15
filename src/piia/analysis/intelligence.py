"""Boundary for optional code-intelligence enrichment.

The native scanner is the source of truth and always runs.  An enrichment
provider may add architecture, health, and dead-code facts, but it cannot make
an analysis fail.  Keeping that contract in this small facade prevents the
pipeline from depending on a provider's Python internals or output model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from piia.analysis import repowise


class CodeIntelligenceProvider(Protocol):
    """Minimal interface accepted by the deterministic pipeline."""

    name: str

    def available(self) -> bool: ...

    def version(self) -> str | None: ...

    def analyze(self, path: Path, *, timeout: float, index: bool = True) -> dict[str, Any]: ...


class RepowiseProvider:
    """Subprocess adapter for the supported Repowise CLI contract."""

    name = "repowise"

    def __init__(self, binary: str = "repowise") -> None:
        self.binary = binary

    def available(self) -> bool:
        return repowise.available(self.binary)

    def version(self) -> str | None:
        return repowise.version(self.binary)

    def analyze(self, path: Path, *, timeout: float, index: bool = True) -> dict[str, Any]:
        return repowise.analyze(path, binary=self.binary, timeout=timeout, index=index)


def configured_provider(binary: str = "repowise") -> CodeIntelligenceProvider:
    """Return the configured enrichment provider.

    This factory is deliberately the only provider selection point used by the
    pipeline.  Configuration can grow without leaking provider-specific calls
    into repository resolution, scoring, or document generation.
    """

    return RepowiseProvider(binary)
