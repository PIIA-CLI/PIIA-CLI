-- PIIA managed-service control plane (PostgreSQL 15+).
-- Stores billing and evidence metadata only; never raw repository source.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE marketplace_accounts (
    github_account_id BIGINT PRIMARY KEY,
    github_login TEXT NOT NULL,
    github_account_type TEXT NOT NULL CHECK (github_account_type IN ('Organization', 'User')),
    plan_id BIGINT,
    plan_name TEXT,
    subscription_status TEXT NOT NULL CHECK (
        subscription_status IN ('active', 'free_trial', 'pending_cancel', 'cancelled', 'suspended')
    ),
    billing_cycle TEXT CHECK (billing_cycle IN ('monthly', 'yearly')),
    unit_count INTEGER CHECK (unit_count IS NULL OR unit_count >= 0),
    effective_at TIMESTAMPTZ,
    next_billing_at TIMESTAMPTZ,
    free_trial_ends_at TIMESTAMPTZ,
    marketplace_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX marketplace_accounts_login_idx
    ON marketplace_accounts (lower(github_login));
CREATE INDEX marketplace_accounts_entitlement_idx
    ON marketplace_accounts (subscription_status, plan_id);

CREATE TABLE app_installations (
    installation_id BIGINT PRIMARY KEY,
    github_account_id BIGINT NOT NULL REFERENCES marketplace_accounts(github_account_id),
    repository_selection TEXT NOT NULL CHECK (repository_selection IN ('all', 'selected')),
    suspended_at TIMESTAMPTZ,
    installed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX app_installations_account_idx
    ON app_installations (github_account_id);

CREATE TABLE repositories (
    github_repository_id BIGINT PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES app_installations(installation_id),
    github_account_id BIGINT NOT NULL REFERENCES marketplace_accounts(github_account_id),
    full_name TEXT NOT NULL,
    private BOOLEAN NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (github_account_id, full_name)
);

CREATE INDEX repositories_installation_idx ON repositories (installation_id, enabled);

CREATE TABLE webhook_deliveries (
    delivery_id TEXT PRIMARY KEY,
    event_name TEXT NOT NULL,
    action TEXT,
    payload_sha256 TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    processing_error TEXT
);

CREATE TABLE analysis_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_account_id BIGINT REFERENCES marketplace_accounts(github_account_id),
    github_repository_id BIGINT REFERENCES repositories(github_repository_id),
    request_id TEXT NOT NULL,
    analysis_digest TEXT NOT NULL,
    target_commit TEXT,
    highest_relatedness TEXT NOT NULL CHECK (
        highest_relatedness IN ('none', 'low', 'moderate', 'high')
    ),
    gate_passed BOOLEAN NOT NULL,
    trigger_kind TEXT NOT NULL CHECK (
        trigger_kind IN ('cli', 'action', 'github_app', 'api', 'scheduled')
    ),
    actor_login TEXT,
    artifact_object_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (github_account_id, request_id),
    UNIQUE (github_account_id, analysis_digest, target_commit)
);

CREATE INDEX analysis_runs_repository_time_idx
    ON analysis_runs (github_repository_id, created_at DESC);

CREATE TABLE certifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id UUID NOT NULL REFERENCES analysis_runs(id),
    certificate_number TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    signature_algorithm TEXT NOT NULL DEFAULT 'Ed25519',
    signing_key_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'valid' CHECK (status IN ('valid', 'revoked')),
    revoked_at TIMESTAMPTZ,
    revocation_reason TEXT,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE usage_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_account_id BIGINT NOT NULL REFERENCES marketplace_accounts(github_account_id),
    analysis_run_id UUID REFERENCES analysis_runs(id),
    meter TEXT NOT NULL CHECK (meter IN ('managed_scan', 'certified_report', 'retained_snapshot')),
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    idempotency_key TEXT NOT NULL UNIQUE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    billing_provider_reference TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX usage_ledger_account_time_idx
    ON usage_ledger (github_account_id, occurred_at DESC);
