# iblai-infra — Claude Development Guide

## Overview

Interactive CLI tool for provisioning ibl.ai platform infrastructure on AWS. Built with Python, Typer, Rich, and questionary. Uses Terraform for resource management.

**Command pattern:** `iblai infra <command>`

## Project Structure

```
iblai-infra/
├── pyproject.toml                          # uv/hatch config, dynamic version, entry point: iblai = iblai_infra.cli:app
├── src/iblai_infra/
│   ├── __init__.py                         # __version__ = "1.2.3"
│   ├── __main__.py                         # python -m iblai_infra support
│   ├── cli.py                              # Typer app: root `iblai` + `infra` subgroup + `ingress` subgroup + landing screen menu
│   ├── app.py                              # Wizard orchestrator (5-step flow)
│   ├── models.py                           # Pydantic models — contract between wizard & Terraform, ingress registry
│   ├── ui.py                               # Rich console, ibl.ai branding, progress helpers
│   ├── prompts/
│   │   ├── credentials.py                  # Step 1: AWS auth (profile/keys/env), show_step param
│   │   ├── infrastructure.py               # Steps 2-3: project, compute, network, SSH
│   │   ├── dns_certs.py                    # Step 4: domain, Route53, certificates
│   │   └── review.py                       # Step 5: summary + confirm
│   ├── providers/
│   │   └── aws.py                          # AWS helpers: STS validation, Route53, key pairs, IP detect, permission checks
│   ├── terraform/
│   │   ├── runner.py                       # TerraformRunner: setup/init/plan/apply/destroy with JSON streaming
│   │   ├── state.py                        # ProjectState + session + ingress registry + lock backends (~/.iblai-infra/)
│   │   ├── templates/aws/single-server/
│   │   │   ├── main.tf                     # VPC, subnets, ALB, EC2, S3, certs, DNS
│   │   │   ├── variables.tf                # All Terraform variables
│   │   │   ├── outputs.tf                  # IPs, ALB DNS, S3 buckets, SSH command
│   │   │   └── user_data.sh               # Docker, AWS CLI, UFW, systemd setup
│   │   └── templates/aws/multi-server/
│   │       ├── main.tf                     # VPC (4 subnet tiers), NAT, ALB, N×app EC2, 1×services EC2, optional RDS/Redis, EFS, S3, certs, DNS
│   │       ├── variables.tf                # All multi-server variables (compute, managed services, secrets marked sensitive)
│   │       ├── outputs.tf                  # App server IPs (list), services IP, RDS/Redis endpoints, backward-compat singular outputs
│   │       ├── user_data_app.sh            # App server bootstrap (Docker, AWS CLI, NFS, UFW)
│   │       └── user_data_services.sh       # Services server bootstrap (Docker, AWS CLI, internal UFW)
│   └── ansible/
│       ├── __init__.py
│       ├── runner.py                       # AnsibleRunner: preflight, SSH test, inventory, playbook execution
│       └── templates/single-server/        # Ansible playbook + roles (docker, awscli, python, ibl_cli_ops, ibl_platform, ibl_dm, ibl_edx, ibl_spa, final_steps)
├── tests/
│   ├── conftest.py                         # Shared fixtures (aws_credentials, infra_config, project_state, workspace_root)
│   ├── test_models.py                      # Pydantic model validation, all enum combos, edge cases
│   ├── test_state.py                       # State persistence, session save/load/clear
│   ├── test_cli.py                         # CLI commands, _run_setup branches, _resolve_credentials
│   ├── test_app.py                         # Wizard orchestrator (_show_workspace, _show_results, _offer_setup)
│   ├── test_ui.py                          # Rich UI helpers, banner, step_header, summary_panel
│   ├── providers/test_aws.py               # AWS helpers: sessions, credentials, hosted zones, key pairs, permissions
│   ├── terraform/test_runner.py            # TerraformRunner: tfvars generation, event parsing, labels
│   ├── ansible/test_runner.py              # AnsibleRunner: role extraction, failure detection, preflight, SSH test
│   └── prompts/
│       ├── test_validators.py              # IP, CIDR, domain validation
│       ├── test_review.py                  # Review prompt with all SSH × cert × env combinations
│       └── test_setup.py                   # Setup prompt flow, SSH key resolution, key permissions
```

