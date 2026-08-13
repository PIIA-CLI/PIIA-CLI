"""Rendering and writing artifacts.

Five formats from one document object:

===========  ==================================================================
``md``       Markdown -- the primary human and agent format.
``json``     The full document payload, schema-versioned.
``html``     One self-contained file: no external CSS, fonts, JS or network.
``docx``     Word, for counsel who redline in Word. Needs ``[docx]``.
``pdf``      Print-ready, rendered from the HTML. Needs ``[pdf]``.
===========  ==================================================================

``analysis.json`` and ``exhibit-a.md`` are always written alongside: the first
is the deterministic evidence on its own, the second is the exhibit alone, for
pasting into an agreement a company already has.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from piia.analysis.models import AnalysisBundle
from piia.documents.assemble import PiiaDocument
from piia.errors import DependencyMissingError, DocumentError

#: Formats the user may request with ``--format``.
ARTIFACT_FORMATS = ("md", "json", "html", "docx", "pdf")

DISCLAIMER_LABEL = "Not legal advice."

_ROMAN = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]


@dataclass
class Artifact:
    kind: str
    path: str
    bytes: int
    format: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "bytes": self.bytes, "format": self.format}


def _roman(number: int) -> str:
    value = int(number)
    out: list[str] = []
    for amount, glyph in _ROMAN:
        while value >= amount:
            out.append(glyph)
            value -= amount
    return "".join(out) or "I"


def environment(*, autoescape_html: bool = False) -> Environment:
    env = Environment(
        loader=PackageLoader("piia", "templates"),
        autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml"), default=False)
        if autoescape_html
        else False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["roman"] = _roman
    return env


def render_markdown(doc: PiiaDocument) -> str:
    template = environment().get_template("piia.md.j2")
    return template.render(doc=doc.to_dict(), disclaimer_label=DISCLAIMER_LABEL)


def render_html(doc: PiiaDocument) -> str:
    template = environment(autoescape_html=True).get_template("piia.html.j2")
    return template.render(doc=doc.to_dict(), disclaimer_label=DISCLAIMER_LABEL)


def render_exhibit_a(doc: PiiaDocument) -> str:
    """Exhibit A on its own, sliced out of the full Markdown rendering."""
    markdown = render_markdown(doc)
    marker = "## Exhibit A"
    start = markdown.find(marker)
    if start == -1:
        return markdown
    end = markdown.find("## Exhibit B", start)
    body = markdown[start:end] if end != -1 else markdown[start:]
    header = (
        f"# Exhibit A -- Prior Inventions\n\n"
        f"*Extracted from the Proprietary Information and Inventions Agreement between "
        f"{doc.company['name']} and {doc.signatory['full_name']}, effective "
        f"{doc.effective_date}.*\n\n"
        f"> **{DISCLAIMER_LABEL}** {doc.disclaimer}\n\n"
        f"Analysis digest: `{doc.evidence['analysis_digest']}`\n\n---\n\n"
    )
    return header + body.replace(marker, "## Prior Inventions Excluded from Assignment", 1)


def render_pdf(doc: PiiaDocument) -> bytes:
    try:
        from weasyprint import HTML  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DependencyMissingError("weasyprint", "pdf", "render PDF output") from exc
    try:
        return HTML(string=render_html(doc)).write_pdf()  # type: ignore[no-any-return]
    except Exception as exc:
        raise DocumentError(
            f"PDF rendering failed: {exc}",
            remediation=(
                "WeasyPrint needs system libraries (pango, cairo, gdk-pixbuf). "
                "See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html, "
                "or render --format html and print from a browser."
            ),
        ) from exc


def write_documents(
    doc: PiiaDocument,
    *,
    output_dir: Path,
    formats: list[str],
    bundle: AnalysisBundle | None = None,
    basename: str = "piia",
) -> list[Artifact]:
    """Render and write every requested format. Returns what was written."""
    unknown = [f for f in formats if f not in ARTIFACT_FORMATS]
    if unknown:
        raise DocumentError(
            "Unknown output format(s): " + ", ".join(unknown),
            details=[{"field": "format", "issue": f} for f in unknown],
            remediation="Valid formats: " + ", ".join(ARTIFACT_FORMATS),
        )

    directory = Path(output_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DocumentError(
            f"Could not create output directory {directory}: {exc}",
            remediation="Choose a writable path with --output-dir.",
        ) from exc

    artifacts: list[Artifact] = []

    def write_text(name: str, text: str, kind: str, fmt: str) -> None:
        path = directory / name
        path.write_text(text, encoding="utf-8")
        artifacts.append(Artifact(kind=kind, path=str(path), bytes=len(text.encode()), format=fmt))

    def write_bytes(name: str, blob: bytes, kind: str, fmt: str) -> None:
        path = directory / name
        path.write_bytes(blob)
        artifacts.append(Artifact(kind=kind, path=str(path), bytes=len(blob), format=fmt))

    if "md" in formats:
        write_text(f"{basename}.md", render_markdown(doc), "agreement", "md")
        write_text("exhibit-a-prior-inventions.md", render_exhibit_a(doc), "exhibit-a", "md")
    if "json" in formats:
        write_text(
            f"{basename}.json",
            json.dumps(doc.to_dict(), indent=2, default=str) + "\n",
            "agreement",
            "json",
        )
    if "html" in formats:
        write_text(f"{basename}.html", render_html(doc), "agreement", "html")
    if "docx" in formats:
        from piia.documents.docx import render_docx

        write_bytes(f"{basename}.docx", render_docx(doc), "agreement", "docx")
    if "pdf" in formats:
        write_bytes(f"{basename}.pdf", render_pdf(doc), "agreement", "pdf")

    if bundle is not None:
        write_text(
            "analysis.json",
            json.dumps(bundle.to_dict(), indent=2, default=str) + "\n",
            "analysis",
            "json",
        )
    return artifacts
