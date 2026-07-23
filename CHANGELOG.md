# Changelog

## [1.15.0] — 2026-07-23

### Added
- **Post-provision DNS instructions when no hosted zone is used.** Terraform only creates DNS records automatically on the Route53 + ACM (AWS) and managed-existing-zone (GCP) paths. On every other path — an uploaded certificate, HTTP-only, or simply no matching hosted zone in the account — the load balancer is created but DNS is left to the operator, and the results screen previously showed only the load balancer address in a table row with no guidance. The provisioning summary now spells out exactly which records to create: for external DNS it lists a record for every platform subdomain pointing at the load balancer (`CNAME` → ALB DNS name on AWS, `A` → load-balancer IP on GCP), also written to `dns-records.txt` in the workspace, and notes that setup should run only once they resolve. When the stack created a new GCP Cloud DNS zone it now prints the zone's nameservers to delegate at the registrar (these were emitted as a Terraform output but never displayed). Route53/managed paths get a one-line confirmation that records were created. Applies to `provision`, `provision-env`, and `launch` (all share the results renderer).

Test count: 757 passing.

## [1.14.0] — 2026-07-23

### Changed
- **Platform subdomain set updated and documented.** Two backend data-API subdomains (`mentor.data`, `web.data`) were removed, and two application subdomains were renamed (`mentorai` → `os`, `skillsai` → `lms`). The change is applied consistently across every layer that references the subdomain set: DNS A-records and TLS certificate SANs (AWS single-server, AWS multi-server, and GCP templates), the `IBL_SUBDOMAINS` list in `models.py`, the application config that serves those endpoints, and the CSRF-exempt domain list. The full set is now listed in the README under "What gets created" and in `docs/architecture.md`.
- **Behavior change:** environments created before this release will reconcile to the new DNS records and certificate on the next `apply`, and the two renamed application endpoints require a re-setup to take effect.

Test count: 750 passing.

## [1.13.0] — 2026-07-03

### Added
- **GCP provider — single-server deployments on Google Cloud.** `iblai infra provision` now starts by asking which cloud; picking GCP walks the same wizard against a GCP project (project ID, region/zone, ADC or service-account-key auth — validated on the spot). The Terraform stack (`templates/gcp/single-server/`) provisions: a VPC with one regional subnet; firewall rules (SSH restricted to the operator IP, health checks from Google's probe ranges); a Compute Engine VM (Ubuntu 22.04, SSH keys via instance metadata, startup script mirroring the AWS user-data); an unmanaged instance group behind a **global external Application Load Balancer** (`EXTERNAL_MANAGED`) with a static IP, HTTP→HTTPS redirect at the edge, and a health check that probes the LMS heartbeat with a `learn.<domain>` Host header (GCP requires a literal 200; probing `/` hits the platform nginx catch-all's 301 and serves 503 "no healthy upstream" for everything — verified live and designed out); a **Google-managed SSL certificate** covering the base domain + all platform subdomains (validates asynchronously — the CLI says so and prints nameservers to delegate when it created the zone); and Cloud DNS A-records (existing zone auto-detected, or created via `CREATE_DNS_ZONE=true`). Multi-server and call-server remain AWS-only.
- **`PROVIDER=gcp` non-interactive path** — `iblai infra provision-env -f .env` dispatches to a GCP builder (`gcp_env_provision.py`) with zone auto-detection (`CERT_METHOD=auto`), DNS-conflict cleanup, and the same validation UX as AWS. New `.env.provision.gcp.example` documents every key.
- **`providers/gcp.py`** — GCP SDK helpers mirroring the AWS provider surface: credential validation (ADC or service-account key), Cloud DNS zone discovery, conflicting-record find/delete, and read-only permission probes. Google libraries ship as an optional extra: `uv sync --extra gcp` (AWS-only installs are unaffected; GCP entry points fail with a clear install hint when the extra is missing).
- **`iblai infra permissions --provider gcp [--check --project <ID>]`** — prints the required roles (`compute.admin`, `dns.admin`, `iam.serviceAccountUser`) + APIs (`compute`, `dns`) with a copy-paste `gcloud services enable` line, and `--check` probes them against live credentials.
- **`CloudProvider` axis on `InfraConfig`** — `cloud: aws|gcp` (default `aws`; existing `state.json` files deserialize unchanged) plus `GCPCredentials`; `credentials` is now optional with a per-cloud validator. `TerraformRunner` dispatches template tree, tfvars, and subprocess env (`GOOGLE_*`/`CLOUDSDK_*`) on it. `status`/`list` display the cloud.
- **`docs/GCP.md`** — step-by-step GCP guide for new operators: prerequisites (project, APIs, auth), interactive + `.env` provisioning, DNS-delegation and async-certificate behavior, the AWS S3 storage convention, teardown, and a troubleshooting table.
- **`iblai-cli-ops` version auto-resolved from the prod-images pin.** `iblai-prod-images`' `pyproject.toml` pins its `ibl-cli` dependency via `[tool.uv.sources]`, but uv ignores that table on git-URL installs — so the Ansible role must force-install `iblai-cli-ops` at an explicit tag, and operators previously had to *know* the right one. Setup/resetup now ask a single version question (**prod-images release tag**, default `main` — previously never prompted) and resolve the matching cli-ops tag from the pin via the GitHub API using the operator's PAT (`env_utils.resolve_pinned_cli_ops_tag`, monorepo-subdir aware), prompting manually only when the pin is unreadable. Same resolution in `setup-env` (`CLI_OPS_RELEASE_TAG` now optional) and `launch`/`launch-env` (`--cli-tag`/`CLI_TAG` now optional).