## Architecture

### CLI Structure (Typer)

- **Root app** (`iblai`): `--version`, `--help`
- **Subgroup** (`iblai infra`): `provision`, `retry <name>`, `setup [name]`, `resetup <name>`, `launch`, `destroy <name>`, `status <name>`, `list`, `permissions`, `auth`
- **Nested subgroup** (`iblai infra ingress`): `add`, `remove`, `list`, `configure`, `status`, `claim`, `release`
- Running `iblai infra` with no arguments shows branded landing screen with interactive arrow-key menu
- The landing screen menu uses `questionary.select()` to dispatch to commands directly
- When launching provision from the menu, calls `run_provision_wizard(show_banner=False)` to avoid double banner
- Entry point in `pyproject.toml`: `iblai = "iblai_infra.cli:app"`

### Session Persistence

- Credentials saved to `~/.iblai-infra/session.json` after any successful authentication
- Stores: method, profile, region, account_id, arn (never secret keys)
- `load_session()` validates saved credentials via STS on load; clears if invalid
- `clear_session()` removes the session file
- `iblai infra auth` clears session and re-prompts for credentials
- Functions live in `terraform/state.py`: `save_session()`, `load_session()`, `clear_session()`

### Credential Resolution (`_resolve_credentials()` in cli.py)

Shared helper used by any command needing AWS auth. Resolution order:
1. Explicit `--profile` flag (if passed)
2. Saved session from `~/.iblai-infra/session.json`
3. **Interactive wizard** — launches the full credentials prompt

No silent auto-detection from `~/.aws/` or environment variables. The user always explicitly chooses their auth method.

### Wizard Flow (app.py)

5 interactive steps, each in its own prompt module:
1. **Credentials** — AWS profile / access keys / env vars, validated via STS (`show_step=True`)
2. **Project & Compute** — name, environment (dev/staging/prod), deployment type (single/multi-server), then either single-server compute or multi-server config
3. **Network & SSH** — VPC CIDR, VPN IP (auto-detected), SSH key (generate/import/AWS keypair)
4. **DNS & Certs** — domain, Route53 zone detection, cert method (ACM/upload/none)
5. **Review** — full summary panel (multi-server shows server counts, managed services, subnet tiers), confirm

After confirmation: `TerraformRunner.setup()` → `init()` → `plan()` → `apply()` → show results.

### Setup Command

Unified `iblai infra setup [name]` command with two paths:
- **With name:** loads `ProjectState` from Terraform workspace, auto-populates IP/domain/SSH from state, runs `prompt_setup(state)` → `AnsibleRunner`
- **Without name:** prompts for everything (project name, IP, SSH key, domain, creds) via `prompt_bootstrap()`, creates synthetic `ProjectState` with `provider="bootstrap"`, runs same `AnsibleRunner`

Both paths share `_confirm_and_run()` for the review summary → confirm → ansible execution flow. Bootstrap projects use `provider="bootstrap"` to distinguish from Terraform-provisioned ones (affects `destroy` behavior — no Terraform teardown).

### Retry Command

`iblai infra retry <name>` retries a failed Terraform provisioning. Reuses the existing workspace, re-copies `.tf` templates (to pick up fixes), preserves `terraform.tfvars`, and checks for conflicting CNAME records before running `init` → `plan` → `apply`.

`run_provision_wizard(show_banner: bool = True)` — controls whether the ASCII banner is shown (set to `False` when launched from the landing screen menu).

### Resetup Command

`iblai infra resetup <name>` re-configures an existing environment with a new domain and fresh secrets. No Terraform runs — only Ansible.

**Guards:** project must exist, status `"created"`, instance IP in outputs, `ansible-playbook` installed.

**3-step interactive prompt** (`prompt_resetup` in `prompts/setup.py`):
1. **SSH Access** — resolves private key from state or prompts
2. **Platform Configuration** — domain selection (ingress picker if entries exist, otherwise free-text), CLI ops release tag
3. **Credentials** — AWS keys + GitHub token

