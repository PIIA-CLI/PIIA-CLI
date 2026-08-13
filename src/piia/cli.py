"""The ``piia`` command line interface.

Conventions, chosen so an AI coding agent can drive this tool without a wrapper:

* Every command emits the same envelope, in human Markdown or JSON.
* Every command answers ``--schema`` with the JSON Schema of its own output.
* JSON goes to ``stdout``; progress, logs and warnings go to ``stderr``.
* Exit codes are stable and specific (see :mod:`piia.errors`).
* Every error carries a ``remediation`` string, so a failed invocation tells the
  caller what to run next instead of just what went wrong.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from piia.analysis import repowise as repowise_mod
from piia.analysis.models import AnalysisBundle
from piia.analysis.pipeline import analyze as run_analysis
from piia.analysis.repos import git_version
from piia.config import Settings
from piia.documents.assemble import PiiaDocument, assemble
from piia.documents.corporate import parse_corporate_document
from piia.documents.writer import ARTIFACT_FORMATS, write_documents
from piia.envelope import Envelope
from piia.errors import ConfigError, DocumentError, PiiaError, UsageError
from piia.llm.client import ChatClient, Message
from piia.llm.drafting import BATCH_SIZE, draft
from piia.output import FORMATS, OutputFormat, emit, resolve_format, supports_color
from piia.output.schemas import DATA_SCHEMAS, all_schemas, envelope_schema
from piia.version import ENVELOPE_VERSION, __version__

CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "max_content_width": 100,
}

EPILOG = """\
\b
Examples:
  piia doctor                                   check git, repowise and the model endpoint
  piia analyze -t . -p ../old-project           deterministic comparison, no model call
  piia analyze -t owner/app --priors-file p.txt read prior works from a file
  piia generate -t . --priors-file priors.txt \\
      --signatory "Ada Lovelace" --company "Acme, Inc." \\
      -f md -f json -f html                     draft the agreement and its exhibits
  piia generate ... --no-llm                    deterministic draft, no model at all
  piia analyze --schema | jq .properties.data   discover the output contract

\b
Model configuration (any OpenAI- or Anthropic-compatible endpoint):
  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_NAME, LLM_TEMPERATURE, LLM_API_STYLE
  Run 'piia init' to write a .env template, 'piia config show' to see what is in effect.

