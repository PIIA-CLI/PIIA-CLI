"""Analysis orchestration: specs in, :class:`AnalysisBundle` out.

The pipeline is the only place that knows the order of operations, so both the
``analyze`` and ``generate`` commands share exactly one deterministic path.
Progress is reported through a callback which the CLI wires to ``stderr``,
never to ``stdout`` -- JSON consumers must see one document and nothing else.
"""

from __future__ import annotations

import platform
import sys
from collections.abc import Callable
from pathlib import Path

from piia.analysis.compare import compare, summarize
from piia.analysis.intelligence import CodeIntelligenceProvider, configured_provider
from piia.analysis.models import AnalysisBundle, RepoAnalysis
from piia.analysis.native import scan_repository
from piia.analysis.repos import (
    LocalIndex,
    git_version,
    parse_repo_spec,
    read_git_facts,
    resolve,
)
from piia.config import Settings
from piia.envelope import utc_now_iso
from piia.errors import RepositoryError
from piia.version import __version__

Progress = Callable[[str], None]


def _noop(_message: str) -> None:
    return None


#: Substrings that promote a note to a warning. Everything else -- "found this
#: checkout locally" -- is recorded but not surfaced.
NOTEWORTHY = ("skipped", "cloned", "ambiguous", "repowise", "cap", "not a git")


def _record(note: str, notes: list[str], warnings: list[str]) -> None:
    notes.append(note)
    if any(marker in note.lower() for marker in NOTEWORTHY):
        warnings.append(note)


def analyze(
    *,
    target: str,
    priors: list[str],
    settings: Settings,
    progress: Progress | None = None,
    use_repowise: bool | None = None,
) -> AnalysisBundle:
    """Resolve, scan and compare every repository named on the command line."""
    say = progress or _noop
    cfg = settings.analysis
    workspace = Path(cfg.workspace_dir)

    want_repowise = cfg.use_repowise if use_repowise is None else use_repowise
    intelligence = configured_provider(cfg.repowise_bin)
    repowise_ready = want_repowise and intelligence.available()

    notes: list[str] = []
    warnings: list[str] = []
    if want_repowise and not repowise_ready:
        warnings.append(
            "repowise was requested but is not installed; continuing with the built-in "
            "deterministic scanner only. Install it with 'pip install piia-cli[repowise]'."
        )

    say(f"Indexing local checkouts under: {', '.join(cfg.search_roots)}")
    index = LocalIndex(cfg.search_roots, depth=cfg.search_depth)
    say(f"Found {index.size} local git checkout(s) available for matching")

    target_ref = parse_repo_spec(target, role="target")
    target_ref, note = resolve(
        target_ref,
        index=index,
        workspace=workspace,
        clone_missing=cfg.clone_missing,
    )
    if note:
        _record(note, notes, warnings)
        say(note)
    target_analysis = _analyze_one(
        target_ref,
        settings=settings,
        intelligence=intelligence,
        repowise_ready=repowise_ready,
        say=say,
        warnings=warnings,
    )

    seen: set[str] = {str(Path(target_ref.path).resolve())} if target_ref.path else set()
    prior_analyses: list[RepoAnalysis] = []
    for spec in priors:
        ref = parse_repo_spec(spec, role="prior")
        try:
            ref, note = resolve(
                ref, index=index, workspace=workspace, clone_missing=cfg.clone_missing
            )
        except RepositoryError as exc:
            _record(f"{ref.name}: skipped -- {exc.message}", notes, warnings)
            say(f"! {ref.name}: {exc.message}")
            continue
        if note:
            _record(note, notes, warnings)
            say(note)
        resolved_path = str(Path(ref.path).resolve()) if ref.path else None
        if resolved_path and resolved_path in seen:
            _record(
                f"{ref.name}: skipped -- resolves to an already-analyzed checkout",
                notes,
                warnings,
            )
            continue
        if resolved_path:
            seen.add(resolved_path)
        prior_analyses.append(
            _analyze_one(
                ref,
                settings=settings,
                intelligence=intelligence,
                repowise_ready=repowise_ready,
                say=say,
                warnings=warnings,
            )
        )

    say(f"Comparing {len(prior_analyses)} prior work(s) against {target_analysis.name}")
    overlaps = [compare(target_analysis, prior) for prior in prior_analyses]
    overlaps.sort(key=lambda o: (-o.overlap_score, o.repository))

    return AnalysisBundle(
        target=target_analysis,
        prior_works=prior_analyses,
        overlaps=overlaps,
        aggregate=summarize(target_analysis, prior_analyses, overlaps),
        tool_versions=tool_versions(intelligence, repowise_ready),
        generated_at=utc_now_iso(),
        notes=notes,
        warnings=warnings,
    )


def _analyze_one(
    ref,
    *,
    settings: Settings,
    intelligence: CodeIntelligenceProvider,
    repowise_ready: bool,
    say: Progress,
    warnings: list[str],
) -> RepoAnalysis:
    cfg = settings.analysis
    path = Path(ref.path)
    say(f"Scanning {ref.name} ({ref.source}) at {path}")
    if not path.is_dir():
        raise RepositoryError(
            f"{ref.name}: {path} is not a directory.",
            remediation="Check the path, or let piia clone the repository instead.",
        )

    git_facts = read_git_facts(path, history_limit=cfg.git_history_limit)
    scan = scan_repository(path, max_files=cfg.max_files, exclude=cfg.exclude)
    analysis = RepoAnalysis(
        ref=ref,
        git=git_facts,
        technology=scan.technology,
        metrics=scan.metrics,
        license=scan.license,
        description=scan.description,
        readme_excerpt=scan.readme_excerpt,
        notes=list(scan.notes),
    )
    if not git_facts.is_git_repo:
        analysis.notes.append(
            "Not a git checkout: commit history, dates and contributors are unavailable."
        )
    if git_facts.is_shallow:
        analysis.notes.append(
            "Shallow checkout: first-commit date is the shallow boundary, not the true origin."
        )

    if repowise_ready:
        say(f"  running repowise over {ref.name} (deterministic, keyless)")
        result = intelligence.analyze(
            path,
            timeout=cfg.repowise_timeout,
            index=True,
        )
        analysis.repowise = result
        for degraded in result.get("degraded", []):
            message = f"{ref.name}: repowise -- {degraded}"
            analysis.notes.append(message)
            warnings.append(message)

    say(
        f"  {ref.name}: {analysis.metrics.get('analyzed_files', 0)} source files, "
        f"{len(analysis.technology.dependencies)} dependencies, "
        f"primary language {analysis.technology.primary_language or 'unknown'}"
    )
    return analysis


def tool_versions(
    intelligence: CodeIntelligenceProvider, repowise_ready: bool = False
) -> dict[str, str | None]:
    return {
        "piia": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(terse=True),
        "git": git_version(),
        intelligence.name: intelligence.version() if repowise_ready else None,
    }
