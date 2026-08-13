"""piia -- automated Proprietary Information and Inventions Agreement generator.

The package is split into four layers:

``piia.analysis``
    Deterministic layer. Clones repositories, inventories their technology,
    optionally shells out to ``repowise``, and compares prior works against a
    target work. Given the same commits it always produces the same output.

``piia.llm``
    Non-deterministic layer. A provider-agnostic chat client plus the drafting
    prompts that turn the deterministic analysis into legal prose.

``piia.documents``
    Assembles the PIIA and its exhibits and renders them to Markdown, JSON,
    HTML, DOCX and PDF.

``piia.output``
    The dual-mode CLI output contract: one internal payload rendered either as
    human Markdown or as a schema-stable JSON envelope.
"""

from piia.version import __version__

__all__ = ["__version__"]
