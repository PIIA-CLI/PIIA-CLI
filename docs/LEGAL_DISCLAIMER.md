# Legal disclaimer

**piia-cli is a drafting aid. It is not legal advice, and it is not a lawyer.**

Using it creates no attorney-client relationship with anyone. Nothing it
produces has been reviewed by counsel for your jurisdiction, your industry, or
your situation.

## What this means in practice

- **Every generated document must be reviewed by a qualified lawyer** admitted
  in the relevant jurisdiction before it is signed. The fixed clause skeleton in
  `src/piia/documents/clauses.py` is a reasonable starting point drawn from
  common practice; it is not a substitute for advice on your facts.
- **Invention-assignment law is jurisdiction-specific.** Several US states
  (including California, Delaware, Illinois, Kansas, Minnesota, North Carolina,
  Utah and Washington) restrict what an employer may require you to assign, and
  some require specific written notice to the employee. Many countries outside
  the US treat moral rights, employee inventions and works made for hire quite
  differently. `piia` does not adapt its text to any of this.
- **Restrictive covenants may be void.** The non-solicitation section is marked
  *intentionally omitted* by default for exactly this reason. No non-compete is
  included at all.
- **The relatedness score is a technical measurement, not a legal conclusion.**
  It compares declared technologies and dependencies. It does not assess
  copyright, patentability, trade secrets, ownership, or whether any particular
  work is in fact "related to the Company's business" as a court would read that
  phrase.
- **Model-written text can be wrong.** Descriptions, characterisations and
  proposed clauses drafted by a language model may be inaccurate, incomplete or
  misleading. Every generated document labels which parts a model wrote
  (`provenance.model_generated_fields`) and flags technologies the model claimed
  that the deterministic scan did not find. Read those labels.
- **A missing prior work is your risk, not the tool's.** `piia` can only analyse
  repositories you give it. Work that never reached a repository you listed —
  work on a former employer's systems, in a private notebook, on paper, or in
  someone else's account — will not appear in Exhibit A. Omitting a Prior
  Invention is the failure mode this tool is meant to reduce, not one it can
  eliminate.

## Privacy and confidentiality

- The deterministic layer reads your repositories **locally**. Nothing is
  uploaded to run it.
- The model layer sends a **compacted summary** to whatever endpoint you
  configure: repository names and URLs, technology and dependency lists, commit
  dates, contributor names, a short README excerpt, and — if you pass
  `--corporate-document` — excerpts from the intellectual-property,
  confidentiality and governing-law sections of that document. It does not send
  your source code.
- **Where that goes is your choice.** If the material is privileged or
  confidential, point `LLM_BASE_URL` at a local or self-hosted model, or run with
  `--no-llm` and use the deterministic draft. Legal drafts sent to a third-party
  API may be logged or retained by that provider, and may affect privilege.
- `piia generate --dry-run` shows exactly what would be resolved and how many
  model calls would be made, before any of it happens.

## No warranty

This software is distributed under AGPL-3.0-or-later **without any warranty**;
see sections 15 and 16 of the [LICENSE](../LICENSE). The authors and
contributors accept no liability for any consequence of using it, including any
agreement executed in reliance on its output.