Returns `SetupConfig` with `is_resetup=True`. Does **not** prompt for image tags, edX version, or admin credentials.

**What `is_resetup=True` triggers in Ansible** (`ibl_platform/tasks/main.yml`):
1. Restore postgres data dir ownership (uid 999) → restart postgres → wait for ready
2. Capture current MySQL root password
3. `ibl config rotate-secrets -f --include-auth` — regenerate all secrets
4. Sync new postgres password (`ALTER USER` from config.yml)
5. Sync new MySQL passwords (root + openedx users, using old→new password)

All other tasks (domain config, proxy, ECR login, edX settings) run unconditionally.

**Domain update flow** — when `resetup` changes the base domain:
- `config.yml`: `BASE_DOMAIN` updated via `ibl config save`
- `auth.yml`: OAuth/OIDC redirect URIs rewritten by `final_steps` role
- Nginx proxy: `ibl global-proxy launch-without-security` regenerates all server_name directives
- DB registrations: `final_steps` re-creates oauth/oidc clients with new domain URLs

### Launch Command

`iblai infra launch` — non-interactive, CI/CD-friendly command that provisions infrastructure from a pre-built AMI and configures the platform.

Accepts `--domain <domain>` or `--ingress <name>` (resolved from the ingress registry) for domain specification. All other parameters passed via CLI flags.

**Multi-server flags:** `--deployment-type multi-server`, `--app-server-count N`, `--services-instance-type`, `--services-volume-size`, `--enable-mysql`, `--enable-postgres`, `--enable-redis`. When `--deployment-type multi-server`, builds `MultiServerConfig` with auto-generated DB/Redis passwords.

**Call-server flags:** `--deployment-type call-server`, `--enable-sip/--no-sip`. Reuses `--instance-type` (default `t3.large`) and `--volume-size` (default 40). Skips admin-email/password validation since LiveKit has no admin user. Uses isolated `10.1.0.0/16` VPC and `call_playbook.yml` Ansible playbook with 5 roles (no edX, no DM, no SPAs). `--domain` should be the **parent** domain (e.g. `stg1.iblai.org`), not `call.stg1.iblai.org` — `ibl call` auto-prepends the `call.` prefix itself.

**Flow:** builds `InfraConfig` + `ProjectState` → `TerraformRunner` (provisions VPC/ALB/EC2/certs/DNS) → `AnsibleRunner` with `LAUNCH_ROLE_LABELS` (4 roles: cli_ops, launch config, service restart, final steps).

Sets `state.provider = "launch"` to distinguish from interactive provisioning.

### Provision-Env Command

`iblai infra provision-env -f .env` — non-interactive counterpart to the `provision` wizard. Single-server only, no AMI required. Reads every answer from a `.env` file and runs Terraform end-to-end (no Ansible — operator follows up with `iblai infra setup <name>`).

**Schema** (`.env.provision.example` is the source of truth). Required: `AWS_ACCESS_KEY_ID`+`AWS_SECRET_ACCESS_KEY` (or `AWS_PROFILE`), `PROJECT_NAME`, `DOMAIN`, `VPN_IP` (`auto` → uses `detect_current_ip()`). Optional with sane defaults: `AWS_DEFAULT_REGION`, `ENVIRONMENT` (dev/staging/prod), `INSTANCE_TYPE`, `VOLUME_SIZE`/`VOLUME_TYPE`, `VPC_CIDR`, `SSH_KEY_METHOD` (`generate`/`existing_file`/`aws_keypair`), `CERT_METHOD` (`auto`/`acm`/`upload`/`none`), `HOSTED_ZONE_ID`, `AUTO_DELETE_CONFLICTING_DNS` (default `true`).

**Implementation:** `src/iblai_infra/env_provision.py::build_infra_config_from_env(env, *, auto_delete_cnames)` — pure builder that validates, resolves AWS creds via STS, runs `find_conflicting_records` + `delete_route53_records` for CNAME conflicts when ACM is in use, then returns an `InfraConfig`. The CLI wrapper at `cli.py::provision_env` plumbs that into `TerraformRunner`. Shared helpers (`load_env_file`, `mask`, `parse_bool`) live in `src/iblai_infra/env_utils.py`. Sets `state.provider = "provision-env"`.

