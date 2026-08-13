"""Output-format resolution.

Precedence, highest first:

1. An explicit ``--json`` / ``-o <format>`` flag.
2. ``PIIA_OUTPUT_FORMAT`` in the environment.
3. A non-TTY ``stdout`` (pipe, CI, agent capture) -- defaults to ``json``,
   because whatever is reading is almost certainly a program.
4. An interactive TTY -- defaults to ``human``.

Colour is decided separately: ANSI never appears inside JSON, and ``NO_COLOR``
(https://no-color.org) or ``PIIA_NO_COLOR`` disables it everywhere.
"""

from __future__ import annotations

import os
import sys
from enum import StrEnum


class OutputFormat(StrEnum):
    HUMAN = "human"
    JSON = "json"
    JSON_PRETTY = "json-pretty"
    JSONL = "jsonl"

    @property
    def is_json(self) -> bool:
        return self is not OutputFormat.HUMAN


#: Accepted ``-o`` values, including the ``ndjson`` alias for ``jsonl``.
FORMATS: dict[str, OutputFormat] = {
    "human": OutputFormat.HUMAN,
    "md": OutputFormat.HUMAN,
    "markdown": OutputFormat.HUMAN,
    "json": OutputFormat.JSON,
    "json-pretty": OutputFormat.JSON_PRETTY,
    "pretty": OutputFormat.JSON_PRETTY,
    "jsonl": OutputFormat.JSONL,
    "ndjson": OutputFormat.JSONL,
}


def resolve_format(
    explicit: str | None = None,
    *,
    json_shorthand: bool = False,
    stream: object | None = None,
) -> OutputFormat:
    """Resolve the effective output format. See module docstring for precedence."""
    if json_shorthand and not explicit:
        return OutputFormat.JSON
    if explicit:
        key = explicit.strip().lower()
        if key not in FORMATS:
            valid = ", ".join(sorted({f.value for f in OutputFormat}))
            raise ValueError(f"unknown output format {explicit!r}; expected one of: {valid}")
        return FORMATS[key]

    env = os.environ.get("PIIA_OUTPUT_FORMAT", "").strip().lower()
    if env in FORMATS:
        return FORMATS[env]

    out = stream if stream is not None else sys.stdout
    isatty = getattr(out, "isatty", None)
    if callable(isatty):
        try:
            if not isatty():
                return OutputFormat.JSON
        except (ValueError, OSError):  # closed or detached stream
            return OutputFormat.JSON
    return OutputFormat.HUMAN


def supports_color(fmt: OutputFormat, *, stream: object | None = None) -> bool:
    """True only for human mode on a colour-capable TTY with colour not disabled."""
    if fmt.is_json:
        return False
    if os.environ.get("NO_COLOR") or os.environ.get("PIIA_NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    out = stream if stream is not None else sys.stdout
    isatty = getattr(out, "isatty", None)
    if callable(isatty):
        try:
            return bool(isatty())
        except (ValueError, OSError):
            return False
    return False
