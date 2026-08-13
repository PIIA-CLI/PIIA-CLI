# The output contract

Humans and programs consume the same commands, so the design principle is
**semantic parity**: every command computes one payload and renders it either as
human Markdown or as a JSON envelope. There is no second code path that could
drift, and the JSON keys are the same keys the human view is built from.

The contract is versioned independently of the package
(`piia.version.ENVELOPE_VERSION`, currently `1.0.0`). Pin against that, not
against the release number.

## Choosing a format

Precedence, highest first:

1. `--json`, or `-o human|json|json-pretty|jsonl` (`ndjson` and `md` are aliases).
2. `PIIA_OUTPUT_FORMAT` in the environment.
3. **`stdout` is not a TTY** → `json`. Whatever is reading is almost certainly a
   program.
4. Interactive TTY → `human`.

Colour is decided separately, and never appears inside JSON. `NO_COLOR`,
`PIIA_NO_COLOR`, `TERM=dumb` and `--no-color` all disable it.

## Stream discipline

| Stream | Human mode | JSON mode |
| :--- | :--- | :--- |
| `stdout` | The rendered Markdown | **The envelope, and nothing else** |
| `stderr` | Progress lines | Progress lines |

So `piia analyze --json \| jq` and an agent's captured `stdout` never see a stray
byte. `-q` silences progress entirely.

## The envelope

```json
{
  "$schema": "https://raw.githubusercontent.com/PIIA-CLI/PIIA-CLI/main/schemas/v1/cli-envelope.json",
  "version": "1.0.0",
  "command": "analyze",
  "status": "success",
  "timestamp": "2026-08-13T19:51:10Z",
  "execution": {
    "duration_ms": 21447,
    "request_id": "req_51bb4bf41d1c493a",
    "tool_version": "0.1.0"
  },
  "data": { "...": "command-specific; see --schema" },
  "error": null,
  "warnings": [],
  "pagination": null
}
```

| Field | Notes |
| :--- | :--- |
| `status` | `success`, `error`, or `partial` — `partial` means it worked but something degraded, so check `warnings` |
| `execution.request_id` | Correlation id. Pin it with `PIIA_REQUEST_ID` for reproducible output |
| `timestamp` | RFC 3339 UTC. Pin it with `PIIA_FROZEN_TIME` for golden-file tests |
| `data` | `null` on error, never partially filled |
| `warnings[]` | `{code, message, remediation}` — non-fatal degradations |

## Errors

On failure the envelope still renders on `stdout`, `data` is `null`, and the exit
code is specific:

```json
{
  "status": "error",
  "data": null,
  "error": {
    "code": "CONFIG_INVALID",
    "message": "The model endpoint is not configured: LLM_BASE_URL is unset.",
    "details": [{ "field": "LLM_BASE_URL", "issue": "required but not set" }],
    "remediation": "Run 'piia init' to write a .env template, then set LLM_BASE_URL and LLM_MODEL_NAME. Or pass --no-llm for a deterministic draft with no model call."
  }
}
```

`code` is stable — branch on it, never on `message`. `remediation` is the point:
a failed invocation tells the caller what to run next, which is what makes the
tool self-correcting in an agent loop.

| Exit | Code family | Meaning |
| :--- | :--- | :--- |
| `0` | — | Success (or `partial`) |
| `1` | `PIIA_ERROR` | Unclassified failure; `doctor` also exits `1` on a failed check |
| `2` | `CONFIG_INVALID`, `USAGE_INVALID` | Bad configuration or invocation |
| `3` | `REPOSITORY_UNAVAILABLE` | A repository could not be resolved, cloned or read |
| `4` | `GIT_NOT_FOUND` | `git` is not on `PATH` |
| `5` | `REPOWISE_FAILED` | repowise was required and failed |
| `6` | `LLM_REQUEST_FAILED`, `LLM_RESPONSE_INVALID` | Endpoint failure, or unusable output after repair |
| `7` | `DOCUMENT_RENDER_FAILED` | Assembly or rendering failed |
| `8` | `OPTIONAL_DEPENDENCY_MISSING` | A format needs an extra (`[docx]`, `[pdf]`) |
| `9` | `IO_ERROR` | Filesystem error |
| `130` | `INTERRUPTED` | Ctrl-C |

## Discovering the schema

Every command answers `--schema` with the JSON Schema (2020-12) of its own
output, so nothing has to be inferred from one sample:

```bash
piia analyze --schema                     # this command's envelope
piia schema generate                      # same, by name
piia schema                               # the universal envelope
piia schema --all                         # every command, one document
```

Commands with published `data` schemas: `analyze`, `generate`, `render`,
`doctor`, `config show`, `version`, `init`.

## `jsonl` for streaming

`-o jsonl` splits one envelope into newline-delimited records: a `meta` record
carrying the envelope minus `data`, one record per top-level list in `data`, then
a `data` record for the scalars. A forty-repository analysis becomes forty rows a
shell loop can consume:

```bash
piia analyze -t . --priors-file priors.txt -o jsonl \
  | jq -r 'select(.record_type=="overlaps") | [.repository, .relatedness] | @tsv'
```

## Stability promises

Within envelope version `1.x`:

- Fields are **added**, never removed or retyped.
- `error.code` values and exit codes do not change meaning.
- `status` stays within `success | error | partial`.
- New `data` fields may appear, so parse permissively.
- The analysis `digest` algorithm is stable: same commits, same digest. It
  deliberately excludes `generated_at` and `tool_versions`, so re-running on a
  different machine still reproduces it.