**Multi-server / call-server are explicitly rejected** with a hint pointing at the wizard — keeps the schema small and the failure mode obvious.

### Setup-Env Command

`iblai infra setup-env [<name>] -f .env` — non-interactive Ansible bootstrap from a `.env` file. Single-server only (multi/call rejected upstream). Two modes:

- **Provisioned-name:** `setup-env kapsix -f .env` — loads `ProjectState`, derives `target_host` / `ssh_private_key_path` / `base_domain` / `aws_default_region` from it. `.env` only carries credentials, image tags, admin user, optional integrations.
- **Free-standing:** `setup-env -f .env` (no name) — builds a synthetic `ProjectState` with `provider="bootstrap"` (matching `_run_setup_interactive`). `.env` must include `PROJECT_NAME`, `TARGET_HOST`, `SSH_PRIVATE_KEY_PATH`, `BASE_DOMAIN`.

**Schema** (`.env.setup.example` is the source of truth). Always required: AWS keys, `GIT_TOKEN` (or `GIT_ACCESS_TOKEN`), `ADMIN_USERNAME`/`ADMIN_EMAIL`/`ADMIN_PASSWORD`. Free-standing additionally needs the four "where to deploy" fields. Optional integrations follow the same trigger pattern as `iblai infra launch` — SMTP enabled when `SMTP_HOST` set, Stripe when `STRIPE_SECRET_KEY` set, Google SSO when `GOOGLE_SSO_CLIENT_ID` set, Microsoft SSO when `MICROSOFT_SSO_CLIENT_ID` set.

**Implementation:** `src/iblai_infra/env_setup.py` — `build_setup_config_from_env(env, *, state)` returns a `SetupConfig`; `build_bootstrap_state_from_env(env)` synthesises the ProjectState for free-standing mode. CLI wrapper at `cli.py::setup_env` shows `ui.private_access_notice()` as an informational banner (no confirmation), then runs `AnsibleRunner.preflight()` → `setup()` → `run()` with the default single-server playbook + `ROLE_LABELS`. Reuses `validate_key_permissions` (promoted from `_validate_key_permissions` in `prompts/setup.py`) to auto-fix SSH-key permissions to `0o600`.

### Service Update Command

`iblai infra service-update` — updates container images and restarts services without infrastructure changes or secret rotation. Two modes:

**`--host` mode:** Updates an existing server directly (Ansible only).
**`--ami-id` mode:** Launches EC2 from AMI via boto3, runs Ansible service update, registers in ALB target group.

