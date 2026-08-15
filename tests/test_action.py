"""GitHub Action contract: local scan, stable outputs, and policy exit status."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from piia.automation.action import POLICY_EXIT_CODE, evaluate_gate, execute, parse_priors


def completed(payload: dict, *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["piia"], returncode=returncode, stdout=json.dumps(payload), stderr=""
    )


def analysis_envelope(band: str = "moderate") -> dict:
    return {
        "status": "success",
        "data": {
            "digest": "sha256:abc123",
            "target": {"name": "company-app"},
            "overlaps": [
                {
                    "repository": "prior-app",
                    "overlap_score": 0.42,
                    "relatedness": band,
                }
            ],
        },
    }


def test_parse_priors_deduplicates_newline_and_comma_values() -> None:
    assert parse_priors("one\ntwo, one\n") == ["one", "two"]


def test_gate_uses_relatedness_order_not_rounded_score() -> None:
    overlaps = analysis_envelope("moderate")["data"]["overlaps"]
    assert evaluate_gate(overlaps, "high") == (True, "moderate")
    assert evaluate_gate(overlaps, "moderate") == (False, "moderate")
    assert evaluate_gate(overlaps, "never") == (True, "moderate")


def test_action_writes_report_summary_and_outputs(tmp_path: Path) -> None:
    report = tmp_path / "result.json"
    output = tmp_path / "github-output"
    summary = tmp_path / "summary.md"
    seen: list[str] = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.extend(argv)
        return completed(analysis_envelope("moderate"))

    code = execute(
        {
            "INPUT_TARGET": ".",
            "INPUT_PRIORS": "../prior-a\n../prior-b",
            "INPUT_FAIL_ON_RELATEDNESS": "high",
            "INPUT_USE_REPOWISE": "false",
            "INPUT_CLONE_MISSING": "false",
            "INPUT_REPORT_PATH": str(report),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        command_runner=runner,
    )

    assert code == 0
    assert seen.count("--prior") == 2
    assert "--no-repowise" in seen
    assert "--no-clone-missing" in seen
    assert json.loads(report.read_text())["data"]["digest"] == "sha256:abc123"
    assert "analysis_digest=sha256:abc123" in output.read_text()
    assert "gate_passed=true" in output.read_text()
    assert "PIIA prior-work gate: PASS" in summary.read_text()


def test_action_returns_distinct_policy_exit_after_writing_evidence(tmp_path: Path) -> None:
    report = tmp_path / "result.json"

    def runner(_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed(analysis_envelope("high"))

    code = execute(
        {
            "INPUT_PRIORS": "../prior",
            "INPUT_FAIL_ON_RELATEDNESS": "high",
            "INPUT_REPORT_PATH": str(report),
        },
        command_runner=runner,
    )

    assert code == POLICY_EXIT_CODE
    assert report.is_file()


def test_action_rejects_missing_prior_inputs_without_running() -> None:
    called = False

    def runner(_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return completed(analysis_envelope())

    assert execute({}, command_runner=runner) == 2
    assert called is False
