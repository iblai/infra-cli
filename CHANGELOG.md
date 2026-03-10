# Changelog

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
- 16 ibl.ai platform subdomain records (when using Route53)
- Real-time Terraform progress with JSON event streaming and Rich Live display
- `iblai infra provision` — interactive provisioning wizard
- `iblai infra destroy` — destroy infrastructure with double-confirmation for production
- `iblai infra status <name>` — show infrastructure details and workspace info
- `iblai infra list` — list all managed environments
- ibl.ai branded terminal UI with Rich theme and questionary styling
- Project state persistence at `~/.iblai-infra/projects/`
- Workspace visibility showing Terraform files during and after provisioning
