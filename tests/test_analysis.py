"""Deterministic layer: scanning, git facts, resolution and comparison."""

from __future__ import annotations

from pathlib import Path

import pytest

from piia.analysis.compare import _classify, compare, summarize
from piia.analysis.models import RELATEDNESS_HIGH, RELATEDNESS_NONE
from piia.analysis.native import scan_repository
from piia.analysis.pipeline import analyze
from piia.analysis.repos import (
    LocalIndex,
    normalize_remote,
    parse_repo_spec,
    read_git_facts,
    resolve,
)
from piia.config import Settings
from piia.errors import RepositoryError


class TestNormalizeRemote:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/Acme/Repo.git",
            "https://github.com/acme/repo",
            "git@github.com:Acme/Repo.git",
            "ssh://git@github.com/acme/repo.git",
            "https://github.com/acme/repo/",
        ],
    )
    def test_every_remote_form_collapses_to_one_key(self, url: str) -> None:
        assert normalize_remote(url) == "github.com/acme/repo"

    def test_host_is_significant(self) -> None:
        assert normalize_remote("https://gitlab.com/acme/repo") == "gitlab.com/acme/repo"

    def test_none_for_empty(self) -> None:
        assert normalize_remote(None) is None
        assert normalize_remote("") is None


class TestParseRepoSpec:
    def test_local_path_wins_over_url_parsing(self, target_repo: Path) -> None:
        ref = parse_repo_spec(str(target_repo))
        assert ref.source == "local-path"
        assert ref.path == str(target_repo.resolve())
        assert ref.remote_url == "https://github.com/acme/company-app.git"

    def test_shorthand(self) -> None:
        ref = parse_repo_spec("acme/widget")
        assert (ref.owner, ref.name) == ("acme", "widget")
        assert ref.url == "https://github.com/acme/widget"

    def test_full_url(self) -> None:
        ref = parse_repo_spec("https://gitlab.com/team/sub/project.git")
        assert ref.name == "project"
        assert ref.host == "gitlab.com"

    def test_garbage_raises_with_remediation(self) -> None:
        with pytest.raises(RepositoryError) as exc:
            parse_repo_spec("!!!")
        assert exc.value.remediation


class TestLocalDiscovery:
    def test_matches_by_remote_url_despite_directory_rename(
        self, tmp_path: Path, target_repo: Path
    ) -> None:
        renamed = target_repo.parent / "totally-different-name"
        target_repo.rename(renamed)
        index = LocalIndex([str(renamed.parent)], depth=1)
        ref = parse_repo_spec("https://github.com/acme/company-app")
        path, kind = index.lookup(ref)
        assert path == renamed.resolve()
        assert kind == "remote-url"

    def test_matches_by_directory_name_when_no_remote(self, tmp_path: Path) -> None:
        from tests.conftest import make_repo

        repo = make_repo(tmp_path / "checkouts", "solo-project", files={"a.py": "x = 1\n"})
        index = LocalIndex([str(repo.parent)], depth=1)
        path, kind = index.lookup(parse_repo_spec("someone/solo-project"))
        assert path == repo.resolve()
        assert kind == "directory-name"

    def test_resolve_prefers_local_over_clone(self, target_repo: Path) -> None:
        index = LocalIndex([str(target_repo.parent)], depth=1)
        ref, note = resolve(
            parse_repo_spec("https://github.com/acme/company-app"),
            index=index,
            clone_missing=False,
        )
        assert ref.source == "local-discovery"
        assert note and "existing local checkout" in note

    def test_resolve_refuses_to_clone_when_disabled(self, tmp_path: Path) -> None:
        index = LocalIndex([str(tmp_path)], depth=1)
        with pytest.raises(RepositoryError) as exc:
            resolve(
                parse_repo_spec("https://github.com/nobody/nothing"),
                index=index,
                clone_missing=False,
            )
        assert "cloning is disabled" in exc.value.message
        assert "--search-root" in (exc.value.remediation or "")


