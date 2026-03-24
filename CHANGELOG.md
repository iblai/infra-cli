# Changelog

## [1.2.1] — 2026-03-24

### Fixed
- pgvector extension task used hardcoded `postgres` user and `ibl_dm_db` database — now reads `$POSTGRES_USER` and `$POSTGRES_DB` from container environment, matching actual DM postgres configuration (`ibl`/`dlmanager`)
- `pg_isready` health check also updated to use `$POSTGRES_USER` instead of hardcoded `postgres`
- Ansible runner reported false failures when tasks with `ignore_errors: true` emitted `fatal:` lines — runner now trusts `proc.returncode` as the primary success signal and shows ignored errors as warnings instead of failing the run
- Removed `ignore_errors: true` from pgvector task since it should now succeed with correct credentials

## [1.2.0] — 2026-03-20

### Added
- `iblai infra bootstrap` command — set up the IBL platform on any existing server (any cloud, bare metal) without Terraform provisioning
- Interactive bootstrap wizard collects server IP, SSH key, domain, image tags, and AWS/GitHub credentials
- Bootstrap projects tracked with `provider="bootstrap"` — `list`, `status`, and `destroy` all work
- Destroy guard for bootstrap projects skips Terraform teardown and marks project as destroyed
- "Bootstrap existing server" option in landing screen menu

## [1.1.0] — 2026-03-18

### Added
- `edx_supporting_service_defaults` — set default image tags for edX supporting services (MySQL 8.0.40, Elasticsearch, Redis, MongoDB) during provisioning
- Architecture diagrams (single-server and multi-server AWS topologies) in README
- Branded README header with badges, install instructions, and dependency documentation

### Fixed
- MySQL version pinned to 8.0.40 instead of 8.4.0 — 8.4.0 caused compatibility issues with edX
- LMS container health verified (running and not restarting) before OAuth2 application creation
- Retries added to OAuth2 creation for container restart resilience
- Postgres data directory recursively chowned to UID 999 before DM launch
- `/ibl/` directory ownership set to SSH user before any services launch
- `apache2-utils` added to prerequisites for `htpasswd` availability
- LMS health check and OAuth creation use `docker exec` instead of `tutor` CLI
- Langfuse secrets generated before DM launch when AI features are enabled

## [0.7.0] — 2026-03-12

### Added
- `ibl_spa` Ansible role — creates OAuth2 Application in edX for SPA SSO, sets SPA config defaults, authenticates Docker with ECR, and launches Auth, Mentor, and Skills SPA containers
- SPA image tag prompts in setup wizard: Auth SPA (`1.13.15`), Mentor SPA (`0.35.14`), Skills SPA (`0.9.8`)
- `spa_auth_image_tag`, `spa_mentor_image_tag`, `spa_skills_image_tag` fields on `SetupConfig` model
- 3 new platform subdomains: `api.`, `platform.`, `prometheus.`
- `web.data.` subdomain for SPA data API

### Changed
- Playbook now runs 9 roles: docker, awscli, python, ibl_cli_ops, ibl_platform, ibl_dm, ibl_edx, **ibl_spa**, final_steps
- `_build_extra_vars()` passes SPA image tags to playbook
- ACM certificate domain lists updated: cert 1 adds `api.` and `web.data.`; cert 2 adds `platform.` and `prometheus.`
- `IBL_SUBDOMAINS` updated from 16 to 19 entries (added `api`, `web.data`, `platform`, `prometheus`; removed `status`)

## [0.6.3] — 2026-03-10

### Added
- AI features prompt — asks user whether to enable AI for DM (`IBL_DM.ENABLE_IBL_AI` and `IBL_DM.ENABLE_IBL_AI_PLUS`), defaults to enabled
- `enable_ai` field on `SetupConfig` model, passed through to Ansible extra vars
- `ibl_platform` role configures both AI settings based on user choice

## [0.6.2] — 2026-03-10

### Fixed
- Create `ibl_local_default` docker network in `ibl_platform` role after global proxy launch — DM compose requires it as an external network but the proxy only creates `ibl_default`
- Add container verification to `ibl_dm` role — fails with actionable error if no DM containers are running after launch
- Add container verification to `ibl_edx` role — fails if no edX containers are running after launch
- Broadened DM container filter from `ibl-dm-pro` to `ibl_dm` to match actual container naming

## [0.6.1] — 2026-03-10

### Fixed
- Default DM image tag changed from `4.190.0-ai` to `4.189.1-ai` — previous tag did not exist in ECR, causing silent `ibl dm launch` failure

## [0.6.0] — 2026-03-10

### Added
- Full platform setup via Ansible — 8 roles: docker, awscli, python, ibl_cli_ops, ibl_platform, ibl_dm, ibl_edx, final_steps
- DM and edX image tag prompts with defaults (`4.189.1-ai`, `sumac.2.4.13`); sets ECR image URIs before launch
- `ibl_platform` role configures edX version, base domain, environment, and DM/edX container images
- `ibl_dm` role runs `ibl dm launch` (timeout 1800s)
- `ibl_edx` role runs `ibl edx launch` (timeout 3600s)
- `final_steps` role runs `ibl config save`, `ibl global-proxy reload`, `ibl launch --ibl-oauth --ibl-oidc --ibl-edx-manager`, and `ibl dm auth-setup`
- `dm_image_tag` and `edx_image_tag` fields on `SetupConfig` model

