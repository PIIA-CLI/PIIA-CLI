"""Renderers.

One envelope in, one of four renderings out. JSON goes to ``stdout`` and
nothing else ever does in JSON mode -- progress and logs are on ``stderr`` -- so
`jq`, `piia ... | llm`, and agent tool pipes never see a stray byte.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TextIO

from piia.envelope import Envelope
from piia.output.format import OutputFormat, supports_color

# --------------------------------------------------------------------------
# ANSI helpers. Human mode only -- see supports_color().
# --------------------------------------------------------------------------
_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}

GLYPHS = {
    "ok": ("✔", "green"),
    "success": ("✔", "green"),
    "pass": ("✔", "green"),
    "warn": ("⚠", "yellow"),
    "partial": ("⚠", "yellow"),
    "degraded": ("⚠", "yellow"),
    "skipped": ("–", "dim"),
    "error": ("✖", "red"),
    "fail": ("✖", "red"),
    "missing": ("✖", "red"),
    # Relatedness bands, from compare.py.
    "high": ("◆", "red"),
    "moderate": ("◈", "yellow"),
    "low": ("◇", "cyan"),
    "none": ("·", "dim"),
    "unknown": ("?", "dim"),
}


class Style:
    """Tiny colouriser that degrades to plain text when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *names: str) -> str:
        if not self.enabled or not names:
            return text
        prefix = "".join(_ANSI[n] for n in names if n in _ANSI)
        return f"{prefix}{text}{_ANSI['reset']}" if prefix else text

    def status(self, state: str) -> str:
        glyph, color = GLYPHS.get(str(state).lower(), ("•", "cyan"))
        label = str(state).replace("_", " ").title()
        return self(f"{glyph} {label}", color)


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    """Render a GitHub-flavoured Markdown table (left-aligned)."""
    body = [[("" if c is None else str(c)) for c in row] for row in rows]
    if not body:
        return []
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(":---" for _ in headers) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return lines


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(value)


# --------------------------------------------------------------------------
# Per-command human views. Each returns Markdown lines for envelope["data"].
# A command without a view falls back to _generic_view, so adding a command
# never breaks human mode.
# --------------------------------------------------------------------------
HumanView = Callable[[dict[str, Any], Style], list[str]]
HUMAN_VIEWS: dict[str, HumanView] = {}


def human_view(command: str) -> Callable[[HumanView], HumanView]:
    def decorate(fn: HumanView) -> HumanView:
        HUMAN_VIEWS[command] = fn
        return fn

    return decorate


def _generic_view(data: dict[str, Any], s: Style) -> list[str]:
    lines: list[str] = []
    for key, value in data.items():
        label = key.replace("_", " ").title()
        if isinstance(value, dict):
            lines += [f"**{label}**", ""]
            lines += [f"- {k}: `{v}`" for k, v in value.items()]
            lines.append("")
        elif isinstance(value, list):
            lines += [f"**{label}** ({len(value)})", ""]
            lines += [f"- {json.dumps(v) if isinstance(v, dict | list) else v}" for v in value]
            lines.append("")
        else:
            lines.append(f"- **{label}**: {value}")
    return lines


@human_view("doctor")
def _doctor_view(data: dict[str, Any], s: Style) -> list[str]:
    lines = ["## Environment check", ""]
    rows = [
        (
            c.get("name", ""),
            s.status(c.get("status", "")),
            c.get("detail", ""),
        )
        for c in data.get("checks", [])
    ]
    lines += table(["Check", "Status", "Detail"], rows)
    lines.append("")
    summary = data.get("summary", {})
    lines.append(
        f"> {summary.get('passed', 0)} passed, {summary.get('warnings', 0)} warning(s), "
        f"{summary.get('failures', 0)} failure(s)."
    )
    remediations = [c for c in data.get("checks", []) if c.get("remediation")]
    if remediations:
        lines += ["", "### Suggested next steps", ""]
        for c in remediations:
            lines.append(f"- **{c['name']}** -- {c['remediation']}")
    return lines


