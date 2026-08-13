"""Shared fixtures.

Tests build real git repositories in ``tmp_path`` rather than mocking git: the
deterministic layer's whole value is that it reads what is actually on disk, so
mocking the filesystem would test nothing that matters.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from piia.analysis.compare import compare, summarize
from piia.analysis.models import (
    AnalysisBundle,
    GitFacts,
    LanguageStat,
    RepoAnalysis,
    RepoRef,
    TechInventory,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the developer's own environment leaking into assertions."""
    for key in list(os.environ):
        if key.startswith(("LLM_", "PIIA_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PIIA_FROZEN_TIME", "2026-01-01T00:00:00Z")
    monkeypatch.setenv("PIIA_REQUEST_ID", "req_test0000000000")
    monkeypatch.setenv("NO_COLOR", "1")


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "Test Author"),
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test Author",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


def make_repo(
    root: Path,
    name: str,
    *,
    files: dict[str, str],
    remote: str | None = None,
    author: str = "Test Author",
    date: str = "2024-01-01T00:00:00+00:00",
) -> Path:
    """Create a real git repository with one commit."""
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": author,
            "GIT_AUTHOR_EMAIL": f"{author.split()[0].lower()}@example.com",
            "GIT_COMMITTER_NAME": author,
            "GIT_COMMITTER_EMAIL": "committer@example.com",
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
        },
    )
    if remote:
        git(repo, "remote", "add", "origin", remote)
    return repo


@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    """An AI service: FastAPI + PyTorch + Postgres, first commit 2025."""
    return make_repo(
        tmp_path / "checkouts",
        "company-app",
        files={
            "requirements.txt": "fastapi==0.110.0\nuvicorn\ntorch\ntransformers\npsycopg2-binary\nredis\npytest\n",
            "Dockerfile": "FROM python:3.11\n",
            "src/main.py": "print('service')\n" * 40,
            "src/model.py": "import torch\n" * 30,
            "README.md": "# Company App\n\nAn inference service for document understanding.\n",
            "LICENSE": "MIT License\n\nPermission is hereby granted, free of charge\n",
            ".github/workflows/ci.yml": "name: ci\n",
        },
        remote="https://github.com/acme/company-app.git",
        date="2025-06-01T00:00:00+00:00",
    )


@pytest.fixture
def related_prior_repo(tmp_path: Path) -> Path:
    """A prior work sharing the target's AI stack, first commit 2023."""
    return make_repo(
        tmp_path / "checkouts",
        "earlier-vision",
        files={
            "requirements.txt": "fastapi\ntorch\ntransformers\nopencv-python\nredis\n",
            "Dockerfile": "FROM python:3.11\n",
            "vision/detect.py": "import torch\n" * 25,
            "README.md": "# Earlier Vision\n\nObject detection research from 2023.\n",
            "LICENSE": "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3\n",
        },
        remote="git@github.com:someone/earlier-vision.git",
        date="2023-02-01T00:00:00+00:00",
    )


@pytest.fixture
def unrelated_prior_repo(tmp_path: Path) -> Path:
    """A prior work with nothing in common: a Rust CLI."""
    return make_repo(
        tmp_path / "checkouts",
        "rust-tool",
        files={
            "Cargo.toml": '[package]\nname = "rust-tool"\n\n[dependencies]\nclap = "4"\n',
            "src/main.rs": "fn main() {}\n" * 20,
            "README.md": "# Rust Tool\n\nA file renamer.\n",
        },
        remote="https://gitlab.com/someone/rust-tool.git",
        date="2022-05-01T00:00:00+00:00",
    )


