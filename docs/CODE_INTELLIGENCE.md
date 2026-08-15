# Code-intelligence provider

PIIA-CLI's native scanner and relatedness formula are the required deterministic
core. Code-intelligence enrichment is optional and enters through the narrow
`CodeIntelligenceProvider` protocol in `analysis/intelligence.py`.

The supported provider is the `ithllc/repowise` fork at commit
`8b58b52dcaa8aa221f00cd75ac06d91421f0376b` (Repowise 0.43.0). The GitHub Action
image installs that exact source revision. Local package installs are pinned to
the compatible 0.43.0 release; contributors validating the fork itself can use:

```bash
pip install \
  "repowise @ git+https://github.com/ithllc/repowise.git@8b58b52dcaa8aa221f00cd75ac06d91421f0376b"
```

PIIA-CLI invokes only deterministic, keyless commands through a subprocess:
`init --no-prose --mode fast`, `health --format json`, `export --format json
--full`, and `dead-code --format json`. Editor setup, agent files, onboarding,
key persistence, telemetry, and workspace discovery are disabled. A provider
timeout or output-shape change becomes a warning; document generation continues
with native evidence.

Both projects are AGPL-3.0-or-later. No runtime API key, royalty, or per-scan fee
is required by that license. A proprietary distribution or hosted layer should
be reviewed with counsel and, where appropriate, separately licensed from all
relevant copyright holders.

Provider updates are deliberate: update the fork, change the pinned commit and
version together, run the live compatibility test, and inspect the trimmed
health, architecture, and dead-code payloads before merging.
