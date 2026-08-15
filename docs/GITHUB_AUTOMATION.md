# GitHub automation and commercial boundary

The PIIA Action runs deterministic relatedness analysis inside the customer's
GitHub-hosted or self-hosted runner. Source code and the analysis payload do not
leave that runner. The complete JSON envelope is available as a workflow
artifact, while the digest and gate verdict are exposed as action outputs.

This is intentionally useful without a subscription. The CLI is AGPL software,
and a local paywall could be removed by any recipient. The durable paid product
is the managed layer around the open scanner: organization policy, retained
history, signed attestations, App-owned check runs, support, and team reporting.

## Example workflow

Check out the company repository and each private prior work before invoking the
action. The workflow's `GITHUB_TOKEN` normally cannot read unrelated private
repositories, so use a narrowly scoped secret or GitHub App token for those
additional checkouts.

```yaml
name: Prior-invention gate

on:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  relatedness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          path: company

      - uses: actions/checkout@v4
        with:
          repository: founder/prior-project
          token: ${{ secrets.PRIOR_REPOS_TOKEN }}
          path: prior-project

      - id: piia
        uses: PIIA-CLI/PIIA-CLI@v1
        with:
          target: company
          priors: prior-project
          fail_on_relatedness: high
          use_repowise: false

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: piia-analysis
          path: piia-analysis.json
```

`use_repowise` defaults to `false` because graph indexing is materially slower
than the native scanner and should be scheduled deliberately in pull-request
workflows. The Docker image includes the tested Repowise fork snapshot, so
turning it on does not install a moving version.

## Inputs and policy behavior

`priors` accepts newline- or comma-separated paths and clone URLs.
`priors_file` accepts the same format as the CLI. At least one must be supplied.

`fail_on_relatedness` is the lowest band that fails the action:

| Value | Fails on |
| :--- | :--- |
| `high` | high only |
| `moderate` | moderate or high |
| `low` | low, moderate, or high |
| `never` | never; report-only mode |

A policy finding exits with status 10 after writing the report and outputs. Tool
or configuration failures retain the CLI's existing exit-code contract.

## Why the paid product is a GitHub App

GitHub Marketplace billing plans apply to GitHub Apps and OAuth Apps, not to a
standalone Action listing. The rollout is therefore:

1. Publish the free Action and establish real installations and scan quality.
2. Add a free GitHub App that owns managed check runs and organization policy.
3. After publisher verification and Marketplace eligibility, add paid App plans.
4. Offer metered certified reports for occasional users and included report
   allowances for subscribers.

The proposed App should request only:

- Repository metadata: read (mandatory GitHub App baseline).
- Contents: read, only if the service performs a managed scan.
- Checks: write, to publish an App-owned check run.
- Pull requests: read, only when pull-request context is displayed.

It does not need Actions, administration, issues, or organization-member write
permissions. Webhook deliveries must be authenticated with
`X-Hub-Signature-256`, deduplicated by delivery ID, and retained without raw
source or full legal documents.

The initial PostgreSQL model is in
[`deploy/marketplace/postgres.sql`](../deploy/marketplace/postgres.sql). It keeps
subscriptions, installations, scan digests, certifications, and usage entries
separate so billing and the analysis engine remain independent.

## What customers pay for

The local CLI and local Action remain free. Paid value is service work that a
copy of the source does not provide by itself:

- cryptographically signed, time-stamped analysis attestations;
- retained cross-founder history and drift monitoring;
- centrally managed thresholds and required checks;
- investor/counsel-ready organization exports;
- hosted execution for bots that need an API instead of a workstation;
- operational support and service availability.

A fair launch experiment is a low one-off certification price plus an
organization subscription with included certifications. Do not hard-code prices
into the scanner; plan IDs and meter prices belong in Marketplace or the payment
provider so pricing can be tested without releasing new software.

This architecture is technical and commercial planning, not legal advice. Have
open-source and product counsel review AGPL compliance, output terms, and claims
made by any certification product before launch.