def _analysis(
    name: str,
    *,
    role: str = "prior",
    frameworks: list[str] | None = None,
    ml_ai: list[str] | None = None,
    deps: list[str] | None = None,
    first: str = "2023-01-01",
    license_id: str | None = "MIT",
    contributors: list[str] | None = None,
) -> RepoAnalysis:
    from piia.analysis.models import Dependency

    tech = TechInventory(
        primary_language="Python",
        languages=[LanguageStat(name="Python", files=10, bytes=10000, share=1.0)],
        frameworks=frameworks or [],
        ml_ai=ml_ai or [],
        dependencies=[Dependency(name=d, ecosystem="pypi") for d in (deps or [])],
    )
    return RepoAnalysis(
        ref=RepoRef(
            name=name,
            url=f"https://github.com/acme/{name}",
            owner="acme",
            path=f"/tmp/{name}",
            source="local-discovery",
            role=role,
        ),
        git=GitFacts(
            commit="a" * 40,
            first_commit_date=first,
            last_commit_date="2025-01-01",
            commit_count=42,
            contributors=contributors or ["Test Author"],
        ),
        technology=tech,
        metrics={"analyzed_files": 10, "total_files": 12},
        license=license_id,
        description=f"{name} description",
    )


@pytest.fixture
def bundle() -> AnalysisBundle:
    """A small in-memory bundle: one target, two priors, one of each band."""
    target = _analysis(
        "company-app",
        role="target",
        frameworks=["FastAPI"],
        ml_ai=["PyTorch", "Hugging Face Transformers"],
        deps=["fastapi", "torch", "transformers"],
        first="2025-06-01",
    )
    priors = [
        _analysis(
            "earlier-vision",
            frameworks=["FastAPI"],
            ml_ai=["PyTorch", "Hugging Face Transformers", "OpenCV"],
            deps=["fastapi", "torch", "transformers", "opencv-python"],
            first="2023-02-01",
            license_id="AGPL-3.0",
            contributors=["Test Author", "Second Author"],
        ),
        _analysis("rust-tool", frameworks=[], ml_ai=[], deps=["clap"], first="2022-05-01",
                  license_id=None),
    ]
    overlaps = [compare(target, p) for p in priors]
    return AnalysisBundle(
        target=target,
        prior_works=priors,
        overlaps=overlaps,
        aggregate=summarize(target, priors, overlaps),
        tool_versions={"piia": "0.1.0", "git": "2.34.1", "repowise": None},
        generated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def corporate_document(tmp_path: Path) -> Path:
    path = tmp_path / "founders-agreement.md"
    path.write_text(
        """Co-Founders' Agreement

Draft

March 3, 2025

Acme Robotics, Inc.

as Company

and

Ada Lovelace

as Co-Founder and Chief Technology Officer

and

Grace Hopper

as Co-Founder and Chief Executive Officer

**4\\. Intellectual Property Rights**

4.1 Assignment: Each Co-Founder assigns to the Company all inventions created
in the course of their engagement, subject to Section 4.2 below. This clause is
intentionally long enough to be recognised as a section body by the parser.

4.2 Prior Inventions: Each Co-Founder represents that there are no prior
inventions which belong to such Co-Founder and relate to the Company's business
which are not assigned hereunder.

**7\\. Confidentiality and Non-Disclosure**

7.1 Each Co-Founder shall keep confidential all non-public information of the
Company, and this body is likewise long enough to be captured as a section
rather than skipped as a table-of-contents entry.

**18\\. Governing Law and Jurisdiction**

18.1 Governing Law: This Agreement shall be governed by and construed in
accordance with the laws of the State of Delaware, without giving effect to any
choice of law provisions.
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def openai_reply() -> object:
    """Factory for a fake OpenAI-shaped HTTP transport."""
    import httpx

    def factory(payloads: list[str], *, status: int = 200) -> httpx.MockTransport:
        remaining = list(payloads)

        def handler(request: httpx.Request) -> httpx.Response:
            body = remaining.pop(0) if remaining else (payloads[-1] if payloads else "")
            return httpx.Response(
                status,
                json={
                    "model": "test-model",
                    "choices": [{"message": {"role": "assistant", "content": body},
                                 "finish_reason": "stop"}],
                    "usage": {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5},
                },
                request=request,
            )

        return httpx.MockTransport(handler)

    return factory


@pytest.fixture
def json_reply() -> object:
    """Helper to serialise a dict as a model reply body."""

    def factory(payload: dict) -> str:
        return json.dumps(payload)

    return factory
