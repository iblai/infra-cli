# Changelog

## [1.5.1] — 2026-04-30

### Fixed
- **Private-access gate fires on `provision` → "Run platform setup now?"** path. The post-provision shortcut (`app._offer_setup`) bypassed `_confirm_private_access_or_abort()` because it never reached `_run_setup_provisioned`/`_run_setup_interactive`/`_run_resetup`. Operators going from `iblai infra provision` straight into setup now see the same prerequisites notice + Y/N confirm before any prompts collect input

## [1.5.0] — 2026-04-30

### Added
- **Monorepo subdirectory installs** — `--cli-ops-repo` / `--prod-images-repo` (and the matching setup prompts) now accept a `repo/subdir` path, e.g. `kaplan-iblai-infra-ops/kaplan-iblai-prod-images`. The ansible role appends `&subdirectory=<subdir>` to the install URL so a single client monorepo can host both `iblai-cli-ops` and the prod-images package
- **`parse_repo_path()` helper** in `models.py` — splits operator input into `(repo, subdir)`. Bare `iblai-cli-ops` keeps the canonical behavior; subdir-form unlocks per-client monorepo deployments
- **`cli_ops_subdir` / `prod_images_subdir` extra-vars** passed through `AnsibleRunner` to the `ibl_cli_ops` role (single-server + call-server templates)

## [1.4.0] — 2026-04-14

### Added
- **Multi-server deployment type** — `iblai infra provision` now offers a deployment type selector: single-server (existing) or multi-server. Multi-server provisions N app servers (2-10) in public subnets behind an ALB + 1 services server in a private subnet, with optional managed RDS MySQL/PostgreSQL and Redis ElastiCache
- **`DeploymentType` enum** — `SINGLE` / `MULTI` on `InfraConfig`, defaults to `SINGLE` for backward compatibility
- **`MultiServerConfig` model** — app server count/type/volume, services server type/volume, managed service toggles. DB passwords and Redis auth tokens generated at runtime, excluded from state serialization via `Field(exclude=True)`
- **Multi-server Terraform templates** (`templates/aws/multi-server/`) — VPC with 4 subnet tiers (public/private/database/cache), NAT gateways per AZ, 6 security groups (ALB, app, services, RDS, Redis, EFS), EFS shared media storage, optional RDS MySQL 8.4 + PostgreSQL 15 (multi-AZ), optional Redis ElastiCache (multi-AZ, encrypted)
- **Multi-server wizard prompts** — interactive configuration for app server count, instance types, volume sizes, managed database and Redis toggles
- **Multi-server review panel** — shows server counts, managed services status, subnet tiers
- **`launch` multi-server flags** — `--deployment-type`, `--app-server-count`, `--services-instance-type`, `--services-volume-size`, `--enable-mysql`, `--enable-postgres`, `--enable-redis`
- **Type column in `list` command** — shows `single` or `multi (N)` for each environment
- **New resource labels** — NAT Gateway, Elastic IP, RDS Database, DB Subnet Group, Redis Cluster, Cache Subnet Group, EFS File System, EFS Mount Target
- **Terraform gitignore entries** — `.terraform/`, `*.tfvars`, `*.tfstate` added to `.gitignore`

## [1.3.1] — 2026-04-07

### Added
- **Smoke tests in service-update** — after nginx restart, verifies SSO login for all 4 browser test users, DM API accessibility, and Mentor chat endpoint. Reports a clear pass/fail summary in CI logs before handing off to Playwright tests. Advisory only (does not fail the pipeline)

### Fixed
- **Target group registration order** — `register_target()` now registers the new instance FIRST, then deregisters old targets. Prevents empty target group (ALB 503) if the pipeline fails between deregister and register

## [1.3.0] — 2026-04-03

### Added
- **`resetup` command** — `iblai infra resetup <name>` re-configures an existing environment with a new base domain and fresh secrets. Rotates all secrets (`ibl config rotate-secrets -f --include-auth`), syncs PostgreSQL and MySQL passwords, then restarts all services
- **`launch` command** — `iblai infra launch` provisions AWS infrastructure from a pre-built AMI via Terraform (VPC, ALB, ACM certs, Route53, EC2) and configures the platform via Ansible in a single non-interactive command. All input via CLI flags for CI/CD workflows
- **`launch-env` command** — `iblai infra launch-env` reads a `.env` file from the current directory, shows a summary with masked secrets, confirms, then launches. Simplest path for local use
- **`service-update` command** — `iblai infra service-update` updates container images and restarts services without infrastructure changes or secret rotation. Two modes: `--host` for existing servers, `--ami-id` to launch EC2 from AMI + update + register in ALB target group. Designed for CI/CD image update workflows
- **`.env.example`** — template with all launch variables using safe placeholder values (RFC 5737 IPs, AWS example keys)
- **AMI support in Terraform** — new `ami_id` and `skip_user_data` variables allow launching EC2 from a custom AMI instead of vanilla Ubuntu
- **Launch Ansible playbook** — `launch_playbook.yml` with lean roles for AMI-based deployments
- **Service update Ansible playbook** — `service_update_playbook.yml` with 2 roles (ibl_cli_ops, ibl_service_update) for day-2 image updates
- **`ibl_launch` role** — starts databases, sets domain, rotates secrets, syncs PostgreSQL and MySQL passwords after rotation
- **`ibl_launch_services` role** — ECR login, DM update, edX stop/start, SPA restart with health checks, proxy reload
- **`ibl_service_update` role** — ECR login, edX stop/prune/config save/start, DM config save/update, DM migrations, SPA restart with health checks, nginx restart
- **SPA health checks** — all SPA launches/restarts now verify HTTP 200 on Auth (5000), Mentor (5001), Skills (5002) with 10 retries at 15s intervals
- **Ansible progress display** — shows current task description (e.g. "Wait for DM web to be ready") instead of just "Running"
- **Split `final_steps` role** into 3 focused roles: `integrations` (OAuth/OIDC, edX-manager, DM auth-setup, edX sync), `admin_setup` (OpenAI key, super admins, CSRF domains, LLM key), `data_seeding` (flows, LLM registry, mentors, RBAC, TimescaleDB views, analytics views)
- **TimescaleDB support** — `ENABLE_TIMESCALEDB=true` set in platform config, `setup_timescale_views --full-setup` and `refresh_analytics_views` run during data seeding
- **`HIDE_ANALYTICS='false'`** — set as quoted string in SPA mentor config
- **CLI ops release tag prompt** — both setup and resetup now prompt for iblai-cli-ops release tag
- **iblai-prod-images installation** — ibl_cli_ops role installs via `uv pip install iblai-images[sumac]` from `iblai/iblai-prod-images`, which pins both CLI ops and all container image versions
- **AnsibleRunner parameterization** — supports multiple playbooks and role label sets (setup, launch, service-update)
- **EC2 launch + target group helpers** — `launch_instance`, `wait_for_instance_running`, `register_target`, `terminate_instance` in `providers/aws.py`

