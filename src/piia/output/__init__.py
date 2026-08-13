"""The dual-mode output contract: human Markdown on one side, JSON on the other."""

from piia.output.format import (
    FORMATS,
    OutputFormat,
    resolve_format,
    supports_color,
)
from piia.output.render import emit

__all__ = ["FORMATS", "OutputFormat", "emit", "resolve_format", "supports_color"]
