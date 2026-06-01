<div align="center">

<a href="https://ibl.ai"><img src="https://ibl.ai/images/iblai-logo.png" alt="ibl.ai" width="300"></a>

# Infra CLI

Interactive CLI for provisioning and configuring the [ibl.ai](https://ibl.ai) platform on AWS. Handles end-to-end infrastructure creation with Terraform and full application setup with Ansible. Can also bootstrap existing servers (any cloud or bare metal) without Terraform.

[![AWS](https://img.shields.io/badge/AWS-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com)
[![Terraform](https://img.shields.io/badge/Terraform-7B42BC?logo=terraform&logoColor=white)](https://www.terraform.io)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-blue.svg)](#license)

</div>

> **Note:** This repository contains the installation and infrastructure provisioning guide. Access to the ibl.ai Docker images and platform codebase requires a license. To get started, reach out to our team at [ibl.ai/contact](https://ibl.ai/contact).

---

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- **[Terraform](https://developer.hashicorp.com/terraform/install)** installed and on PATH
- **AWS account** with EC2, ELB, S3, ACM, Route53, IAM, and STS permissions
- **SSH access** to the target EC2 instance (key is generated or provided during provisioning)

### Dependencies installed automatically

The following are installed as Python package dependencies when you install iblai-infra-ops:

- **ansible-core** (>= 2.15) -- used by `iblai infra setup` to configure the server
- **boto3** -- AWS SDK for authentication and resource management
- **terraform** -- called as a subprocess (must be installed separately, see above)

### What the Ansible setup installs on the target server

The setup phase installs and configures the following on the provisioned EC2 instance:

- **[iblai-cli-ops](https://github.com/iblai/iblai-cli-ops)** -- the IBL platform management CLI, installed inside a pyenv virtualenv on the server. Required by every service launch. **Private repository — unauthenticated requests see a 404.**
- **Docker Engine** with docker compose
- **pyenv** with Python 3.11.8
- **AWS CLI v2** for ECR authentication and S3 access

## Install

Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repo
git clone git@github.com:iblai/iblai-infra-ops.git
cd iblai-infra-ops

# Create a virtual environment and install
uv venv
source .venv/bin/activate
uv pip install .
```

For development:

```bash
uv pip install -e ".[dev]"
```

Using pip:

```bash
pip install .
```

### Verify installation

```bash
iblai --version
```

Verify Ansible is available (installed as a dependency):

```bash
ansible-playbook --version
```

Verify Terraform is installed:

```bash
terraform --version
```

## Usage

Run `iblai infra` to see all available commands and a getting-started guide.

### 1. Check IAM permissions

Before provisioning, verify your AWS credentials have the required permissions:

```bash
iblai infra permissions              # Show required IAM policy JSON
iblai infra permissions --check      # Dry-run verification against active credentials
```

### 2. Provision infrastructure

```bash
iblai infra provision
```

Interactive wizard that walks you through:

1. **AWS credentials** -- profile, access keys, or environment variables
2. **Deployment topology** -- single-server, multi-server (N app servers + 1 services server), or call-server (standalone LiveKit)
3. **Project & compute** -- name, environment (dev/staging/prod), instance type, volume size
4. **Network & SSH** -- VPC CIDR, VPN IP for SSH access, SSH key setup
5. **Domain & certificates** -- base domain, Route53 integration, certificate method (ACM, upload, or none)
6. **WAF (optional)** -- single-server only; attach an AWS WAFv2 Web ACL to the ALB with admin-only allow rules for Swagger UI / edX Studio / `/admin/` / DM `/data` plus AWS managed rule groups. Default off. Skip here and add it later with `iblai infra waf enable <name>`.
7. **Review** -- full summary before applying

Sizing guidance: single / multi-server require a **100 GB minimum** root volume. Picking a 32 GB-RAM instance prints a non-blocking heads-up suggesting 64 GB (e.g. `m5.4xlarge` / `r5.2xlarge`) when AI features will be enabled.

Terraform runs with real-time progress showing each resource as it's created.

### 3. Setup the platform

```bash
iblai infra setup              # Set up an existing server (any provider, bare metal)
iblai infra setup <name>       # Set up a Terraform-provisioned environment by name
```

Both paths run the same Ansible playbook. The difference is where the inputs come from:

- **With a project name** -- auto-populates IP, domain, SSH key, and AWS credentials from the Terraform state
- **Without a project name** -- prompts for server IP, SSH key, domain, image tags, and credentials interactively. No Terraform required.

The playbook runs sequential roles grouped by concern:

| Phase | Roles | What it does |
|---|---|---|
| Host setup | `docker`, `awscli`, `python` | Docker Engine + compose, AWS CLI v2, pyenv + Python 3.11.8 |
| Platform install | `ibl_cli_ops`, `ibl_platform` | Installs [iblai-prod-images](https://github.com/iblai/iblai-prod-images) (pins `iblai-cli-ops` + image versions); configures base domain, CORS, RBAC, gateway, defaults |
| Core services | `ibl_dm`, `ibl_edx`, `ibl_spa` | Launches DM (Django + Postgres + Redis + Celery + Flowise + Langfuse), edX (LMS / CMS / MySQL / MongoDB / Elasticsearch / Forum), and the Auth / Mentor / Skills SPAs |
| Finalization | `integrations`, `admin_setup`, `data_seeding`, `ibl_tenant_platform` | OAuth/OIDC setup, syncs edX with DM, creates super admin, seeds CSRF / flows / LLM registry / mentors / RBAC; launches a tenant `Platform` via `run_launch_steps` when `PLATFORM_NAME` is set to anything other than `main` |
| Optional integrations | `smtp_config`, `stripe_config`, `google_sso_config`, `microsoft_sso_config` | Each role no-ops unless its trigger key (`SMTP_HOST` / `STRIPE_SECRET_KEY` / `GOOGLE_SSO_CLIENT_ID` / `MICROSOFT_SSO_CLIENT_ID`) is set |
| Post-tasks | `ibl global-proxy reload` | Final nginx reload so any SSO-driven edX/SPA restarts are picked up before exit |

The setup wizard prompts for: target host + SSH key, base domain, tenant platform name (blank for `main` — `main` itself is reserved), `iblai-cli-ops` release tag, enable-AI toggle, OpenAI key, super admin credentials, GitHub PAT, and AWS credentials. Reserved usernames (e.g. `ibl_admin`) are rejected — the new default suggestion is `platform_admin`. Stripe billing UI and advertising are **off by default**; enable Stripe by passing `STRIPE_SECRET_KEY`.

### 4. Non-interactive provision + setup (`.env` file)

Skip the wizards. Same Terraform + same Ansible roles as the interactive flow, driven from a `.env` file. **Single-server only** (multi / call still use the wizard).

```bash
# Provision (Terraform) — fresh single-server, no AMI required
cp .env.provision.example .env.provision && $EDITOR .env.provision
iblai infra provision-env -f .env.provision

# Bootstrap (Ansible) — against the just-provisioned project
cp .env.setup.example .env.setup && $EDITOR .env.setup
iblai infra setup-env <project-name> -f .env.setup
```

**Free-standing server** (any cloud, no Terraform): omit the project name and add `TARGET_HOST`, `SSH_PRIVATE_KEY_PATH`, `BASE_DOMAIN`, `PROJECT_NAME` to `.env.setup`, then `iblai infra setup-env -f .env.setup`.

**Schema:** `.env.provision.example` and `.env.setup.example` document every key inline (required vs. optional, defaults, integration triggers).

**Security:** populated `.env` files are gitignored (`.env.*` blocked except `*.example`). The CLI never persists secrets to `state.json` — they ride `--extra-vars` into Ansible at run time only.

### 5. Re-setup an existing environment

```bash
iblai infra resetup <name>
```

Re-configures a previously set up environment with a new domain and fresh secrets. Prompts for the new base domain, CLI ops release tag, and credentials. Runs `ibl config rotate-secrets` to regenerate all secrets, syncs database passwords (PostgreSQL and MySQL), then restarts all services.

Use this when you need to change the domain or rotate credentials on a running environment without reprovisioning the infrastructure.

### 6. Manage optional features post-provision

Some features (currently WAF; more to follow — SMTP, Stripe, SSO providers) can be turned on or off against an already-provisioned stack without re-running the wizard or destroying anything. Each feature gets its own subgroup under `iblai infra <feature>`:

```bash
iblai infra waf enable [<name>]              # interactive: prompts for admin IPs/CIDRs
iblai infra waf enable-env [<name>] -f .env  # non-interactive: reads WAF_ALLOWED_IPS
iblai infra waf disable <name> [--yes]       # removes Web ACL + IPSet; ALB stays intact
iblai infra waf status [<name>]              # table of all eligible stacks, or detail panel for one
```

Running `enable` on a stack that already has WAF on prompts to update the allowlist (current IPs pre-filled for easy edit). Single-server only — multi-server / call-server / bootstrap projects are rejected with a clear error. Each toggle re-runs Terraform on the existing workspace; the original S3 bucket names and other resources are preserved.

### 7. Launch from AMI

One-shot Terraform + Ansible from a pre-built AMI. Two equivalent entry points — `.env` for ergonomics, flags for CI/CD.

```bash
# .env-driven (review + confirm)
cp .env.example .env && $EDITOR .env
iblai infra launch-env

# Fully non-interactive (CI/CD pipelines)
iblai infra launch \
  --ami-id $AMI_ID --domain $DOMAIN --hosted-zone-id $HOSTED_ZONE_ID \
  --aws-key-id $AWS_ACCESS_KEY_ID --aws-secret-key $AWS_SECRET_ACCESS_KEY \
  --ssh-public-key "$SSH_PUBLIC_KEY" --ssh-key $SSH_KEY_PATH \
  --git-token $GIT_TOKEN --vpn-ip $VPN_IP \
  --admin-email $ADMIN_EMAIL --admin-password $ADMIN_PASSWORD
```

**Flow:** Terraform creates VPC / ALB / ACM / Route53 and launches EC2 from the AMI → Ansible sets the domain, rotates secrets, syncs DB passwords, restarts services, runs OAuth + admin + seeding → final `ibl global-proxy reload`.

See `iblai infra launch --help` for optional flags (instance type, volume size, region, `--platform-name`, SMTP / Stripe / SSO toggles, `--enable-ai`).

### 8. Service update (image updates, CI/CD)

Update container images and restart services on an existing server or a freshly launched AMI. No infrastructure provisioning, no secret rotation.

**Update an existing server:**

```bash
iblai infra service-update \
  --host 10.0.1.50 \
  --ssh-key ~/.ssh/key.pem \
  --git-token $GIT_TOKEN
```

**Launch from AMI + update + register in ALB target group:**

```bash
iblai infra service-update \
  --ami-id $AMI_ID \
  --subnet-id $SUBNET_ID \
  --security-group-id $SECURITY_GROUP_ID \
  --target-group-arn $TARGET_GROUP_ARN \
  --key-pair-name $KEY_PAIR_NAME \
  --ssh-key ~/.ssh/key.pem \
  --git-token $GIT_TOKEN \
  --aws-key-id $AWS_ACCESS_KEY_ID \
  --aws-secret-key $AWS_SECRET_ACCESS_KEY
```

What it does: installs latest `iblai-prod-images` (new image versions) → edX stop/start → DM update → DM migrations → SPA restart → nginx restart.

### 9. Manage environments

```bash
iblai infra list                # List all managed environments
iblai infra status <name>       # Show infrastructure details and outputs
iblai infra auth                # Switch AWS credentials
iblai infra destroy <name>      # Tear down infrastructure or remove bootstrap project
```

## Authentication

The CLI always lets you choose how to authenticate -- it never silently auto-detects credentials. On first use, it walks you through authentication interactively.

Supported methods:
- **AWS profiles** from `~/.aws/config` and `~/.aws/credentials` (type to filter)
- **Environment variables** (`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`)
- **Manual entry** -- access key + secret key (masked input)

Your session is saved after authentication and reused across commands until you switch credentials or it expires.

## Architecture

### AWS Single Server

<div align="center">
<img src="https://ibl.ai/architecture/aws-single.png" alt="AWS Single Server Architecture" width="800">
</div>

### AWS Multi Server

<div align="center">
<img src="https://ibl.ai/architecture/aws-multi.png" alt="AWS Multi Server Architecture" width="800">
</div>

## What gets created

### AWS Infrastructure (Terraform)

- VPC with 2 public subnets across availability zones (10.0.0.0/16)
- EC2 instance (Ubuntu 22.04) with encrypted EBS volume (AES-256)
- Application Load Balancer with TLS 1.2/1.3 termination
- ACM certificates (RSA 2048-bit, DNS-validated, auto-renewed)
- Security groups (SSH restricted to VPN CIDR, HTTP/HTTPS from ALB only)
- 3 S3 buckets with server-side encryption (backups, media, static)
- Route53 hosted zone with 19 subdomain A-records
- *Optional:* AWS WAFv2 Web ACL attached to the ALB with admin-IP allowlist, six AWS managed rule groups, and a path-traversal block (opt-in via the wizard or `iblai infra waf enable`)

### Platform Services (Ansible)

- **iblai-edx-pro** -- LMS, CMS, workers, MySQL 8.0, Redis, MongoDB, Elasticsearch, Forum, Notes, Meilisearch, SMTP relay, Caddy
- **iblai-dm-pro** -- Django web, ASGI, Celery worker/beat, PostgreSQL 16, Redis, Flowise AI
- **iblai-web-frontend** -- Auth, Mentor AI, Skills AI single-page applications
- **Monitoring** -- Prometheus, Grafana, AlertManager, metric exporters
- **Nginx** reverse proxy

## Workspace

All Terraform state, SSH keys, and project configuration are stored at:

```
~/.iblai-infra/projects/<project-name>/
```

## Development

### Running tests

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

With coverage:

```bash
uv run pytest tests/ --cov=iblai_infra --cov-report=term-missing
```

### Project structure

```
iblai-infra-ops/
├── src/iblai_infra/
│   ├── cli.py                  # Typer CLI commands
│   ├── app.py                  # Application logic
│   ├── models.py               # Pydantic models
│   ├── ui.py                   # Rich terminal UI
│   ├── env_provision.py        # .env → InfraConfig (provision-env)
│   ├── env_setup.py            # .env → SetupConfig  (setup-env)
│   ├── prompts/                # Interactive questionary prompts
│   ├── providers/              # AWS provider (STS, EC2, S3, WAFv2)
│   ├── features/               # Post-provision feature toggles (`iblai infra <feature> <action>`)
│   │   └── waf.py              #   `iblai infra waf` — enable / enable-env / disable / status
│   ├── terraform/              # Terraform runner + templates
│   │   └── templates/aws/      # single-server (incl. optional waf.tf), multi-server, call-server
│   └── ansible/                # Ansible runner + templates
│       └── templates/single-server/
│           ├── playbook.yml             # interactive setup + setup-env
│           ├── launch_playbook.yml      # AMI launch + launch-env
│           ├── service_update_playbook.yml
│           └── roles/                   # ansible roles (see playbook table)
├── tests/                      # 648 tests, ~2s
├── docs/                       # Architecture diagrams
└── pyproject.toml
```

## License

Proprietary -- ibl.ai