@human_view("analyze")
def _analyze_view(data: dict[str, Any], s: Style) -> list[str]:
    target = data.get("target", {})
    priors = data.get("prior_works", [])
    overlaps = {o["repository"]: o for o in data.get("overlaps", [])}

    lines = [
        f"## Prior-work analysis for `{target.get('name', '?')}`",
        "",
        f"**Target work** -- {target.get('url', 'n/a')}",
        "",
    ]
    tech = target.get("technology", {})
    lines += [
        f"- Primary language: **{tech.get('primary_language') or 'unknown'}**",
        f"- Files analyzed: {target.get('metrics', {}).get('analyzed_files', 0)}",
        f"- Frameworks: {', '.join(tech.get('frameworks', [])) or 'none detected'}",
        "",
        f"### Prior works ({len(priors)})",
        "",
    ]

    rows = []
    for p in priors:
        name = p.get("name", "?")
        ov = overlaps.get(name, {})
        rows.append(
            (
                f"`{name}`",
                p.get("technology", {}).get("primary_language") or "-",
                _pct(ov.get("overlap_score", 0)),
                s.status(ov.get("relatedness", "unknown")),
                ov.get("recommendation", "-"),
            )
        )
    lines += table(
        ["Prior work", "Language", "Overlap", "Relatedness", "Recommendation"], rows
    )

    shared = data.get("aggregate", {}).get("shared_technologies", [])
    if shared:
        lines += ["", "### Technologies shared with the target work", ""]
        lines.append(", ".join(f"`{t}`" for t in shared[:40]))
    carve = data.get("aggregate", {}).get("carve_out_candidates", [])
    if carve:
        lines += [
            "",
            f"### Carve-out candidates ({len(carve)})",
            "",
            *[f"- `{c}`" for c in carve],
        ]
    lines += [
        "",
        "> Deterministic layer only -- no model was called. "
        "Run `piia generate` to draft the agreement, or `--json` for the full payload.",
    ]
    return lines


@human_view("generate")
def _generate_view(data: dict[str, Any], s: Style) -> list[str]:
    doc = data.get("document", {})
    lines = [
        f"## PIIA drafted for **{doc.get('signatory', {}).get('full_name', '?')}**",
        "",
        f"- Company: **{doc.get('company', {}).get('name', '?')}**",
        f"- Governing law: {doc.get('governing_law') or 'not specified'}",
        f"- Sections: {len(doc.get('sections', []))}",
        f"- Prior inventions carved out: **{len(doc.get('prior_inventions', []))}**",
        "",
    ]
    rows = [
        (
            f"`{pi.get('repository', {}).get('name', '?')}`",
            pi.get("title", ""),
            pi.get("ownership", "-"),
            pi.get("license") or "-",
            s.status(pi.get("relatedness", "unknown")),
        )
        for pi in doc.get("prior_inventions", [])
    ]
    if rows:
        lines += ["### Exhibit A -- Prior Inventions", ""]
        lines += table(["Repository", "Title", "Ownership", "License", "Relatedness"], rows)
        lines.append("")

    provenance = data.get("provenance", {})
    lines += [
        "### Provenance",
        "",
        f"- Deterministic analysis: `{provenance.get('analysis_digest', 'n/a')}`",
        f"- Model: `{provenance.get('model') or 'none (deterministic draft)'}`",
        f"- Endpoint: `{provenance.get('base_url') or 'n/a'}`",
        f"- Temperature: `{provenance.get('temperature', 'n/a')}`",
        "",
    ]
    artifacts = data.get("artifacts", [])
    if artifacts:
        lines += ["### Artifacts written", ""]
        lines += table(
            ["Kind", "Path", "Bytes"],
            [(a.get("kind"), f"`{a.get('path')}`", a.get("bytes")) for a in artifacts],
        )
        lines.append("")
    lines += [
        "> " + s("Not legal advice.", "bold") + " Generated drafts must be reviewed by "
        "qualified counsel in the relevant jurisdiction before signature.",
    ]
    return lines


