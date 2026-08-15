"""Entrypoint for the Docker-based GitHub Action.

The action deliberately runs the scanner on the customer's runner and uploads
nothing.  A managed service can consume the emitted digest later, but the free
action remains useful and privacy-preserving on its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

BAND_RANK = {"none": 0, "low": 1, "moderate": 2, "high": 3}
THRESHOLDS = {"never", "low", "moderate", "high"}
POLICY_EXIT_CODE = 10

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def parse_priors(raw: str) -> list[str]:
    """Parse newline- or comma-separated prior-work references, preserving order."""
    values: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        value = line.strip()
        if value and value not in values:
            values.append(value)
    return values


def as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def evaluate_gate(overlaps: list[dict[str, Any]], threshold: str) -> tuple[bool, str]:
    """Return ``(passed, highest_band)`` for a relatedness policy."""
    if threshold not in THRESHOLDS:
        raise ValueError(f"fail_on_relatedness must be one of: {', '.join(sorted(THRESHOLDS))}")
    highest = max(
        (str(item.get("relatedness", "none")).lower() for item in overlaps),
        key=lambda band: BAND_RANK.get(band, 0),
        default="none",
    )
    if threshold == "never":
        return True, highest
    return BAND_RANK.get(highest, 0) < BAND_RANK[threshold], highest


def _append_key_value(path: str | None, key: str, value: str) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(f"{key}={value}\n")


def _summary(data: dict[str, Any], *, passed: bool, highest: str, threshold: str) -> str:
    target = data.get("target", {})
    digest = data.get("digest", "")
    rows = []
    for item in data.get("overlaps", []):
        repository = str(item.get("repository", "unknown")).replace("|", "\\|")
        score = float(item.get("overlap_score", 0))
        band = str(item.get("relatedness", "none"))
        rows.append(f"| `{repository}` | {score:.3f} | {band} |")
    table = "\n".join(rows) if rows else "| _No prior works resolved_ | 0.000 | none |"
    verdict = "PASS" if passed else "FAIL"
    return f"""## PIIA prior-work gate: {verdict}

- Target: `{target.get("name", "unknown")}`
- Analysis digest: `{digest}`
- Highest relatedness: **{highest}**
- Failure threshold: **{threshold}**

| Prior work | Score | Relatedness |
| :--- | ---: | :--- |
{table}

> This is technical evidence for counsel review, not legal advice.
"""


def execute(
    environ: Mapping[str, str] | None = None,
    *,
    command_runner: CommandRunner = subprocess.run,
) -> int:
    """Run the analysis and emit GitHub Action outputs."""
    env = dict(os.environ if environ is None else environ)
    target = env.get("INPUT_TARGET", ".").strip() or "."
    priors = parse_priors(env.get("INPUT_PRIORS", ""))
    priors_file = env.get("INPUT_PRIORS_FILE", "").strip()
    threshold = env.get("INPUT_FAIL_ON_RELATEDNESS", "high").strip().lower() or "high"
    report_path = Path(env.get("INPUT_REPORT_PATH", "piia-analysis.json"))

    if not priors and not priors_file:
        print("PIIA Action: provide INPUT_PRIORS or INPUT_PRIORS_FILE.", file=sys.stderr)
        return 2
    if threshold not in THRESHOLDS:
        print(
            "PIIA Action: fail_on_relatedness must be one of: " + ", ".join(sorted(THRESHOLDS)),
            file=sys.stderr,
        )
        return 2

    argv = [env.get("PIIA_ACTION_BINARY", "piia"), "analyze", "--target", target]
    for prior in priors:
        argv.extend(["--prior", prior])
    if priors_file:
        argv.extend(["--priors-file", priors_file])
    argv.append("--repowise" if as_bool(env.get("INPUT_USE_REPOWISE")) else "--no-repowise")
    argv.append(
        "--clone-missing"
        if as_bool(env.get("INPUT_CLONE_MISSING"), default=True)
        else "--no-clone-missing"
    )
    argv.extend(["--json", "--quiet"])

    completed = command_runner(argv, text=True, capture_output=True, check=False)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        if completed.stdout:
            sys.stderr.write(completed.stdout)
        return completed.returncode

    try:
        envelope = json.loads(completed.stdout)
        data = envelope["data"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"PIIA Action: scanner returned an invalid JSON envelope: {exc}", file=sys.stderr)
        return 1

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")

    passed, highest = evaluate_gate(data.get("overlaps", []), threshold)
    digest = str(data.get("digest", ""))
    _append_key_value(env.get("GITHUB_OUTPUT"), "analysis_digest", digest)
    _append_key_value(env.get("GITHUB_OUTPUT"), "highest_relatedness", highest)
    _append_key_value(env.get("GITHUB_OUTPUT"), "gate_passed", str(passed).lower())
    _append_key_value(env.get("GITHUB_OUTPUT"), "report_path", str(report_path))

    summary = _summary(data, passed=passed, highest=highest, threshold=threshold)
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    else:
        print(summary)

    if not passed:
        print(
            f"PIIA Action: policy failed; {highest} relatedness meets the {threshold} threshold.",
            file=sys.stderr,
        )
        return POLICY_EXIT_CODE
    return 0


def main() -> None:
    raise SystemExit(execute())


if __name__ == "__main__":
    main()