piia-cli is free software under AGPL-3.0-or-later.
Source and issues: https://github.com/PIIA-CLI/PIIA-CLI
"""


# ---------------------------------------------------------------------------
# Shared option groups
# ---------------------------------------------------------------------------
def output_options(func: Callable[..., Any]) -> Callable[..., Any]:
    func = click.option(
        "-o",
        "--output",
        "output_format",
        type=click.Choice(sorted(FORMATS), case_sensitive=False),
        default=None,
        help="Output format. Defaults to 'human' on a TTY and 'json' when piped.",
    )(func)
    func = click.option(
        "--json", "json_flag", is_flag=True, help="Shorthand for '-o json'."
    )(func)
    func = click.option(
        "--schema",
        "schema_flag",
        is_flag=True,
        help="Print the JSON Schema of this command's output and exit.",
    )(func)
    func = click.option("--no-color", is_flag=True, help="Disable ANSI colour.")(func)
    func = click.option(
        "-q", "--quiet", is_flag=True, help="Suppress progress output on stderr."
    )(func)
    func = click.option(
        "--config",
        "config_path",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help="Path to piia.yaml (default: nearest one, searching upward).",
    )(func)
    func = click.option(
        "--env-file",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help="Path to a .env file (default: nearest one, searching upward).",
    )(func)
    return func


def repo_options(func: Callable[..., Any]) -> Callable[..., Any]:
    func = click.option(
        "-t",
        "--target",
        required=True,
        help="Target work: the company's repository. Path, clone URL, or owner/name.",
    )(func)
    func = click.option(
        "-p",
        "--prior",
        "priors",
        multiple=True,
        help="A prior work. Repeatable. Path, clone URL, or owner/name.",
    )(func)
    func = click.option(
        "--priors-file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="File of prior works, one per line. '#' comments and blank lines ignored.",
    )(func)
    func = click.option(
        "--search-root",
        "search_roots",
        multiple=True,
        type=click.Path(file_okay=False, path_type=Path),
        help=(
            "Directory to search for existing local checkouts before cloning. "
            "Repeatable. Defaults to the working directory and its parent."
        ),
    )(func)
    func = click.option(
        "--clone-missing/--no-clone-missing",
        default=None,
        help="Clone repositories with no local checkout (default: enabled).",
    )(func)
    func = click.option(
        "--repowise/--no-repowise",
        "use_repowise",
        default=None,
        help="Enrich the analysis with repowise when it is installed (default: enabled).",
    )(func)
    func = click.option(
        "--exclude",
        "excludes",
        multiple=True,
        help="Gitignore-style path pattern to skip while scanning. Repeatable.",
    )(func)
    func = click.option(
        "--max-files",
        type=int,
        default=None,
        help="Cap on files walked per repository (default: 60000).",
    )(func)
    return func


def llm_options(func: Callable[..., Any]) -> Callable[..., Any]:
    func = click.option("--base-url", default=None, help="Override LLM_BASE_URL.")(func)
    func = click.option("--model", default=None, help="Override LLM_MODEL_NAME.")(func)
    func = click.option(
        "--api-key",
        default=None,
        help="Override LLM_API_KEY. Prefer the environment or .env for secrets.",
    )(func)
    func = click.option(
        "--temperature", type=float, default=None, help="Override LLM_TEMPERATURE (default 0.1)."
    )(func)
    func = click.option(
        "--api-style",
        type=click.Choice(["openai", "anthropic"]),
        default=None,
        help="Wire format of the endpoint (default: openai).",
    )(func)
    func = click.option(
        "--system-prompt-file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="Replace the built-in drafting system prompt.",
    )(func)
    func = click.option(
        "--no-llm",
        is_flag=True,
        help="Skip the model entirely and produce a fully deterministic draft.",
    )(func)
    return func


# ---------------------------------------------------------------------------
# Execution harness
# ---------------------------------------------------------------------------
class Runner:
    """Wraps one command: format resolution, progress, envelope, exit code."""

    def __init__(
        self,
        command: str,
        *,
        output_format: str | None,
        json_flag: bool,
        no_color: bool,
        quiet: bool,
    ) -> None:
        self.command = command
        try:
            self.format = resolve_format(output_format, json_shorthand=json_flag)
        except ValueError as exc:
            raise UsageError(str(exc), remediation=f"Valid: {', '.join(sorted(FORMATS))}") from exc
        self.color = False if no_color else supports_color(self.format)
        self.quiet = quiet
        self.envelope = Envelope.start(command)

    def say(self, message: str) -> None:
        """Progress line -- always stderr, never stdout."""
        if not self.quiet:
            click.echo(f"  {message}", err=True)

    def finish(self, data: dict[str, Any]) -> None:
        self.envelope.succeed(data)
        emit(self.envelope, self.format, color=self.color)
        raise SystemExit(self.envelope.exit_code)

    def fail(self, exc: PiiaError) -> None:
        self.envelope.fail(exc)
        emit(self.envelope, self.format, color=self.color)
        raise SystemExit(self.envelope.exit_code)

    def emit_schema(self) -> None:
        click.echo(json.dumps(envelope_schema(self.command), indent=2))
        raise SystemExit(0)


def _run(command: str, body: Callable[[Runner], dict[str, Any]], **flags: Any) -> None:
    runner = Runner(
        command,
        output_format=flags.get("output_format"),
        json_flag=bool(flags.get("json_flag")),
        no_color=bool(flags.get("no_color")),
        quiet=bool(flags.get("quiet")),
    )
    if flags.get("schema_flag"):
        runner.emit_schema()
    try:
        runner.finish(body(runner))
    except PiiaError as exc:
        runner.fail(exc)
    except click.ClickException:
        raise
    except KeyboardInterrupt:
        runner.fail(
            PiiaError(
                "Interrupted.",
                code="INTERRUPTED",
                exit_code=130,
                remediation="Re-run the command; completed clones are reused.",
            )
        )
    except OSError as exc:
        runner.fail(
            PiiaError(
                f"Filesystem error: {exc}",
                code="IO_ERROR",
                exit_code=9,
                remediation="Check paths and permissions, then re-run.",
            )
        )


# ---------------------------------------------------------------------------
# Settings assembly
# ---------------------------------------------------------------------------
def _load_settings(
    *,
    config_path: Path | None,
    env_file: Path | None,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    return Settings.load(config_path=config_path, env_path=env_file, overrides=overrides or {})


def _collect_priors(priors: tuple[str, ...], priors_file: Path | None) -> list[str]:
    collected = [p.strip() for p in priors if p.strip()]
    if priors_file:
        for raw in priors_file.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                collected.append(line)
    deduped: list[str] = []
    for item in collected:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _analysis_overrides(
    *,
    search_roots: tuple[Path, ...],
    clone_missing: bool | None,
    use_repowise: bool | None,
    excludes: tuple[str, ...],
    max_files: int | None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if search_roots:
        overrides["analysis.search_roots"] = [str(p) for p in search_roots]
    if clone_missing is not None:
        overrides["analysis.clone_missing"] = clone_missing
    if use_repowise is not None:
        overrides["analysis.use_repowise"] = use_repowise
    if excludes:
        overrides["analysis.exclude"] = list(excludes)
    if max_files is not None:
        overrides["analysis.max_files"] = max_files
    return overrides


def _llm_overrides(
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    temperature: float | None,
    api_style: str | None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if base_url:
        overrides["llm.base_url"] = base_url
    if model:
        overrides["llm.model"] = model
    if api_key:
        overrides["llm.api_key"] = api_key
    if temperature is not None:
        overrides["llm.temperature"] = temperature
    if api_style:
        overrides["llm.api_style"] = api_style
    return overrides


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------
@click.group(context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.version_option(__version__, "-V", "--version", prog_name="piia")
def cli() -> None:
    """Generate a PIIA from code: analyze prior repositories, carve out prior IP.

    A PIIA (Proprietary Information and Inventions Agreement) assigns what you
    build for a company to that company. Exhibit A is where you carve out what
    you built before -- and that exhibit is what founders get wrong, because it
    is written from memory.

    piia builds it from evidence instead: it inventories your prior repositories
    and the company's repository deterministically, scores how closely each
    prior work relates to the company's business, and then uses a model you
    choose to draft the exhibit and the descriptive clauses around that
    evidence. The deterministic half always runs; the model half is optional.
    """


# -- analyze ---------------------------------------------------------------
@cli.command(short_help="Compare prior works against a target work. No model call.")
@repo_options
@output_options
def analyze(
    target: str,
    priors: tuple[str, ...],
    priors_file: Path | None,
    search_roots: tuple[Path, ...],
    clone_missing: bool | None,
    use_repowise: bool | None,
    excludes: tuple[str, ...],
    max_files: int | None,
    **flags: Any,
) -> None:
    """Run the deterministic analysis and print the comparison.

    Nothing here calls a model, so the result is reproducible: the same commits
    always produce the same output and the same digest.
    """

    def body(runner: Runner) -> dict[str, Any]:
        prior_list = _collect_priors(priors, priors_file)
        if not prior_list:
            raise UsageError(
                "No prior works were given.",
                details=[{"field": "prior", "issue": "at least one is required"}],
                remediation=(
                    "Pass --prior <repo> (repeatable), or --priors-file with one repository "
                    "per line."
                ),
            )
        settings = _load_settings(
            config_path=flags.get("config_path"),
            env_file=flags.get("env_file"),
            overrides=_analysis_overrides(
                search_roots=search_roots,
                clone_missing=clone_missing,
                use_repowise=use_repowise,
                excludes=excludes,
                max_files=max_files,
            ),
        )
        runner.say(f"Analyzing 1 target work and {len(prior_list)} prior work(s)")
        bundle = run_analysis(
            target=target, priors=prior_list, settings=settings, progress=runner.say
        )
        for note in bundle.warnings:
            runner.envelope.warn("ANALYSIS_NOTE", note)
        return bundle.to_dict()

    _run("analyze", body, **flags)


# -- generate --------------------------------------------------------------
@cli.command(short_help="Analyze, then draft the PIIA and its exhibits.")
@repo_options
@llm_options
@click.option("--signatory", default=None, help="Full legal name of the person signing.")
@click.option("--signatory-title", default=None, help="Their role, e.g. 'Chief Technology Officer'.")
@click.option("--company", default=None, help="Company legal name. Read from --corporate-document if omitted.")
@click.option("--jurisdiction", default=None, help="Company's jurisdiction of incorporation.")
@click.option("--governing-law", default=None, help="Governing law, e.g. 'the State of Delaware'.")
@click.option("--effective-date", default=None, help="Effective date (default: today, ISO 8601).")
@click.option(
    "-c",
    "--corporate-document",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Existing agreement to read the company, parties and governing law from.",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to write artifacts (default: piia-out/<signatory>-<company>/).",
)
@click.option(
    "-f",
    "--format",
    "doc_formats",
    multiple=True,
    type=click.Choice(ARTIFACT_FORMATS),
    help="Document format to write. Repeatable. Default: md and json.",
)
@click.option(
    "--include-non-solicitation",
    is_flag=True,
    help="Include the non-solicitation section instead of marking it intentionally omitted.",
)
@click.option(
    "--batch-size",
    type=int,
    default=BATCH_SIZE,
    show_default=True,
    help="Prior works per model call. Lower it for small-context models.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Resolve inputs and report the plan without drafting or writing files.",
)
@output_options
def generate(
    target: str,
    priors: tuple[str, ...],
    priors_file: Path | None,
    search_roots: tuple[Path, ...],
    clone_missing: bool | None,
    use_repowise: bool | None,
    excludes: tuple[str, ...],
    max_files: int | None,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    temperature: float | None,
    api_style: str | None,
    system_prompt_file: Path | None,
    no_llm: bool,
    signatory: str | None,
    signatory_title: str | None,
    company: str | None,
    jurisdiction: str | None,
    governing_law: str | None,
    effective_date: str | None,
    corporate_document: Path | None,
    output_dir: Path | None,
    doc_formats: tuple[str, ...],
    include_non_solicitation: bool,
    batch_size: int,
    dry_run: bool,
    **flags: Any,
) -> None:
    """Produce a complete PIIA: the agreement, Exhibit A, the evidence, the artifacts.

    The deterministic analysis runs first and always. The model then drafts the
    descriptive content around that evidence. With --no-llm, or if the endpoint
    is unreachable, every field falls back to a deterministic template and the
    run still produces a complete document.
    """

    def body(runner: Runner) -> dict[str, Any]:
        prior_list = _collect_priors(priors, priors_file)
        if not prior_list:
            raise UsageError(
                "No prior works were given.",
                details=[{"field": "prior", "issue": "at least one is required"}],
                remediation="Pass --prior <repo> (repeatable), or --priors-file.",
            )

        overrides = _analysis_overrides(
            search_roots=search_roots,
            clone_missing=clone_missing,
            use_repowise=use_repowise,
            excludes=excludes,
            max_files=max_files,
        )
        overrides.update(
            _llm_overrides(
                base_url=base_url,
                model=model,
                api_key=api_key,
                temperature=temperature,
                api_style=api_style,
            )
        )
        for key, value in (
            ("document.company_name", company),
            ("document.company_jurisdiction", jurisdiction),
            ("document.governing_law", governing_law),
            ("document.signatory_name", signatory),
            ("document.signatory_title", signatory_title),
            ("document.effective_date", effective_date),
            ("document.corporate_document", str(corporate_document) if corporate_document else None),
        ):
            if value:
                overrides[key] = value
        if doc_formats:
            overrides["document.formats"] = list(doc_formats)

        settings = _load_settings(
            config_path=flags.get("config_path"),
            env_file=flags.get("env_file"),
            overrides=overrides,
        )
        if system_prompt_file:
            settings.llm.system_prompt = system_prompt_file.read_text(encoding="utf-8")

        corporate = None
        if settings.document.corporate_document:
            runner.say(f"Reading corporate document {settings.document.corporate_document}")
            corporate = parse_corporate_document(settings.document.corporate_document)
            for warning in corporate.warnings:
                runner.envelope.warn("CORPORATE_DOCUMENT", warning)
            runner.say(
                f"  company={corporate.company_name or 'not found'}; "
                f"governing law={corporate.governing_law or 'not found'}; "
                f"parties={len(corporate.parties)}; sections={len(corporate.sections)}"
            )

        use_model = not no_llm
        if use_model and not settings.llm.configured:
            raise ConfigError(
                "Model drafting was requested but the endpoint is not configured.",
                details=[
                    {"field": "LLM_BASE_URL", "issue": "set" if settings.llm.base_url else "unset"},
                    {"field": "LLM_MODEL_NAME", "issue": "set" if settings.llm.model else "unset"},
                ],
                remediation=(
                    "Run 'piia init' to write a .env template and set LLM_BASE_URL and "
                    "LLM_MODEL_NAME, or pass --no-llm for a deterministic draft."
                ),
            )

        if dry_run:
            return _plan(
                target=target,
                priors=prior_list,
                settings=settings,
                use_model=use_model,
                corporate=corporate,
                output_dir=output_dir,
                include_non_solicitation=include_non_solicitation,
            )

        runner.say(f"Analyzing 1 target work and {len(prior_list)} prior work(s)")
        bundle = run_analysis(
            target=target, priors=prior_list, settings=settings, progress=runner.say
        )
        for note in bundle.warnings:
            runner.envelope.warn("ANALYSIS_NOTE", note)

        client: ChatClient | None = None
        try:
            if use_model:
                runner.say(
                    f"Drafting with {settings.llm.model} at {settings.llm.base_url} "
                    f"(temperature {settings.llm.temperature})"
                )
                client = ChatClient(settings.llm)
            content = draft(
                bundle,
                company_name=(
                    settings.document.company_name
                    or (corporate.company_name if corporate else None)
                    or "the Company"
                ),
                signatory_name=settings.document.signatory_name or "the Signatory",
                governing_law=settings.document.governing_law
                or (corporate.governing_law if corporate else None),
                corporate_excerpt=corporate.excerpt_for_model() if corporate else None,
                client=client,
                system_override=settings.llm.system_prompt,
                progress=runner.say,
                batch_size=batch_size,
            )
        finally:
            if client is not None:
                client.close()

        for note in content.degraded:
            runner.envelope.warn("DRAFTING_DEGRADED", note)

        document = assemble(
            bundle=bundle,
            content=content,
            settings=settings,
            corporate=corporate,
            include_non_solicitation=include_non_solicitation,
        )
        directory = Path(output_dir) if output_dir else Path(settings.document.output_dir) / document.slug
        runner.say(f"Writing {', '.join(settings.document.formats)} to {directory}")
        artifacts = write_documents(
            document,
            output_dir=directory,
            formats=settings.document.formats,
            bundle=bundle,
        )
        for artifact in artifacts:
            runner.say(f"  wrote {artifact.path} ({artifact.bytes} bytes)")

        return {
            "document": document.to_dict(),
            "artifacts": [a.to_dict() for a in artifacts],
            "output_dir": str(directory),
            "provenance": document.provenance,
            "analysis_digest": document.evidence["analysis_digest"],
        }

    _run("generate", body, **flags)


def _plan(
    *,
    target: str,
    priors: list[str],
    settings: Settings,
    use_model: bool,
    corporate: Any,
    output_dir: Path | None,
    include_non_solicitation: bool,
) -> dict[str, Any]:
    return {
        "dry_run": True,
        "target": target,
        "prior_works": priors,
        "search_roots": settings.analysis.search_roots,
        "clone_missing": settings.analysis.clone_missing,
        "repowise": {
            "requested": settings.analysis.use_repowise,
            "available": repowise_mod.available(settings.analysis.repowise_bin),
        },
        "model": {
            "enabled": use_model,
            "base_url": settings.llm.base_url,
            "model": settings.llm.model,
            "temperature": settings.llm.temperature,
            "api_style": settings.llm.api_style,
            "estimated_calls": 2 + max(1, (len(priors) + BATCH_SIZE - 1) // BATCH_SIZE)
            if use_model
            else 0,
        },
        "document": {
            "company": settings.document.company_name
            or (corporate.company_name if corporate else None),
            "signatory": settings.document.signatory_name,
            "governing_law": settings.document.governing_law
            or (corporate.governing_law if corporate else None),
            "formats": settings.document.formats,
            "output_dir": str(output_dir or Path(settings.document.output_dir)),
            "non_solicitation_included": include_non_solicitation,
        },
        "corporate_document": corporate.to_dict() if corporate else None,
    }


# -- render ----------------------------------------------------------------
@cli.command(short_help="Re-render a saved piia.json into other formats.")
@click.argument("document", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-f",
    "--format",
    "doc_formats",
    multiple=True,
    type=click.Choice(ARTIFACT_FORMATS),
    help="Format to write. Repeatable. Default: md.",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to write (default: alongside the input document).",
)
@output_options
def render(
    document: Path,
    doc_formats: tuple[str, ...],
    output_dir: Path | None,
    **flags: Any,
) -> None:
    """Render an existing ``piia.json`` to Markdown, HTML, DOCX or PDF.

    Useful for producing a DOCX for counsel, or a PDF for a signature packet,
    without re-running the analysis or paying for another model call.
    """

    def body(runner: Runner) -> dict[str, Any]:
        try:
            payload = json.loads(document.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DocumentError(
                f"{document} is not valid JSON: {exc}",
                remediation="Pass the piia.json written by 'piia generate'.",
            ) from exc
        if "sections" not in payload or "evidence" not in payload:
            raise DocumentError(
                f"{document} does not look like a piia document.",
                details=[{"field": "document", "issue": "missing 'sections' or 'evidence'"}],
                remediation="Pass the piia.json written by 'piia generate --format json'.",
            )
        doc = _document_from_dict(payload)
        formats = list(doc_formats) or ["md"]
        directory = Path(output_dir) if output_dir else document.parent
        runner.say(f"Rendering {', '.join(formats)} into {directory}")
        artifacts = write_documents(doc, output_dir=directory, formats=formats)
        return {
            "artifacts": [a.to_dict() for a in artifacts],
            "source": str(document),
            "output_dir": str(directory),
        }

    _run("render", body, **flags)


def _document_from_dict(payload: dict[str, Any]) -> PiiaDocument:
    """Rebuild a document object from a saved payload."""
    known = {
        "title", "company", "signatory", "effective_date", "governing_law", "recitals",
        "sections", "exhibits", "prior_inventions", "review_checklist", "open_source_notes",
        "supplemental_clauses", "executive_summary", "company_business", "technical_field",
        "evidence", "provenance", "disclaimer", "schema_version", "generated_at",
    }
    missing = [k for k in ("title", "sections", "evidence", "provenance") if k not in payload]
    if missing:
        raise DocumentError(
            "The document payload is missing required keys: " + ", ".join(missing),
            remediation="Regenerate it with 'piia generate --format json'.",
        )
    kwargs = {k: v for k, v in payload.items() if k in known}
    kwargs.setdefault("company", {})
    kwargs.setdefault("signatory", {})
    kwargs.setdefault("recitals", [])
    kwargs.setdefault("exhibits", [])
    kwargs.setdefault("prior_inventions", [])
    kwargs.setdefault("review_checklist", [])
    kwargs.setdefault("open_source_notes", [])
    kwargs.setdefault("supplemental_clauses", [])
    kwargs.setdefault("executive_summary", "")
    kwargs.setdefault("company_business", "")
    kwargs.setdefault("technical_field", "")
    kwargs.setdefault("effective_date", "")
    kwargs.setdefault("governing_law", "")
    return PiiaDocument(**kwargs)  # type: ignore[arg-type]


# -- doctor ----------------------------------------------------------------
@cli.command(short_help="Check git, repowise, configuration and the model endpoint.")
@click.option(
    "--skip-llm", is_flag=True, help="Do not contact the model endpoint."
)
@llm_options
@output_options
def doctor(
    skip_llm: bool,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    temperature: float | None,
    api_style: str | None,
    system_prompt_file: Path | None,
    no_llm: bool,
    **flags: Any,
) -> None:
    """Verify the environment before a real run.

    Exits non-zero if anything required is missing, so it works as a CI gate.
    """

    def body(runner: Runner) -> dict[str, Any]:
        settings = _load_settings(
            config_path=flags.get("config_path"),
            env_file=flags.get("env_file"),
            overrides=_llm_overrides(
                base_url=base_url,
                model=model,
                api_key=api_key,
                temperature=temperature,
                api_style=api_style,
            ),
        )
        checks: list[dict[str, Any]] = []

        def check(name: str, status: str, detail: str, remediation: str | None = None) -> None:
            checks.append(
                {"name": name, "status": status, "detail": detail, "remediation": remediation}
            )
            runner.say(f"{name}: {status} -- {detail}")

        check("piia", "ok", f"version {__version__}, output contract {ENVELOPE_VERSION}")
        check(
            "python",
            "ok" if sys.version_info >= (3, 11) else "error",
            sys.version.split()[0],
            None if sys.version_info >= (3, 11) else "piia requires Python 3.11 or newer.",
        )

        git = git_version()
        check(
            "git",
            "ok" if git else "error",
            git or "not found on PATH",
            None if git else "Install git: https://git-scm.com/downloads",
        )

        rw_version = repowise_mod.version(settings.analysis.repowise_bin)
        if rw_version:
            check("repowise", "ok", f"version {rw_version} (optional enrichment enabled)")
        else:
            check(
                "repowise",
                "warn",
                "not installed -- the built-in deterministic scanner will be used alone",
                "pip install piia-cli[repowise]",
            )

        check(
            "config file",
            "ok" if settings.config_file else "skipped",
            settings.config_file or "none found (defaults in use)",
        )
        check(
            "env file",
            "ok" if settings.env_file else "skipped",
            settings.env_file or "none found",
        )

        roots = [Path(r) for r in settings.analysis.search_roots]
        existing = [str(r) for r in roots if r.is_dir()]
        check(
            "checkout search roots",
            "ok" if existing else "warn",
            ", ".join(existing) or "none of the configured roots exist",
            None if existing else "Pass --search-root <dir> pointing at your checkouts.",
        )

        for name, tool, extra in (
            ("DOCX support", "docx", "docx"),
            ("PDF support", "weasyprint", "pdf"),
        ):
            try:
                __import__(tool)
                check(name, "ok", f"{tool} is installed")
            except ImportError:
                check(
                    name,
                    "skipped",
                    f"{tool} not installed (only needed for --format {extra})",
                    f"pip install piia-cli[{extra}]",
                )

        if skip_llm or no_llm:
            check("model endpoint", "skipped", "not contacted (--skip-llm)")
        elif not settings.llm.configured:
            check(
                "model endpoint",
                "warn",
                "LLM_BASE_URL and/or LLM_MODEL_NAME are unset",
                "Run 'piia init', or use 'piia generate --no-llm' for deterministic drafts.",
            )
        else:
            check(
                "model configuration",
                "ok",
                f"{settings.llm.model} at {settings.llm.base_url} "
                f"(style={settings.llm.api_style}, temperature={settings.llm.temperature}, "
                f"api key {'set' if settings.llm.api_key else 'not set'})",
            )
            runner.say("Contacting the model endpoint with a one-token probe...")
            try:
                with ChatClient(settings.llm) as client:
                    response = client.complete(
                        [Message(role="user", content="Reply with the single word: ready")]
                    )
                check(
                    "model endpoint",
                    "ok",
                    f"responded in {response.latency_ms}ms "
                    f"(model={response.model or settings.llm.model}, "
                    f"reply={response.text.strip()[:40]!r})",
                )
            except PiiaError as exc:
                check("model endpoint", "error", exc.message, exc.remediation)

        summary = {
            "passed": sum(1 for c in checks if c["status"] == "ok"),
            "warnings": sum(1 for c in checks if c["status"] == "warn"),
            "failures": sum(1 for c in checks if c["status"] == "error"),
            "skipped": sum(1 for c in checks if c["status"] == "skipped"),
        }
        if summary["failures"]:
            runner.envelope.warn(
                "DOCTOR_FAILURES",
                f"{summary['failures']} check(s) failed.",
                "Address the remediation lines above and re-run 'piia doctor'.",
            )
            runner.envelope.exit_code = 1
        data = {"checks": checks, "summary": summary}
        if summary["failures"]:
            runner.envelope.succeed(data)
            runner.envelope.exit_code = 1
            emit(runner.envelope, runner.format, color=runner.color)
            raise SystemExit(1)
        return data

    _run("doctor", body, **flags)


# -- init ------------------------------------------------------------------
ENV_TEMPLATE = """\
# piia-cli configuration -- model endpoint
# Any OpenAI-compatible endpoint works: OpenAI, Azure OpenAI, LiteLLM, vLLM,
# Ollama (/v1), LM Studio, OpenRouter, Together, Groq, DeepSeek, Mistral, or
# Google's OpenAI-compatible Gemini endpoint. Set LLM_API_STYLE=anthropic to
# use Anthropic's native Messages API instead.
# See docs/LLM_PROVIDERS.md for a table of working values.

LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=
LLM_MODEL_NAME=

# Low temperature for legal drafting. Raise it only if you want more variation.
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=8192
LLM_TIMEOUT=180
LLM_MAX_RETRIES=3
LLM_API_STYLE=openai

# Optional: replace the built-in drafting system prompt.
# LLM_SYSTEM_PROMPT_FILE=./prompts/drafting.md

# Optional: extra HTTP headers, as JSON (for gateways and proxies).
# LLM_EXTRA_HEADERS={"X-Org-Id": "acme"}

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
# Directories searched for existing local checkouts before anything is cloned.
# Comma-separated. Defaults to the working directory and its parent.
# PIIA_SEARCH_ROOTS=~/src,~/work
# PIIA_CLONE_MISSING=true
# PIIA_USE_REPOWISE=true
# PIIA_EXCLUDE=vendor/,*.generated.*

# ---------------------------------------------------------------------------
# Document defaults (all overridable per run on the command line)
# ---------------------------------------------------------------------------
# PIIA_COMPANY_NAME=Acme, Inc.
# PIIA_SIGNATORY_NAME=Ada Lovelace
# PIIA_SIGNATORY_TITLE=Chief Technology Officer
# PIIA_GOVERNING_LAW=the State of Delaware
# PIIA_DOCUMENT_FORMATS=md,json,html
"""

CONFIG_TEMPLATE = """\
# piia-cli project configuration.
# Precedence: CLI flags > environment > .env > this file > defaults.
# Secrets belong in .env or the environment, not here -- this file is committed.