@human_view("render")
def _render_view(data: dict[str, Any], s: Style) -> list[str]:
    lines = ["## Rendered", ""]
    lines += table(
        ["Kind", "Path", "Bytes"],
        [(a.get("kind"), f"`{a.get('path')}`", a.get("bytes")) for a in data.get("artifacts", [])],
    )
    return lines


@human_view("config show")
def _config_view(data: dict[str, Any], s: Style) -> list[str]:
    lines = ["## Effective configuration", ""]
    lines += table(
        ["Setting", "Value", "Source"],
        [
            (f"`{k}`", v.get("value"), v.get("source"))
            for k, v in data.get("settings", {}).items()
        ],
    )
    lines += ["", "> Secrets are redacted. Set values in `.env`, `piia.yaml`, or the environment."]
    return lines


@human_view("version")
def _version_view(data: dict[str, Any], s: Style) -> list[str]:
    return [
        f"piia {s(data.get('version', '?'), 'bold')}",
        "",
        f"- Output contract: `{data.get('envelope_version')}`",
        f"- Python: `{data.get('python')}`",
        f"- git: `{data.get('git') or 'not found'}`",
        f"- repowise: `{data.get('repowise') or 'not installed'}`",
        f"- License: `{data.get('license')}`",
    ]


def _render_human(env: Envelope, s: Style) -> str:
    body: list[str] = []
    if env.status == "error" and env.error:
        err = env.error
        body += [
            s(f"✖ {err.get('code', 'ERROR')}", "red", "bold"),
            "",
            err.get("message", ""),
        ]
        for d in err.get("details") or []:
            body.append(f"  - **{d.get('field', 'detail')}**: {d.get('issue', '')}")
        if err.get("remediation"):
            body += ["", "**Try this:**", "", "```bash", err["remediation"], "```"]
    else:
        view = HUMAN_VIEWS.get(env.command, _generic_view)
        body += view(env.data or {}, s)

    for w in env.warnings:
        body += ["", s(f"⚠ {w.code}: {w.message}", "yellow")]
        if w.remediation:
            body.append(f"  {s(w.remediation, 'dim')}")

    footer = s(
        f"request_id={env.request_id} duration={env.duration_ms}ms command={env.command}",
        "dim",
    )
    return "\n".join([*body, "", footer, ""])


def emit(
    env: Envelope,
    fmt: OutputFormat,
    *,
    stream: TextIO | None = None,
    color: bool | None = None,
) -> None:
    """Write ``env`` to ``stream`` in ``fmt``."""
    out = stream or sys.stdout
    if fmt is OutputFormat.HUMAN:
        enabled = supports_color(fmt, stream=out) if color is None else color
        out.write(_render_human(env, Style(enabled)))
    elif fmt is OutputFormat.JSON:
        out.write(json.dumps(env.to_dict(), separators=(",", ":"), default=str) + "\n")
    elif fmt is OutputFormat.JSON_PRETTY:
        out.write(json.dumps(env.to_dict(), indent=2, sort_keys=False, default=str) + "\n")
    elif fmt is OutputFormat.JSONL:
        payload = env.to_dict()
        records = _jsonl_records(payload)
        for record in records:
            out.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    out.flush()


def _jsonl_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Split an envelope into newline-delimited records.

    Emits one ``meta`` record carrying the envelope minus ``data``, then one
    record per top-level list in ``data`` (so a 40-repo analysis streams as 40
    rows a shell loop can consume), and finally a ``data`` record for scalars.
    """
    meta = {k: v for k, v in payload.items() if k != "data"}
    meta["record_type"] = "meta"
    records: list[dict[str, Any]] = [meta]
    data = payload.get("data") or {}
    scalars: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            for item in value:
                records.append({"record_type": key, **item})
        else:
            scalars[key] = value
    if scalars:
        records.append({"record_type": "data", **scalars})
    return records