### Changed
- Simplified runner to single-phase Ansible execution (removed two-phase SSH/Fabric approach)
- `_build_extra_vars()` now passes `base_domain`, `edx_version`, `env_config`, `dm_image_tag`, `edx_image_tag` to playbook
- Removed `fabric` dependency — all remote execution handled by Ansible

### Fixed
- Tests updated to match runner rewrite — removed tests for deleted JSON-parsing methods, added tests for all 8 roles

## [0.5.0] — 2026-03-10

### Added
- Comprehensive pytest test suite — 380 tests covering models, providers, Terraform runner, Ansible runner, CLI commands, prompts, validators, review flows, state management, and UI helpers
- Dev dependencies in `pyproject.toml`: `pytest>=8.0`, `pytest-cov>=4.1`
- Pytest configuration: `--strict-markers`, `testpaths = ["tests"]`, `slow` marker
- Test coverage for all enum combinations (SSH method × cert method × environment), IP/CIDR/domain validators, and session persistence paths

### Fixed
- `load_state()` now skips corrupt `state.json` files instead of crashing — previously a single corrupt workspace would prevent loading any project by name
- Replaced fragile `AnsibleRunner.__new__()` hack in `_run_setup()` with a direct `shutil.which()` check for ansible-playbook

## [0.4.0] — 2026-03-09

### Added
- `iblai infra auth` command — switch or re-authenticate AWS credentials at any time
- Session persistence — credentials saved to `~/.iblai-infra/session.json` after authentication; reused across all commands until explicitly cleared or expired
- Interactive landing screen — running `iblai infra` shows a branded menu with arrow-key navigation to launch any command directly
- Type-to-filter for long lists — regions, AWS profiles, instance types, and key pairs use `questionary.autocomplete()` for instant filtering

### Changed
- Credential resolution order: explicit `--profile` flag → saved session → interactive wizard (no silent auto-detection)
- `prompt_credentials()` accepts `show_step` parameter — step header only shown during the full 5-step wizard
- `run_provision_wizard()` accepts `show_banner` parameter — avoids double banner when launched from the landing screen menu
- Simplified saved session display: shows "Authenticated — user (account)" instead of full ARN details
- Command names in instructional text now highlighted with `[brand]` color
- Dynamic versioning — `pyproject.toml` uses `[tool.hatch.version]` pointing to `__init__.py`

### Fixed
- `ctx.invoke()` passing `OptionInfo` objects instead of actual values to Pydantic models — now passes explicit defaults
- Volume type default mismatch (`"gp3 (recommended)"` vs `"gp3"`) causing validation error
- Non-ASCII em dashes in Terraform security group descriptions rejected by AWS API
- Duplicate "Authenticated as" messages during permission checks
- Double banner when launching provision from the landing screen menu
- Removed "recommended" labels from instance type and volume type choices

## [0.3.0] — 2026-03-09

### Added
- Interactive authentication fallback — when AWS credentials are missing or invalid, any command that needs auth now offers to launch the credentials wizard instead of failing
- Shared `_resolve_credentials()` helper in CLI that tries env vars, `~/.aws/` profiles, then falls back to the interactive Step 1 wizard

## [0.2.0] — 2026-03-09

### Added
- `iblai infra permissions` command — displays minimum IAM policy JSON required for provisioning
- `--check` flag for dry-run permission verification against active AWS credentials (EC2, ELB, S3, ACM, Route 53, IAM, STS)
- `--profile` and `--region` flags for targeting specific credentials during permission checks
- Branded landing screen when running `iblai infra` with no arguments — shows all available commands and a getting-started guide

## [0.1.0] — 2026-03-09

### Added
- Interactive provisioning wizard with 5-step flow (credentials, compute, network, DNS, review)
- AWS authentication: profile, access keys, or environment variables with STS validation
- EC2 single-server provisioning with configurable instance type and volume
- VPC, public subnets (multi-AZ), internet gateway, and route tables
- Application Load Balancer with security groups
- Three certificate modes: ACM (auto-managed via Route53), upload (IAM server cert), or none (HTTP only)
- Three SSH key modes: generate Ed25519 keypair, provide existing public key, or use AWS key pair
- SSH access restricted to user-provided VPN IP
- S3 buckets for backups, media, and static files
- 19 ibl.ai platform subdomain records (when using Route53)
- Real-time Terraform progress with JSON event streaming and Rich Live display
- `iblai infra provision` — interactive provisioning wizard
- `iblai infra destroy` — destroy infrastructure with double-confirmation for production
- `iblai infra status <name>` — show infrastructure details and workspace info
- `iblai infra list` — list all managed environments
- ibl.ai branded terminal UI with Rich theme and questionary styling
- Project state persistence at `~/.iblai-infra/projects/`
- Workspace visibility showing Terraform files during and after provisioning
