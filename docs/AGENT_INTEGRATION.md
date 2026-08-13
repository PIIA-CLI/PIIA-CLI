# Driving piia from an AI coding agent

Claude Code, Gemini CLI, Codex and similar agents work by running shell commands
and reading `stdout`, `stderr` and exit codes. `piia` is built for that: the CLI
is a contract, not a UI. Read [OUTPUT_CONTRACT.md](OUTPUT_CONTRACT.md) for the
envelope itself; this page is how to use it.

## Give the agent this

```
Tool: piia (installed; run `piia --help`)

Purpose: build a PIIA's Prior Inventions exhibit from repository evidence.

Discovery:
  piia --help                 all commands
  piia <cmd> --help           all flags for one command
  piia <cmd> --schema         JSON Schema of that command's output
  piia schema --all           every schema in one document

Rules:
  - Always pass --json (or -o json). Parse envelope.data.
  - Branch on envelope.error.code, never on error.message.
  - On a non-zero exit, read envelope.error.remediation and follow it.
  - Progress is on stderr; stdout in JSON mode is exactly one JSON document.
  - `piia analyze` calls no model and is safe to run freely.
  - `piia generate --dry-run` reports the plan without drafting or writing.
  - Never invent Prior Inventions. Only repositories the user names go in.
```

## A typical agent loop

```bash
# 1. Is the environment usable? Exits non-zero if something required is missing.
piia doctor --json

# 2. What does the evidence say? No model call, so no cost and no risk.
piia analyze -t . --priors-file priors.txt --json > analysis.json

# 3. Which prior works actually need careful carve-out language?
jq -r '.data.overlaps[]
       | select(.relatedness=="high" or .relatedness=="moderate")
       | "\(.repository)\t\(.overlap_score)\t\(.relatedness)"' analysis.json

# 4. What would a full run do, before doing it?
piia generate -t . --priors-file priors.txt --signatory "Ada Lovelace" \
  --dry-run --json | jq '.data.model.estimated_calls, .data.document'

# 5. Draft it.
piia generate -t . --priors-file priors.txt \
  --signatory "Ada Lovelace" --company "Acme, Inc." \
  -f md -f json -f docx --json > result.json

# 6. Report the parts a human must check.
jq -r '.data.document.review_checklist[]
       | select(.severity=="high") | "- \(.item) — \(.why)"' result.json
```

## Useful queries

```bash
# Every prior work that does NOT predate the company's repository — a red flag.
jq -r '.data.overlaps[] | select(.predates_target == false) | .repository' analysis.json

# Copyleft licences among the prior works.
jq -r '.data.prior_works[]
       | select(.license != null)
       | select(.license | test("GPL|MPL"; "i"))
       | "\(.name): \(.license)"' analysis.json

# Which repositories were cloned rather than found locally.
jq -r '.data.prior_works[]
       | select(.repository.source=="clone") | .name' analysis.json

# Technologies the model claimed but the scan could not confirm.
jq -r '.data.document.prior_inventions[]
       | select(.unverified_technologies != null)
       | "\(.repository): \(.unverified_technologies | join(", "))"' result.json

# Which document fields a model wrote, versus the fixed template.
jq '.data.provenance | {model, model_generated_fields, deterministic_fields, degraded}' result.json

# Stream a large analysis row by row instead of holding it in memory.
piia analyze -t . --priors-file priors.txt -o jsonl \
  | jq -r 'select(.record_type=="overlaps") | [.repository, .relatedness] | @tsv'
```

## Handling failure without a human

Every error carries a `remediation` string precisely so an agent can recover:

| `error.code` | Exit | What the agent should do |
| :--- | :--- | :--- |
| `CONFIG_INVALID` | 2 | Run `piia init`, set `LLM_BASE_URL`/`LLM_MODEL_NAME`, or retry with `--no-llm` |
| `USAGE_INVALID` | 2 | Read `details[].field` and fix that flag |
| `REPOSITORY_UNAVAILABLE` | 3 | Add `--search-root <dir>`, pass an explicit path, or drop the repository |
| `GIT_NOT_FOUND` | 4 | Tell the user to install git; do not proceed |
| `LLM_REQUEST_FAILED` | 6 | Retry once, then fall back to `--no-llm` and say so in the report |
| `LLM_RESPONSE_INVALID` | 6 | Lower `--batch-size`, or fall back to `--no-llm` |
| `OPTIONAL_DEPENDENCY_MISSING` | 8 | `pip install piia-cli[docx]` / `[pdf]`, or drop that `--format` |

A reasonable default policy: retry a `6` once, degrade to `--no-llm` on a second
failure, and surface everything else to the user with the remediation attached.

## Reproducibility

```bash
PIIA_FROZEN_TIME=2026-01-01T00:00:00Z PIIA_REQUEST_ID=req_fixed \
  piia analyze -t . -p ../old --json
```

Both pins make the envelope byte-stable, which makes golden-file tests possible.
The analysis `digest` is stable regardless: same commits, same digest.

## Things an agent should not do

- **Do not fabricate prior works.** Only repositories the user explicitly lists
  belong in Exhibit A. Guessing creates a false representation in a signed
  document.
- **Do not present output as reviewed.** Every artifact carries a
  "Not legal advice" notice. Pass that on; do not summarise it away.
- **Do not send a corporate document to a hosted model without asking.**
  `--corporate-document` puts excerpts of that agreement in the prompt. If it may
  be privileged, use a local endpoint or `--no-llm`.
- **Do not silently drop a repository that failed to resolve.** It appears in
  `warnings` and in `data.notes`; report it.
- **Do not treat `status: "partial"` as a clean success.** It means something
  degraded — read `warnings` and `provenance.degraded`.

## MCP

There is no MCP server yet. The CLI is deliberately MCP-shaped: `--schema` gives
you the output contract per command, so wrapping `piia` as an MCP tool is a thin
adapter. See [issue tracker](https://github.com/PIIA-CLI/PIIA-CLI/issues) if you
want to build one.