class TestGitFacts:
    def test_reads_commit_dates_and_authors(self, target_repo: Path) -> None:
        facts = read_git_facts(target_repo)
        assert facts.is_git_repo
        assert facts.commit and len(facts.commit) == 40
        assert facts.first_commit_date == "2025-06-01"
        assert facts.commit_count == 1
        assert facts.contributors == ["Test Author"]

    def test_non_repo_is_reported_not_raised(self, tmp_path: Path) -> None:
        (tmp_path / "plain").mkdir()
        assert read_git_facts(tmp_path / "plain").is_git_repo is False


class TestScanner:
    def test_detects_stack_from_manifests_and_files(self, target_repo: Path) -> None:
        result = scan_repository(target_repo)
        tech = result.technology
        assert tech.primary_language == "Python"
        assert "FastAPI" in tech.frameworks
        assert "PyTorch" in tech.ml_ai
        assert "PostgreSQL" in tech.datastores
        assert "Redis" in tech.datastores
        assert "Docker" in tech.infrastructure
        assert "GitHub Actions" in tech.ci
        assert "pytest" in tech.testing
        assert result.license == "MIT"
        assert "document understanding" in (result.readme_excerpt or "")

    def test_ignores_vendored_directories(self, tmp_path: Path) -> None:
        from tests.conftest import make_repo

        repo = make_repo(
            tmp_path / "c",
            "mixed",
            files={
                "app.py": "x = 1\n",
                ".cache/llama.cpp/huge.cpp": "int main(){}\n" * 5000,
                "node_modules/dep/index.js": "module.exports={}\n" * 500,
            },
        )
        result = scan_repository(repo)
        assert result.technology.primary_language == "Python"
        assert not any(lang.name == "C++" for lang in result.technology.languages)

    def test_exclude_patterns_are_honoured(self, target_repo: Path) -> None:
        result = scan_repository(target_repo, exclude=["src/*"])
        assert result.metrics["analyzed_files"] < 3

    def test_dependency_versions_are_captured(self, target_repo: Path) -> None:
        deps = {d.name: d.version for d in scan_repository(target_repo).technology.dependencies}
        assert deps["fastapi"] == "==0.110.0"

    def test_malformed_manifest_does_not_fail_the_scan(self, tmp_path: Path) -> None:
        from tests.conftest import make_repo

        repo = make_repo(
            tmp_path / "c",
            "broken",
            files={"package.json": "{ this is not json", "app.py": "x = 1\n"},
        )
        result = scan_repository(repo)
        assert result.technology.primary_language == "Python"
        assert "npm" in result.technology.package_managers


class TestComparison:
    def test_displayed_boundary_score_and_band_cannot_disagree(self) -> None:
        published = round(0.5499999999999999, 4)
        assert published == 0.55
        assert _classify(published) == RELATEDNESS_HIGH

    def test_shared_stack_scores_high_and_explains_itself(self, bundle) -> None:
        overlap = bundle.overlap_for("earlier-vision")
        assert overlap is not None
        assert overlap.relatedness == RELATEDNESS_HIGH
        assert "PyTorch" in overlap.shared_technologies
        assert overlap.predates_target is True
        assert any("precedes" in line for line in overlap.rationale)
        assert overlap.recommendation

    def test_unrelated_work_scores_none(self, bundle) -> None:
        overlap = bundle.overlap_for("rust-tool")
        assert overlap is not None
        assert overlap.relatedness == RELATEDNESS_NONE
        assert overlap.overlap_score < 0.12

    def test_ubiquitous_technology_counts_for_less_than_substantive(self) -> None:
        from tests.conftest import _analysis

        target = _analysis(
            "t",
            role="target",
            ml_ai=["NumPy", "pandas", "PyTorch"],
            frameworks=["FastAPI"],
            deps=["numpy", "pandas", "torch", "fastapi"],
        )
        # One prior shares only the ubiquitous half, the other only the
        # substantive half. Both share two of four dependencies, so the
        # difference in score comes purely from the category weighting.
        ubiquitous_only = _analysis("u", ml_ai=["NumPy", "pandas"], deps=["numpy", "pandas"])
        substantive_only = _analysis(
            "s", ml_ai=["PyTorch"], frameworks=["FastAPI"], deps=["torch", "fastapi"]
        )
        weak = compare(target, ubiquitous_only)
        strong = compare(target, substantive_only)
        assert strong.overlap_score > weak.overlap_score
        assert any("No substantive" in line for line in weak.rationale)
        assert "PyTorch" in strong.shared_technologies

    def test_score_is_symmetric_in_inputs_but_normalised_on_target(self) -> None:
        from tests.conftest import _analysis

        big = _analysis("big", ml_ai=["PyTorch", "OpenCV", "JAX"], deps=["torch", "opencv-python"])
        small = _analysis("small", ml_ai=["PyTorch"], deps=["torch"])
        # A prior work that covers all of a small target scores higher than the
        # reverse: coverage is measured against the target.
        assert compare(small, big).overlap_score > compare(big, small).overlap_score

    def test_aggregate_lists_carve_out_candidates_by_score(self, bundle) -> None:
        summary = bundle.aggregate
        assert summary["prior_work_count"] == 2
        assert summary["carve_out_candidates"] == ["earlier-vision"]
        assert "PyTorch" in summary["shared_technologies"]

    def test_summarize_with_no_priors(self) -> None:
        from tests.conftest import _analysis

        target = _analysis("t", role="target")
        summary = summarize(target, [], [])
        assert summary["prior_work_count"] == 0
        assert summary["highest_overlap"] == 0.0


