# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The CLI output contract is versioned separately
(`piia.version.ENVELOPE_VERSION`); pin against that if you consume the JSON.

## [Unreleased]

## [0.1.0] — 2026-08-13

First release. Output contract `1.0.0`.

### Deterministic analysis

- Repository resolution that **prefers an existing local checkout** over
  cloning, matched by git remote URL first and directory name second — so a
  checkout whose directory was renamed is still found. Falls back to a clone into
  the workspace, and records which of the three happened per repository.
- Built-in scanner requiring nothing but a filesystem: languages by extension and
  bytes, dependency manifests across 12 ecosystems (pip, poetry, uv, conda, npm,
  pnpm, yarn, bun, go, cargo, bundler, composer, maven, gradle, sbt, swiftpm,
  pub, mix, cocoapods, deno), infrastructure, CI, cloud, datastores, protocols,
  frontend, testing and AI/ML signatures. Vendored and cache directories are
  skipped so a bundled `llama.cpp` cannot make a Python service look like C++.
- Git facts: commit, branch, first and last commit dates, commit count,
  contributors, tags, shallow-clone detection.
- SPDX license detection from `LICENSE`, `pyproject.toml` and `package.json`.
- Relatedness scoring — `0.65 × weighted technology coverage + 0.35 × shared
  declared dependencies` — with per-category weights, a discount for ubiquitous
  technologies, temporal precedence checking, and a written rationale for every
  score. Bands: `high`/`moderate`/`low`/`none`.
- A stable content `digest` over the analysis: the same commits reproduce the
  same digest, which is printed in the generated document.
- Optional [repowise](https://github.com/repowise-dev/repowise) enrichment, using
  only its keyless commands, degrading to a warning on any failure.

### Model drafting

- Provider-agnostic client with two wire formats — `openai` (OpenAI, Azure,
  Gemini's compatible endpoint, Ollama, vLLM, llama.cpp, LM Studio, LiteLLM,
  OpenRouter, Together, Groq, DeepSeek, Mistral) and `anthropic` (native Messages
  API). No provider SDKs.
- Structured output attempted via `response_format`, silently abandoned if the
  endpoint rejects it; JSON extracted from fenced or prose-wrapped replies; two
  repair turns quoting the specific problem back to the model.
- Retries with jittered backoff on `408/409/425/429/5xx`, honouring `Retry-After`.
- Batched drafting (`--batch-size`, default 4) so small-context models cope with
  long prior-work lists.
- **Every task has a deterministic fallback.** `--no-llm`, an unreachable
  endpoint, or a misbehaving model still produces a complete document, with what
  degraded recorded in `provenance.degraded`.
- Deterministic facts are authoritative: a model cannot overwrite a commit, date,
  license or score, and a technology it names that the scan did not find is
  quarantined as unverified rather than accepted.

### Documents

- A fixed 10-section clause skeleton with numeric cross-references that resolve,
  plus Exhibit A (Prior Inventions), Exhibit B (reproducible evidence) and
  Exhibit C (counsel review checklist).
- Non-solicitation marked *intentionally omitted* by default, without
  renumbering; `--include-non-solicitation` restores it. No non-compete.
- Deterministic extraction of company, governing law, effective date, parties and
  relevant sections from an existing corporate agreement (`--corporate-document`).
- Output as Markdown, JSON, self-contained HTML, DOCX (`[docx]`) and PDF
  (`[pdf]`), plus a standalone Exhibit A and the analysis on its own.

### CLI

- `init`, `doctor`, `analyze`, `generate`, `render`, `config show`, `schema`,
  `version`.
- One universal JSON envelope; human Markdown and JSON render from the same
  payload. Format auto-detected from TTY, overridable with `-o`/`--json`/
  `PIIA_OUTPUT_FORMAT`. `jsonl` for streaming.
- `--schema` on every command, and `piia schema --all`.
- JSON strictly on `stdout`, progress strictly on `stderr`, no ANSI inside JSON.
- Stable, specific exit codes, and a `remediation` string on every error.
- Configuration precedence: CLI flags → environment → `.env` → `piia.yaml` →
  defaults, with `piia config show` reporting the source of each value and
  redacting secrets.

[Unreleased]: https://github.com/PIIA-CLI/PIIA-CLI/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/PIIA-CLI/PIIA-CLI/releases/tag/v0.1.0
