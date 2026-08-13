# Security policy

## Reporting a vulnerability

Please **do not open a public issue.** Use GitHub's private reporting:
[Report a vulnerability](https://github.com/PIIA-CLI/PIIA-CLI/security/advisories/new).

Include what you did, what happened, and the impact you think it has. Expect an
acknowledgement within a few days. Please give us a reasonable window to ship a
fix before disclosing publicly.

## Supported versions

The latest release on `main` is supported. `piia-cli` is pre-1.0; fixes go into
the next release rather than into patches of older ones.

## What we consider a vulnerability

- Leaking a secret: `LLM_API_KEY` (or any credential) appearing in output, logs,
  written artifacts, or a prompt sent to a model. `Settings.describe()` redacts
  keys and `piia config show` uses it — a path around that is a bug.
- Sending data to a model endpoint that the documented behaviour says is not
  sent — in particular **source code**, or any part of a `--corporate-document`
  outside the intellectual-property, confidentiality and governing-law sections.
- Command injection through a repository URL, path, branch name, or config
  value. `git` and `repowise` are always invoked with an argv list and never
  through a shell; a way to break that is a bug.
- Path traversal: writing outside `--output-dir` or the workspace directory,
  including via a crafted repository or signatory name.
- A crafted repository causing arbitrary code execution during a scan. The
  scanner only reads files; it never executes anything from a repository, does
  not run build tools, and does not import scanned code.

## What is not a vulnerability

- **Legally wrong output.** `piia` is a drafting aid, not a lawyer. Bad clause
  text or a wrong relatedness score is a normal bug — open an issue. See
  [docs/LEGAL_DISCLAIMER.md](docs/LEGAL_DISCLAIMER.md).
- **Your model provider retaining your prompts.** `piia` sends a compacted
  analysis summary to the endpoint *you* configure. Where that goes and what
  happens to it is between you and that provider. Use a local endpoint or
  `--no-llm` for sensitive material.
- **Cloning a private repository you have credentials for.** That is the feature.
  `piia` prefers an existing local checkout and only clones what you name.
- **`LLM_VERIFY_SSL=false` doing what it says.** It is documented as being for
  internal endpoints with self-signed certificates.

## Notes for operators

- `.env` is gitignored by default and `piia init` appends it to `.gitignore` if
  it is missing. Keep keys there or in the environment, never in `piia.yaml`,
  which is meant to be committed.
- Generated artifacts under `piia-out/` may be privileged and contain repository
  names, local paths and contributor names. `.gitignore` excludes them; keep it
  that way.
- `git` is run with `GIT_TERMINAL_PROMPT=0` and `GIT_ASKPASS=echo`, so a clone
  fails fast rather than hanging on a credential prompt in CI.
- `repowise` is invoked with `REPOWISE_SKIP_EDITOR_SETUP=1` and `DO_NOT_TRACK=1`,
  and only its keyless commands are used, so it neither modifies your global
  editor configuration nor spends tokens.
