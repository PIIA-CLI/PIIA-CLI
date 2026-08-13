# Contributing to piia-cli

Thanks for helping. The most valuable contributions are usually the smallest:
one more technology signature, one more manifest format, one more provider shape.

## Setup

```bash
git clone https://github.com/PIIA-CLI/PIIA-CLI && cd PIIA-CLI
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,docx]"

pytest                       # the whole suite, no network, no model
ruff check src tests
mypy
```

The test suite builds **real git repositories** in `tmp_path` and uses a fake
HTTP transport for the model. Nothing in CI touches the network or an endpoint,
and nothing should need to.

## The one architectural rule

**Deterministic and non-deterministic code stay separate.**

| Layer | Package | Rule |
| :--- | :--- | :--- |
| Deterministic | `piia.analysis`, `piia.documents.clauses`, `piia.documents.corporate` | Same input → same output, always. No model, no network beyond `git clone`, no randomness. |
| Non-deterministic | `piia.llm` | May call a model. **Must** have a deterministic fallback. |

Two consequences, both load-bearing:

- **A model can never overwrite a fact.** Commits, dates, licenses,
  contributors and relatedness scores come from the analysis. The model supplies
  prose. `_merge_invention` in `drafting.py` enforces this — read it before
  changing anything about how model output is used.
- **Operative clause text is fixed** (`documents/clauses.py`). A generator that
  produced different legal language every run could not be reviewed by counsel
  and then trusted. Model output goes into named slots and exhibits, never into
  the skeleton.

## Adding technology detection

This is usually one row in `src/piia/analysis/signatures.py`:

```python
# A dependency implies a technology.
DEPENDENCY_SIGNATURES = {
    "polars": ("ml_ai", "Polars"),
}

# A file name implies one.
FILE_SIGNATURES = {
    "biome.json": ("frontend", "Biome"),
}

# A manifest implies an ecosystem and a package manager.
MANIFESTS = {
    "requirements.in": ("pypi", "pip-tools"),
}
```

Guidelines:

- **Only claim what a named file or named dependency proves.** No inference from
  file contents, no heuristics that could be wrong in a legal document.
- Use the **canonical display name** with its real casing (`PyTorch`, not
  `pytorch`). Matching is case-insensitive; the exhibit shows what you write here.
- Pick the category that reflects why it matters for relatedness. `CATEGORY_WEIGHTS`
  decides how much a shared entry counts.
- If it is so common it says nothing about substantive similarity (NumPy,
  requests, pytest), add it to `UBIQUITOUS` so it gets discounted.
- Add a test asserting your signature fires on a fixture repository.

## Adding a manifest parser

Add a `_parse_*` function in `analysis/native.py` and register the filename in
`MANIFESTS`. Parsers must be **soft-failing**: a malformed manifest degrades
detection, it never fails a run. There is a test for exactly that
(`test_malformed_manifest_does_not_fail_the_scan`) — keep it passing.

## Adding an LLM provider

Usually nothing to add: if it speaks OpenAI's `/chat/completions` or Anthropic's
`/v1/messages`, it already works. Contribute a **verified configuration block**
to [docs/LLM_PROVIDERS.md](docs/LLM_PROVIDERS.md) with the exact `LLM_BASE_URL`
and `LLM_MODEL_NAME` you tested.

A genuinely different wire format means a new `api_style` in `llm/client.py`:
`endpoint`, `headers()`, `_body()` and `_parse()`. Add tests using
`httpx.MockTransport` — never a live endpoint.

## Legal text changes

Changes to `documents/clauses.py` get extra scrutiny, and rightly so.

- Say **which jurisdiction** the change is for and **why** in the PR.
- Cite the statute, rule or standard practice you are following.
- Do not add restrictive covenants that are unenforceable in common
  jurisdictions. Non-solicitation is already opt-in; no non-compete exists.
- Cross-references are numeric ("Section 4.3"). If you add, remove or reorder a
  section, fix every reference and update
  `test_section_numbering_matches_the_cross_references`.
- Nobody here is your lawyer, and neither is this project. Contributions are
  offered without warranty.

## Output contract changes

`piia.version.ENVELOPE_VERSION` is a promise. Within `1.x` you may **add**
fields; you may not remove them, retype them, or change the meaning of an
`error.code` or an exit code. Update
[docs/OUTPUT_CONTRACT.md](docs/OUTPUT_CONTRACT.md) and the schemas in
`output/schemas.py` in the same PR.

## Pull requests

- One concern per PR.
- Tests for new behaviour; a regression test for a bug fix.
- `pytest` and `ruff check src tests` pass locally.
- Update the docs the change affects — including this file, if the change
  changes how one contributes.
- By contributing you agree your contribution is licensed under
  **AGPL-3.0-or-later**.

## Reporting bugs

Include the command you ran, the full JSON envelope (`--json`; the `request_id`
helps), and `piia version --json`. Redact anything confidential — remember that
an analysis contains repository names, paths and contributor names.

Security issues: see [SECURITY.md](SECURITY.md). Do not open a public issue.
