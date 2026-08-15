# piia-cli

**Build your PIIA's Prior Inventions exhibit from your actual code, not from memory.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/PIIA-CLI/PIIA-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/PIIA-CLI/PIIA-CLI/actions/workflows/ci.yml)
[![Model agnostic](https://img.shields.io/badge/models-OpenAI%20%7C%20Anthropic%20%7C%20Gemini%20%7C%20Ollama%20%7C%20vLLM-informational)](docs/LLM_PROVIDERS.md)

When you sign a **PIIA** (Proprietary Information and Inventions Agreement — also
called a CIIAA or an invention assignment agreement), you assign everything you
invent for the company to the company. **Exhibit A** is the one place you carve
out what you built *before*.

Almost nobody fills it in properly. It gets written from memory, days after the
work it describes, and it is the exhibit that matters years later when a
diligence lawyer asks which side of the line a repository falls on.

`piia` builds it from evidence:

1. **Deterministic analysis.** It inventories your prior repositories and the
   company's repository from what is actually on disk — languages, manifests,
   declared dependencies, infrastructure, git history — and scores how closely
   each prior work relates to the company's field of business.
2. **Model drafting.** It hands that evidence to a model *you* choose and asks
   for the exhibit entries and the descriptive clauses. Nothing else.
3. **Documents out.** A complete agreement plus three exhibits, in Markdown,
   JSON, self-contained HTML, DOCX and PDF.

Step 1 always runs and is reproducible. Step 2 is optional: with `--no-llm`, or
if your endpoint is down, every field falls back to a deterministic template and
you still get a complete document.

> [!IMPORTANT]
> **This is not legal advice.** `piia` is a drafting aid. Every document it
> produces must be reviewed by qualified counsel in the relevant jurisdiction
> before it is signed. See [docs/LEGAL_DISCLAIMER.md](docs/LEGAL_DISCLAIMER.md).

---

## Install

```bash
pip install piia-cli                  # core
pip install "piia-cli[all]"           # + DOCX, PDF and pinned repowise enrichment
```

Requires Python 3.11+ and `git` on your `PATH`.

## Quickstart

```bash
piia init                             # writes .env, piia.yaml, priors.txt
$EDITOR .env                          # set LLM_BASE_URL and LLM_MODEL_NAME
$EDITOR priors.txt                    # list your prior repositories
piia doctor                           # check git, repowise and the endpoint

piia generate \
  --target . \
  --priors-file priors.txt \
  --signatory "Ada Lovelace" \
  --company "Acme, Inc." \
  --governing-law "the State of Delaware" \
  --format md --format json --format html --format docx
```

Artifacts land in `piia-out/<signatory>-<company>/`:

| File | What it is |
| :--- | :--- |
| `piia.md` | The agreement and all three exhibits, Markdown |
| `piia.json` | The same document as structured data |
| `piia.html` | One self-contained file — no CSS, fonts, JS or network |
| `piia.docx` | Word, for counsel who redline in Word |
| `piia.pdf` | Print-ready, for a signature packet |
| `exhibit-a-prior-inventions.md` | Exhibit A alone, for pasting into an agreement you already have |
| `analysis.json` | The deterministic evidence on its own |

Want to see the analysis before drafting anything? `piia analyze` calls no model:

```bash
piia analyze --target . --priors-file priors.txt
```

## Your prior repositories are probably already on disk

`piia` looks for an existing checkout before it clones anything. For each
repository you name it tries, in order:

1. **A path** you passed — used as-is.
2. **An existing checkout** under your search roots, matched by **git remote
   URL** first and by directory name second. A remote match works even when the
   directory has a different name, so listing the GitHub URL is enough.
3. **A clone** into `workspace/repos/<owner>/<name>`, only if the first two
   found nothing.

Search roots default to the working directory and its parent, which covers the
usual `~/src/<repo>` layout. Point them anywhere:

```bash
piia analyze -t . --priors-file priors.txt --search-root ~/src --search-root ~/work
piia analyze -t . -p ../old-project --no-clone-missing   # never touch the network
```

Which of the three happened is recorded per repository (`resolved_from` in
Exhibit B), because an auditor needs to know whether a claim came from your own
checkout or from a fresh clone.

## How relatedness is scored

The comparison answers the question a PIIA turns on: *does this earlier work
relate to the company's business?* The formula is deliberately simple, because
you may have to defend it years from now:

```
overlap_score = 0.65 × weighted_technology_coverage
              + 0.35 × shared_declared_dependencies
```

- **Weighted technology coverage** — how much of the *target's* technology
  surface the prior work already covers, weighted by category (a shared
  framework or AI/ML library counts far more than a shared language), with
  ubiquitous technologies discounted so "we both use NumPy" cannot carry a
  finding.
- **Shared declared dependencies** — the fraction of the target's third-party
  dependencies the prior work also declares. Concrete and hard to argue with.

| Band | Score | What it means for the exhibit |
| :--- | :--- | :--- |
| `high` | ≥ 0.55 | Disclose and carve out explicitly; the boundary must be in writing |
| `moderate` | ≥ 0.30 | Disclose; silence could later read as an implied assignment |
| `low` | ≥ 0.12 | Disclose for completeness |
| `none` | < 0.12 | Optional; listing it is still the safer default |

Every score ships with the rationale that produced it, plus whether the prior
work's first commit actually precedes the company's — the temporal half of a
Prior Invention. Nothing is claimed that isn't traceable to a named file, a
named dependency, or a git fact.

## Model configuration

Any OpenAI-compatible or Anthropic-compatible endpoint. No provider SDKs, one
HTTP dependency, so adding a provider is configuration, not code.

```bash
LLM_BASE_URL=http://localhost:11434/v1    # required
LLM_MODEL_NAME=qwen2.5-coder:14b          # required
LLM_API_KEY=                              # optional — many local servers need none
LLM_TEMPERATURE=0.1                       # default: low, for legal drafting
LLM_MAX_TOKENS=8192
LLM_TIMEOUT=180
LLM_MAX_RETRIES=3
LLM_API_STYLE=openai                      # or 'anthropic'
LLM_SYSTEM_PROMPT_FILE=./prompts/mine.md  # optional: replace the drafting prompt
LLM_EXTRA_HEADERS={"X-Org-Id":"acme"}     # optional: for gateways
```

Verified shapes for OpenAI, Anthropic, Gemini, Azure, Ollama, vLLM, LM Studio,
llama.cpp, OpenRouter, Together, Groq, DeepSeek, Mistral and LiteLLM are in
[**docs/LLM_PROVIDERS.md**](docs/LLM_PROVIDERS.md).

Precedence, highest first: **CLI flags → environment → `.env` → `piia.yaml` →
defaults.** `piia config show` prints every effective value and where it came
from, with secrets redacted.

## Built for AI coding agents

Claude Code, Gemini CLI, Codex and friends drive shells. So the CLI is a
contract, not a UI:

```bash
piia analyze --schema            # JSON Schema of this command's own output
piia schema --all                # every command's schema, one document
piia analyze -t . -p ../old --json | jq '.data.overlaps[] | select(.relatedness=="high")'
```

- **One envelope, two modes.** Every command computes one payload and renders it
  either as human Markdown or as a versioned JSON envelope. Same keys, same
  data — no second code path to drift.
- **Format auto-detection.** Human on a TTY, JSON when piped. `-o
  human|json|json-pretty|jsonl`, `--json` shorthand, `PIIA_OUTPUT_FORMAT`.
- **Clean streams.** JSON on `stdout` and nothing else, ever. Progress and
  warnings on `stderr`. No ANSI inside JSON.
- **Specific exit codes.** `2` usage/config, `3` repository, `4` git missing,
  `6` model, `7` document, `8` missing extra, `9` I/O, and `10` Action policy.
- **Every error tells you what to run next.** Errors carry a stable `code` and a
  `remediation` string, so a failed invocation is self-correcting.

See [**docs/AGENT_INTEGRATION.md**](docs/AGENT_INTEGRATION.md) and
[**docs/OUTPUT_CONTRACT.md**](docs/OUTPUT_CONTRACT.md).

## GitHub Action

Run the same deterministic comparison as a pull-request gate without uploading
source code:

```yaml
- uses: PIIA-CLI/PIIA-CLI@v1
  with:
    target: company
    priors: |
      prior-project-a
      prior-project-b
    fail_on_relatedness: high
```

The free Action writes a complete JSON evidence artifact and a GitHub step
summary. See [**docs/GITHUB_AUTOMATION.md**](docs/GITHUB_AUTOMATION.md) for
private-repository checkout, policy, permissions, and the separate GitHub App
boundary for managed commercial features.

## Optional: repowise enrichment

[repowise](https://github.com/repowise-dev/repowise) (also AGPL-3.0) indexes a
repository into a dependency graph, health model and architecture model. When it
is installed, `piia` enriches each scan with architectural facts a file walk
cannot see — layers, subsystems, hotspots, dead code.

```bash
pip install "piia-cli[repowise]"
piia generate ... --repowise         # on by default when installed
piia analyze  ... --no-repowise      # or skip it
```

Automated environments use the tested `ithllc/repowise` fork snapshot rather
than a moving release. The provider contract, exact revision, side-effect
controls, and update procedure are documented in
[**docs/CODE_INTELLIGENCE.md**](docs/CODE_INTELLIGENCE.md).

Only **keyless** repowise commands are ever run (`init --no-prose`, `export`,
`health`, `dead-code`) — a deterministic layer should not spend your tokens. And
it is never fatal: if repowise is missing, times out, or changes its JSON shape,
the run degrades to a warning and the built-in scanner carries on.

## What's in the generated document

| Part | Written by |
| :--- | :--- |
| Sections 1–10 (definitions, confidentiality, assignment, prior inventions, third-party/open-source, no conflicts, further assurances, return of property, non-solicitation, general) | **Fixed template.** Identical every run, so counsel can review it once and trust it thereafter |
| Company Business definition, technical field, executive summary | **Model**, from the target repository's evidence |
| Exhibit A entries — title, description, relationship to the business, carve-out language | **Model**, from each repository's evidence |
| Exhibit A facts — commit, dates, license, contributors, relatedness | **Deterministic.** The model cannot overwrite these |
| Exhibit B — the reproducible evidence and analysis digest | **Deterministic** |
| Exhibit C — the counsel review checklist | **Both**, each item labelled by source |

Two properties worth knowing:

- **A technology the model names that the scan did not find is quarantined**,
  listed separately as unverified rather than silently accepted.
- **The analysis digest is stable.** Re-run at the same commits and you get the
  same digest, which is printed in the document — so an exhibit can be audited
  years later.

Non-solicitation is marked *intentionally omitted* by default (it is void or
narrowly limited in many jurisdictions) without renumbering anything. Add it
with `--include-non-solicitation`.

## Commands

```
piia init        write .env, piia.yaml and priors.txt templates
piia doctor      check git, repowise, configuration and the model endpoint
piia analyze     compare prior works against a target work — no model call
piia generate    analyze, then draft the PIIA and its exhibits
piia render      re-render a saved piia.json into other formats
piia config show effective settings and where each came from
piia schema      JSON Schemas for every command's output
piia version     version and environment details
```

`piia <command> --help` for every flag. `piia generate --dry-run` reports the
plan — what resolved where, how many model calls, what will be written — without
drafting or writing anything.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

The AGPL is deliberate. If you run a modified `piia` as a network service — a
"PIIA generator as a service" — the AGPL requires you to offer your users the
modified source. That keeps improvements to a legal-drafting tool in the open,
where they can be reviewed. It also matches repowise, which `piia` integrates.

Documents you generate are **yours**. The AGPL covers this software, not its
output.

## Contributing

Adding technology detection is usually one row in a table:
[`src/piia/analysis/signatures.py`](src/piia/analysis/signatures.py). See
[CONTRIBUTING.md](CONTRIBUTING.md).

```bash
git clone https://github.com/PIIA-CLI/PIIA-CLI && cd PIIA-CLI
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,docx]"
pytest && ruff check src tests
```
