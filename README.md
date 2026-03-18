<div align="center">

<a href="https://ibl.ai"><img src="https://ibl.ai/images/iblai-logo.png" alt="ibl.ai" width="300"></a>

# Infra CLI

Interactive CLI for provisioning and configuring the [ibl.ai](https://ibl.ai) platform on AWS. Handles end-to-end infrastructure creation with Terraform and full application setup with Ansible.

[![AWS](https://img.shields.io/badge/AWS-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com)
[![Terraform](https://img.shields.io/badge/Terraform-7B42BC?logo=terraform&logoColor=white)](https://www.terraform.io)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-blue.svg)](#license)

</div>

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

There are two ways to set up the IBL platform on a server:

**Option A: After Terraform provisioning** -- if you used `iblai infra provision` to create the server:

```bash
iblai infra setup <project-name>
```

**Option B: Bootstrap an existing server** -- if you already have a server (any cloud, bare metal, etc.):

```bash
iblai infra bootstrap
```

The bootstrap command walks you through providing the server IP, SSH key, domain, image tags, and credentials interactively. No Terraform state required.

Both options run the same Ansible playbook with 9 sequential roles:

| Role | What it does |
|------|-------------|
| `docker` | Installs Docker Engine, docker compose, and apache2-utils |
| `awscli` | Installs AWS CLI v2 for ECR and S3 access |
| `python` | Installs pyenv and Python 3.11.8 |
| `iblai_cli_ops` | Clones and installs [iblai-cli-ops](https://github.com/iblai/ibl-cli-ops) in a virtualenv |
| `base_config` | Configures base domain, environment, image tags, and service defaults |
| `iblai_dm_pro` | Launches iblai-dm-pro (PostgreSQL, Redis, Django, Celery, Flowise) |
| `iblai_edx_pro` | Launches iblai-edx-pro (LMS, CMS, MySQL, MongoDB, Redis, Elasticsearch) |
| `iblai_spa` | Configures OAuth2 SSO and launches iblai-web-frontend (Auth, Mentor AI, Skills AI) |
| `auth_config` | Reloads proxy, sets up OAuth/OIDC integrations, runs DM auth setup |

The setup wizard prompts for:
- Target host IP and SSH key path
- Base domain and environment config
- DM and edX Docker image tags
- SPA image tags (Auth, Mentor, Skills)
- Whether to enable AI features

### 4. Manage environments

```bash
iblai infra list                # List all managed environments
iblai infra status <name>       # Show infrastructure details and outputs
iblai infra auth                # Switch AWS credentials
iblai infra destroy <name>      # Tear down infrastructure (with confirmation)
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
├── tests/                      # 380+ tests
├── docs/                       # Architecture diagrams
└── pyproject.toml
```

## License

Proprietary -- ibl.ai
