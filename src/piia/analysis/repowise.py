"""Optional ``repowise`` integration.

`repowise <https://github.com/repowise-dev/repowise>`_ is a codebase
intelligence tool (also AGPL-3.0) that indexes a repository into a dependency
graph, health model and architecture model. When it is installed we use it to
enrich the built-in scan with architectural facts -- layers, hotspots, health
KPIs, dead code -- that a file walk cannot see.

Two rules govern this module:

* **Keyless.** Only ``repowise`` commands that need no model are ever run
  (``init --no-prose``, ``export``, ``health``, ``dead-code``). Users are not
  charged for tokens by a tool that is supposed to be deterministic.
* **Never fatal.** Every failure degrades to a warning. ``repowise`` missing,
  timing out, or changing its JSON shape must not fail a PIIA run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

RepowiseResult = dict[str, Any]

#: Passed to every invocation: keep repowise out of the user's global editor
#: config and out of any telemetry, since we are calling it as a library.
SAFE_ENV = {
    "REPOWISE_SKIP_EDITOR_SETUP": "1",
    "DO_NOT_TRACK": "1",
    "NO_COLOR": "1",
}


def available(binary: str = "repowise") -> bool:
    return shutil.which(binary) is not None


def version(binary: str = "repowise") -> str | None:
    if not available(binary):
        return None
    proc = _run([binary, "--version"], timeout=60)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip().split()[-1] or None


def is_indexed(path: Path) -> bool:
    return (Path(path) / ".repowise").is_dir()


def analyze(
    path: Path,
    *,
    binary: str = "repowise",
    timeout: float = 900.0,
    index: bool = True,
    fast: bool = True,
) -> RepowiseResult:
    """Run the keyless repowise commands over ``path``.

    Returns a result dict that always has ``available``, ``commands`` and
    ``degraded`` keys, so callers can report what happened without special-casing.
    """
    result: RepowiseResult = {
        "available": available(binary),
        "version": None,
        "indexed": False,
        "commands": [],
        "degraded": [],
        "health": None,
        "architecture": None,
        "dead_code": None,
    }
    if not result["available"]:
        result["degraded"].append("repowise is not installed")
        return result

    result["version"] = version(binary)
    repo = Path(path)

    if not is_indexed(repo):
        if not index:
            result["degraded"].append("repository is not indexed and indexing was disabled")
            return result
        args = [binary, "init", str(repo), "--no-prose", "--yes", "--no-editor-setup"]
        if fast:
            args += ["--mode", "fast"]
        proc = _run(args, timeout=timeout, cwd=repo)
        result["commands"].append(" ".join(args))
        if proc is None or proc.returncode != 0:
            detail = (proc.stderr.strip().splitlines()[-1] if proc and proc.stderr else "timeout")
            result["degraded"].append(f"repowise init failed: {detail[:300]}")
            return result
    result["indexed"] = is_indexed(repo)
    if not result["indexed"]:
        result["degraded"].append("repowise init reported success but wrote no index")
        return result

    health = _json_command([binary, "health", str(repo), "--format", "json"], repo, timeout)
    if health is None:
        result["degraded"].append("repowise health produced no JSON")
    else:
        result["health"] = _trim_health(health)
        result["commands"].append("repowise health --format json")

    architecture = _export_json(binary, repo, timeout)
    if architecture is None:
        result["degraded"].append("repowise export produced no JSON")
    else:
        result["architecture"] = _trim_architecture(architecture)
        result["commands"].append("repowise export --format json --full")

    dead = _json_command([binary, "dead-code", str(repo), "--format", "json"], repo, timeout)
    if dead is not None:
        result["dead_code"] = _trim_dead_code(dead)
        result["commands"].append("repowise dead-code --format json")

    return result


# ---------------------------------------------------------------------------
# Process plumbing
# ---------------------------------------------------------------------------
def _run(
    argv: list[str], *, timeout: float, cwd: Path | None = None
) -> subprocess.CompletedProcess[str] | None:
    env = {**os.environ, **SAFE_ENV}
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def _json_command(argv: list[str], cwd: Path, timeout: float) -> dict[str, Any] | None:
    proc = _run(argv, timeout=timeout, cwd=cwd)
    if proc is None or proc.returncode != 0:
        return None
    return _first_json_object(proc.stdout)


def _export_json(binary: str, repo: Path, timeout: float) -> dict[str, Any] | None:
    """``repowise export`` writes files; find and read the JSON it produced."""
    out_dir = repo / ".repowise" / "export-piia"
    argv = [
        binary, "export", str(repo), "--format", "json", "--full", "--output", str(out_dir)
    ]
    proc = _run(argv, timeout=timeout, cwd=repo)
    if proc is None:
        return None
    inline = _first_json_object(proc.stdout)
    if inline:
        return inline
    if not out_dir.is_dir():
        return None
    candidates = sorted(
        (p for p in out_dir.rglob("*.json") if p.is_file()),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first balanced top-level JSON object from mixed output."""
    if not text:
        return None
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(stripped)):
            char = stripped[idx]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(stripped[start : idx + 1])
                    except json.JSONDecodeError:
                        break
                    return parsed if isinstance(parsed, dict) else None
        start = stripped.find("{", start + 1)
    return None


# ---------------------------------------------------------------------------
# Trimming. repowise payloads can be megabytes; a PIIA needs the summary, and
# the drafting prompt has a context budget. Unknown keys are dropped rather
# than guessed at, so a repowise schema change degrades to fewer facts.
# ---------------------------------------------------------------------------
def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: source[k] for k in keys if k in source}


def _trim_health(payload: dict[str, Any]) -> dict[str, Any]:
    out = _pick(payload, "score", "grade", "kpis", "summary", "generated_at")
    files = payload.get("files") or payload.get("lowest_scoring") or []
    if isinstance(files, list):
        out["lowest_scoring_files"] = [
            _pick(f, "path", "score", "grade") for f in files[:10] if isinstance(f, dict)
        ]
    return out


def _trim_architecture(payload: dict[str, Any]) -> dict[str, Any]:
    out = _pick(
        payload,
        "repo",
        "repository",
        "name",
        "overview",
        "summary",
        "generated_at",
        "provenance",
        "stats",
        "metrics",
    )
    for key in ("layers", "subsystems", "components", "entry_points", "hotspots", "decisions"):
        value = payload.get(key)
        if isinstance(value, list):
            out[key] = [
                _pick(v, "id", "name", "title", "path", "kind", "summary", "score", "files")
                if isinstance(v, dict)
                else v
                for v in value[:40]
            ]
        elif isinstance(value, dict):
            out[key] = {k: value[k] for k in list(value)[:40]}
    externals = payload.get("externals") or payload.get("dependencies")
    if isinstance(externals, list):
        out["externals"] = [
            e if isinstance(e, str) else _pick(e, "name", "version", "ecosystem")
            for e in externals[:120]
        ]
    return out


def _trim_dead_code(payload: dict[str, Any]) -> dict[str, Any]:
    out = _pick(payload, "summary", "total", "counts")
    findings = payload.get("findings") or payload.get("items") or []
    if isinstance(findings, list):
        out["finding_count"] = len(findings)
        out["findings"] = [
            _pick(f, "path", "symbol", "kind", "line") for f in findings[:20] if isinstance(f, dict)
        ]
    return out