llm:
  # base_url: http://localhost:11434/v1
  # model: qwen2.5-coder:14b
  temperature: 0.1
  api_style: openai

analysis:
  # Existing checkouts are found here before anything is cloned.
  # search_roots:
  #   - ~/src
  clone_missing: true
  use_repowise: true
  exclude: []

document:
  # company_name: Acme, Inc.
  # governing_law: the State of Delaware
  formats:
    - md
    - json
  output_dir: piia-out

# A file of prior works, one repository per line, is usually easier than flags:
#   piia generate -t . --priors-file priors.txt --signatory "Ada Lovelace"
"""

PRIORS_TEMPLATE = """\
# Prior works: one repository per line.
# A local path, a clone URL, or owner/name shorthand. '#' starts a comment.
#
# Existing local checkouts are matched by git remote URL first, so listing the
# GitHub URL is enough even when the directory has a different name.
#
# https://github.com/you/earlier-project
# ../another-project
# you/a-third-project
"""


@cli.command(short_help="Write .env, piia.yaml and priors.txt templates.")
@click.option(
    "-d",
    "--directory",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Where to write the templates.",
)
@click.option("--force", is_flag=True, help="Overwrite files that already exist.")
@output_options
def init(directory: Path, force: bool, **flags: Any) -> None:
    """Scaffold configuration in the current project.

    Writes ``.env`` (secrets, gitignored), ``.env.example`` (committed),
    ``piia.yaml`` and ``priors.txt``. Existing files are left alone unless
    ``--force`` is passed.
    """

    def body(runner: Runner) -> dict[str, Any]:
        directory.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        skipped: list[str] = []
        for name, content in (
            (".env", ENV_TEMPLATE),
            (".env.example", ENV_TEMPLATE),
            ("piia.yaml", CONFIG_TEMPLATE),
            ("priors.txt", PRIORS_TEMPLATE),
        ):
            path = directory / name
            if path.exists() and not force:
                skipped.append(str(path))
                runner.say(f"exists, left alone: {path}")
                continue
            path.write_text(content, encoding="utf-8")
            written.append(str(path))
            runner.say(f"wrote {path}")

        gitignore = directory / ".gitignore"
        ignored = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
        if ".env" not in ignored:
            with gitignore.open("a", encoding="utf-8") as handle:
                handle.write("\n# piia-cli\n.env\npiia-out/\n")
            written.append(str(gitignore))
            runner.say(f"appended .env and piia-out/ to {gitignore}")

        return {
            "written": written,
            "skipped": skipped,
            "next_steps": [
                "Set LLM_BASE_URL and LLM_MODEL_NAME in .env",
                "List your prior repositories in priors.txt",
                "Run 'piia doctor' to verify the setup",
                "Run 'piia generate -t . --priors-file priors.txt --signatory \"Your Name\"'",
            ],
        }

    _run("init", body, **flags)


# -- config ----------------------------------------------------------------
@cli.group(short_help="Inspect configuration.")
def config() -> None:
    """Inspect the effective configuration and where each value came from."""


@config.command("show", short_help="Print effective settings with their sources.")
@output_options
def config_show(**flags: Any) -> None:
    """Show every setting, its value and which source supplied it.

    Secrets are redacted to their last four characters.
    """

    def body(runner: Runner) -> dict[str, Any]:
        settings = _load_settings(
            config_path=flags.get("config_path"), env_file=flags.get("env_file")
        )
        return {
            "settings": settings.describe(),
            "config_file": settings.config_file,
            "env_file": settings.env_file,
        }

    _run("config show", body, **flags)


# -- schema ----------------------------------------------------------------
@cli.command(short_help="Print JSON Schemas for every command's output.")
@click.argument("command", required=False)
@click.option("--all", "want_all", is_flag=True, help="Every command's schema in one document.")
def schema(command: str | None, want_all: bool) -> None:
    """Print the JSON Schema of a command's output.

    \b
      piia schema                  the universal envelope
      piia schema analyze          the analyze response
      piia schema --all            every command, in one document
    """
    if want_all:
        click.echo(json.dumps(all_schemas(), indent=2))
        return
    if command and command not in DATA_SCHEMAS:
        raise click.BadParameter(
            f"unknown command {command!r}. Known: {', '.join(sorted(DATA_SCHEMAS))}"
        )
    click.echo(json.dumps(envelope_schema(command), indent=2))


# -- version ---------------------------------------------------------------
@cli.command(short_help="Print version and environment details.")
@output_options
def version(**flags: Any) -> None:
    """Print the tool version, the output-contract version and what is installed."""

    def body(runner: Runner) -> dict[str, Any]:
        import platform

        return {
            "version": __version__,
            "envelope_version": ENVELOPE_VERSION,
            "python": sys.version.split()[0],
            "platform": platform.platform(terse=True),
            "git": git_version(),
            "repowise": repowise_mod.version(),
            "docx": _module_version("docx"),
            "pdf": _module_version("weasyprint"),
            "license": "AGPL-3.0-or-later",
            "source": "https://github.com/PIIA-CLI/PIIA-CLI",
        }

    _run("version", body, **flags)


def _module_version(name: str) -> str | None:
    try:
        from importlib.metadata import version as dist_version

        return dist_version({"docx": "python-docx"}.get(name, name))
    except Exception:
        return None


def main() -> None:
    """Console-script entry point."""
    # Never let an unexpected traceback reach a JSON consumer.
    try:
        cli.main(standalone_mode=True)
    except PiiaError as exc:  # pragma: no cover - defence in depth
        click.echo(json.dumps({"status": "error", "error": exc.to_dict()}), err=True)
        raise SystemExit(exc.exit_code) from exc


if __name__ == "__main__":  # pragma: no cover
    main()


# Keep unused-import checkers honest about the re-exports the CLI relies on.
__all__ = ["cli", "main"]
_ = (AnalysisBundle, OutputFormat, shutil)