class TestPipeline:
    def test_end_to_end_deterministic_run(
        self,
        target_repo: Path,
        related_prior_repo: Path,
        unrelated_prior_repo: Path,
        tmp_path: Path,
    ) -> None:
        settings = Settings.load(cwd=tmp_path, env={})
        settings.analysis.search_roots = [str(target_repo.parent)]
        settings.analysis.search_depth = 1
        settings.analysis.clone_missing = False
        settings.analysis.use_repowise = False

        bundle = analyze(
            target="https://github.com/acme/company-app",
            priors=[
                "git@github.com:someone/earlier-vision.git",
                "https://gitlab.com/someone/rust-tool",
            ],
            settings=settings,
        )
        assert bundle.target.name == "company-app"
        assert {p.name for p in bundle.prior_works} == {"earlier-vision", "rust-tool"}
        assert all(p.ref.source == "local-discovery" for p in bundle.prior_works)
        assert bundle.overlaps[0].repository == "earlier-vision"  # sorted by score
        assert bundle.digest(bundle.to_dict()).startswith("sha256:")

    def test_digest_is_stable_across_runs(
        self, target_repo: Path, related_prior_repo: Path, tmp_path: Path
    ) -> None:
        settings = Settings.load(cwd=tmp_path, env={})
        settings.analysis.search_roots = [str(target_repo.parent)]
        settings.analysis.search_depth = 1
        settings.analysis.clone_missing = False
        settings.analysis.use_repowise = False
        kwargs = {
            "target": "https://github.com/acme/company-app",
            "priors": ["https://github.com/someone/earlier-vision"],
            "settings": settings,
        }
        first = analyze(**kwargs).to_dict()["digest"]
        second = analyze(**kwargs).to_dict()["digest"]
        assert first == second

    def test_target_is_not_analyzed_twice_as_a_prior(
        self, target_repo: Path, tmp_path: Path
    ) -> None:
        settings = Settings.load(cwd=tmp_path, env={})
        settings.analysis.search_roots = [str(target_repo.parent)]
        settings.analysis.search_depth = 1
        settings.analysis.clone_missing = False
        settings.analysis.use_repowise = False
        bundle = analyze(
            target=str(target_repo),
            priors=[str(target_repo)],
            settings=settings,
        )
        assert bundle.prior_works == []
        assert any("already-analyzed" in note for note in bundle.warnings)

    def test_unresolvable_prior_is_skipped_not_fatal(
        self, target_repo: Path, tmp_path: Path
    ) -> None:
        settings = Settings.load(cwd=tmp_path, env={})
        settings.analysis.search_roots = [str(target_repo.parent)]
        settings.analysis.search_depth = 1
        settings.analysis.clone_missing = False
        settings.analysis.use_repowise = False
        bundle = analyze(
            target=str(target_repo),
            priors=["https://github.com/nobody/does-not-exist"],
            settings=settings,
        )
        assert bundle.prior_works == []
        assert any("skipped" in note for note in bundle.warnings)
