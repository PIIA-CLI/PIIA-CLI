"""Single source of truth for the package and output-contract versions."""

from __future__ import annotations

__version__ = "0.1.0"

#: Version of the JSON envelope / output contract. Bumped independently of the
#: package version: consumers pin against this, not against ``__version__``.
ENVELOPE_VERSION = "1.0.0"

#: Version of the analysis bundle schema produced by the deterministic layer.
ANALYSIS_SCHEMA_VERSION = "1.0.0"

#: Version of the PIIA document schema produced by the drafting layer.
DOCUMENT_SCHEMA_VERSION = "1.0.0"