### Changed
- **Stale `cli_ops_release_tag` default (`3.19.0`) removed from every input layer** (model default, prompts, launch flags, `.env` parsing). **Behavior change:** flows that previously fell back to installing `iblai-cli-ops@3.19.0` when no tag was given now install the version pinned by the selected prod-images release (or `main` when the pin can't be read).
- **GCP object storage stays on AWS S3 by design** — the GCP stack provisions no buckets; the platform keeps its existing S3 integration. Operators pre-create the three buckets on the standard naming convention and supply AWS credentials at the setup step (documented in `docs/GCP.md` and the review/summary panels).
- **README restructured** — five-step Quick start, cloud-split prerequisites, GCP threaded through every relevant section, refreshed project structure.

### Fixed
- **`iblai infra setup <name>` crashed on GCP-provisioned states** (`AttributeError: 'NoneType' object has no attribute 'region'`) — the credentials step assumed every state carries AWS provisioning credentials to offer for reuse. GCP states (no AWS credential block) now skip the reuse offer and prompt for fresh S3/ECR keys; same guard applied to the `setup-env` region derivation.
- **`iblai infra waf` on a non-AWS stack** now rejects with a clear "AWS WAFv2 only" error instead of silently no-oping, and GCP stacks are excluded from the eligible-project picker.
- **GCP `provision-env` surfaces model-validation failures** (e.g. the 100 GB single-server volume floor) as clean one-line errors instead of a raw traceback.

Test count: 749 passing.

## [1.12.0] — 2026-06-26

### Fixed
- **TimescaleDB extension now created during DM bootstrap** — the DM postgres image ships TimescaleDB preloaded (`shared_preload_libraries=timescaledb`), but the flow only ever ran `CREATE EXTENSION vector`; `timescaledb` was never created, so `setup_timescale_views --full-setup` (which ran under `ignore_errors: true`) silently degraded and analytics hypertables were never built. The `ibl_dm` role now runs `CREATE EXTENSION IF NOT EXISTS timescaledb` right after pgvector (idempotent, postgres-superuser). Confirmed against a field environment whose DB had only `plpgsql` + `vector`.
- **Microsoft SSO now uses the standard `azuread-oauth2` backend** — the `microsoft_sso_config` role derived the provider `backend_name` (and the `/auth/login` + `/auth/complete` SSO URLs) from `platform_name` (e.g. `main-oauth2`), which is not a registered Azure AD social-auth backend, so sign-in never completed and operators had to hand-fix the LMS `OAuth2ProviderConfig`. All backend references — `OAuth2ProviderConfig.backend_name`, the `IBL_EDX.IBL_EDX_BASE_OAUTH_SSO_BACKEND` block (`IBL_OAUTH_SSO_NAME` / `TRACKED_PROVIDERS`), `other_settings.backend_uri`, and `IBL_SPA.AUTH.IBL_DIRECT_SSO_URL` — now use the constant `azuread-oauth2`, matching the provider slug and the Azure-registered redirect URI. `other_settings.platform_key` still carries the tenant `platform_name`.
- **`setup_timescale_views` failures now surface** — replaced the blanket `ignore_errors: true` on the data_seeding "Setup TimescaleDB views" task with a `register` + `failed_when` (fails on a genuine Python traceback, tolerates benign non-zero exits / idempotent re-runs) and a debug that prints the command output.

### Added
- **`IBL_DM.ENABLE_RBAC_GROUP_MANAGEMENT=true` set by default** — added to the `ibl_platform` "Enable DM RBAC" block alongside the existing `ENABLE_RBAC` / `ENABLE_RBAC_SEEDING` / `ENABLE_TIMESCALEDB` defaults (previously had to be set by hand).
- **Azure AD redirect-URI prerequisite documented** — `.env.setup.example` and the `microsoft_sso_config` end-of-run confirmation now spell out the exact redirect URI the client must register in their Azure AD app: `https://learn.<BASE_DOMAIN>/auth/complete/azuread-oauth2/`.

## [1.11.0] — 2026-06-01

### Added
- **Optional AWS WAFv2 on the single-server ALB** — opt-in at provision time via the wizard (a new sub-step of "Domain & Certificates" — default off), `provision-env` (`ENABLE_WAF=true` + `WAF_ALLOWED_IPS=…` in the `.env`), or `launch` / `launch-env` (`--enable-waf` + `--waf-allowed-ips`, or matching env keys). Attaches a Regional WAFv2 Web ACL to the ALB with rules tuned for ibl.ai's subdomain layout: admin-only allow rules (gated on an operator IP allowlist) for DM Swagger UI, edX Studio (CMS), Django `/admin/`, and DM `/data`; public allow rule for `learn.<base>` and `apps.learn.<base>`; six AWS managed rule groups (IpReputation, KnownBadInputs, Common, SQLi, WordPress, PHP); and a path-traversal block for `.git` / `.env` / `.htaccess` / `.svn` / `.hg` / `.DS_Store`. Total estimated WCU ≈ 1355 (under the 1500 default). Allowlist accepts both bare IPs (auto-suffixed `/32`) and CIDR.
- **`iblai infra waf` post-provision subgroup** — toggle WAFv2 on an already-provisioned single-server stack without re-running the wizard. Four commands: `enable [<name>]` (interactive; on a project that already has WAF on, warns and prompts to update the allowlist with current IPs pre-filled), `enable-env [<name>] -f .env` (non-interactive, reads `WAF_ALLOWED_IPS`), `disable <name> [--yes]` (Y/N confirm by default; `--yes` for CI; removes the Web ACL + IPSet + association, leaves the ALB intact), `status [<name>]` (table of all WAF-eligible projects with no arg, detail panel with one). Rejects multi-server, call-server, bootstrap, and non-`created` projects up-front with a clear error. Subgroup module lives at `src/iblai_infra/features/waf.py`; the `features/` package docstring documents the pattern for the next optional-feature toggles (SMTP, Stripe, SSO providers) so they can drop in with the same `enable / enable-env / disable / status` shape.
- **`TerraformRunner.reapply()`** — shared helper for re-running Terraform on an existing workspace with the latest `state.config`. Re-copies `.tf` templates (so template fixes propagate), reads the existing `terraform.tfvars` to pin the original `bucket_suffix` (prevents accidental S3 bucket renames once the date-stamp window has rolled over), regenerates the rest of tfvars from `state.config`, then runs `init` → `plan` → `apply`. Returns parsed outputs. Used by both the new `iblai infra waf <action>` commands and the refactored `iblai infra retry`.
- **WAFv2 entries in `REQUIRED_IAM_POLICY`** + a `wafv2:ListWebACLs` smoke check in `check_permissions()` so `iblai infra permissions [--check]` surfaces WAF readiness up-front instead of failing mid-apply.

### Changed
- **`_generate_tfvars(self, bucket_suffix: str | None = None)`** — accepts an optional pinned suffix. When `None` (today's default for first `setup()`), resolves the suffix from AWS as before. When provided, uses the pinned value. Load-bearing change for the new `reapply()` helper.
- **`iblai infra retry`** now uses `TerraformRunner.reapply()` instead of inlining template-copy + init/plan/apply, removing a drift point with the new WAF subgroup. Behaviour is unchanged for operators: the existing failure-recovery guards and Route 53 CNAME conflict cleanup still run.

## [1.10.2] — 2026-05-20

### Added
- **`docs/develoment.md`** — developer guide for testing in-progress iblai apps against a local `ibl edx` (Tutor) stack via a `docker-compose.override.yml` at `/ibl/app/ibl-edx/ibl-edx-pro/env/local/`. Covers: cloning the app branch into `~/github/<app-name>`, the **Sumac** mount path (`/openedx/edx-platform/requirements/<app>/src/<pkg>`, unversioned) vs. the **Olive** mount path (`/openedx/requirements/<app>-<version>/src/<pkg>`, version-pinned — discoverable via `ibl tutor local run lms bash` + `pip list | grep <app>`), the first-time apply (`ibl edx stop && ibl edx start -d`) vs. iteration loop (`ibl tutor local restart lms cms`), and selective mounting across `lms` / `cms` / `lms-worker` / `cms-worker`. Resolves [#1770](https://github.com/iblai/iblai-infra-cli/issues/1770).

## [1.10.1] — 2026-05-20

### Fixed
- **SPA-ready wait budget bumped from `10×15s` (150s) to `30×15s` (450s)** across all six SPA wait tasks (Auth / Mentor / Skills in both `ibl_spa` — initial setup — and `ibl_launch_services` — AMI launch / launch-env). Root cause: the SPA container images do NOT ship with `node_modules` baked in, so `pnpm install` runs inside the container on first boot (~80–120s observed in the field), then Next.js starts. Combined with `docker compose pull` + image-extraction overhead, cold-start can comfortably exceed the older 150s budget on a slower instance or marginal network — the wait task gives up, the playbook bails, but the SPA finishes installing seconds later and serves `200`. False-negative failure. The new 450s budget covers the worst-case cold-start with comfortable headroom without making real failures take an unreasonable amount of time to surface. Each task gets an inline comment explaining the budget so a future maintainer doesn't shrink it without re-tracing this. A future image-level prebake of `node_modules` in `iblai-prod-images` would make this faster end-to-end, but the ansible wait is now robust to the current image shape regardless.

## [1.10.0] — 2026-05-20

### Added
- **`ibl_tenant_platform` ansible role** — launches a tenant `Platform` (Platform + admin User + UserPlatformLink) via `run_launch_steps` when `PLATFORM_NAME` is set to anything other than `main`. NOT a raw `Platform.objects.create()` — the state machine fires every after_launch signal (default apps, edX hooks, UserPlatformLink flags). Wired into both `playbook.yml` (setup / setup-env) and `launch_playbook.yml` (launch / launch-env). Skips + logs on re-runs when the tenant already exists. Also writes `PLATFORM_NAME=<KEY>` (uppercase) at the root of `/ibl/config.yml` and enforces `Platform.show_paywall=False` + `Platform.is_advertising=False` as defense in depth. Surfaces the generated admin password via the `IBLAI_FIXTURE_OUTPUT` pipeline — printed once after the Rich Live display tears down, never persisted to disk.
- **Microsoft SSO writes `IBL_SPA.AUTH`** — `microsoft_sso_config` now also patches `EXTERNAL_IDP_LOGOUT_URL` and `IBL_DIRECT_SSO_URL` (using `microsoft_sso_tenant_id`, falling back to `common`), then restarts the Auth + Mentor SPAs so the new auth flow takes effect.
- **`INSTANCE_RAM_GB` helper + 32 GB memory warning** — non-blocking heads-up suggesting 64 GB (e.g. `m5.4xlarge` / `r5.2xlarge`) when the operator picks a 32 GB instance. Always shown in the interactive provision wizard and `provision-env`; conditional in `launch` / `launch-env` (only when AI is enabled).
- **Final `ibl global-proxy reload`** added as `post_tasks` in both `playbook.yml` and `launch_playbook.yml`, so any nginx state touched by SSO roles (edX restarts in `google_sso_config` / `microsoft_sso_config`) is reloaded before the playbook exits.
- **`RESERVED_ADMIN_USERNAMES` + `RESERVED_PLATFORM_NAMES`** — `models.py` constants, surfaced via `is_reserved_admin_username()` and `is_reserved_platform_name()` helpers and an `InfraConfig` model_validator.

### Changed
- **Stripe billing UI off by default** — `IBL_SPA.MENTOR.STRIPE_ENABLED=false` and `IBL_SPA.MENTOR.ENABLE_ADVERTISING=false` are now written unconditionally by `ibl_spa` (fresh installs) and `ibl_launch_services` (AMI launches). **Behavior change:** Stripe-using deployments must explicitly flip `IBL_SPA.MENTOR.STRIPE_ENABLED` back to `'true'` post-setup. The previous "always on" SPA flag surfaced billing UI even when Stripe wasn't actually configured.
- **100 GB minimum root volume for single / multi server** — enforced by Pydantic (`InfraConfig` model_validator gated on `DeploymentType.SINGLE`, plus `MultiServerConfig.validate_volume_sizes`) and matching interactive + CLI + .env input checks. **Behavior change:** values below 100 GB are now rejected upfront. Default `ComputeConfig.volume_size` bumped 50 → 100. Call-server unchanged (LiveKit only needs ~40 GB).
- **`ADMIN_USERNAME=ibl_admin` rejected at every input layer** — reserved for the SPA OAuth Application owner the platform itself maintains. New default suggestion is `platform_admin`. Interactive prompts, `.env` parsers, and `--admin-username` flag all reject `ibl_admin` with a clear reserved-name error. **Behavior change:** scripted deploys passing `ADMIN_USERNAME=ibl_admin` must rename.
- **`PLATFORM_NAME=main` rejected as an explicit input** — unset / blank silently resolves to `main` (preserving SSO `backend_name=main-oauth2` and skipping the tenant launcher). **Behavior change:** scripted deploys passing `PLATFORM_NAME=main` should drop the line.
- **README** — refreshed against current playbook (16 roles, phase-grouped table), three deployment topologies, sizing guidance, tenant launcher, reserved-name rules. -50 lines net.

### Removed
- **All references to a specific canonical-client name** from comments, docstrings, prompt instructions, error hints, and example .env files. Placeholders: `<client>` for monorepo org names, `acme` for tenant-key examples.

### Fixed
- **Slow `_test_ssh()` retry-path tests** — five tests in `tests/ansible/test_runner.py` exercise the SSH-retry exhaust path (10 retries × 15 s sleep). They now mock `time.sleep` alongside the existing `subprocess.run` mock, cutting ~11 minutes off the full suite. Test count: 562 passing in ~1.3 s.

## [1.7.0] — 2026-05-06

### Added
- **Optional Microsoft (Azure AD) SSO setup** via a new `microsoft_sso_config` ansible role. When the operator opts in (Y/N prompt during `iblai infra setup`, or `--microsoft-sso-client-id` for `iblai infra launch`), the role does two things: (1) patches `IBL_EDX.IBL_EDX_BASE_OAUTH_SSO_BACKEND` in `/ibl/config.yml` via direct Python yaml manipulation (since the block has nested dicts + a list, which `ibl config save --set` cannot round-trip), runs `ibl config save`, and bounces edX so the new Django settings take effect; (2) creates an `OAuth2ProviderConfig` row on the LMS for the `azuread-oauth2` slug, with `backend_name` derived from `platform_name`, `sync_learner_profile_data=True`, and a Microsoft-specific `other_settings` JSON carrying `platform_key`, `backend_uri`, and the Azure AD federated `logout_url`. Idempotent — the heavy `ibl config save` + edX restart only run when the config block actually differs from the desired state, and the `OAuth2ProviderConfig` save uses `current(slug)` to skip when the latest revision already matches
- **`SetupConfig.platform_name`** — top-level field (defaults to `main`), prompted at the start of Step 2 (Platform Configuration). Lowercased + stripped on input. Drives both the SSO `backend_name` (`<platform_name>-oauth2`) and the `other_settings.platform_key`. Always populated; the SSO roles read it whether or not their feature flag is enabled
- **`SetupConfig.microsoft_sso_*` fields** — `microsoft_sso_enabled`, `microsoft_sso_client_id`, `microsoft_sso_client_secret`, `microsoft_sso_tenant_id`, `microsoft_sso_organization`. Client secret is `Field(exclude=True)` so it never lands in `state.json`
- **Launch CLI flags** — `--platform-name` (default `main`), `--microsoft-sso-client-id` (the trigger), `--microsoft-sso-client-secret`, `--microsoft-sso-tenant-id`, `--microsoft-sso-organization`. Same env-var pattern as Stripe / SMTP / Google SSO

## [1.6.0] — 2026-05-06

### Added
- **Optional Google SSO setup** via a new `google_sso_config` ansible role. When the operator opts in (Y/N prompt during `iblai infra setup`, or `--google-sso-client-id` for `iblai infra launch`), the role creates an `OAuth2ProviderConfig` row on the LMS for the python-social-auth `google-oauth2` backend, bound to `learn.<base_domain>`. Captures Client ID, Client Secret (no-echo password prompt), and an optional organization short_name. Secret is `Field(exclude=True)` on `SetupConfig` so it never lands in `state.json` and rides extra-vars to ansible at run time only. Idempotent — re-runs check the latest revision and skip the save when values match
- **`SetupConfig.google_sso_*` fields** — `google_sso_enabled`, `google_sso_client_id`, `google_sso_client_secret`, `google_sso_organization`
- **Launch CLI flags** — `--google-sso-client-id` (the trigger), `--google-sso-client-secret`, `--google-sso-organization`. Same env-var pattern as Stripe/SMTP

## [1.5.4] — 2026-05-05

### Changed
- **Pin direct runtime dependencies to currently running freeze versions for issue #1633** — updated `pyproject.toml` to exact pins for `ansible-core==2.19.9`, `boto3==1.42.97`, `pydantic==2.13.3`, `questionary==2.1.1`, `rich==15.0.0`, and `typer==0.25.0`, then regenerated `uv.lock` so lock and install metadata are aligned to the same tested dependency set.

## [1.5.3] — 2026-05-01

### Fixed
- **Fresh-provision LMS crash loop** (`ibl_platform` role). Newer `iblai-cli-ops` (5.x+) ships an import-time check in `ibl-edx-sso-backend-app/constants.py` that rejects a missing or placeholder `IBL_FERNET_KEY`. Fresh bootstrap user_data writes a placeholder, so LMS/CMS crash-loop with `ImproperlyConfigured` and the "Wait for LMS to be ready" task times out at 40 retries. Ports the same fernet guard from `ibl_service_update` to `ibl_platform`: reads the key, rotates only when empty/`BAD_FERNET_KEY`/the known template default, leaves real keys untouched. Idempotent

## [1.5.2] — 2026-05-01

### Fixed
- **`ibl-cli` resolves to PyPI's wrong package on fresh provisions** (`ibl_cli_ops` role). When `iblai-prod-images` was installed via `uv pip install` of a git URL, uv silently ignored its `[tool.uv.sources]` (project-only) and fell through to PyPI's unrelated `ibl-cli==2.0.11`, which is missing `ibl/templates/config/defaults.yml`. `ibl --help` then crashed in the very next "Verify ibl CLI is available" task. The role now does a second explicit `uv pip install ... --reinstall` of `iblai-cli-ops` at the operator-specified repo+tag (honoring `cli_ops_subdir` for monorepo layouts), overriding the wrong transitive dependency. Applies to both `single-server` and `call-server` templates

## [1.5.1] — 2026-04-30

### Fixed
- **Private-access gate fires on `provision` → "Run platform setup now?"** path. The post-provision shortcut (`app._offer_setup`) bypassed `_confirm_private_access_or_abort()` because it never reached `_run_setup_provisioned`/`_run_setup_interactive`/`_run_resetup`. Operators going from `iblai infra provision` straight into setup now see the same prerequisites notice + Y/N confirm before any prompts collect input

## [1.5.0] — 2026-04-30

### Added
- **Monorepo subdirectory installs** — `--cli-ops-repo` / `--prod-images-repo` (and the matching setup prompts) now accept a `repo/subdir` path, e.g. `<client>-iblai-infra-ops/<client>-iblai-prod-images`. The ansible role appends `&subdirectory=<subdir>` to the install URL so a single client monorepo can host both `iblai-cli-ops` and the prod-images package
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