**Ansible flow (`service_update_playbook.yml`, 2 roles):**
1. **`ibl_cli_ops`** — installs `iblai-images[sumac]` from `iblai/iblai-prod-images@{prod_images_tag}` (default: main)
2. **`ibl_service_update`** — the hardened service restart sequence:
   - Restore postgres data dir ownership to 999:999
   - ECR login (uses server's existing AWS creds)
   - Config save (platform + tutor — regenerates compose files)
   - Ensure edX running (`ibl edx start -d`) + wait for LMS health
   - Ensure DM containers running (`docker compose up -d`) + wait for DM health (60 retries for collectstatic)
   - DM migrations (`migrate --noinput`)
   - Force restart all SPAs (`docker compose down; docker compose up -d`) + health checks
   - Proxy reload + nginx restart

**Key learnings baked into this flow:**
- DM `collectstatic` takes 10-15 min on cold boot — never use `ibl dm update` (force-recreates containers)
- Mentor SPA doesn't auto-start from AMI — must use `down + up`, not just `up -d`
- Postgres data dir gets chowned to ubuntu by pre-tasks — must restore to uid 999
- `--prod-images-tag` flag controls which version of `iblai-prod-images` to install

**AWS helpers** (`providers/aws.py`): `launch_instance`, `wait_for_instance_running`, `register_target`, `terminate_instance`

**GitHub Actions integration** (`iblai/iblai-web-ops`): reusable workflow adds temp SSH SG rule for runner IP, runs service-update, revokes rule. Uses `CI=true` detection for plain text output.

### Ingress System

Pre-provisioned domain endpoints (DNS + ACM certs + ALB listener) that environments can be assigned to. Eliminates cert validation and DNS propagation delays during resetup/launch.

**Registry** (`~/.iblai-infra/ingress.json`):
```json
{
  "entries": [{"name": "stg1", "domain": "stg1.example.com", "created_at": "..."}],
  "lock": {"backend": "s3", "bucket": "my-bucket", "prefix": "ingress-locks"}
}
```

Backward-compatible: if the file contains a bare list `[{...}]`, it auto-migrates to the registry format.

**Models** (`models.py`):
- `IngressEntry` — name, domain, created_at
- `IngressLockConfig` — backend (`"local"` or `"s3"`), bucket, prefix
- `IngressRegistry` — entries + lock config

**State functions** (`terraform/state.py`):
- Registry CRUD: `load_ingress_registry()`, `save_ingress_registry()`, `load_ingress()`, `add_ingress()`, `remove_ingress()`
- Lock config: `configure_ingress_lock(bucket, prefix)`
- Lock operations: `claim_ingress(name, claimed_by)`, `release_ingress_lock(name)`, `get_ingress_status()`
- Two backends: **local** (files in `~/.iblai-infra/locks/`) and **S3** (objects at `s3://<bucket>/<prefix>/<name>.lock`)

**CLI commands** (`iblai infra ingress <subcommand>`):

| Command | Purpose |
|---------|---------|
| `add <name> <domain>` | Register an endpoint |
| `remove <name>` | Unregister an endpoint |
| `list` | List all registered endpoints |
| `configure --bucket <bucket>` | Set S3 as lock backend |
| `status` | Show free/claimed status for all endpoints |
| `claim [name] --by <id> [--quiet]` | Claim a free slot (`--quiet` prints only domain for CI piping) |
| `release <name>` | Free a claimed slot |

**Resetup integration** (`_select_domain()` in `prompts/setup.py`):
- If ingress entries exist: `questionary.select()` picker with entries + "Custom domain..." fallback
- If no entries: standard free-text prompt

**Launch integration**: `--ingress <name>` flag resolves to domain from registry, alternative to `--domain`.

**CI/CD pattern** (GitHub Actions with ephemeral runners):
1. Re-register endpoints at workflow start (4 `ingress add` commands)
2. `ingress configure --bucket <bucket>` for persistent S3 locks
3. `ingress claim --by "run-$ID" --quiet` → capture domain
4. Run launch/resetup with claimed domain
5. `ingress release <name>` on teardown or failure

### Prompt Patterns

- **Short lists** (≤5 items): `questionary.select()` — arrow-key navigation
- **Long lists** (regions, profiles, instance types, key pairs): `questionary.autocomplete()` — type to filter
- `questionary.autocomplete()` only accepts plain strings, not `Choice` objects. Use label-to-value mapping dicts:
  ```python
  labels = {"us-east-1": "us-east-1", ...}  # or {"t3.2xlarge  - 8 vCPU, 32 GB RAM": "t3.2xlarge"}
  selection = questionary.autocomplete("Pick:", choices=list(labels.keys())).ask()
  value = labels[selection]
  ```
- **Important:** `questionary.fuzzy()` does NOT exist. Only: `select`, `autocomplete`, `text`, `password`, `path`, `confirm`, `checkbox`, `rawselect`
- `prompt_credentials(show_step: bool = True)` — `show_step=False` hides "Step 1 of 5" when called outside the wizard

### Models (Pydantic)

`InfraConfig` is the **single contract** between the wizard prompts and Terraform execution:
- `deployment_type` (`DeploymentType`: `SINGLE` or `MULTI`, defaults to `SINGLE`)
- `AWSCredentials` (method, profile, keys, region, account_id)
- `NetworkConfig` (vpc_cidr, vpn_ip with IP validation)
- `ComputeConfig` (instance_type, volume_size ≥20GB, volume_type) — used for single-server
- `MultiServerConfig` (optional, used for multi-server — see below)
- `SSHConfig` (method, key_name, public_key, private_key_path)
- `CertificateConfig` (method: acm/upload/none, zone_id, cert files)
- `DNSConfig` (base_domain, use_route53, hosted_zone_id, 16 subdomains)

`MultiServerConfig` — multi-server compute and managed services:
- `app_server_count` (2-10), `app_server_instance_type`, `app_server_volume_size`
- `services_instance_type`, `services_volume_size`
- `enable_mysql`, `enable_postgres`, `enable_redis` (all default `False`)
- DB/Redis passwords use `Field(exclude=True)` — generated at runtime, never serialized to `state.json`

`ProjectState` tracks lifecycle: initialized → created → failed → destroyed.

`IngressEntry`, `IngressLockConfig`, `IngressRegistry` — ingress endpoint management (see Ingress System section).

`SetupConfig` is the **contract** between setup prompts and `AnsibleRunner`:
- SSH access (private_key_path, ssh_user, target_host)
- Platform config (base_domain, edx_version, env_config, image tags for DM/edX/SPAs, enable_ai)
- Credentials (aws_access_key_id, aws_secret_access_key, aws_default_region, git_access_token)
- Optional: openai_api_key, admin_username, admin_email, admin_password

### Terraform Runner

- Uses `terraform apply -json` for structured event streaming
- Parses `apply_start`, `apply_progress`, `apply_complete`, `apply_errored` events
- `terraform show -json tfplan` for accurate resource count before apply
- Rich Live display: resource status table + progress bar, `transient=True`
- `_copy_templates()` selects template directory based on `config.deployment_type.value` (`single-server` or `multi-server`)
- `_generate_tfvars()` converts InfraConfig → terraform.tfvars; emits multi-server variables (app_server_count, services config, enable_mysql/postgres/redis, DB passwords) when `deployment_type == MULTI`
- `RESOURCE_LABELS` maps AWS resource types to human-friendly names (includes NAT Gateway, RDS, ElastiCache, EFS for multi-server)

### Ansible Runner

- Runs `ansible-playbook playbook.yml --extra-vars <JSON>` as a subprocess
- Secrets (AWS keys, GitHub token) passed via `--extra-vars`, never written to disk
- Parses stdout line-by-line: `TASK [role : desc]` patterns for progress, `fatal:`/`FAILED!` for errors
- Rich Live display: role status table + progress bar, `transient=True`
- **Error handling:** trusts `proc.returncode` as the primary success signal. Tasks with `ignore_errors: true` emit `fatal:` lines but Ansible returns 0 — runner shows these as warnings, not failures
- `ROLE_LABELS` maps role names to human-friendly labels (9 roles)
- DM postgres tasks read `$POSTGRES_USER` and `$POSTGRES_DB` from container env (not hardcoded)
- DM and edX roles verify containers via web endpoint readiness (not just `docker ps`) and check `RestartCount` to catch crash-looping containers
- `final_steps` role: config save, proxy reload, launch oauth/oidc/edx-manager, dm auth-setup, edx sync-with-manager, configure OpenAI credential (if provided), create super admin (DM + LMS), seed CSRF exempt domains, enable UseMainLLMKey for main platform, seed flows/llm-registry/base-mentors/rbac-data
- Django `JSONField` values must be passed as dicts, not `json.dumps()` strings — auto-serialization handles encoding
- SPA boolean config values (`ENABLE_RBAC`, `STRIPE_ENABLED`, etc.) must be written as quoted strings (`'true'`/`'false'`) via Python yaml — `ibl config save --set` cannot handle quoted string values
- `ibl-edx-uwsgi` plugin and other list-type config values must be manipulated via Python yaml, not `ibl config save --set` — the CLI's `printvalue` returns Python list repr that can't be round-tripped

### IAM Permission Checks

- `iblai infra permissions` — displays the minimum IAM policy JSON required for provisioning
- `iblai infra permissions --check` — dry-run verification against active credentials
- Checks 7 services: EC2, ELB, S3, ACM, Route 53, IAM, STS
- Uses harmless read-only API calls (e.g., `DryRun=True` for EC2, `list_*` for others)
- `REQUIRED_IAM_POLICY` dict and `check_permissions()` live in `providers/aws.py`
- Accepts `--profile` and `--region` flags for targeting specific credentials

### State Management

- Workspace root: `~/.iblai-infra/projects/<name>/`
- Session file: `~/.iblai-infra/session.json`
- Ingress registry: `~/.iblai-infra/ingress.json`
- Ingress locks (local backend): `~/.iblai-infra/locks/<name>.lock`
- State file: `state.json` (Pydantic `ProjectState` serialized)
- Terraform files copied to workspace from templates

### Certificate Modes (in Terraform)

| Mode | DNS | HTTPS | Implementation |
|------|-----|-------|----------------|
| ACM | Route53 auto-managed | Yes | ACM certs + DNS validation + HTTPS listener |
| Upload | External (user-managed) | Yes | IAM server cert + HTTPS listener |
| None | External (user-managed) | No | HTTP only (user warned) |

Uses `locals` with `use_acm`, `use_upload`, `use_https` booleans and conditional `count`.

### Deployment Types

Three Terraform topologies selected via `DeploymentType` enum:

**Single-server** (`templates/aws/single-server/`):
- 1 EC2 instance (public subnet) behind ALB
- VPC with 2 public subnets (multi-AZ)
- All services on one machine

**Multi-server** (`templates/aws/multi-server/`):
- N app servers (2-10, public subnets, behind ALB) — run edX/LMS/CMS
- 1 services server (private subnet) — runs DM, SPAs, databases
- VPC with 4 subnet tiers: public, private, database, cache (2-3 AZs)
- NAT gateways (one per AZ) for private subnet outbound
- EFS for shared OpenEdX media across app servers
- Optional managed MySQL 8.4 (RDS, multi-AZ, encrypted)
- Optional managed PostgreSQL 15 (RDS, multi-AZ, encrypted)
- Optional Redis ElastiCache (multi-AZ, encrypted, auth token)
- 6 security groups: ALB, app servers, services, RDS, Redis, EFS

**Call-server** (`templates/aws/call-server/`):
- 1 EC2 instance with Elastic IP in an **isolated VPC** (default `10.1.0.0/16`, distinct from single/multi 10.0/16)
- 2 public subnets (multi-AZ for future NLB if needed)
- **No ALB** — LiveKit needs direct UDP/TCP, so traffic hits the EIP directly
- **No S3, no RDS, no ACM** — LiveKit terminates TLS in-process (typically via Caddy/Let's Encrypt driven by `ibl call start`)
- Optional Route53 A record (`hosted_zone_id` → `<base_domain>` → EIP)
- 1 security group with the full LiveKit port set from [LiveKit's self-hosting guide](https://docs.livekit.io/transport/self-hosting/ports-firewall/):
  - Always open: TCP 22 (SSH, `vpn_ip/32`), TCP 80/443, TCP 7880 (API/WS), TCP 7881 (ICE-TCP), UDP 7882 (ICE mux), UDP 50000-60000 (ICE host), TCP 5349 (TURN/TLS), UDP 3478 (TURN/STUN)
  - SIP stack (opened only when `enable_sip=true`): TCP+UDP 5060, TCP 5061, UDP 10000-20000 (RTP)
- Ansible: `docker` + `awscli` + `python` + `ibl_cli_ops` + `ibl_call` (5 roles). Skips `ibl_platform`, `ibl_dm`, `ibl_edx`, `ibl_spa`, `integrations`, `admin_setup`, `data_seeding` — LiveKit is standalone.
- `ibl_call` role runs: persist `IBL_ROOT=/ibl/` in `~/.bashrc` → `ibl config save --set BASE_DOMAIN=…` → `ibl config environment call-only` → ECR login → `ibl call up` → wait for `:7880` → `ibl call show-call-secrets` (printed to operator terminal, never persisted locally). **Use `ibl call up`, not `ibl call start`** — `start` in `iblai-cli-ops ≤ 5.8.1` passes `--remove-orphans` to a `docker compose` subcommand that Docker Compose v5 rejects.
- **BASE_DOMAIN convention:** pass the **parent** domain (e.g. `stg1.iblai.org`), NOT `call.stg1.iblai.org`. `ibl call` auto-prepends `call.` when generating `LIVEKIT_WS_URL`, so the doubled form produces `wss://call.call.stg1.iblai.org`. Provision prompt asks for "Call server base domain" and shows the WS URL that will be generated.

**Open source safety:** Templates contain zero hardcoded IPs, SSH keys, account IDs, or secrets. DB passwords and Redis auth tokens are generated at runtime via `generate_password()`, passed through `terraform.tfvars` (in `~/.iblai-infra/` workspace, not in the repo), and excluded from `state.json` serialization via `Field(exclude=True)`. LiveKit API key + secret are generated by `ibl call start` on the server and printed to the operator via Ansible `debug` — they never hit the local machine.

**Backward compatibility:** `deployment_type` defaults to `SINGLE`; `multi_server` and `call_server` both default to `None`. Existing `state.json` files deserialize correctly. Multi-server outputs include backward-compat singular outputs (`instance_id`, `instance_public_ip`, `ssh_command` pointing at first app server). Call-server outputs include the same three singular names (pointing at the EIP).

## Brand & UI

- **Primary color:** `#2175C5` (ibl.ai blue)
- **Palette:** `#5BA3E0` (light), `#A8D0F2` (pale), `#174E87` (dark), `#0E3259` (navy)
- Rich theme applied globally via `IBL_THEME`
- questionary styled via `PROMPT_STYLE`
- ASCII art logo banner in `ui.banner()`
- Step progress breadcrumb bar in `ui.step_header()`
- Command references in instructional text use `[brand]...[/brand]` for highlighting

## Dependencies

- `typer>=0.12` — CLI framework
- `rich>=13.7` — Terminal UI
- `questionary>=2.0` — Interactive prompts
- `pydantic>=2.5` — Data validation
- `boto3>=1.34` — AWS SDK

## Testing

- **543 tests**, all via pytest: `uv run pytest tests/ -v`
- Coverage report: `uv run pytest tests/ --cov=iblai_infra --cov-report=term-missing`
- Dev dependencies: `uv sync --extra dev`
- Test patterns:
  - Fixtures in `tests/conftest.py` provide `aws_credentials`, `infra_config`, `project_state`, `setup_config`, `workspace_root` (patched to tmp_path)
  - Rich Console tests: always include `theme=ui.IBL_THEME` to avoid `MissingStyle` errors
  - ANSI stripping: use `re.sub(r"\x1b\[[0-9;]*m", "", text)` when asserting Rich output with `force_terminal=True`
  - Local imports in functions (e.g., `load_session` importing `validate_credentials`): patch at the **source module** (`iblai_infra.providers.aws.validate_credentials`), not the importing module
  - `typer.Exit` wraps `click.exceptions.Exit` — catch with `pytest.raises((SystemExit, typer.Exit, click.exceptions.Exit))`
  - questionary mocking: `patch("questionary.select")` then `mock.return_value.ask.return_value = "value"`

## Conventions

- Python 3.11+, `from __future__ import annotations`
- `src/` layout with hatchling build
- Dynamic versioning: `pyproject.toml` uses `[tool.hatch.version]` pointing to `__init__.py`
- Package manager: `uv`
- All prompts return Pydantic models, never raw dicts
- UI helpers (`ui.success()`, `ui.error()`, etc.) for all terminal output
- Terraform templates use standard HCL, not Jinja2
- Terraform template strings must use ASCII only (no em dashes, special characters) — AWS APIs reject non-ASCII in descriptions
- State persisted as JSON via Pydantic's `.model_dump_json()`
- When using `ctx.invoke()` with Typer, always pass explicit values for all parameters (Typer passes `OptionInfo` objects as defaults, which break Pydantic validation)
