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

- **[iblai-cli-ops](https://github.com/iblai/ibl-cli-ops)** -- the IBL platform management CLI, cloned and installed inside a pyenv virtualenv on the server. This is a required dependency for all service launches. **Note:** This is a private repository -- unauthenticated users or those without access will see a 404.
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
2. **Project & compute** -- name, environment (dev/staging/prod), instance type, volume size
3. **Network & SSH** -- VPC CIDR, VPN IP for SSH access, SSH key setup
4. **Domain & certificates** -- base domain, Route53 integration, certificate method (ACM, upload, or none)
5. **Review** -- full summary before applying

Terraform runs with real-time progress showing each resource as it's created.

### 3. Setup the platform

```bash
iblai infra setup              # Set up an existing server (any provider, bare metal)
iblai infra setup <name>       # Set up a Terraform-provisioned environment by name
```

Both paths run the same Ansible playbook. The difference is where the inputs come from:

- **With a project name** -- auto-populates IP, domain, SSH key, and AWS credentials from the Terraform state
- **Without a project name** -- prompts for server IP, SSH key, domain, image tags, and credentials interactively. No Terraform required.

The playbook runs 9 sequential roles:

| Role | What it does |
|------|-------------|
| `docker` | Installs Docker Engine, docker compose, and apache2-utils |
| `awscli` | Installs AWS CLI v2 for ECR and S3 access |
| `python` | Installs pyenv and Python 3.11.8 |
| `ibl_cli_ops` | Installs [iblai-prod-images](https://github.com/iblai/iblai-prod-images) (which includes iblai-cli-ops and pinned image versions) via `uv pip install` |
| `ibl_platform` | Configures base domain, environment, image tags, CORS, RBAC, unified API gateway, and service defaults |
| `ibl_dm` | Launches iblai-dm-pro (PostgreSQL with pgvector, Redis, Django, Celery, Langfuse, Minio) |
| `ibl_edx` | Launches iblai-edx-pro (LMS, CMS, MySQL, MongoDB, Redis, Elasticsearch, MFE) |
| `ibl_spa` | Creates OAuth2 apps, configures and launches Auth, Mentor AI, and Skills AI SPAs |
| `final_steps` | Reloads proxy, OAuth/OIDC setup, syncs edX with DM, creates super admins, seeds CSRF domains, flows, LLM registry, mentors, and RBAC data |

The setup wizard prompts for:
- Target host IP and SSH key path
- Base domain and environment config
- iblai-cli-ops release tag (image versions are pinned by [iblai-prod-images](https://github.com/iblai/iblai-prod-images))
- Whether to enable AI features
- OpenAI API key (optional)
- Super admin credentials (username, email, password)
- GitHub PAT and AWS credentials for the VM

### 4. Non-interactive provision + setup (`.env` file)

Skip the wizards. Same Terraform + same 9-role Ansible playbook as the interactive flow, just driven from a `.env` file. **Single-server only** (multi/call still use the wizard).

```bash
# Provision (Terraform) — fresh single-server, no AMI required
cp .env.provision.example .env.provision
$EDITOR .env.provision                       # fill in PROJECT_NAME, DOMAIN, AWS creds, etc.
iblai infra provision-env -f .env.provision

# Bootstrap (Ansible) — runs against the project just provisioned
cp .env.setup.example .env.setup
$EDITOR .env.setup                           # fill in GIT_TOKEN, admin creds, etc.
iblai infra setup-env <project-name> -f .env.setup
```

**Free-standing server (any cloud, no Terraform):** omit the project name and add `TARGET_HOST`, `SSH_PRIVATE_KEY_PATH`, `BASE_DOMAIN`, `PROJECT_NAME` to your `.env.setup`:

```bash
iblai infra setup-env -f .env.setup          # builds a synthetic ProjectState, runs Ansible
```

**`.env` schema:** `.env.provision.example` and `.env.setup.example` document every key with synthetic placeholders. Required vs. optional, defaults, and integration triggers (SMTP / Stripe / Google SSO / Microsoft SSO — each enabled when its trigger key is set) are inline.

**Security note:** populated `.env` files are gitignored by default (`.gitignore` blocks `.env.*` except the `*.example` templates). Never commit a real `.env`. The CLI never persists secrets to `state.json` — they ride `--extra-vars` into Ansible at run time only.

### 5. Re-setup an existing environment

```bash
iblai infra resetup <name>
```

Re-configures a previously set up environment with a new domain and fresh secrets. Prompts for the new base domain, CLI ops release tag, and credentials. Runs `ibl config rotate-secrets` to regenerate all secrets, syncs database passwords (PostgreSQL and MySQL), then restarts all services.

Use this when you need to change the domain or rotate credentials on a running environment without reprovisioning the infrastructure.

### 6. Launch from AMI

**Simplest way — using a `.env` file:**

```bash
cp .env.example .env      # Copy the template
vim .env                   # Fill in your values
iblai infra launch-env     # Review summary, confirm, launch
```

The CLI reads `.env` from the current directory, shows a summary of what will be launched, and asks for confirmation before proceeding.

**Non-interactive (CI/CD) — using flags:**

```bash
iblai infra launch \
  --ami-id $AMI_ID \
  --domain $DOMAIN \
  --hosted-zone-id $HOSTED_ZONE_ID \
  --aws-key-id $AWS_ACCESS_KEY_ID \
  --aws-secret-key $AWS_SECRET_ACCESS_KEY \
  --ssh-public-key "$SSH_PUBLIC_KEY" \
  --ssh-key $SSH_KEY_PATH \
  --git-token $GIT_TOKEN \
  --admin-email $ADMIN_EMAIL \
  --admin-password $ADMIN_PASSWORD \
  --vpn-ip $VPN_IP
```

Fully non-interactive command for CI/CD pipelines (e.g. GitHub Actions). Provisions AWS infrastructure from a pre-built AMI via Terraform, then configures the platform via Ansible — all in one step.

**What it does:**
1. **Terraform** -- creates VPC, ALB, ACM certificates, Route53 DNS records, and launches EC2 from the specified AMI
2. **Ansible** -- sets domain, rotates secrets, syncs database passwords, restarts all services (DM, edX, SPAs), runs final setup (OAuth, admin creation, data seeding)

**Cleanup:**

```bash
iblai infra destroy <name>    # Tears down all Terraform resources
```

**Using a `.env` file:**

Copy `.env.example` to `.env`, fill in real values, then:

```bash
source .env
iblai infra launch \
  --ami-id $AMI_ID \
  --domain $DOMAIN \
  --hosted-zone-id $HOSTED_ZONE_ID \
  --aws-key-id $AWS_ACCESS_KEY_ID \
  --aws-secret-key $AWS_SECRET_ACCESS_KEY \
  --ssh-public-key "$SSH_PUBLIC_KEY" \
  --ssh-key $SSH_KEY_PATH \
  --git-token $GIT_TOKEN \
  --admin-email $ADMIN_EMAIL \
  --admin-password $ADMIN_PASSWORD \
  --vpn-ip $VPN_IP
```

See `iblai infra launch --help` for all optional flags (instance type, volume size, region, AI features, etc.).

### 7. Service update (image updates, CI/CD)

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

### 8. Manage environments

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
│   ├── prompts/                # Interactive questionary prompts
│   ├── providers/              # AWS provider (STS, EC2, S3)
│   ├── terraform/              # Terraform runner and templates
│   │   └── templates/aws/single-server/
│   └── ansible/                # Ansible runner and templates
│       └── templates/single-server/
│           ├── playbook.yml
│           └── roles/          # 9 Ansible roles
├── tests/                      # 357 tests
├── docs/                       # Architecture diagrams
└── pyproject.toml
```

## License

Proprietary -- ibl.ai