### Changed
- **Image versions controlled by iblai-prod-images** — removed all hardcoded image tags from Ansible roles (DM, edX, MFE, postgres, SPA, supporting services). The CLI now rejects overrides; versions are pinned by the `iblai-images` package
- **Removed image tag prompts** — setup no longer asks for DM, edX, or SPA image tags. `SetupConfig` model no longer has image tag fields
- **Removed hardcoded MySQL 8.0.40** — was causing version mismatch crashes when AMI data was created with MySQL 8.4.0. The CLI's `default.yml` now provides the correct version

### Fixed
- **PostgreSQL password sync after secret rotation** — resetup and launch capture the current password before rotation and use it to ALTER USER after rotation
- **MySQL password sync after secret rotation** — same capture-before-rotate pattern for both root and openedx MySQL users
- **PostgreSQL data directory ownership** — resetup restores postgres data dir to uid 999 before restarting, preventing "Permission denied" errors after the recursive chown on /ibl
- **State base_domain update on resetup** — `iblai infra list` now shows the new domain after resetup
- **`destroy` command handles `provider="launch"`** — launch-created projects can be properly destroyed

## [1.2.3] — 2026-03-26

### Added
- Super admin credentials prompt — setup wizard asks for admin username (default `ibl_admin`), email, and password; creates superuser in both DM and LMS via Django shell in `final_steps` role
- Optional OpenAI API key prompt — when provided, creates a `GlobalCredential` entry in DM with `is_preferred=True`; skippable with blank input
- `UseMainLLMKey` configuration — `final_steps` role enables `use_main_key=True` for the `main` platform so tenants inherit the global LLM credential
- `openai_api_key`, `admin_username`, `admin_email`, `admin_password` fields on `SetupConfig` model
- `ibl_web` OAuth2 application created in LMS (public, password grant) — client ID used for `IBL_SPA.AUTH.IBL_OAUTH2_CLIENT_ID`
- CSRF exempt domain seeding — 24 platform subdomains added to `CsrfExemptDomain` in LMS for CORS support
- Unified API gateway enabled by default (`IBL_REVERSE_PROXY.ENABLE_UNIFIED_API_GATEWAY=true`)
- MFE image (`ibl-edx-mfe-pro:sumac.0.3.2`) and JWT auth (`ENABLE_JWT_AUTH=True`) set in `ibl_platform` role
- CORS enabled for edX (`IBL_EDX_CORS_HEADER.CORS_ORIGIN_ALLOW_ALL=true`)
- DM RBAC enabled (`IBL_DM.ENABLE_RBAC=true`, `IBL_DM.ENABLE_RBAC_SEEDING=true`)
- `IBL_DM.ALLOW_TENANTS_TO_USE_MAIN_LLM_CREDENTIALS=true` set before DM launch
- `ibl-edx-uwsgi` plugin ensured in `IBL_EDX.PLUGINS` via Python yaml (safe append)
- Full SPA configuration: `DEFAULT_APP_URL`, `ENVIRONMENT`, `SKIP_TEST`, `ENABLE_APP_SITE_ASSOCIATION`, `CANVAS_ADMIN_ONLY`, `STRIPE_ENABLED` with quoted boolean values written via Python yaml
- `ibl edx sync-with-manager --users` in `final_steps` role
- Seed commands in order: `seed_flows` → `seed_llm_registry` → `seed_base_mentors` → `seed_rbac_data`
- `ibl config save && ibl global-proxy reload` after SPA launches

### Fixed
- DM container verification now waits for the web endpoint to respond (up to 10 minutes) instead of only checking `docker ps` — catches crash-looping containers that still show as "Running"
- DM verification checks `RestartCount` and fails with actionable error (suggests `ibl dm migrate`) if container has restarted more than 3 times
- edX container verification also checks LMS `/heartbeat` endpoint readiness and restart count
- `GlobalCredential.value` stored as dict directly (not `json.dumps`) — `JSONField` auto-serializes; double-serializing caused 500 on admin page
- SPA quoted boolean values (`'true'`/`'false'`) written via Python yaml to avoid `ibl config save --set` quoting syntax errors
- `ibl-edx-uwsgi` plugin appended via Python yaml to avoid `ibl config printvalue` list parsing errors

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
